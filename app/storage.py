"""Backup-Ziel: lokaler Pfad oder SMB-Freigabe.

Die SMB-Freigabe wird per ``mount.cifs`` direkt an ``/backups`` gehaengt. Dadurch
arbeitet der gesamte restliche Code unveraendert weiter - packen, entpacken,
Inhaltsverzeichnis, Aufbewahrung und der Index-Neuaufbau sehen einfach ein
Dateisystem.

Voraussetzung im Container: ``cifs-utils`` (im Image enthalten) und die
Capabilities ``SYS_ADMIN`` (einhaengen) sowie ``DAC_READ_SEARCH`` (setzt
mount.cifs selbst, um die Zugangsdatendatei zu lesen). Fehlen sie, scheitert
mount.cifs mit "Unable to apply new capability set." - deshalb pruefen wir sie
vorab und nennen in der Oberflaeche genau die fehlende Berechtigung.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import config

CRED_FILE = Path("/run/dockvault-smb.cred")
MASK = "•" * 8  # Platzhalter, den die Oberflaeche statt des Passworts sieht


class StorageError(RuntimeError):
    pass


# ---------------------------------------------------------------- Zustand

def _mount_table() -> list[tuple[str, str, str]]:
    out = []
    try:
        for line in Path("/proc/mounts").read_text("utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 3:
                out.append((parts[0], parts[1], parts[2]))
    except OSError:
        pass
    return out


def is_mounted(path: Path | None = None) -> bool:
    target = str(path or config.BACKUP_DIR).rstrip("/")
    return any(mp.rstrip("/") == target for _src, mp, _fs in _mount_table())


def mounted_source(path: Path | None = None) -> tuple[str, str] | None:
    target = str(path or config.BACKUP_DIR).rstrip("/")
    for src, mp, fs in _mount_table():
        if mp.rstrip("/") == target:
            return src, fs
    return None


def cifs_available() -> bool:
    return shutil.which("mount.cifs") is not None


# mount.cifs braucht beide: SYS_ADMIN zum Einhaengen, DAC_READ_SEARCH setzt es
# selbst, um die Zugangsdatendatei zu lesen - fehlt es, bricht es mit
# "Unable to apply new capability set." ab, was ohne Kontext nichtssagend ist.
_REQUIRED_CAPS = {"SYS_ADMIN": 21, "DAC_READ_SEARCH": 2}


def missing_capabilities() -> list[str]:
    try:
        for line in Path("/proc/self/status").read_text("utf-8").splitlines():
            if line.startswith("CapEff:"):
                effective = int(line.split()[1], 16)
                return [name for name, bit in _REQUIRED_CAPS.items()
                        if not effective & (1 << bit)]
    except (OSError, ValueError, IndexError):
        pass
    return []


def _capability_hint(missing: list[str]) -> str:
    flags = " ".join(f"--cap-add {name}" for name in missing)
    return (f"Dem Container fehlt: {', '.join(missing)}. Ergaenze in den Extra Parameters "
            f"des Containers '{flags}' und starte ihn neu.")


def unc_path(settings: dict[str, Any] | None = None) -> str:
    settings = settings or config.all_settings()
    host = (settings.get("smb_host") or "").strip()
    share = (settings.get("smb_share") or "").strip().strip("/")
    sub = (settings.get("smb_path") or "").strip().strip("/")
    base = f"//{host}/{share}"
    return f"{base}/{sub}" if sub else base


def status() -> dict[str, Any]:
    settings = config.all_settings()
    target_type = settings.get("target_type", "local")
    info = mounted_source()
    usage: dict[str, Any] = {"total": 0, "used": 0, "free": 0}
    writable = False

    try:
        du = shutil.disk_usage(config.BACKUP_DIR)
        usage = {"total": du.total, "used": du.used, "free": du.free}
    except OSError:
        pass

    # Nur pruefen, nicht schreiben: status() wird oft aufgerufen, und auf
    # Freigaben mit Papierkorb wuerde jede Testdatei dort Muell hinterlassen.
    writable = os.access(config.BACKUP_DIR, os.W_OK) and config.BACKUP_DIR.is_dir()

    return {
        "target_type": target_type,
        "path": str(config.BACKUP_DIR),
        "mounted": info is not None,
        "mount_source": info[0] if info else None,
        "mount_fs": info[1] if info else None,
        "writable": writable,
        "cifs_available": cifs_available(),
        "missing_capabilities": missing_capabilities(),
        "unc": unc_path(settings) if target_type == "smb" else None,
        "ready": _ready(target_type, info, writable),
        **usage,
    }


def _ready(target_type: str, info: tuple[str, str] | None, writable: bool) -> bool:
    if target_type == "smb":
        return bool(info) and info[1].startswith("cifs") and writable
    return writable


def require_ready() -> None:
    """Vor jedem Backup: schreibt nicht ins Nirwana, wenn SMB nicht haengt."""
    state = status()
    if state["ready"]:
        return
    if state["target_type"] == "smb" and not state["mounted"]:
        raise StorageError(
            "Das SMB-Backup-Ziel ist nicht eingebunden. Ohne Einbindung wuerden "
            "Sicherungen im Container landen und beim Neustart verloren gehen. "
            "Pruefe die Verbindung unter Einstellungen -> Backup-Ziel.")
    raise StorageError(f"Backup-Ziel {config.BACKUP_DIR} ist nicht beschreibbar.")


# ---------------------------------------------------------------- Ein-/Aushaengen

def _write_credentials(user: str, password: str, domain: str) -> Path:
    """Zugangsdaten in eine Datei - niemals als Argument, sonst stehen sie in ps."""
    CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"username={user}", f"password={password}"]
    if domain:
        lines.append(f"domain={domain}")
    CRED_FILE.write_text("\n".join(lines) + "\n", "utf-8")
    CRED_FILE.chmod(0o600)
    return CRED_FILE


def _mount_options(settings: dict[str, Any], cred: Path) -> str:
    opts = [
        f"credentials={cred}",
        "uid=0", "gid=0",
        "file_mode=0660", "dir_mode=0770",
        "nobrl",          # SQLite/Sperren auf CIFS entschaerfen
        "noserverino",    # stabile Inodes, sonst stolpert rename gelegentlich
        "actimeo=1",
    ]
    version = (settings.get("smb_version") or "").strip()
    if version and version != "auto":
        opts.append(f"vers={version}")
    extra = (settings.get("smb_options") or "").strip()
    if extra:
        opts.append(extra.lstrip(","))
    return ",".join(opts)


def _run_mount(source: str, mountpoint: Path, options: str,
               timeout: int = 45) -> subprocess.CompletedProcess:
    mountpoint.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["mount", "-t", "cifs", source, str(mountpoint), "-o", options],
        capture_output=True, text=True, timeout=timeout, check=False)


def _explain(stderr: str, returncode: int) -> str:
    text = (stderr or "").strip()
    low = text.lower()
    if ("capability set" in low or "operation not permitted" in low
            or "permission denied (mount" in low):
        missing = missing_capabilities()
        if missing:
            return _capability_hint(missing) + f" (Meldung: {text or returncode})"
        return ("Einbinden nicht erlaubt - der Container darf nicht mounten. "
                f"(Meldung: {text or returncode})")
    if "no such device" in low or "wrong fs type" in low:
        return (f"Der Host stellt kein CIFS bereit oder die SMB-Version passt nicht. "
                f"Probiere eine andere SMB-Version. (Meldung: {text})")
    if "permission denied" in low or "logon failure" in low or "13" in low.split():
        return f"Anmeldung abgelehnt - Benutzer, Passwort oder Domaene pruefen. ({text})"
    if "no such file or directory" in low or "2" == low.strip():
        return f"Freigabe oder Unterpfad existiert nicht. ({text})"
    if "host is down" in low or "connection timed out" in low or "unreachable" in low:
        return f"Server nicht erreichbar - Hostname/IP und Netzwerk pruefen. ({text})"
    return text or f"mount endete mit Code {returncode}"


def _ensure_subpath(settings: dict[str, Any], cred: Path) -> None:
    """Legt den Unterordner in der Freigabe an, falls er noch fehlt.

    Ein nicht existierender Unterordner ist der haeufigste Grund fuer ein
    fehlschlagendes ``mount`` - und aus der Meldung "No such file or directory"
    nicht zu erraten. Also: Freigabe-Wurzel kurz einhaengen, Ordner anlegen,
    wieder aushaengen.
    """
    sub = (settings.get("smb_path") or "").strip().strip("/")
    if not sub:
        return

    host = (settings.get("smb_host") or "").strip()
    share = (settings.get("smb_share") or "").strip().strip("/")
    root_unc = f"//{host}/{share}"
    probe = Path(tempfile.mkdtemp(prefix="dv-smbroot-"))
    try:
        result = _run_mount(root_unc, probe, _mount_options(settings, cred), timeout=30)
        if result.returncode != 0:
            # Kein Zugriff auf die Wurzel - der eigentliche Mount meldet das gleich
            # ohnehin mit passender Erklaerung.
            return
        target = probe / sub
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        subprocess.run(["umount", "-l", str(probe)], capture_output=True,
                       text=True, timeout=20, check=False)
        try:
            os.rmdir(probe)
        except OSError:
            pass


def mount_smb(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or config.all_settings()
    if not cifs_available():
        raise StorageError("mount.cifs fehlt im Image - bitte das Image aktualisieren.")
    missing = missing_capabilities()
    if missing:
        raise StorageError(_capability_hint(missing))
    for field, label in (("smb_host", "Server"), ("smb_share", "Freigabe"),
                         ("smb_user", "Benutzer")):
        if not (settings.get(field) or "").strip():
            raise StorageError(f"{label} ist nicht gesetzt.")

    if is_mounted():
        unmount()

    cred = _write_credentials(settings.get("smb_user", ""),
                              settings.get("smb_password", ""),
                              (settings.get("smb_domain") or "").strip())
    _ensure_subpath(settings, cred)
    source = unc_path(settings)
    try:
        result = _run_mount(source, config.BACKUP_DIR, _mount_options(settings, cred))
    except subprocess.TimeoutExpired as exc:
        raise StorageError(f"Zeitueberschreitung beim Einbinden von {source}") from exc

    if result.returncode != 0:
        raise StorageError(_explain(result.stderr, result.returncode))
    if not is_mounted():
        raise StorageError(f"{source} wurde nicht eingebunden (keine Fehlermeldung).")
    return {"mounted": True, "source": source}


def unmount() -> dict[str, Any]:
    if not is_mounted():
        return {"mounted": False, "changed": False}
    result = subprocess.run(["umount", "-l", str(config.BACKUP_DIR)],
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0 and is_mounted():
        raise StorageError(result.stderr.strip() or "Aushaengen fehlgeschlagen")
    return {"mounted": False, "changed": True}


def test_smb(params: dict[str, Any]) -> dict[str, Any]:
    """Probeweise in ein temporaeres Verzeichnis einbinden - ohne /backups anzufassen."""
    if not cifs_available():
        raise StorageError("mount.cifs fehlt im Image - bitte das Image aktualisieren.")
    missing = missing_capabilities()
    if missing:
        raise StorageError(_capability_hint(missing))

    merged = {**config.all_settings(), **{k: v for k, v in params.items() if v is not None}}
    if merged.get("smb_password") in (MASK, None):
        merged["smb_password"] = config.get("smb_password", "")

    for field, label in (("smb_host", "Server"), ("smb_share", "Freigabe"),
                         ("smb_user", "Benutzer")):
        if not (merged.get(field) or "").strip():
            raise StorageError(f"{label} ist nicht gesetzt.")

    source = unc_path(merged)
    probe = Path(tempfile.mkdtemp(prefix="dv-smbtest-"))
    cred = _write_credentials(merged.get("smb_user", ""), merged.get("smb_password", ""),
                              (merged.get("smb_domain") or "").strip())
    _ensure_subpath(merged, cred)
    try:
        result = _run_mount(source, probe, _mount_options(merged, cred), timeout=30)
        if result.returncode != 0:
            raise StorageError(_explain(result.stderr, result.returncode))

        entries = sorted(p.name for p in probe.iterdir())[:15]
        try:
            du = shutil.disk_usage(probe)
            free, total = du.free, du.total
        except OSError:
            free = total = 0

        marker = probe / ".dockvault-schreibtest"
        try:
            marker.write_text("ok", "utf-8")
            marker.unlink()
            writable = True
            write_error = None
        except OSError as exc:
            writable = False
            write_error = str(exc)

        return {"ok": True, "source": source, "writable": writable,
                "write_error": write_error, "free": free, "total": total,
                "entries": entries, "entry_count": len(entries)}
    finally:
        subprocess.run(["umount", "-l", str(probe)], capture_output=True,
                       text=True, timeout=20, check=False)
        try:
            os.rmdir(probe)
        except OSError:
            pass


def apply_target(previous_type: str | None = None) -> dict[str, Any]:
    """Nach dem Speichern der Einstellungen den Zielzustand herstellen."""
    settings = config.all_settings()
    target_type = settings.get("target_type", "local")
    if target_type == "smb":
        return mount_smb(settings)
    if previous_type == "smb" or is_mounted():
        unmount()
    return {"mounted": False}
