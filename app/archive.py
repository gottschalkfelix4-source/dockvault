"""Packen und Entpacken der Nutzdaten (tar + zstd/gzip) mit Fortschritt und Pruefsumme."""
from __future__ import annotations

import fnmatch
import gzip
import hashlib
import os
import stat as stat_mod
import tarfile
from pathlib import Path
from typing import Any, Callable, IO

try:
    import zstandard
    HAVE_ZSTD = True
except ImportError:  # pragma: no cover - Fallback auf gzip
    zstandard = None  # type: ignore[assignment]
    HAVE_ZSTD = False

ProgressCb = Callable[[int, int], None] | None
CancelCb = Callable[[], None] | None


class ArchiveCancelled(RuntimeError):
    """Wird geworfen, wenn ein Pack- oder Entpackvorgang abgebrochen wurde."""

SUFFIX = {"zstd": ".tar.zst", "gzip": ".tar.gz", "none": ".tar"}


class _HashingWriter:
    """Schreibt durch und bildet nebenbei die SHA256-Summe des Archivs."""

    def __init__(self, fh: IO[bytes]):
        self._fh = fh
        self._hash = hashlib.sha256()
        self.written = 0

    def write(self, data: bytes) -> int:
        self._hash.update(data)
        self.written += len(data)
        return self._fh.write(data)

    def flush(self) -> None:
        self._fh.flush()

    def tell(self) -> int:
        return self.written

    @property
    def digest(self) -> str:
        return self._hash.hexdigest()


def _close_quietly(handle: Any) -> None:
    """Schliessen, ohne dass Folgefehler den eigentlichen Abbruch verdecken."""
    try:
        handle.close()
    except Exception:  # noqa: BLE001
        pass


def choose_compression(name: str) -> str:
    if name == "zstd" and not HAVE_ZSTD:
        return "gzip"
    return name if name in SUFFIX else "zstd" if HAVE_ZSTD else "gzip"


def suffix_for(compression: str) -> str:
    return SUFFIX.get(compression, ".tar.zst")


def detect_compression(path: Path) -> str:
    name = path.name
    if name.endswith(".tar.zst"):
        return "zstd"
    if name.endswith(".tar.gz"):
        return "gzip"
    return "none"


def _excluded(rel_path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(os.path.basename(rel_path), pattern):
            return True
    return False


def measure(source: Path, excludes: list[str]) -> dict[str, int]:
    """Groesse des zu sichernden Baums - und was die Dateimuster herausnehmen.

    Der ausgefilterte Anteil wird mitgezaehlt, damit sich der Unterschied zu
    ``du -sh`` erklaeren laesst. Ohne diese Zahl wirkt ein Backup, das z. B.
    Plex' knapp ein Gigabyte grossen Cache-Ordner auslaesst, schlicht falsch.
    """
    if source.is_file():
        return {"bytes": source.stat().st_size, "files": 1,
                "excluded_bytes": 0, "excluded_files": 0}

    total = count = skipped_bytes = skipped_files = 0

    def _add_skipped(path: str) -> None:
        nonlocal skipped_bytes, skipped_files
        try:
            st = os.lstat(path)
        except OSError:
            return
        skipped_bytes += st.st_size
        skipped_files += 1

    for root, dirs, files in os.walk(source, onerror=lambda _e: None):
        rel_root = os.path.relpath(root, source)
        keep_dirs = []
        for name in dirs:
            if _excluded(os.path.normpath(os.path.join(rel_root, name)), excludes):
                # Ganzer Ast faellt weg - Inhalt fuer die Statistik trotzdem zaehlen
                for sub_root, _sub_dirs, sub_files in os.walk(os.path.join(root, name),
                                                             onerror=lambda _e: None):
                    for sub in sub_files:
                        _add_skipped(os.path.join(sub_root, sub))
            else:
                keep_dirs.append(name)
        dirs[:] = keep_dirs

        for filename in files:
            rel = os.path.normpath(os.path.join(rel_root, filename))
            full = os.path.join(root, filename)
            if _excluded(rel, excludes):
                _add_skipped(full)
                continue
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if stat_mod.S_ISSOCK(st.st_mode):
                continue
            total += st.st_size
            count += 1

    return {"bytes": total, "files": count,
            "excluded_bytes": skipped_bytes, "excluded_files": skipped_files}


def pack(source: Path, dest: Path, *, compression: str = "zstd", level: int = 6,
         excludes: list[str] | None = None, progress: ProgressCb = None,
         cancelled: CancelCb = None) -> dict[str, Any]:
    """Packt ``source`` nach ``dest``. Gibt Groessen, Pruefsumme und Dateizahl zurueck."""
    excludes = excludes or []
    compression = choose_compression(compression)
    dest.parent.mkdir(parents=True, exist_ok=True)
    stats = measure(source, excludes)
    base = source.name if source.is_file() else "."

    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        return _pack_into(tmp, dest, source, base, excludes, compression, level,
                          stats, progress, cancelled)
    except BaseException:
        tmp.unlink(missing_ok=True)   # keine halben Archive hinterlassen
        raise


def _pack_into(tmp: Path, dest: Path, source: Path, base: str, excludes: list[str],
               compression: str, level: int, stats: dict[str, int],
               progress: ProgressCb, cancelled: CancelCb) -> dict[str, Any]:
    total_bytes = stats["bytes"]
    done_bytes = 0
    files_added = 0
    skipped: list[str] = []
    with open(tmp, "wb") as raw:
        writer = _HashingWriter(raw)
        stream: Any
        if compression == "zstd":
            compressor = zstandard.ZstdCompressor(level=level, threads=-1)
            stream = compressor.stream_writer(writer)
        elif compression == "gzip":
            stream = gzip.GzipFile(fileobj=writer, mode="wb", compresslevel=min(level, 9))
        else:
            stream = writer

        tar = tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT,
                           bufsize=1024 * 256)
        try:
            for path, arcname in _walk(source, base, excludes):
                # Vor jeder Datei pruefen - sonst laeuft ein grosses appdata
                # nach dem Abbruch noch minutenlang weiter.
                if cancelled:
                    cancelled()
                try:
                    st = os.lstat(path)
                    if stat_mod.S_ISSOCK(st.st_mode):
                        skipped.append(arcname)
                        continue
                    tar.add(path, arcname=arcname, recursive=False)
                    if os.path.isfile(path) and not os.path.islink(path):
                        done_bytes += st.st_size
                        files_added += 1
                        if progress and (files_added % 50 == 0 or done_bytes >= total_bytes):
                            progress(done_bytes, total_bytes)
                except (OSError, tarfile.TarError) as exc:
                    skipped.append(f"{arcname}: {exc}")
        except BaseException:
            # Das Archiv wird ohnehin verworfen. Beim Schliessen wuerde zstd
            # erst noch den gesamten mehrfaedigen Puffer auskomprimieren - bei
            # einem Abbruch sind das leicht Dutzende Sekunden Wartezeit fuer
            # ein Ergebnis, das niemand haben will. Also Ziel dichtmachen und
            # die dadurch scheiternden Schreibvorgaenge schlucken.
            _close_quietly(raw)
            raise
        finally:
            _close_quietly(tar)
            if compression in ("zstd", "gzip"):
                _close_quietly(stream)

        archive_bytes = writer.written
        digest = writer.digest

    tmp.replace(dest)
    if progress:
        progress(total_bytes, total_bytes)
    return {
        "source_bytes": total_bytes,
        "archive_bytes": archive_bytes,
        "files": files_added,
        "expected_files": stats["files"],
        "excluded_bytes": stats["excluded_bytes"],
        "excluded_files": stats["excluded_files"],
        "sha256": digest,
        "compression": compression,
        "skipped": skipped[:50],
        "skipped_count": len(skipped),
    }


def _walk(source: Path, base: str, excludes: list[str]):
    if source.is_file():
        yield str(source), base
        return
    yield str(source), "."
    for root, dirs, files in os.walk(source, onerror=lambda _e: None):
        rel_root = os.path.relpath(root, source)
        dirs[:] = sorted(d for d in dirs
                         if not _excluded(os.path.normpath(os.path.join(rel_root, d)), excludes))
        for name in dirs:
            rel = os.path.normpath(os.path.join(rel_root, name))
            yield os.path.join(root, name), rel
        for name in sorted(files):
            rel = os.path.normpath(os.path.join(rel_root, name))
            if _excluded(rel, excludes):
                continue
            yield os.path.join(root, name), rel


def _open_stream(archive: Path, compression: str):
    raw = open(archive, "rb")
    if compression == "zstd":
        if not HAVE_ZSTD:
            raw.close()
            raise RuntimeError("Archiv ist zstd-komprimiert, aber python-zstandard fehlt")
        return zstandard.ZstdDecompressor().stream_reader(raw), raw
    if compression == "gzip":
        return gzip.GzipFile(fileobj=raw, mode="rb"), raw
    return raw, raw


def _safe_member(member: tarfile.TarInfo, dest: Path) -> tarfile.TarInfo | None:
    """Verhindert Pfad-Ausbruch, behaelt aber Rechte und Eigentuemer bei."""
    name = member.name.replace("\\", "/")
    if name.startswith("/") or ".." in Path(name).parts:
        return None
    if member.islnk() or member.issym():
        target = member.linkname.replace("\\", "/")
        if member.islnk() and (target.startswith("/") or ".." in Path(target).parts):
            return None
        if member.issym() and target.startswith("/"):
            return None
    resolved = (dest / name).resolve()
    if not str(resolved).startswith(str(dest.resolve())):
        return None
    return member


def unpack(archive: Path, dest: Path, *, compression: str | None = None,
           progress: ProgressCb = None, cancelled: CancelCb = None) -> dict[str, Any]:
    """Entpackt ``archive`` nach ``dest`` und erhaelt dabei Rechte/Eigentuemer."""
    compression = compression or detect_compression(archive)
    dest.mkdir(parents=True, exist_ok=True)
    total = archive.stat().st_size
    restored = 0
    errors: list[str] = []

    stream, raw = _open_stream(archive, compression)
    try:
        tar = tarfile.open(fileobj=stream, mode="r|", bufsize=1024 * 256)
        for member in tar:
            if cancelled:
                cancelled()
            checked = _safe_member(member, dest)
            if checked is None:
                errors.append(f"uebersprungen (unsicherer Pfad): {member.name}")
                continue
            try:
                tar.extract(checked, path=dest, set_attrs=True, filter="fully_trusted")
                restored += 1
                if progress and restored % 100 == 0:
                    progress(min(raw.tell(), total), total)
            except (OSError, tarfile.TarError) as exc:
                errors.append(f"{member.name}: {exc}")
        tar.close()
    finally:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass
        raw.close()

    if progress:
        progress(total, total)
    return {"restored": restored, "errors": errors[:50], "error_count": len(errors)}


def list_entries(archive: Path, limit: int = 2000,
                 compression: str | None = None) -> list[dict[str, Any]]:
    """Inhaltsverzeichnis fuer die Vorschau im Restore-Assistenten."""
    compression = compression or detect_compression(archive)
    stream, raw = _open_stream(archive, compression)
    out: list[dict[str, Any]] = []
    try:
        tar = tarfile.open(fileobj=stream, mode="r|", bufsize=1024 * 256)
        for member in tar:
            out.append({
                "name": member.name,
                "size": member.size,
                "dir": member.isdir(),
                "mode": oct(member.mode)[-4:],
                "mtime": member.mtime,
            })
            if len(out) >= limit:
                break
        tar.close()
    finally:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass
        raw.close()
    return out


def verify(archive: Path, expected_sha256: str) -> bool:
    digest = hashlib.sha256()
    with open(archive, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected_sha256
