"""Duenner Wrapper um die Docker-Engine-API."""
from __future__ import annotations

import threading
from typing import Any

import docker
from docker.errors import APIError, NotFound

from . import config

_client: docker.DockerClient | None = None
_lock = threading.Lock()

UNRAID_MANAGED_LABEL = "net.unraid.docker.managed"
UNRAID_ICON_LABEL = "net.unraid.docker.icon"
UNRAID_WEBUI_LABEL = "net.unraid.docker.webui"


class DockerUnavailable(RuntimeError):
    pass


def client() -> docker.DockerClient:
    global _client
    with _lock:
        if _client is None:
            try:
                _client = docker.DockerClient(
                    base_url=f"unix://{config.DOCKER_SOCKET}", version="auto", timeout=120)
            except Exception as exc:  # noqa: BLE001 - jede Verbindungsart kann scheitern
                raise DockerUnavailable(
                    f"Docker-Socket {config.DOCKER_SOCKET} nicht erreichbar: {exc}") from exc
        return _client


def ping() -> bool:
    try:
        return bool(client().ping())
    except Exception:  # noqa: BLE001
        return False


def engine_info() -> dict[str, Any]:
    info = client().info()
    version = client().version()
    return {
        "docker_version": version.get("Version"),
        "api_version": version.get("ApiVersion"),
        "os": info.get("OperatingSystem"),
        "kernel": info.get("KernelVersion"),
        "containers": info.get("Containers", 0),
        "running": info.get("ContainersRunning", 0),
        "images": info.get("Images", 0),
        "storage_driver": info.get("Driver"),
    }


# ---------------------------------------------------------------- Container

def _mounts_of(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    mounts = []
    for mount in attrs.get("Mounts") or []:
        mounts.append({
            "type": mount.get("Type"),
            "name": mount.get("Name"),
            "source": mount.get("Source"),
            "destination": mount.get("Destination"),
            "mode": mount.get("Mode"),
            "rw": mount.get("RW", True),
        })
    return mounts


def summarise(container: Any) -> dict[str, Any]:
    attrs = container.attrs
    cfg = attrs.get("Config", {}) or {}
    labels = cfg.get("Labels") or {}
    state = attrs.get("State", {}) or {}
    networks = list(((attrs.get("NetworkSettings") or {}).get("Networks") or {}).keys())
    ports = []
    for port, bindings in ((attrs.get("NetworkSettings") or {}).get("Ports") or {}).items():
        for binding in bindings or []:
            ports.append({"container": port, "host": binding.get("HostPort")})
    return {
        "id": container.id[:12],
        "full_id": container.id,
        "name": container.name,
        "image": cfg.get("Image") or (container.image.tags[0] if container.image.tags else ""),
        "image_id": attrs.get("Image", "")[:19],
        "state": state.get("Status", "unknown"),
        "running": bool(state.get("Running")),
        "health": ((state.get("Health") or {}).get("Status")),
        "started_at": state.get("StartedAt"),
        "created": attrs.get("Created"),
        "restart_policy": ((attrs.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name"),
        "autostart": ((attrs.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name")
        in ("always", "unless-stopped"),
        "unraid_managed": labels.get(UNRAID_MANAGED_LABEL) == "dockerman",
        "icon": labels.get(UNRAID_ICON_LABEL),
        "webui": labels.get(UNRAID_WEBUI_LABEL),
        "networks": networks,
        "ports": ports,
        "mounts": _mounts_of(attrs),
        "labels": labels,
    }


def _summarise_brief(raw: dict[str, Any]) -> dict[str, Any]:
    """Aus der Listen-API von Docker - ohne teures inspect pro Container."""
    labels = raw.get("Labels") or {}
    status = raw.get("Status") or ""
    health = None
    if "(healthy)" in status:
        health = "healthy"
    elif "(unhealthy)" in status:
        health = "unhealthy"
    elif "(health: starting)" in status:
        health = "starting"

    ports = []
    for port in raw.get("Ports") or []:
        if port.get("PublicPort"):
            ports.append({"container": f"{port.get('PrivatePort')}/{port.get('Type', 'tcp')}",
                          "host": str(port["PublicPort"])})

    mounts = []
    for mount in raw.get("Mounts") or []:
        mounts.append({
            "type": mount.get("Type"), "name": mount.get("Name"),
            "source": mount.get("Source"), "destination": mount.get("Destination"),
            "mode": mount.get("Mode"), "rw": mount.get("RW", True),
        })

    return {
        "id": raw.get("Id", "")[:12],
        "full_id": raw.get("Id", ""),
        "name": (raw.get("Names") or ["/?"])[0].lstrip("/"),
        "image": raw.get("Image", ""),
        "image_id": raw.get("ImageID", "")[:19],
        "state": raw.get("State", "unknown"),
        "running": raw.get("State") == "running",
        "health": health,
        "status_text": status,
        "created": raw.get("Created"),
        "unraid_managed": labels.get(UNRAID_MANAGED_LABEL) == "dockerman",
        "icon": labels.get(UNRAID_ICON_LABEL),
        "webui": labels.get(UNRAID_WEBUI_LABEL),
        "networks": list(((raw.get("NetworkSettings") or {}).get("Networks") or {}).keys()),
        "ports": ports,
        "mounts": mounts,
        "labels": labels,
    }


def list_containers(all_containers: bool = True) -> list[dict[str, Any]]:
    """Schnelle Uebersicht: ein API-Aufruf statt eines inspect je Container."""
    raw = client().api.containers(all=all_containers)
    return [_summarise_brief(entry) for entry in raw]


def get_container(name: str) -> Any:
    try:
        return client().containers.get(name)
    except NotFound as exc:
        raise KeyError(f"Container '{name}' existiert nicht") from exc


def inspect(name: str) -> dict[str, Any]:
    return get_container(name).attrs


def exists(name: str) -> bool:
    try:
        get_container(name)
        return True
    except KeyError:
        return False


def logs(name: str, tail: int = 200) -> str:
    container = get_container(name)
    return container.logs(tail=tail, timestamps=True).decode("utf-8", "replace")


def stats(name: str) -> dict[str, Any]:
    container = get_container(name)
    raw = container.stats(stream=False)
    cpu_pct = 0.0
    try:
        cpu = raw["cpu_stats"]
        pre = raw["precpu_stats"]
        delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sys_delta = cpu["system_cpu_usage"] - pre["system_cpu_usage"]
        if sys_delta > 0:
            cpu_pct = (delta / sys_delta) * cpu.get("online_cpus", 1) * 100
    except (KeyError, TypeError):
        pass
    mem = raw.get("memory_stats", {})
    return {
        "cpu_percent": round(cpu_pct, 2),
        "memory_used": mem.get("usage", 0),
        "memory_limit": mem.get("limit", 0),
    }


def control(name: str, action: str, timeout: int = 30) -> str:
    container = get_container(name)
    if action == "start":
        container.start()
    elif action == "stop":
        container.stop(timeout=timeout)
    elif action == "restart":
        container.restart(timeout=timeout)
    elif action == "pause":
        container.pause()
    elif action == "unpause":
        container.unpause()
    elif action == "remove":
        container.remove(force=True)
    else:
        raise ValueError(f"Unbekannte Aktion: {action}")
    return action


# ---------------------------------------------------------------- Volumes / Netze

def list_volumes() -> list[dict[str, Any]]:
    out = []
    for volume in client().volumes.list():
        out.append({
            "name": volume.name,
            "driver": volume.attrs.get("Driver"),
            "mountpoint": volume.attrs.get("Mountpoint"),
            "labels": volume.attrs.get("Labels") or {},
        })
    return out


def volume_mountpoint(name: str) -> str | None:
    try:
        return client().volumes.get(name).attrs.get("Mountpoint")
    except NotFound:
        return None


def ensure_volume(name: str, driver: str = "local",
                  labels: dict[str, str] | None = None) -> None:
    try:
        client().volumes.get(name)
    except NotFound:
        client().volumes.create(name=name, driver=driver, labels=labels or {})


def list_networks() -> list[dict[str, Any]]:
    out = []
    for net in client().networks.list():
        out.append({
            "name": net.name,
            "id": net.id[:12],
            "driver": net.attrs.get("Driver"),
            "scope": net.attrs.get("Scope"),
            "ipam": net.attrs.get("IPAM"),
        })
    return out


def ensure_network(name: str, driver: str = "bridge",
                   ipam: dict[str, Any] | None = None) -> bool:
    """Legt ein fehlendes Netzwerk an. True, wenn es neu erstellt wurde."""
    if name in ("bridge", "host", "none"):
        return False
    try:
        client().networks.get(name)
        return False
    except NotFound:
        kwargs: dict[str, Any] = {"name": name, "driver": driver or "bridge"}
        if ipam and ipam.get("Config"):
            pool = []
            for entry in ipam["Config"]:
                pool.append(docker.types.IPAMPool(
                    subnet=entry.get("Subnet"), gateway=entry.get("Gateway"),
                    iprange=entry.get("IPRange")))
            kwargs["ipam"] = docker.types.IPAMConfig(
                driver=(ipam.get("Driver") or "default"), pool_configs=pool)
        client().networks.create(**kwargs)
        return True


def pull_image(reference: str) -> str:
    image = client().images.pull(reference)
    return image.id


def image_exists(reference: str) -> bool:
    try:
        client().images.get(reference)
        return True
    except (NotFound, APIError):
        return False
