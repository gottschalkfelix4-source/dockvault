"""REST-API der Web-Oberflaeche."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import (__version__, backup, config, db, docker_api, events, restore,
               runner, scheduler, storage, unraid)

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- Modelle

class BackupRequest(BaseModel):
    container: str
    options: dict[str, Any] | None = None


class BulkBackupRequest(BaseModel):
    containers: list[str] = Field(default_factory=list)
    all_containers: bool = False
    options: dict[str, Any] | None = None


class RestoreRequest(BaseModel):
    backup_ref: str
    restore_data: bool = True
    restore_template: bool = True
    recreate_container: bool = True
    start_container: bool = True
    replace_existing: bool = False
    wipe_target: bool = False
    artifacts: list[str] | None = None
    path_map: dict[str, str] = Field(default_factory=dict)
    target_name: str | None = None
    verify: bool | None = None


class ContainerAction(BaseModel):
    action: str


class ScheduleRequest(BaseModel):
    name: str
    cron: str
    containers: list[str] = Field(default_factory=list)
    all_containers: bool = False
    enabled: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- System

@router.get("/status")
def status() -> dict[str, Any]:
    docker_ok = docker_api.ping()
    info: dict[str, Any] = {}
    if docker_ok:
        try:
            info = docker_api.engine_info()
        except Exception as exc:  # noqa: BLE001
            info = {"error": str(exc)}

    backups = db.list_backups(limit=5000)
    total_archive = sum(b["archive_bytes"] or 0 for b in backups)
    containers: list[dict[str, Any]] = []
    if docker_ok:
        try:
            containers = docker_api.list_containers()
        except Exception:  # noqa: BLE001
            containers = []

    protected = {b["container"] for b in backups if b["status"] in ("completed", "partial")}
    excluded = set(config.get("exclude_containers", []))
    unprotected = [c["name"] for c in containers
                   if c["name"] not in protected and c["name"] not in excluded]

    return {
        "version": __version__,
        "docker": {"available": docker_ok, **info},
        "unraid": {
            "templates_dir": str(config.TEMPLATES_DIR),
            "templates_available": unraid.available(),
            "template_count": len(unraid.list_templates()),
        },
        "storage": {"backup_dir": str(config.BACKUP_DIR), **storage.status()},
        "counts": {
            "containers": len(containers),
            "running": sum(1 for c in containers if c["running"]),
            "backups": len(backups),
            "protected": len(protected),
            "unprotected": len(unprotected),
            "schedules": len(db.list_schedules()),
        },
        "unprotected_containers": unprotected[:20],
        "total_archive_bytes": total_archive,
        "active_jobs": runner.active_jobs(),
    }


def _disk_usage(path: Path) -> dict[str, Any]:
    try:
        import shutil as _sh
        usage = _sh.disk_usage(path)
        return {"total": usage.total, "used": usage.used, "free": usage.free}
    except OSError:
        return {"total": 0, "used": 0, "free": 0}


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    defaults = {**config.DEFAULTS, **{k: "" for k in config.SECRET_KEYS}}
    return {"settings": config.public_settings(), "defaults": defaults,
            "restore_roots": config.RESTORE_ROOTS, "secret_mask": config.SECRET_MASK}


@router.put("/settings")
def put_settings(patch: dict[str, Any]) -> dict[str, Any]:
    previous_type = config.get("target_type", "local")
    config.update(patch)
    db.add_event("settings.changed", "Einstellungen geaendert",
                 detail={"keys": [k for k in patch if k not in config.SECRET_KEYS]})
    scheduler.reload_all()

    # Ein geaendertes Backup-Ziel sofort herstellen, damit der Nutzer direkt sieht,
    # ob es funktioniert - statt es erst beim naechsten Backup zu merken.
    target_changed = any(k == "target_type" or k.startswith("smb_") for k in patch)
    storage_result: dict[str, Any] | None = None
    if target_changed:
        try:
            storage_result = {"ok": True, **storage.apply_target(previous_type)}
            db.add_event("storage.changed",
                         f"Backup-Ziel umgestellt auf {config.get('target_type')}")
        except storage.StorageError as exc:
            storage_result = {"ok": False, "error": str(exc)}
            db.add_event("storage.failed", f"Backup-Ziel nicht verfuegbar: {exc}",
                         level="error")

    return {"settings": config.public_settings(), "storage": storage_result}


# ---------------------------------------------------------------- Backup-Ziel

class SmbParams(BaseModel):
    smb_host: str | None = None
    smb_share: str | None = None
    smb_path: str | None = None
    smb_user: str | None = None
    smb_password: str | None = None
    smb_domain: str | None = None
    smb_version: str | None = None
    smb_options: str | None = None


@router.get("/storage")
def storage_status() -> dict[str, Any]:
    return storage.status()


@router.post("/storage/test")
def storage_test(params: SmbParams) -> dict[str, Any]:
    try:
        return storage.test_smb(params.model_dump())
    except storage.StorageError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Test fehlgeschlagen: {exc}") from exc


@router.post("/storage/mount")
def storage_mount() -> dict[str, Any]:
    try:
        result = storage.mount_smb()
        db.add_event("storage.mounted", f"Backup-Ziel eingebunden: {result['source']}")
        backup.rescan()
        return {"ok": True, **result, "status": storage.status()}
    except storage.StorageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/storage/unmount")
def storage_unmount() -> dict[str, Any]:
    try:
        result = storage.unmount()
        db.add_event("storage.unmounted", "Backup-Ziel ausgehaengt")
        return {"ok": True, **result, "status": storage.status()}
    except storage.StorageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/events/stream")
async def stream(request: Request) -> StreamingResponse:
    queue = events.subscribe()

    async def generator():
        try:
            yield f"data: {json.dumps({'type': 'hello', 'data': {'version': __version__}})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            events.unsubscribe(queue)

    return StreamingResponse(generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- Container

@router.get("/backup-roots/detect")
def detect_backup_roots() -> dict[str, Any]:
    """Vorschlaege fuer Quellverzeichnisse aus den tatsaechlichen Container-Mounts."""
    return {"suggestions": backup.detect_roots(),
            "configured": config.get("backup_roots", [])}


@router.get("/containers")
def list_containers() -> dict[str, Any]:
    try:
        containers = docker_api.list_containers(all_containers=True)
    except docker_api.DockerUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

    by_container: dict[str, dict[str, Any]] = {}
    for record in db.list_backups(limit=5000):
        entry = by_container.setdefault(record["container"],
                                        {"count": 0, "last": None, "bytes": 0,
                                         "last_status": None})
        entry["count"] += 1
        entry["bytes"] += record["archive_bytes"] or 0
        if entry["last"] is None:
            entry["last"] = record["created_at"]
            entry["last_status"] = record["status"]

    excluded = set(config.get("exclude_containers", []))
    templates = unraid.template_name_map()
    for container in containers:
        stats = by_container.get(container["name"], {})
        container["backup"] = {
            "count": stats.get("count", 0),
            "last": stats.get("last"),
            "last_status": stats.get("last_status"),
            "bytes": stats.get("bytes", 0),
            "protected": stats.get("count", 0) > 0,
        }
        container["excluded"] = container["name"] in excluded
        container["has_template"] = container["name"].lower() in templates
        container["busy"] = runner.is_busy(container["name"])
    return {"containers": containers}


@router.get("/containers/{name}")
def container_detail(name: str) -> dict[str, Any]:
    try:
        attrs = docker_api.inspect(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    template = unraid.read_template(name)
    return {
        "name": name,
        "inspect": attrs,
        "summary": docker_api.summarise(docker_api.get_container(name)),
        "template": {"present": bool(template), "file": template[0] if template else None},
        "backups": db.list_backups(container=name, limit=100),
    }


@router.get("/containers/{name}/logs", response_class=PlainTextResponse)
def container_logs(name: str, tail: int = Query(200, ge=1, le=5000)) -> str:
    try:
        return docker_api.logs(name, tail=tail)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc)) from exc


@router.get("/containers/{name}/stats")
def container_stats(name: str) -> dict[str, Any]:
    try:
        return docker_api.stats(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/containers/{name}/action")
def container_action(name: str, payload: ContainerAction) -> dict[str, Any]:
    if name in config.get("exclude_containers", []) and payload.action in ("stop", "remove"):
        raise HTTPException(400, f"'{name}' ist geschuetzt und kann hier nicht "
                                 f"{payload.action} werden")
    try:
        docker_api.control(name, payload.action)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc)) from exc
    db.add_event(f"container.{payload.action}", f"{name}: {payload.action} ausgefuehrt",
                 container=name)
    events.publish("container.changed", {"name": name, "action": payload.action})
    return {"ok": True, "action": payload.action}


@router.get("/containers/{name}/plan")
def backup_plan(name: str) -> dict[str, Any]:
    try:
        return backup.plan(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------- Backups

@router.get("/backups")
def list_backups(container: str | None = None,
                 limit: int = Query(300, ge=1, le=2000)) -> dict[str, Any]:
    return {"backups": db.list_backups(container=container, limit=limit)}


@router.post("/backups")
def start_backup(payload: BackupRequest) -> dict[str, Any]:
    name = payload.container
    if runner.is_busy(name):
        raise HTTPException(409, f"Fuer '{name}' laeuft bereits ein Job")
    if not docker_api.exists(name):
        raise HTTPException(404, f"Container '{name}' existiert nicht")
    job_id = runner.submit("backup", name,
                           lambda ctx: backup.run(ctx, name, trigger="manual",
                                                  options=payload.options))
    return {"job_id": job_id, "container": name}


@router.post("/backups/bulk")
def start_bulk_backup(payload: BulkBackupRequest) -> dict[str, Any]:
    excluded = set(config.get("exclude_containers", []))
    if payload.all_containers:
        names = [c["name"] for c in docker_api.list_containers(all_containers=True)]
    else:
        names = payload.containers
    names = [n for n in names if n not in excluded]
    if not names:
        raise HTTPException(400, "Keine Container ausgewaehlt")

    def _run(ctx: runner.JobContext) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": [], "failed": []}
        for index, name in enumerate(names):
            ctx.check_cancel()
            ctx.step(f"[{index + 1}/{len(names)}] {name}", index / len(names) * 100)
            try:
                backup.run(ctx, name, trigger="manual-bulk", options=payload.options)
                result["ok"].append(name)
            except runner.JobCancelled:
                raise            # Abbruch beendet den Lauf, statt weiterzumachen
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"{name}: {exc}", "error")
                result["failed"].append({"container": name, "error": str(exc)})
        return result

    job_id = runner.submit("backup-bulk", f"{len(names)} Container", _run)
    return {"job_id": job_id, "containers": names}


@router.post("/backups/rescan")
def rescan_backups() -> dict[str, Any]:
    return backup.rescan()


@router.get("/backups/{backup_ref:path}/browse")
def browse_backup(backup_ref: str, artifact: str,
                  limit: int = Query(1000, ge=1, le=5000)) -> dict[str, Any]:
    try:
        return {"entries": restore.browse(backup_ref, artifact, limit=limit)}
    except restore.RestoreError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/backups/{backup_ref:path}/template", response_class=PlainTextResponse)
def backup_template(backup_ref: str) -> str:
    _, path = restore.load_manifest(backup_ref)
    template = path / "template.xml"
    if not template.exists():
        raise HTTPException(404, "Kein Template in diesem Backup")
    return template.read_text("utf-8")


@router.post("/backups/{backup_ref:path}/pin")
def pin_backup(backup_ref: str, pinned: bool = True) -> dict[str, Any]:
    if not db.get_backup(backup_ref):
        raise HTTPException(404, "Backup nicht gefunden")
    db.set_pinned(backup_ref, pinned)
    return {"ok": True, "pinned": pinned}


@router.delete("/backups/{backup_ref:path}")
def delete_backup(backup_ref: str) -> dict[str, Any]:
    record = db.get_backup(backup_ref)
    if not record:
        raise HTTPException(404, "Backup nicht gefunden")
    if record.get("pinned"):
        raise HTTPException(400, "Backup ist angeheftet - erst loesen")
    if not backup.delete(backup_ref):
        raise HTTPException(500, "Backup konnte nicht geloescht werden")
    db.add_event("backup.deleted", f"Backup {backup_ref} geloescht",
                 container=record["container"])
    return {"ok": True}


@router.get("/backups/{backup_ref:path}")
def backup_detail(backup_ref: str) -> dict[str, Any]:
    record = db.get_backup(backup_ref)
    if not record:
        raise HTTPException(404, "Backup nicht gefunden")
    return record


# ---------------------------------------------------------------- Restore

@router.post("/restore/preview")
def restore_preview(payload: RestoreRequest) -> dict[str, Any]:
    try:
        return restore.preview(payload.backup_ref, payload.model_dump())
    except restore.RestoreError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/restore")
def start_restore(payload: RestoreRequest) -> dict[str, Any]:
    options = payload.model_dump()
    ref = options.pop("backup_ref")
    try:
        manifest, _ = restore.load_manifest(ref)
    except restore.RestoreError as exc:
        raise HTTPException(404, str(exc)) from exc
    target = options.get("target_name") or manifest["container"]["name"]
    if runner.is_busy(target):
        raise HTTPException(409, f"Fuer '{target}' laeuft bereits ein Job")
    job_id = runner.submit("restore", target, lambda ctx: restore.run(ctx, ref, options))
    return {"job_id": job_id, "container": target}


# ---------------------------------------------------------------- Unraid-Templates

@router.get("/templates")
def list_templates() -> dict[str, Any]:
    templates = unraid.list_templates()
    try:
        existing = {c["name"].lower() for c in docker_api.list_containers()}
    except Exception:  # noqa: BLE001
        existing = set()
    for template in templates:
        template["container_exists"] = template["name"].lower() in existing
    return {"available": unraid.available(), "dir": str(config.TEMPLATES_DIR),
            "templates": templates}


@router.get("/templates/orphans")
def orphan_templates() -> dict[str, Any]:
    """Templates ohne Container plus Backups ohne Container - alles Wiederherstellbare."""
    orphans = unraid.orphan_templates()
    try:
        existing = {c["name"].lower() for c in docker_api.list_containers()}
    except Exception:  # noqa: BLE001
        existing = set()

    missing_backups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in db.list_backups(limit=5000):
        name = record["container"]
        if name.lower() in existing or name in seen:
            continue
        seen.add(name)
        missing_backups.append({
            "container": name, "backup_ref": record["id"],
            "created_at": record["created_at"], "image": record["image"],
            "archive_bytes": record["archive_bytes"],
            "has_template": bool(record["has_template"]),
        })
    return {"orphan_templates": orphans, "orphan_backups": missing_backups}


@router.get("/templates/file/{filename}", response_class=PlainTextResponse)
def template_file(filename: str) -> str:
    path = config.TEMPLATES_DIR / Path(filename).name
    if not path.exists():
        raise HTTPException(404, "Template nicht gefunden")
    return path.read_text("utf-8", errors="replace")


@router.post("/templates/generate/{name}")
def generate_template(name: str, write: bool = False) -> dict[str, Any]:
    """Template aus einem laufenden Container erzeugen - optional direkt speichern."""
    try:
        attrs = docker_api.inspect(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    xml = unraid.generate_template(attrs, container_name=name)
    result: dict[str, Any] = {"xml": xml, "written": False}
    if write:
        if not unraid.available():
            raise HTTPException(503, f"Template-Verzeichnis {config.TEMPLATES_DIR} "
                                     f"nicht verfuegbar")
        info = unraid.write_template(name, xml)
        db.add_event("template.generated", f"Template fuer {name} erzeugt und gespeichert",
                     container=name, detail=info)
        result.update({"written": True, **info})
    return result


# ---------------------------------------------------------------- Timeline

@router.get("/timeline")
def timeline(days: int = Query(30, ge=1, le=365),
             container: str | None = None) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    backups = db.list_backups(container=container, limit=2000, since=since)
    event_rows = db.list_events(since=since, container=container, limit=1500)

    entries: list[dict[str, Any]] = []
    for record in backups:
        entries.append({
            "type": "backup", "ts": record["created_at"], "container": record["container"],
            "status": record["status"], "backup_ref": record["id"],
            "bytes": record["archive_bytes"], "duration_s": record["duration_s"],
            "trigger": record["trigger"], "pinned": bool(record["pinned"]),
            "has_template": bool(record["has_template"]),
            "label": f"Backup {record['container']}",
        })
    for event in event_rows:
        if event["kind"].startswith("backup."):
            continue
        entries.append({
            "type": event["kind"], "ts": event["ts"], "container": event["container"],
            "status": event["level"], "label": event["message"],
            "job_id": event["job_id"], "backup_ref": event["backup_id"],
        })
    entries.sort(key=lambda e: e["ts"], reverse=True)

    lanes: dict[str, dict[str, Any]] = {}
    for entry in entries:
        name = entry.get("container") or "System"
        lane = lanes.setdefault(name, {"container": name, "entries": [], "backups": 0,
                                       "failures": 0, "bytes": 0})
        lane["entries"].append(entry)
        if entry["type"] == "backup":
            lane["backups"] += 1
            lane["bytes"] += entry.get("bytes") or 0
            if entry["status"] not in ("completed",):
                lane["failures"] += 1

    return {
        "since": since, "days": days, "entries": entries,
        "lanes": sorted(lanes.values(), key=lambda lane: (-lane["backups"], lane["container"])),
    }


# ---------------------------------------------------------------- Jobs

@router.get("/jobs")
def list_jobs(limit: int = Query(50, ge=1, le=300)) -> dict[str, Any]:
    return {"jobs": db.list_jobs(limit=limit), "active": runner.active_jobs()}


@router.get("/jobs/{job_id}")
def job_detail(job_id: str) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job nicht gefunden")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    if not runner.cancel(job_id):
        raise HTTPException(404, "Job laeuft nicht mehr")
    return {"ok": True}


# ---------------------------------------------------------------- Zeitplaene

@router.get("/schedules")
def list_schedules() -> dict[str, Any]:
    return {"schedules": scheduler.overview()}


@router.post("/schedules")
def create_schedule(payload: ScheduleRequest) -> dict[str, Any]:
    try:
        return scheduler.create(payload.name, payload.cron, payload.containers,
                                payload.all_containers, payload.options, payload.enabled)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: str, payload: ScheduleRequest) -> dict[str, Any]:
    try:
        return scheduler.update(schedule_id, payload.model_dump())
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str) -> dict[str, Any]:
    scheduler.delete(schedule_id)
    return {"ok": True}


@router.post("/schedules/{schedule_id}/run")
def run_schedule(schedule_id: str) -> dict[str, Any]:
    try:
        return {"job_ids": scheduler.run_now(schedule_id)}
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
