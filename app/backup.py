"""Backup-Engine: sichert Konfiguration, Unraid-Template und alle Nutzdaten eines Containers."""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import archive, config, db, docker_api, events, storage, unraid
from .runner import JobCancelled, JobContext

MANIFEST_SCHEMA = 2
MANIFEST_NAME = "manifest.json"


def backup_root(container: str) -> Path:
    return config.BACKUP_DIR / _slug(container)


def _slug(value: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "-" for c in value)


def new_backup_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------- Planung

def plan(container_name: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Was wuerde gesichert? Wird vom UI fuer die Vorschau genutzt."""
    options = _merge_options(options)
    attrs = docker_api.inspect(container_name)
    artifacts = _collect_artifacts(attrs, options, include_skipped=True)
    excludes = options["exclude_patterns"]
    total = 0
    for art in artifacts:
        if not art["include"]:
            # Datenpfade bewusst nicht vermessen: ein Medien-Share zu durchlaufen
            # kann Minuten dauern und der Wert interessiert hier nicht.
            art["estimated_bytes"] = None
            art["files"] = None
            continue
        source = Path(art["source"])
        if source.exists():
            size, files = archive.measure(source, excludes)
            art["estimated_bytes"] = size
            art["files"] = files
            total += size
        else:
            art["estimated_bytes"] = 0
            art["files"] = 0
            art["missing"] = True
    template = unraid.read_template(container_name)
    return {
        "container": container_name,
        "artifacts": [a for a in artifacts if a["include"]],
        "skipped": [a for a in artifacts if not a["include"]],
        "estimated_bytes": total,
        "mount_scope": options["mount_scope"],
        "template": {"present": bool(template), "file": template[0] if template else None,
                     "will_generate": not template and options["generate_missing_template"]},
        "options": options,
    }


def _merge_options(options: dict[str, Any] | None) -> dict[str, Any]:
    settings = config.all_settings()
    merged = {
        "compression": settings["compression"],
        "compression_level": settings["compression_level"],
        "stop_container": settings["stop_container"],
        "include_volumes": settings["include_volumes"],
        "include_binds": settings["include_binds"],
        "include_template": settings["include_template"],
        "generate_missing_template": settings["generate_missing_template"],
        "exclude_paths": settings["exclude_paths"],
        "exclude_patterns": settings["exclude_patterns"],
        "mount_scope": settings["mount_scope"],
        "backup_roots": settings["backup_roots"],
        "max_artifact_gb": settings["max_artifact_gb"],
        "verify_checksums": settings["verify_checksums"],
    }
    if options:
        merged.update({k: v for k, v in options.items() if v is not None})
    return merged


def _under(path: str, roots: list[str]) -> bool:
    """Liegt ``path`` in einem der Wurzelverzeichnisse (oder ist es selbst)?"""
    norm = path.rstrip("/")
    for root in roots:
        base = str(root).rstrip("/")
        if base and (norm == base or norm.startswith(base + "/")):
            return True
    return False


def _depth_below(path: str, base: str) -> int | None:
    """Wie viele Ebenen liegt ``path`` unter ``base``? None, wenn ausserhalb."""
    norm = path.rstrip("/")
    root = base.rstrip("/")
    if norm == root:
        return 0
    if not norm.startswith(root + "/"):
        return None
    return len([p for p in norm[len(root) + 1:].split("/") if p])


def matching_root(source: str, roots: list[str]) -> str | None:
    """Das Quellverzeichnis, unter dem ``source`` liegt - oder None."""
    for root in roots:
        base = str(root).strip().rstrip("/")
        if base and _depth_below(source, base) is not None:
            return base
    return None


def detect_roots() -> list[dict[str, Any]]:
    """Schlaegt Quellverzeichnisse anhand der tatsaechlichen Container vor.

    Sucht in allen Bind-Mounts nach Verzeichnissen mit dem konfigurierten Namen
    (Vorgabe ``appdata``) und meldet, wie viele Container darunter liegen. So
    muss niemand die Pools von Hand zusammensuchen.
    """
    dirname = (config.get("appdata_dirname") or "appdata").strip("/").lower()
    found: dict[str, set[str]] = {}
    try:
        containers = docker_api.list_containers(all_containers=True)
    except Exception:  # noqa: BLE001
        return []

    for container in containers:
        for mount in container.get("mounts") or []:
            if mount.get("type") != "bind":
                continue
            source = mount.get("source") or ""
            # Auf Unraid liegen Pools und Shares ausnahmslos unter /mnt. Ohne
            # diese Grenze schlaegt die Erkennung auch Pfade vor, die zufaellig
            # ein "AppData" im Namen tragen.
            if not source.startswith("/mnt/"):
                continue
            parts = [p for p in source.split("/") if p]
            for index, part in enumerate(parts):
                if part.lower() == dirname:
                    root = "/" + "/".join(parts[:index + 1])
                    found.setdefault(root, set()).add(container["name"])
                    break

    return sorted(
        ({"path": root, "containers": sorted(names), "count": len(names)}
         for root, names in found.items()),
        key=lambda entry: (-entry["count"], entry["path"]),
    )


def classify_mount(source: str, kind: str, options: dict[str, Any]) -> dict[str, Any]:
    """Gehoert dieser Mount ins Backup?

    Entschieden wird ausschliesslich an den vorgegebenen Quellverzeichnissen:
    liegt der Mount darunter, wird er gesichert - sonst nicht. Die Vorsortierung
    passiert damit einmal zentral ("das sind meine appdata-Verzeichnisse") statt
    pro Container aus der Ordnerstruktur geraten zu werden. Medienshares wie
    /mnt/medien koennen so gar nicht erst versehentlich im Backup landen.
    """
    if kind == "volume":
        return {"category": "volume", "include": True,
                "reason": "Benanntes Docker-Volume"}

    roots = options.get("backup_roots") or []
    root = matching_root(source, roots)
    if root:
        return {"category": "config", "include": True, "root": root,
                "reason": f"Liegt in {root}"}

    if options.get("mount_scope", "roots") == "all":
        return {"category": "data", "include": True,
                "reason": "Mitgesichert, weil der Umfang auf 'alle Mounts' steht"}
    if not roots:
        return {"category": "data", "include": False,
                "reason": "Es ist noch kein Quellverzeichnis hinterlegt"}
    return {"category": "data", "include": False,
            "reason": "Liegt in keinem der hinterlegten Quellverzeichnisse"}


def _collect_artifacts(attrs: dict[str, Any], options: dict[str, Any],
                       include_skipped: bool = False) -> list[dict[str, Any]]:
    """Ermittelt die Mounts eines Containers samt Einstufung.

    ``include_skipped`` liefert auch die uebersprungenen Datenpfade zurueck -
    die Vorschau zeigt sie an, damit nachvollziehbar ist, was bewusst fehlt.
    """
    out: list[dict[str, Any]] = []
    excluded_paths = list(options["exclude_paths"])
    seen: set[str] = set()

    for mount in attrs.get("Mounts") or []:
        mtype = mount.get("Type")
        destination = mount.get("Destination") or ""
        source = mount.get("Source") or ""
        name = mount.get("Name") or ""

        # Praefix-Vergleich: ein ausgeschlossenes Verzeichnis nimmt auch alles
        # darunter heraus - so laesst sich ein einzelner Unterordner eines
        # Quellverzeichnisses gezielt ausklammern.
        if _under(destination, excluded_paths) or _under(source, excluded_paths):
            continue
        if mtype == "tmpfs":
            continue

        if mtype == "volume":
            key = f"volume:{name}"
            if key in seen:
                continue
            seen.add(key)
            mountpoint = source or docker_api.volume_mountpoint(name) or ""
            entry = {
                "kind": "volume", "name": name,
                "source": _map_volume_path(name, mountpoint),
                "original_source": mountpoint, "destination": destination,
                "rw": mount.get("RW", True), "driver": mount.get("Driver", "local"),
            }
            verdict = classify_mount(entry["source"], "volume", options)
            if not options["include_volumes"]:
                verdict = {"category": "volume", "include": False,
                           "reason": "Volumes sind in den Einstellungen abgeschaltet"}
        else:
            key = f"bind:{source}"
            if key in seen:
                continue
            seen.add(key)
            entry = {
                "kind": "bind", "name": Path(source).name or _slug(destination),
                "source": source, "original_source": source, "destination": destination,
                "rw": mount.get("RW", True),
            }
            verdict = classify_mount(source, "bind", options)
            if not options["include_binds"]:
                verdict = {"category": verdict["category"], "include": False,
                           "reason": "Bind-Mounts sind in den Einstellungen abgeschaltet"}

        entry.update(verdict)
        if verdict["include"] or include_skipped:
            out.append(entry)
    return out


def _map_volume_path(name: str, mountpoint: str) -> str:
    """Volume-Mountpoint auf den im Container sichtbaren Pfad abbilden."""
    candidate = config.DOCKER_VOLUMES_DIR / name / "_data"
    if candidate.exists():
        return str(candidate)
    return mountpoint


# ---------------------------------------------------------------- Ausfuehrung

def run(ctx: JobContext, container_name: str, *, trigger: str = "manual",
        options: dict[str, Any] | None = None) -> dict[str, Any]:
    opts = _merge_options(options)
    started = time.monotonic()
    backup_id = new_backup_id()

    if container_name in config.get("exclude_containers", []):
        raise ValueError(f"Container '{container_name}' steht auf der Ausschlussliste")

    # Niemals ins Container-Dateisystem sichern, wenn das SMB-Ziel fehlt.
    storage.require_ready()

    ctx.step(f"Backup '{container_name}' wird vorbereitet", 2)
    attrs = docker_api.inspect(container_name)
    cfg = attrs.get("Config") or {}
    image = cfg.get("Image") or ""
    was_running = bool((attrs.get("State") or {}).get("Running"))

    target_dir = backup_root(container_name) / backup_id
    target_dir.mkdir(parents=True, exist_ok=True)
    data_dir = target_dir / "data"
    data_dir.mkdir(exist_ok=True)

    db.upsert_backup({
        "id": f"{_slug(container_name)}/{backup_id}", "container": container_name,
        "created_at": db.now_iso(), "status": "running", "trigger": trigger,
        "image": image, "path": str(target_dir),
    })
    db.add_event("backup.started", f"Backup von {container_name} gestartet",
                 container=container_name, job_id=ctx.job_id)

    scanned = _collect_artifacts(attrs, opts, include_skipped=True)
    artifacts = [a for a in scanned if a["include"]]
    ignored = [a for a in scanned if not a["include"]]
    ctx.log(f"{len(artifacts)} Datenquelle(n) werden gesichert, Image: {image}")
    for entry in ignored:
        ctx.log(f"uebersprungen: {entry['source']} ({entry['reason']})")

    # --- Konfiguration sichern (immer, auch wenn Daten scheitern) --------
    (target_dir / "inspect.json").write_text(
        json.dumps(attrs, indent=2, ensure_ascii=False), "utf-8")
    networks = _snapshot_networks(attrs)
    (target_dir / "networks.json").write_text(
        json.dumps(networks, indent=2, ensure_ascii=False), "utf-8")
    ctx.step("Container-Konfiguration gesichert", 6)

    # --- Unraid-Template -------------------------------------------------
    template_info = _save_template(ctx, container_name, attrs, target_dir, opts)

    # --- Container anhalten ----------------------------------------------
    stopped_by_us = False
    if opts["stop_container"] and was_running and artifacts:
        ctx.step(f"Container '{container_name}' wird angehalten (konsistente Sicherung)", 8)
        try:
            docker_api.control(container_name, "stop", timeout=60)
            stopped_by_us = True
            _note_stopped(container_name, True)
            events.publish("container.changed", {"name": container_name, "state": "exited"})
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"Konnte Container nicht stoppen, sichere im laufenden Zustand: {exc}", "warn")

    saved: list[dict[str, Any]] = []
    total_source = 0
    total_archive = 0
    failures: list[str] = []
    cancelled = False

    try:
        span = 82.0 / max(len(artifacts), 1)
        for index, art in enumerate(artifacts):
            ctx.check_cancel()
            base = 10 + index * span
            source = Path(art["source"])
            label = f"{art['kind']}:{art['name']}"

            if not source.exists():
                ctx.log(f"{label} - Quelle {source} nicht gefunden, uebersprungen", "warn")
                saved.append({**art, "skipped": True, "reason": "Quelle nicht gefunden"})
                continue

            limit_gb = opts["max_artifact_gb"]
            if limit_gb:
                size, _ = archive.measure(source, opts["exclude_patterns"])
                if size > limit_gb * 1024 ** 3:
                    ctx.log(f"{label} - {_human(size)} ueberschreitet Limit "
                            f"({limit_gb} GB), uebersprungen", "warn")
                    saved.append({**art, "skipped": True,
                                  "reason": f"groesser als {limit_gb} GB"})
                    continue

            compression = archive.choose_compression(opts["compression"])
            filename = f"{art['kind']}-{_slug(art['name'])}{archive.suffix_for(compression)}"
            dest = data_dir / filename
            ctx.step(f"Sichere {label} ({art['destination']})", base)

            def on_progress(done: int, total: int, _base=base, _span=span, _label=label) -> None:
                pct = _base + (_span * (done / total if total else 1))
                ctx.progress(pct, f"{_label}: {_human(done)} / {_human(total)}")

            try:
                result = archive.pack(source, dest, compression=compression,
                                      level=opts["compression_level"],
                                      excludes=opts["exclude_patterns"],
                                      progress=on_progress,
                                      cancelled=ctx.check_cancel)
            except JobCancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"{label} fehlgeschlagen: {exc}", "error")
                failures.append(f"{label}: {exc}")
                saved.append({**art, "skipped": True, "reason": str(exc), "failed": True})
                dest.unlink(missing_ok=True)
                continue

            total_source += result["source_bytes"]
            total_archive += result["archive_bytes"]
            ratio = (result["archive_bytes"] / result["source_bytes"] * 100) \
                if result["source_bytes"] else 100
            ctx.log(f"{label}: {result['files']} Dateien, {_human(result['source_bytes'])} "
                    f"-> {_human(result['archive_bytes'])} ({ratio:.0f}%)")
            if result["skipped_count"]:
                ctx.log(f"{label}: {result['skipped_count']} Eintraege uebersprungen", "warn")
            saved.append({**art, "file": filename, "skipped": False, **result})
    except JobCancelled:
        cancelled = True
        raise
    finally:
        if stopped_by_us:
            ctx.step(f"Container '{container_name}' wird wieder gestartet", 94)
            try:
                docker_api.control(container_name, "start")
                _note_stopped(container_name, False)
                events.publish("container.changed", {"name": container_name, "state": "running"})
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"Container konnte nicht neu gestartet werden: {exc}", "error")
                db.add_event("container.start_failed",
                             f"{container_name} konnte nach dem Backup nicht starten: {exc}",
                             level="error", container=container_name, job_id=ctx.job_id)

        if cancelled:
            # Ein halbes Backup ist wertlos und wuerde als "running" im Index
            # haengen bleiben - also restlos entfernen.
            ref = f"{_slug(container_name)}/{backup_id}"
            ctx.log("Abgebrochen - unvollstaendiges Backup wird entfernt", "warn")
            shutil.rmtree(target_dir, ignore_errors=True)
            db.delete_backup(ref)
            db.add_event("backup.cancelled", f"Backup von {container_name} abgebrochen",
                         level="warn", container=container_name, job_id=ctx.job_id)

    duration = time.monotonic() - started
    status = "completed" if not failures else ("partial" if saved else "failed")

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "id": backup_id,
        "backup_ref": f"{_slug(container_name)}/{backup_id}",
        "container": {
            "name": container_name,
            "id": attrs.get("Id", "")[:12],
            "image": image,
            "image_id": attrs.get("Image", ""),
            "created": attrs.get("Created"),
            "was_running": was_running,
        },
        "created_at": db.now_iso(),
        "duration_s": round(duration, 2),
        "trigger": trigger,
        "status": status,
        "job_id": ctx.job_id,
        "compression": archive.choose_compression(opts["compression"]),
        "source_bytes": total_source,
        "archive_bytes": total_archive,
        "template": template_info,
        "artifacts": saved,
        "ignored_mounts": ignored,
        "mount_scope": opts["mount_scope"],
        "networks": networks,
        "failures": failures,
        "options": opts,
        "host": {"unraid_templates": str(config.TEMPLATES_DIR)},
    }
    (target_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")

    db.upsert_backup({
        "id": manifest["backup_ref"], "container": container_name,
        "created_at": manifest["created_at"], "finished_at": db.now_iso(),
        "status": status, "trigger": trigger, "image": image,
        "source_bytes": total_source, "archive_bytes": total_archive,
        "duration_s": round(duration, 2), "has_template": template_info.get("present"),
        "path": str(target_dir), "manifest": manifest,
        "error": "; ".join(failures) if failures else None,
    })
    db.add_event("backup.completed" if status == "completed" else "backup.partial",
                 f"Backup von {container_name} {'abgeschlossen' if status == 'completed' else 'mit Fehlern beendet'}"
                 f" ({_human(total_archive)}, {duration:.0f}s)",
                 level="info" if status == "completed" else "warn",
                 container=container_name, job_id=ctx.job_id,
                 backup_id=manifest["backup_ref"],
                 detail={"source_bytes": total_source, "archive_bytes": total_archive})

    ctx.step(f"Backup abgeschlossen: {_human(total_archive)} in {duration:.0f}s", 98)
    apply_retention(container_name, ctx)
    ctx.progress(100, "Fertig")
    events.publish("backup.created", {"container": container_name,
                                      "backup_id": manifest["backup_ref"], "status": status})
    return manifest


def _snapshot_networks(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    """Netzwerk-Definitionen mitsichern, damit sie beim Restore neu entstehen koennen."""
    out = []
    networks = ((attrs.get("NetworkSettings") or {}).get("Networks") or {})
    for name, entry in networks.items():
        record: dict[str, Any] = {
            "name": name,
            "aliases": entry.get("Aliases") or [],
            "ipam_config": entry.get("IPAMConfig") or {},
            "mac_address": entry.get("MacAddress"),
        }
        try:
            net = docker_api.client().networks.get(name)
            record["driver"] = net.attrs.get("Driver")
            record["ipam"] = net.attrs.get("IPAM")
            record["internal"] = net.attrs.get("Internal", False)
            record["options"] = net.attrs.get("Options") or {}
        except Exception:  # noqa: BLE001 - Netz evtl. schon weg
            record["driver"] = "bridge"
        out.append(record)
    return out


def _save_template(ctx: JobContext, container_name: str, attrs: dict[str, Any],
                   target_dir: Path, opts: dict[str, Any]) -> dict[str, Any]:
    if not opts["include_template"]:
        return {"present": False, "source": "disabled"}

    existing = unraid.read_template(container_name)
    if existing:
        filename, content = existing
        (target_dir / "template.xml").write_text(content, "utf-8")
        ctx.log(f"Unraid-Template gesichert: {filename}")
        return {"present": True, "source": "unraid", "file": filename,
                "stored_as": "template.xml"}

    if not opts["generate_missing_template"]:
        ctx.log("Kein Unraid-Template vorhanden", "warn")
        return {"present": False, "source": "missing"}

    try:
        generated = unraid.generate_template(attrs, container_name=container_name)
        (target_dir / "template.xml").write_text(generated, "utf-8")
        ctx.log("Kein Template gefunden - aus der Container-Konfiguration erzeugt")
        return {"present": True, "source": "generated",
                "file": unraid.template_filename(container_name), "stored_as": "template.xml"}
    except Exception as exc:  # noqa: BLE001
        ctx.log(f"Template konnte nicht erzeugt werden: {exc}", "warn")
        return {"present": False, "source": "error", "error": str(exc)}


# ---------------------------------------------------------------- Aufbewahrung

def apply_retention(container_name: str, ctx: JobContext | None = None) -> list[str]:
    settings = config.all_settings()
    if not settings.get("retention_enabled", True):
        return []

    keep_last = int(settings.get("retention_keep_last") or 0)
    keep_days = int(settings.get("retention_keep_days") or 0)
    backups = db.list_backups(container=container_name, limit=1000)
    backups = [b for b in backups if b["status"] in ("completed", "partial")]
    removed: list[str] = []

    cutoff = None
    if keep_days:
        cutoff = time.time() - keep_days * 86400

    for index, record in enumerate(backups):
        if record.get("pinned"):
            continue
        too_many = keep_last and index >= keep_last
        too_old = False
        if cutoff:
            try:
                ts = datetime.fromisoformat(record["created_at"]).timestamp()
                too_old = ts < cutoff
            except ValueError:
                pass
        # Das jeweils neueste Backup bleibt immer erhalten.
        if index > 0 and (too_many or too_old):
            if delete(record["id"]):
                removed.append(record["id"])

    if removed and ctx:
        ctx.log(f"Aufbewahrung: {len(removed)} alte Sicherung(en) entfernt")
    if removed:
        db.add_event("retention.pruned",
                     f"{len(removed)} alte Sicherung(en) von {container_name} geloescht",
                     container=container_name, detail={"removed": removed})
    return removed


def delete(backup_ref: str) -> bool:
    record = db.get_backup(backup_ref)
    path = Path(record["path"]) if record else (config.BACKUP_DIR / backup_ref)
    try:
        if path.exists() and str(path).startswith(str(config.BACKUP_DIR)):
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        return False
    db.delete_backup(backup_ref)
    events.publish("backup.deleted", {"backup_id": backup_ref})
    return True


# ---------------------------------------------------------------- Index

def ensure_roots_configured() -> dict[str, Any]:
    """Beim ersten Start sinnvolle Quellverzeichnisse eintragen.

    Ohne Vorgabe wuerde nichts gesichert. Der Vorschlag wird bewusst dauerhaft
    gespeichert statt jedes Mal neu geraten - so steht in den Einstellungen
    schwarz auf weiss, woraus gesichert wird, und bleibt aenderbar.
    """
    if config.get("backup_roots"):
        return {"changed": False, "roots": config.get("backup_roots")}
    detected = [entry["path"] for entry in detect_roots()]
    if not detected:
        return {"changed": False, "roots": []}
    config.update({"backup_roots": detected})
    db.add_event("settings.roots_detected",
                 f"Quellverzeichnisse automatisch erkannt: {', '.join(detected)}")
    return {"changed": True, "roots": detected}


STOPPED_STATE = "stopped-by-dockvault.json"


def _stopped_state_path() -> Path:
    return config.CONFIG_DIR / STOPPED_STATE


def _note_stopped(name: str, stopped: bool) -> None:
    """Merkt sich, welche Container DockVault angehalten hat.

    Stirbt der Container mitten im Backup, wuerde der angehaltene Dienst sonst
    unten bleiben - unbemerkt, bis jemand ihn vermisst.
    """
    path = _stopped_state_path()
    try:
        current = set(json.loads(path.read_text("utf-8"))) if path.exists() else set()
    except (OSError, ValueError):
        current = set()
    if stopped:
        current.add(name)
    else:
        current.discard(name)
    try:
        path.write_text(json.dumps(sorted(current)), "utf-8")
    except OSError:
        pass


def restart_orphaned_containers() -> dict[str, Any]:
    """Beim Start: Container wieder hochfahren, die ein abgestuerztes Backup anhielt."""
    path = _stopped_state_path()
    if not path.exists():
        return {"restarted": [], "failed": []}
    try:
        names = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        names = []

    restarted, failed = [], []
    for name in names:
        try:
            if docker_api.exists(name):
                attrs = docker_api.inspect(name)
                if not (attrs.get("State") or {}).get("Running"):
                    docker_api.control(name, "start")
                    restarted.append(name)
                    db.add_event("container.recovered",
                                 f"{name} war nach einem abgebrochenen Backup gestoppt "
                                 f"und wurde wieder gestartet", level="warn", container=name)
        except Exception as exc:  # noqa: BLE001
            failed.append({"container": name, "error": str(exc)})
    path.unlink(missing_ok=True)
    return {"restarted": restarted, "failed": failed}


def cleanup_stale() -> dict[str, Any]:
    """Beim Start: Backups, die als "running" im Index stehen, sind Leichen.

    Ein laufender Job ueberlebt keinen Neustart des Containers. Bleibt so ein
    Eintrag stehen, sieht ein halbes Backup wie ein gueltiges aus - genau das
    darf bei einem Sicherungswerkzeug nicht passieren.
    """
    stale = [b for b in db.list_backups(limit=5000) if b["status"] == "running"]
    for record in stale:
        path = Path(record["path"])
        if str(path).startswith(str(config.BACKUP_DIR)):
            shutil.rmtree(path, ignore_errors=True)
        db.delete_backup(record["id"])
        db.add_event("backup.stale_removed",
                     f"Unvollstaendiges Backup von {record['container']} entfernt "
                     f"(Abbruch oder Neustart)", level="warn", container=record["container"])
    return {"removed": len(stale), "ids": [b["id"] for b in stale]}


def rescan() -> dict[str, Any]:
    """Backup-Verzeichnis einlesen - stellt den Index nach einem Datenverlust wieder her."""
    found = 0
    added = 0
    root = config.BACKUP_DIR
    if not root.is_dir():
        return {"found": 0, "added": 0}
    for manifest_path in root.glob("*/*/manifest.json"):
        found += 1
        try:
            manifest = json.loads(manifest_path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        ref = manifest.get("backup_ref") or \
            f"{manifest_path.parent.parent.name}/{manifest_path.parent.name}"
        if db.get_backup(ref):
            continue
        container = (manifest.get("container") or {}).get("name") or manifest_path.parent.parent.name
        db.upsert_backup({
            "id": ref, "container": container,
            "created_at": manifest.get("created_at") or db.now_iso(),
            "finished_at": manifest.get("created_at"), "status": manifest.get("status", "completed"),
            "trigger": manifest.get("trigger", "manual"),
            "image": (manifest.get("container") or {}).get("image"),
            "source_bytes": manifest.get("source_bytes", 0),
            "archive_bytes": manifest.get("archive_bytes", 0),
            "duration_s": manifest.get("duration_s", 0),
            "has_template": (manifest.get("template") or {}).get("present"),
            "path": str(manifest_path.parent), "manifest": manifest,
        })
        added += 1
    return {"found": found, "added": added}


def _human(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} PB"
