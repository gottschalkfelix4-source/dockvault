"""Hintergrund-Jobs mit Live-Fortschritt fuer Backup, Restore und Wartung."""
from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import db, events

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dv-job")
_active: dict[str, "JobContext"] = {}
_lock = threading.Lock()


class JobCancelled(RuntimeError):
    pass


class JobContext:
    """Wird an die Job-Funktion uebergeben: Logging, Fortschritt, Abbruch."""

    def __init__(self, job_id: str, job_type: str, target: str | None):
        self.job_id = job_id
        self.type = job_type
        self.target = target
        self._cancel = threading.Event()
        self._progress = 0.0
        self._message = "Gestartet"

    # -- Steuerung ------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled("Job wurde abgebrochen")

    # -- Ausgabe --------------------------------------------------------
    def log(self, line: str, level: str = "info") -> None:
        db.add_job_line(self.job_id, line, level)
        events.publish("job.log", {"job_id": self.job_id, "line": line, "level": level})

    def progress(self, percent: float, message: str | None = None) -> None:
        self._progress = max(0.0, min(100.0, percent))
        if message:
            self._message = message
        db.update_job(self.job_id, progress=self._progress, message=self._message)
        events.publish("job.progress", {
            "job_id": self.job_id, "type": self.type, "target": self.target,
            "progress": round(self._progress, 1), "message": self._message,
        })

    def step(self, message: str, percent: float | None = None) -> None:
        self.log(message)
        self.progress(self._progress if percent is None else percent, message)


def submit(job_type: str, target: str | None,
           fn: Callable[[JobContext], Any]) -> str:
    job_id = f"{job_type}-{uuid.uuid4().hex[:10]}"
    db.create_job(job_id, job_type, target)
    ctx = JobContext(job_id, job_type, target)
    with _lock:
        _active[job_id] = ctx
    events.publish("job.started", {"job_id": job_id, "type": job_type, "target": target})

    def _wrapper() -> None:
        try:
            result = fn(ctx)
            db.update_job(job_id, status="completed", progress=100,
                          message="Abgeschlossen", finished_at=db.now_iso(),
                          result=result if isinstance(result, (dict, list)) else None)
            events.publish("job.finished", {
                "job_id": job_id, "type": job_type, "target": target,
                "status": "completed", "result": result,
            })
        except JobCancelled:
            db.update_job(job_id, status="cancelled", message="Abgebrochen",
                          finished_at=db.now_iso())
            ctx.log("Job abgebrochen", "warn")
            events.publish("job.finished", {"job_id": job_id, "type": job_type,
                                            "target": target, "status": "cancelled"})
        except Exception as exc:  # noqa: BLE001 - jeder Fehler landet im Job-Log
            detail = traceback.format_exc(limit=6)
            db.update_job(job_id, status="failed", message=str(exc),
                          error=str(exc), finished_at=db.now_iso())
            ctx.log(f"FEHLER: {exc}", "error")
            ctx.log(detail, "error")
            db.add_event("job.failed", f"{job_type} fehlgeschlagen: {exc}", level="error",
                         container=target, job_id=job_id)
            events.publish("job.finished", {"job_id": job_id, "type": job_type,
                                            "target": target, "status": "failed",
                                            "error": str(exc)})
        finally:
            with _lock:
                _active.pop(job_id, None)

    _executor.submit(_wrapper)
    return job_id


def cancel(job_id: str) -> bool:
    with _lock:
        ctx = _active.get(job_id)
    if ctx:
        ctx.cancel()
        return True
    return False


def active_jobs() -> list[dict[str, Any]]:
    with _lock:
        return [{"job_id": j.job_id, "type": j.type, "target": j.target,
                 "progress": j._progress, "message": j._message}
                for j in _active.values()]


def is_busy(target: str) -> bool:
    with _lock:
        return any(j.target == target for j in _active.values())


def shutdown() -> None:
    with _lock:
        for ctx in _active.values():
            ctx.cancel()
    _executor.shutdown(wait=False, cancel_futures=True)
