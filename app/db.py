"""SQLite-Index fuer Backups, Jobs, Zeitplaene und Timeline-Ereignisse."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

_local = threading.local()
_init_lock = threading.Lock()
_initialised = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS backups (
    id            TEXT PRIMARY KEY,
    container     TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,
    trigger       TEXT NOT NULL DEFAULT 'manual',
    image         TEXT,
    source_bytes  INTEGER DEFAULT 0,
    archive_bytes INTEGER DEFAULT 0,
    duration_s    REAL DEFAULT 0,
    has_template  INTEGER DEFAULT 0,
    path          TEXT NOT NULL,
    manifest      TEXT,
    error         TEXT,
    pinned        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_backups_container ON backups(container, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backups_created  ON backups(created_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    target      TEXT,
    status      TEXT NOT NULL,
    progress    REAL DEFAULT 0,
    message     TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    result      TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs(started_at DESC);

CREATE TABLE IF NOT EXISTS job_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    ts     TEXT NOT NULL,
    level  TEXT NOT NULL DEFAULT 'info',
    line   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_joblog_job ON job_log(job_id, id);

CREATE TABLE IF NOT EXISTS schedules (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    cron           TEXT NOT NULL,
    containers     TEXT NOT NULL DEFAULT '[]',
    all_containers INTEGER DEFAULT 0,
    enabled        INTEGER DEFAULT 1,
    options        TEXT DEFAULT '{}',
    last_run       TEXT,
    last_status    TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    level     TEXT NOT NULL DEFAULT 'info',
    container TEXT,
    job_id    TEXT,
    backup_id TEXT,
    message   TEXT NOT NULL,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_container ON events(container, ts DESC);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


def init() -> None:
    global _initialised
    with _init_lock:
        if _initialised:
            return
        conn = connect()
        conn.executescript(SCHEMA)
        conn.commit()
        _initialised = True


def query(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    cur = connect().execute(sql, tuple(params))
    return [dict(row) for row in cur.fetchall()]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    conn = connect()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


# ---------------------------------------------------------------- Backups

def upsert_backup(record: dict[str, Any]) -> None:
    manifest = record.get("manifest")
    execute(
        """INSERT INTO backups
             (id, container, created_at, finished_at, status, trigger, image,
              source_bytes, archive_bytes, duration_s, has_template, path, manifest, error, pinned)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             finished_at=excluded.finished_at, status=excluded.status,
             source_bytes=excluded.source_bytes, archive_bytes=excluded.archive_bytes,
             duration_s=excluded.duration_s, has_template=excluded.has_template,
             manifest=excluded.manifest, error=excluded.error""",
        (
            record["id"], record["container"], record["created_at"], record.get("finished_at"),
            record.get("status", "running"), record.get("trigger", "manual"), record.get("image"),
            record.get("source_bytes", 0), record.get("archive_bytes", 0), record.get("duration_s", 0),
            1 if record.get("has_template") else 0, str(record["path"]),
            json.dumps(manifest, ensure_ascii=False) if isinstance(manifest, dict) else manifest,
            record.get("error"), 1 if record.get("pinned") else 0,
        ),
    )


def get_backup(backup_id: str) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM backups WHERE id=?", (backup_id,))
    if row and row.get("manifest"):
        try:
            row["manifest"] = json.loads(row["manifest"])
        except ValueError:
            row["manifest"] = None
    return row


def list_backups(container: str | None = None, limit: int = 500,
                 since: str | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT id, container, created_at, finished_at, status, trigger, image, source_bytes,"
           " archive_bytes, duration_s, has_template, path, error, pinned FROM backups WHERE 1=1")
    params: list[Any] = []
    if container:
        sql += " AND container=?"
        params.append(container)
    if since:
        sql += " AND created_at>=?"
        params.append(since)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return query(sql, params)


def set_pinned(backup_id: str, pinned: bool) -> None:
    execute("UPDATE backups SET pinned=? WHERE id=?", (1 if pinned else 0, backup_id))


def delete_backup(backup_id: str) -> None:
    execute("DELETE FROM backups WHERE id=?", (backup_id,))


# ---------------------------------------------------------------- Jobs

def create_job(job_id: str, job_type: str, target: str | None) -> None:
    execute(
        "INSERT INTO jobs (id, type, target, status, progress, message, started_at)"
        " VALUES (?,?,?,'running',0,'Gestartet',?)",
        (job_id, job_type, target, now_iso()),
    )


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    if isinstance(fields.get("result"), (dict, list)):
        fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
    sets = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))


def add_job_line(job_id: str, line: str, level: str = "info") -> None:
    execute("INSERT INTO job_log (job_id, ts, level, line) VALUES (?,?,?,?)",
            (job_id, now_iso(), level, line))


def get_job(job_id: str) -> dict[str, Any] | None:
    job = query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if job:
        job["log"] = query(
            "SELECT ts, level, line FROM job_log WHERE job_id=? ORDER BY id", (job_id,))
        if job.get("result"):
            try:
                job["result"] = json.loads(job["result"])
            except ValueError:
                pass
    return job


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    return query("SELECT id, type, target, status, progress, message, started_at, finished_at, error"
                 " FROM jobs ORDER BY started_at DESC LIMIT ?", (limit,))


# ---------------------------------------------------------------- Events

def add_event(kind: str, message: str, *, level: str = "info", container: str | None = None,
              job_id: str | None = None, backup_id: str | None = None,
              detail: dict[str, Any] | None = None) -> None:
    execute(
        "INSERT INTO events (ts, kind, level, container, job_id, backup_id, message, detail)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (now_iso(), kind, level, container, job_id, backup_id, message,
         json.dumps(detail, ensure_ascii=False) if detail else None),
    )


def list_events(since: str | None = None, container: str | None = None,
                limit: int = 400) -> list[dict[str, Any]]:
    sql = "SELECT * FROM events WHERE 1=1"
    params: list[Any] = []
    if since:
        sql += " AND ts>=?"
        params.append(since)
    if container:
        sql += " AND container=?"
        params.append(container)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    rows = query(sql, params)
    for row in rows:
        if row.get("detail"):
            try:
                row["detail"] = json.loads(row["detail"])
            except ValueError:
                row["detail"] = None
    return rows


def prune_history(keep_events: int = 5000, keep_jobs: int = 300) -> None:
    execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)",
            (keep_events,))
    execute("DELETE FROM job_log WHERE job_id NOT IN"
            " (SELECT id FROM jobs ORDER BY started_at DESC LIMIT ?)", (keep_jobs,))
    execute("DELETE FROM jobs WHERE id NOT IN (SELECT id FROM jobs ORDER BY started_at DESC LIMIT ?)",
            (keep_jobs,))


# ---------------------------------------------------------------- Schedules

def list_schedules() -> list[dict[str, Any]]:
    rows = query("SELECT * FROM schedules ORDER BY created_at")
    for row in rows:
        row["containers"] = json.loads(row.get("containers") or "[]")
        row["options"] = json.loads(row.get("options") or "{}")
        row["enabled"] = bool(row["enabled"])
        row["all_containers"] = bool(row["all_containers"])
    return rows


def save_schedule(rec: dict[str, Any]) -> None:
    execute(
        """INSERT INTO schedules (id, name, cron, containers, all_containers, enabled, options, created_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, cron=excluded.cron,
             containers=excluded.containers, all_containers=excluded.all_containers,
             enabled=excluded.enabled, options=excluded.options""",
        (rec["id"], rec["name"], rec["cron"], json.dumps(rec.get("containers", [])),
         1 if rec.get("all_containers") else 0, 1 if rec.get("enabled", True) else 0,
         json.dumps(rec.get("options", {})), rec.get("created_at") or now_iso()),
    )


def delete_schedule(schedule_id: str) -> None:
    execute("DELETE FROM schedules WHERE id=?", (schedule_id,))


def mark_schedule_run(schedule_id: str, status: str) -> None:
    execute("UPDATE schedules SET last_run=?, last_status=? WHERE id=?",
            (now_iso(), status, schedule_id))
