"""Zeitgesteuerte Backups auf Basis von Cron-Ausdruecken."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import backup, config, db, docker_api, events, runner

_scheduler: BackgroundScheduler | None = None


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone=config.get("timezone", "Europe/Berlin"))
    _scheduler.start()
    reload_all()
    _scheduler.add_job(_nightly_maintenance, CronTrigger(hour=4, minute=30),
                       id="dv-maintenance", replace_existing=True)


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def validate_cron(expression: str) -> str | None:
    """Gibt eine Fehlermeldung zurueck oder None, wenn der Ausdruck gueltig ist."""
    try:
        CronTrigger.from_crontab(expression)
        return None
    except (ValueError, TypeError) as exc:
        return str(exc)


def next_run(expression: str) -> str | None:
    try:
        trigger = CronTrigger.from_crontab(expression,
                                           timezone=config.get("timezone", "Europe/Berlin"))
        nxt = trigger.get_next_fire_time(None, datetime.now(trigger.timezone))
        return nxt.isoformat(timespec="seconds") if nxt else None
    except (ValueError, TypeError):
        return None


def reload_all() -> None:
    if _scheduler is None:
        return
    for job in _scheduler.get_jobs():
        if job.id.startswith("sched-"):
            job.remove()
    for schedule in db.list_schedules():
        if not schedule["enabled"]:
            continue
        error = validate_cron(schedule["cron"])
        if error:
            db.add_event("schedule.invalid",
                         f"Zeitplan '{schedule['name']}' hat einen ungueltigen Cron-Ausdruck: {error}",
                         level="error")
            continue
        _scheduler.add_job(
            _run_schedule, CronTrigger.from_crontab(schedule["cron"]),
            args=[schedule["id"]], id=f"sched-{schedule['id']}", replace_existing=True,
            misfire_grace_time=3600, coalesce=True, max_instances=1,
        )
    events.publish("schedules.reloaded", {"count": len(db.list_schedules())})


def create(name: str, cron: str, containers: list[str], all_containers: bool = False,
           options: dict[str, Any] | None = None, enabled: bool = True) -> dict[str, Any]:
    error = validate_cron(cron)
    if error:
        raise ValueError(f"Ungueltiger Cron-Ausdruck: {error}")
    record = {
        "id": uuid.uuid4().hex[:12], "name": name, "cron": cron,
        "containers": containers, "all_containers": all_containers,
        "enabled": enabled, "options": options or {}, "created_at": db.now_iso(),
    }
    db.save_schedule(record)
    reload_all()
    return record


def update(schedule_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    current = next((s for s in db.list_schedules() if s["id"] == schedule_id), None)
    if not current:
        raise KeyError(f"Zeitplan '{schedule_id}' nicht gefunden")
    merged = {**current, **patch, "id": schedule_id}
    error = validate_cron(merged["cron"])
    if error:
        raise ValueError(f"Ungueltiger Cron-Ausdruck: {error}")
    db.save_schedule(merged)
    reload_all()
    return merged


def delete(schedule_id: str) -> None:
    db.delete_schedule(schedule_id)
    reload_all()


def targets_for(schedule: dict[str, Any]) -> list[str]:
    excluded = set(config.get("exclude_containers", []))
    if schedule["all_containers"]:
        names = [c["name"] for c in docker_api.list_containers(all_containers=True)]
    else:
        names = list(schedule["containers"])
    return [n for n in names if n not in excluded]


def run_now(schedule_id: str) -> list[str]:
    schedule = next((s for s in db.list_schedules() if s["id"] == schedule_id), None)
    if not schedule:
        raise KeyError(f"Zeitplan '{schedule_id}' nicht gefunden")
    return _dispatch(schedule, trigger="manual-schedule")


def _run_schedule(schedule_id: str) -> None:
    schedule = next((s for s in db.list_schedules() if s["id"] == schedule_id), None)
    if not schedule or not schedule["enabled"]:
        return
    _dispatch(schedule, trigger="schedule")


def _dispatch(schedule: dict[str, Any], trigger: str) -> list[str]:
    targets = targets_for(schedule)
    if not targets:
        db.mark_schedule_run(schedule["id"], "leer")
        db.add_event("schedule.skipped", f"Zeitplan '{schedule['name']}': keine Container",
                     level="warn")
        return []

    db.add_event("schedule.started",
                 f"Zeitplan '{schedule['name']}' startet {len(targets)} Backup(s)",
                 detail={"containers": targets})

    job_ids: list[str] = []
    options = schedule.get("options") or {}

    def _sequential(ctx: runner.JobContext) -> dict[str, Any]:
        results = {"ok": [], "failed": []}
        for index, name in enumerate(targets):
            ctx.check_cancel()
            ctx.step(f"[{index + 1}/{len(targets)}] {name}",
                     (index / len(targets)) * 100)
            try:
                backup.run(ctx, name, trigger=trigger, options=options)
                results["ok"].append(name)
            except runner.JobCancelled:
                raise            # Abbruch beendet den Lauf, statt weiterzumachen
            except Exception as exc:  # noqa: BLE001 - ein Fehler stoppt den Lauf nicht
                ctx.log(f"{name}: {exc}", "error")
                results["failed"].append({"container": name, "error": str(exc)})
        db.mark_schedule_run(schedule["id"],
                             "ok" if not results["failed"] else "teilweise")
        return results

    job_ids.append(runner.submit("schedule", schedule["name"], _sequential))
    return job_ids


def _nightly_maintenance() -> None:
    try:
        db.prune_history()
        for container in {b["container"] for b in db.list_backups(limit=2000)}:
            backup.apply_retention(container)
        db.add_event("maintenance", "Naechtliche Wartung ausgefuehrt")
    except Exception as exc:  # noqa: BLE001
        db.add_event("maintenance.failed", f"Wartung fehlgeschlagen: {exc}", level="error")


def overview() -> list[dict[str, Any]]:
    out = []
    for schedule in db.list_schedules():
        out.append({**schedule, "next_run": next_run(schedule["cron"]) if schedule["enabled"] else None,
                    "target_count": len(targets_for(schedule)) if schedule["enabled"] else 0})
    return out
