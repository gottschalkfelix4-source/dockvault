"""DockVault - Docker-Backup und Wiederherstellung fuer Unraid."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import (__version__, api, backup, config, db, docker_api, events, runner,
               scheduler, storage)

logging.basicConfig(
    level=os.environ.get("DV_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("dockvault")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init()
    events.bind_loop(asyncio.get_running_loop())

    log.info("DockVault %s startet", __version__)
    log.info("Backup-Verzeichnis: %s", config.BACKUP_DIR)
    log.info("Unraid-Templates:   %s (%s)", config.TEMPLATES_DIR,
             "gefunden" if config.TEMPLATES_DIR.is_dir() else "NICHT gemountet")

    if docker_api.ping():
        log.info("Docker erreichbar: %s", docker_api.engine_info().get("docker_version"))
    else:
        log.warning("Docker-Socket %s nicht erreichbar - Backup/Restore deaktiviert",
                    config.DOCKER_SOCKET)

    # SMB-Ziel gleich beim Start einbinden, damit Zeitplaene es vorfinden.
    if config.get("target_type") == "smb":
        try:
            result = storage.mount_smb()
            log.info("SMB-Backup-Ziel eingebunden: %s", result["source"])
        except storage.StorageError as exc:
            log.error("SMB-Backup-Ziel konnte nicht eingebunden werden: %s", exc)
            db.add_event("storage.failed",
                         f"SMB-Ziel beim Start nicht eingebunden: {exc}", level="error")

    stats = backup.rescan()
    if stats["added"]:
        log.info("Index ergaenzt: %s Backup(s) aus dem Dateisystem uebernommen", stats["added"])

    scheduler.start()
    db.add_event("system.start", f"DockVault {__version__} gestartet")
    try:
        yield
    finally:
        log.info("DockVault faehrt herunter")
        scheduler.stop()
        runner.shutdown()
        if config.get("target_type") == "smb":
            try:
                storage.unmount()
            except storage.StorageError as exc:
                log.warning("SMB-Ziel konnte nicht ausgehaengt werden: %s", exc)


app = FastAPI(title="DockVault", version=__version__, lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.include_router(api.router)


@app.exception_handler(docker_api.DockerUnavailable)
async def docker_unavailable_handler(_request, exc: docker_api.DockerUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "version": __version__, "docker": docker_api.ping()}


if WEB_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

    # Die Oberflaeche wird mit dem Image aktualisiert - Browser muessen deshalb
    # bei jedem Aufruf revalidieren, sonst laeuft nach einem Update alte UI weiter.
    _NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(full_path: str):
        candidate = (WEB_DIR / full_path).resolve()
        if full_path and str(candidate).startswith(str(WEB_DIR.resolve())) and candidate.is_file():
            return FileResponse(candidate, headers=_NO_CACHE)
        return FileResponse(WEB_DIR / "index.html", headers=_NO_CACHE)


def main() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0",
                port=int(os.environ.get("DV_PORT", "8080")), log_level="info")


if __name__ == "__main__":
    main()
