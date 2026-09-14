"""Restore-Engine.

Vier Bausteine, einzeln oder kombiniert ausfuehrbar:

* **Daten**     - Bind-Mounts und Volumes zurueck an die Originalpfade
* **Template**  - Unraid-Template nach ``templates-user`` schreiben, damit der
                  Container im Docker-Tab der WebGUI wieder auftaucht
* **Container** - Container exakt aus dem gesicherten ``docker inspect`` neu anlegen
                  (Image, Env, Ports, Mounts, Labels, Netzwerke, Restart-Policy)
* **Start**     - Container anschliessend starten

Damit laesst sich ein komplett geloeschter Container vollstaendig wiederherstellen,
auch auf einem frischen Server: fehlende Netzwerke und Volumes werden angelegt und
ein fehlendes Template wird aus der Konfiguration erzeugt.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import archive, config, db, docker_api, events, unraid
from .runner import JobContext

# Felder aus inspect.Config, die beim Anlegen erlaubt sind
_CONFIG_KEYS = [
    "Hostname", "Domainname", "User", "AttachStdin", "AttachStdout", "AttachStderr",
    "Tty", "OpenStdin", "StdinOnce", "Env", "Cmd", "Healthcheck", "Image", "Volumes",
    "WorkingDir", "Entrypoint", "OnBuild", "Labels", "StopSignal", "StopTimeout",
    "Shell", "ExposedPorts", "MacAddress",
]

# Felder aus inspect.HostConfig, die beim Anlegen stoeren
_HOSTCONFIG_DROP = {"Mounts", "ConsoleSize"}


class RestoreError(RuntimeError):
    pass


def load_manifest(backup_ref: str) -> tuple[dict[str, Any], Path]:
    record = db.get_backup(backup_ref)
    if record:
        path = Path(record["path"])
    else:
        path = config.BACKUP_DIR / backup_ref
    manifest_file = path / "manifest.json"
    if not manifest_file.exists():
        raise RestoreError(f"Backup '{backup_ref}' nicht gefunden ({manifest_file})")
    return json.loads(manifest_file.read_text("utf-8")), path


def _path_allowed(target: Path) -> bool:
    resolved = str(target)
    return any(resolved == root or resolved.startswith(root.rstrip("/") + "/")
               for root in config.RESTORE_ROOTS)


# ---------------------------------------------------------------- Vorschau

def preview(backup_ref: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest, path = load_manifest(backup_ref)
    options = options or {}
    path_map: dict[str, str] = options.get("path_map") or {}
    container_name = options.get("target_name") or manifest["container"]["name"]

    artifacts = []
    for art in manifest.get("artifacts", []):
        if art.get("skipped") or not art.get("file"):
            continue
        original = art.get("original_source") or art.get("source") or ""
        target = path_map.get(original, original)
        artifacts.append({
            "kind": art["kind"], "name": art["name"], "destination": art["destination"],
            "original_source": original, "target": target,
            "archive_bytes": art.get("archive_bytes", 0),
            "source_bytes": art.get("source_bytes", 0),
            "files": art.get("files", 0),
            "exists": Path(target).exists() if target else False,
            "allowed": _path_allowed(Path(target)) if target else False,
            "archive_present": (path / "data" / art["file"]).exists(),
        })

    exists = docker_api.exists(container_name)
    template = manifest.get("template") or {}
    missing_networks = []
    for net in manifest.get("networks", []):
        if net["name"] in ("bridge", "host", "none"):
            continue
        try:
            docker_api.client().networks.get(net["name"])
        except Exception:  # noqa: BLE001
            missing_networks.append(net["name"])

    image = manifest["container"].get("image", "")
    return {
        "backup_ref": backup_ref,
        "container": container_name,
        "original_container": manifest["container"]["name"],
        "created_at": manifest.get("created_at"),
        "image": image,
        "image_present": docker_api.image_exists(image) if image else False,
        "container_exists": exists,
        "template": {**template, "target_file": unraid.template_filename(container_name),
                     "templates_dir_available": unraid.available()},
        "artifacts": artifacts,
        "missing_networks": missing_networks,
        "networks": [n["name"] for n in manifest.get("networks", [])],
        "warnings": _warnings(artifacts, exists, missing_networks),
    }


def _warnings(artifacts: list[dict[str, Any]], exists: bool,
              missing_networks: list[str]) -> list[str]:
    out = []
    if exists:
        out.append("Ein Container mit diesem Namen existiert bereits - er wird ersetzt, "
                   "wenn 'Vorhandenen Container ersetzen' aktiv ist.")
    for art in artifacts:
        if not art["allowed"]:
            out.append(f"Zielpfad ausserhalb der erlaubten Bereiche: {art['target']}")
        elif art["exists"]:
            out.append(f"{art['target']} existiert bereits - Dateien werden ueberschrieben.")
        if not art["archive_present"]:
            out.append(f"Archiv fehlt im Backup: {art['name']}")
    if missing_networks:
        out.append(f"Fehlende Docker-Netzwerke werden neu angelegt: {', '.join(missing_networks)}")
    return out


# ---------------------------------------------------------------- Ausfuehrung

def run(ctx: JobContext, backup_ref: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    opts = {
        "restore_data": True,
        "restore_template": True,
        "recreate_container": True,
        "start_container": True,
        "replace_existing": False,
        "wipe_target": False,
        "artifacts": None,        # None = alle
        "path_map": {},
        "target_name": None,
        "verify": config.get("verify_checksums", True),
        **(options or {}),
    }

    manifest, backup_path = load_manifest(backup_ref)
    original_name = manifest["container"]["name"]
    name = opts["target_name"] or original_name
    result: dict[str, Any] = {"backup_ref": backup_ref, "container": name, "steps": []}

    ctx.step(f"Wiederherstellung von '{name}' aus Backup {manifest['id']}", 2)
    db.add_event("restore.started", f"Restore von {name} gestartet (Backup {manifest['id']})",
                 container=name, job_id=ctx.job_id, backup_id=backup_ref)

    existed = docker_api.exists(name)
    if existed and (opts["recreate_container"] or opts["restore_data"]):
        if not opts["replace_existing"] and opts["recreate_container"]:
            raise RestoreError(
                f"Container '{name}' existiert bereits. Aktiviere 'Vorhandenen Container "
                f"ersetzen', um fortzufahren.")
        ctx.step(f"Vorhandenen Container '{name}' anhalten", 5)
        try:
            docker_api.control(name, "stop", timeout=60)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"Stoppen fehlgeschlagen (weiter): {exc}", "warn")

    # ---- 1. Daten -------------------------------------------------------
    if opts["restore_data"]:
        result["steps"].append(_restore_data(ctx, manifest, backup_path, opts))

    # ---- 2. Container entfernen, falls Neuanlage gewuenscht -------------
    if opts["recreate_container"] and existed:
        ctx.step(f"Alten Container '{name}' entfernen", 60)
        try:
            docker_api.control(name, "remove")
        except Exception as exc:  # noqa: BLE001
            raise RestoreError(f"Container '{name}' konnte nicht entfernt werden: {exc}") from exc

    # ---- 3. Unraid-Template --------------------------------------------
    if opts["restore_template"]:
        result["steps"].append(_restore_template(ctx, manifest, backup_path, name))

    # ---- 4. Container neu anlegen --------------------------------------
    if opts["recreate_container"]:
        result["steps"].append(_recreate_container(ctx, manifest, backup_path, name, opts))
    elif opts["start_container"] and docker_api.exists(name):
        ctx.step(f"Container '{name}' starten", 95)
        docker_api.control(name, "start")
        result["steps"].append({"step": "start", "ok": True})

    ctx.progress(100, "Wiederherstellung abgeschlossen")
    db.add_event("restore.completed", f"Restore von {name} abgeschlossen",
                 container=name, job_id=ctx.job_id, backup_id=backup_ref)
    events.publish("restore.finished", {"container": name, "backup_ref": backup_ref})
    return result


def _restore_data(ctx: JobContext, manifest: dict[str, Any], backup_path: Path,
                  opts: dict[str, Any]) -> dict[str, Any]:
    wanted = opts["artifacts"]
    path_map: dict[str, str] = opts["path_map"] or {}
    artifacts = [a for a in manifest.get("artifacts", [])
                 if a.get("file") and not a.get("skipped")
                 and (wanted is None or a["name"] in wanted)]

    if not artifacts:
        ctx.log("Keine Datenarchive im Backup - Schritt uebersprungen", "warn")
        return {"step": "data", "restored": 0, "details": []}

    details = []
    span = 50.0 / len(artifacts)
    for index, art in enumerate(artifacts):
        ctx.check_cancel()
        base = 8 + index * span
        archive_file = backup_path / "data" / art["file"]
        if not archive_file.exists():
            ctx.log(f"Archiv fehlt: {art['file']}", "error")
            details.append({"name": art["name"], "ok": False, "error": "Archiv fehlt"})
            continue

        original = art.get("original_source") or art.get("source") or ""
        target = Path(path_map.get(original, original))
        if not str(target) or not _path_allowed(target):
            ctx.log(f"Zielpfad nicht erlaubt, uebersprungen: {target}", "error")
            details.append({"name": art["name"], "ok": False,
                            "error": f"Zielpfad nicht erlaubt: {target}"})
            continue

        if art["kind"] == "volume":
            docker_api.ensure_volume(art["name"])
            mountpoint = docker_api.volume_mountpoint(art["name"])
            mapped = config.DOCKER_VOLUMES_DIR / art["name"] / "_data"
            if mapped.parent.exists():
                target = mapped
            elif mountpoint:
                target = Path(mountpoint)
            target.mkdir(parents=True, exist_ok=True)

        if opts["verify"] and art.get("sha256"):
            ctx.step(f"Pruefsumme von {art['name']} wird geprueft", base)
            if not archive.verify(archive_file, art["sha256"]):
                ctx.log(f"Pruefsumme von {art['name']} stimmt NICHT - abgebrochen", "error")
                details.append({"name": art["name"], "ok": False,
                                "error": "Pruefsumme stimmt nicht"})
                continue

        if opts["wipe_target"] and target.exists() and target.is_dir():
            ctx.log(f"Zielverzeichnis {target} wird geleert")
            for entry in target.iterdir():
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)

        target.mkdir(parents=True, exist_ok=True)
        ctx.step(f"Entpacke {art['name']} nach {target}", base)

        def on_progress(done: int, total: int, _base=base, _span=span,
                        _name=art["name"]) -> None:
            ctx.progress(_base + _span * (done / total if total else 1),
                         f"{_name}: wird entpackt")

        res = archive.unpack(archive_file, target, progress=on_progress,
                             cancelled=ctx.check_cancel)
        if res["error_count"]:
            ctx.log(f"{art['name']}: {res['error_count']} Fehler beim Entpacken", "warn")
            for line in res["errors"][:5]:
                ctx.log(f"  {line}", "warn")
        ctx.log(f"{art['name']}: {res['restored']} Eintraege nach {target} wiederhergestellt")
        details.append({"name": art["name"], "ok": True, "target": str(target),
                        "restored": res["restored"], "errors": res["error_count"]})

    return {"step": "data", "restored": sum(1 for d in details if d.get("ok")),
            "details": details}


def _restore_template(ctx: JobContext, manifest: dict[str, Any], backup_path: Path,
                      name: str) -> dict[str, Any]:
    template_file = backup_path / "template.xml"
    content: str | None = None
    source = "backup"

    if template_file.exists():
        content = template_file.read_text("utf-8")
    else:
        inspect_file = backup_path / "inspect.json"
        if inspect_file.exists():
            ctx.log("Kein Template im Backup - wird aus der Konfiguration erzeugt")
            attrs = json.loads(inspect_file.read_text("utf-8"))
            content = unraid.generate_template(attrs, container_name=name)
            source = "generated"

    if not content:
        ctx.log("Kein Template verfuegbar - Schritt uebersprungen", "warn")
        return {"step": "template", "ok": False, "reason": "kein Template vorhanden"}

    if name != manifest["container"]["name"]:
        content = _rename_in_template(content, name)

    if not unraid.available():
        ctx.log(f"Template-Verzeichnis {unraid.templates_dir()} nicht verfuegbar - "
                f"ist /boot/config gemountet?", "error")
        return {"step": "template", "ok": False,
                "reason": "Template-Verzeichnis nicht gemountet"}

    ctx.step("Unraid-Template wird geschrieben", 70)
    info = unraid.write_template(name, content)
    ctx.log(f"Template geschrieben: {info['path']} (Quelle: {source})")
    if info.get("previous_saved_as"):
        ctx.log(f"Vorheriges Template gesichert als {info['previous_saved_as']}")
    db.add_event("template.restored", f"Unraid-Template fuer {name} wiederhergestellt",
                 container=name, job_id=ctx.job_id, detail=info)
    return {"step": "template", "ok": True, "source": source, **info}


def _rename_in_template(content: str, new_name: str) -> str:
    import re
    return re.sub(r"<Name>.*?</Name>", f"<Name>{new_name}</Name>", content, count=1)


def _recreate_container(ctx: JobContext, manifest: dict[str, Any], backup_path: Path,
                        name: str, opts: dict[str, Any]) -> dict[str, Any]:
    inspect_file = backup_path / "inspect.json"
    if not inspect_file.exists():
        raise RestoreError("inspect.json fehlt im Backup - Container kann nicht angelegt werden")
    attrs = json.loads(inspect_file.read_text("utf-8"))

    cfg = dict(attrs.get("Config") or {})
    host_cfg = dict(attrs.get("HostConfig") or {})
    image = cfg.get("Image") or manifest["container"].get("image")
    if not image:
        raise RestoreError("Kein Image in der gesicherten Konfiguration")

    # --- Image bereitstellen --------------------------------------------
    if not docker_api.image_exists(image):
        ctx.step(f"Image '{image}' wird geladen", 74)
        try:
            docker_api.pull_image(image)
            ctx.log(f"Image {image} geladen")
        except Exception as exc:  # noqa: BLE001
            raise RestoreError(f"Image '{image}' konnte nicht geladen werden: {exc}") from exc

    # --- Netzwerke sicherstellen ----------------------------------------
    created_networks = []
    for net in manifest.get("networks", []):
        try:
            if docker_api.ensure_network(net["name"], net.get("driver") or "bridge",
                                         net.get("ipam")):
                created_networks.append(net["name"])
                ctx.log(f"Netzwerk '{net['name']}' neu angelegt")
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"Netzwerk '{net['name']}' konnte nicht angelegt werden: {exc}", "warn")

    # --- Benannte Volumes sicherstellen ---------------------------------
    for art in manifest.get("artifacts", []):
        if art.get("kind") == "volume":
            docker_api.ensure_volume(art["name"])

    # --- Create-Body zusammenbauen --------------------------------------
    body: dict[str, Any] = {key: cfg[key] for key in _CONFIG_KEYS if key in cfg}
    body["Image"] = image
    body["HostConfig"] = {k: v for k, v in host_cfg.items() if k not in _HOSTCONFIG_DROP}

    networks = manifest.get("networks", [])
    primary = networks[0] if networks else None
    if primary and primary["name"] not in ("bridge", "host", "none"):
        endpoint: dict[str, Any] = {}
        aliases = [a for a in (primary.get("aliases") or [])
                   if a != manifest["container"].get("id")]
        if aliases:
            endpoint["Aliases"] = aliases
        if primary.get("ipam_config"):
            endpoint["IPAMConfig"] = primary["ipam_config"]
        body["NetworkingConfig"] = {"EndpointsConfig": {primary["name"]: endpoint}}
        body["HostConfig"]["NetworkMode"] = primary["name"]

    ctx.step(f"Container '{name}' wird angelegt", 80)
    api = docker_api.client().api
    response = api._post_json(api._url("/containers/create"), data=body, params={"name": name})
    api._raise_for_status(response)
    container_id = response.json()["Id"]
    ctx.log(f"Container angelegt: {container_id[:12]}")

    # --- Weitere Netzwerke anhaengen ------------------------------------
    for net in networks[1:]:
        if net["name"] in ("host", "none"):
            continue
        try:
            docker_api.client().networks.get(net["name"]).connect(
                container_id, aliases=[a for a in (net.get("aliases") or [])
                                       if a != manifest["container"].get("id")] or None)
            ctx.log(f"Mit Netzwerk '{net['name']}' verbunden")
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"Netzwerk '{net['name']}' konnte nicht verbunden werden: {exc}", "warn")

    started = False
    if opts["start_container"]:
        ctx.step(f"Container '{name}' wird gestartet", 92)
        try:
            docker_api.client().containers.get(container_id).start()
            started = True
            ctx.log("Container laeuft")
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"Start fehlgeschlagen: {exc}", "error")

    events.publish("container.changed", {"name": name,
                                         "state": "running" if started else "created"})
    return {"step": "container", "ok": True, "id": container_id[:12], "started": started,
            "created_networks": created_networks}


# ---------------------------------------------------------------- Template-only

def restore_template_only(ctx: JobContext, template_name: str) -> dict[str, Any]:
    """Container allein aus einem vorhandenen Unraid-Template wiederherstellen.

    Greift, wenn kein DockVault-Backup existiert, aber das Template noch in
    ``templates-user`` liegt - Unraid kann den Container daraus neu anlegen.
    """
    path = unraid.find_template(template_name)
    if not path:
        raise RestoreError(f"Kein Template fuer '{template_name}' gefunden")
    ctx.step(f"Template '{path.name}' gefunden", 50)
    ctx.log("Der Container kann in Unraid unter Docker -> Add Container -> "
            f"Template '{template_name}' mit einem Klick neu erstellt werden.")
    ctx.progress(100, "Template verfuegbar")
    return {"step": "template-only", "ok": True, "template": str(path),
            "hint": "In der Unraid-WebGUI unter Docker das Template auswaehlen und 'Apply' klicken."}


def browse(backup_ref: str, artifact_name: str, limit: int = 1500) -> list[dict[str, Any]]:
    """Inhaltsverzeichnis eines Archivs fuer die Vorschau im Assistenten."""
    manifest, path = load_manifest(backup_ref)
    for art in manifest.get("artifacts", []):
        if art.get("name") == artifact_name and art.get("file"):
            return archive.list_entries(path / "data" / art["file"], limit=limit)
    raise RestoreError(f"Artefakt '{artifact_name}' nicht im Backup enthalten")
