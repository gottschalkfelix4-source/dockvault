"""Konfiguration: Umgebungsvariablen + persistente settings.json."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("DV_CONFIG_DIR", "/config"))
BACKUP_DIR = Path(os.environ.get("DV_BACKUP_DIR", "/backups"))
DB_PATH = CONFIG_DIR / "dockvault.db"
SETTINGS_PATH = CONFIG_DIR / "settings.json"

# Unraid-Pfade im Container (per Bind-Mount durchgereicht)
TEMPLATES_DIR = Path(os.environ.get("DV_TEMPLATES_DIR", "/boot/config/plugins/dockerMan/templates-user"))
DOCKER_VOLUMES_DIR = Path(os.environ.get("DV_DOCKER_VOLUMES", "/var/lib/docker/volumes"))
DOCKER_SOCKET = os.environ.get("DV_DOCKER_SOCKET", "/var/run/docker.sock")

# Name des eigenen Containers - wird nie gesichert oder gestoppt
SELF_NAME = os.environ.get("DV_SELF_NAME", "dockvault")

# Wohin darf ein Restore schreiben? Alles ausserhalb wird abgelehnt.
# /mnt deckt alle Unraid-Pools, Shares, Einzelplatten und Remotes ab. Pools heissen
# frei waehlbar, eine Aufzaehlung waere zwangslaeufig unvollstaendig. Alles ausserhalb
# (/etc, /usr, /root ...) bleibt gesperrt.
RESTORE_ROOTS = [
    "/mnt",
    "/var/lib/docker/volumes",
    "/boot/config",
]

DEFAULTS: dict[str, Any] = {
    # Backup-Ziel
    "target_type": "local",          # local | smb
    "smb_host": "",
    "smb_share": "",
    "smb_path": "",                  # Unterordner in der Freigabe, optional
    "smb_user": "",
    "smb_password": "",
    "smb_domain": "",
    "smb_version": "3.0",            # auto | 3.1.1 | 3.0 | 2.1 | 1.0
    "smb_options": "",               # zusaetzliche mount-Optionen
    # Sicherungsumfang: was von den Mounts eines Containers gesichert wird
    #
    # "roots" sichert ausschliesslich Mounts, die unterhalb eines der unten
    # angegebenen Quellverzeichnisse liegen. Die Vorsortierung passiert damit
    # einmal zentral statt pro Container geraten zu werden - Medienshares wie
    # /mnt/medien koennen so gar nicht erst versehentlich im Backup landen.
    "mount_scope": "roots",          # roots = nur aus den Quellverzeichnissen | all = alles
    "backup_roots": [],              # z. B. /mnt/work/appdata, /mnt/cache/appdata
    "appdata_dirname": "appdata",    # nur fuer den Erkennungs-Vorschlag in der UI
    # Sicherung
    "compression": "zstd",          # zstd | gzip | none
    "compression_level": 6,
    "stop_container": True,          # Container waehrend Backup anhalten (konsistent)
    "include_volumes": True,
    "include_binds": True,
    "include_template": True,
    "generate_missing_template": True,
    "exclude_containers": [SELF_NAME, "dockguard"],
    "exclude_paths": [
        "/var/run/docker.sock",
        "/etc/localtime",
        "/etc/timezone",
        "/dev",
        "/proc",
        "/sys",
    ],
    "exclude_patterns": ["*.sock", "*.pid", "**/cache/**", "**/Cache/**", "**/logs/*.log"],
    "max_artifact_gb": 0,            # 0 = unbegrenzt; sonst Mount ueberspringen wenn groesser
    # Aufbewahrung
    "retention_keep_last": 7,
    "retention_keep_days": 30,
    "retention_enabled": True,
    # Betrieb
    "parallel_jobs": 1,
    "verify_checksums": True,
    "timezone": os.environ.get("TZ", "Europe/Berlin"),
    "ui_theme": "auto",
}

# Werte, die die API niemals im Klartext herausgibt
SECRET_KEYS = {"smb_password"}
SECRET_MASK = "•" * 8

_lock = threading.RLock()
_cache: dict[str, Any] | None = None


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Aeltere Einstellungen auf die aktuellen Schluessel heben."""
    # Frueher gab es getrennte Listen und eine Tiefenregel. Die Tiefenregel hat
    # je nach appdata-Struktur mal zu viel, mal zu wenig erfasst; ersetzt durch
    # ausdrueckliche Quellverzeichnisse.
    if not data.get("backup_roots"):
        merged = list(data.get("appdata_roots") or []) + list(data.get("include_extra_paths") or [])
        if merged:
            data["backup_roots"] = sorted(dict.fromkeys(merged))
    if data.get("mount_scope") == "appdata":
        data["mount_scope"] = "roots"
    for gone in ("appdata_max_depth", "appdata_roots", "include_extra_paths"):
        data.pop(gone, None)
    return data


def _load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            data.update(json.loads(SETTINGS_PATH.read_text("utf-8")))
        except (OSError, ValueError):
            pass
    return _migrate(data)


def all_settings() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load()
        return dict(_cache)


def get(key: str, default: Any = None) -> Any:
    return all_settings().get(key, DEFAULTS.get(key, default))


def public_settings() -> dict[str, Any]:
    """Wie ``all_settings``, aber Geheimnisse nur als Platzhalter."""
    data = all_settings()
    for key in SECRET_KEYS:
        data[key] = SECRET_MASK if data.get(key) else ""
    return data


def update(patch: dict[str, Any]) -> dict[str, Any]:
    global _cache
    with _lock:
        data = _load()
        for key, value in patch.items():
            if key not in DEFAULTS:
                continue
            # Der Platzhalter bedeutet "unveraendert lassen"
            if key in SECRET_KEYS and value == SECRET_MASK:
                continue
            data[key] = value
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        tmp.replace(SETTINGS_PATH)
        try:
            SETTINGS_PATH.chmod(0o600)   # enthaelt das SMB-Passwort
        except OSError:
            pass
        _cache = data
        return dict(data)


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
