"""Unraid-Integration: Community-App-Templates lesen, schreiben und erzeugen.

Unraid verwaltet Container ueber XML-Templates in
``/boot/config/plugins/dockerMan/templates-user/my-<Name>.xml``. Nur Container mit
Template tauchen im Docker-Tab der WebGUI auf und lassen sich dort bearbeiten.

Kernfunktion fuer den Restore: ``generate_template()`` baut aus einem gesicherten
``docker inspect`` ein vollwertiges Template - damit taucht auch ein Container, der
ohne Template angelegt wurde (z. B. per compose), nach dem Restore korrekt in der
Unraid-Oberflaeche auf.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from xml.dom import minidom

from . import config, docker_api

# Labels, die Unraid selbst setzt - nicht als Benutzer-Label ins Template schreiben
_UNRAID_LABELS = {
    "net.unraid.docker.managed",
    "net.unraid.docker.icon",
    "net.unraid.docker.webui",
}

# Env-Variablen, die praktisch immer vom Image stammen
_NOISE_ENV = {"PATH", "HOSTNAME", "HOME", "TERM", "LANG", "LC_ALL", "DEBIAN_FRONTEND"}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def templates_dir() -> Path:
    return config.TEMPLATES_DIR


def available() -> bool:
    return templates_dir().is_dir()


def template_filename(container_name: str) -> str:
    return f"my-{_SAFE_NAME.sub('-', container_name)}.xml"


# ---------------------------------------------------------------- Lesen

def _parse(path: Path) -> dict[str, Any] | None:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    if root.tag != "Container":
        return None

    def text(tag: str) -> str:
        node = root.find(tag)
        return (node.text or "").strip() if node is not None else ""

    return {
        "file": path.name,
        "path": str(path),
        "name": text("Name") or path.stem.removeprefix("my-"),
        "repository": text("Repository"),
        "network": text("Network"),
        "icon": text("Icon"),
        "webui": text("WebUI"),
        "category": text("Category"),
        "support": text("Support"),
        "overview": text("Overview")[:400],
        "mtime": path.stat().st_mtime,
        "size": path.stat().st_size,
    }


def list_templates() -> list[dict[str, Any]]:
    directory = templates_dir()
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.xml")):
        parsed = _parse(path)
        if parsed:
            out.append(parsed)
    return out


def find_template(container_name: str) -> Path | None:
    """Template zu einem Container finden - erst exakt, dann ueber <Name>."""
    directory = templates_dir()
    if not directory.is_dir():
        return None
    direct = directory / template_filename(container_name)
    if direct.exists():
        return direct
    wanted = container_name.lower()
    for path in directory.glob("*.xml"):
        parsed = _parse(path)
        if parsed and parsed["name"].lower() == wanted:
            return path
    return None


def read_template(container_name: str) -> tuple[str, str] | None:
    """(Dateiname, XML-Inhalt) oder None."""
    path = find_template(container_name)
    if not path:
        return None
    try:
        return path.name, path.read_text("utf-8", errors="replace")
    except OSError:
        return None


def template_name_map() -> dict[str, str]:
    """{kleingeschriebener Containername: Dateiname} - einmal fuer viele Abfragen."""
    return {t["name"].lower(): t["file"] for t in list_templates()}


def orphan_templates() -> list[dict[str, Any]]:
    """Templates, zu denen aktuell kein Container existiert - Restore-Kandidaten."""
    try:
        existing = {c["name"].lower() for c in docker_api.list_containers(all_containers=True)}
    except Exception:  # noqa: BLE001 - Docker evtl. nicht erreichbar
        existing = set()
    return [t for t in list_templates() if t["name"].lower() not in existing]


# ---------------------------------------------------------------- Schreiben

def write_template(container_name: str, xml_content: str,
                   keep_backup: bool = True) -> dict[str, Any]:
    directory = templates_dir()
    if not directory.is_dir():
        raise FileNotFoundError(
            f"Template-Verzeichnis {directory} nicht gefunden - ist /boot/config gemountet?")

    target = directory / template_filename(container_name)
    replaced = None
    if target.exists() and keep_backup:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = target.with_suffix(f".xml.bak-{stamp}")
        backup.write_bytes(target.read_bytes())
        replaced = backup.name

    tmp = target.with_suffix(".xml.tmp")
    tmp.write_text(xml_content, "utf-8")
    tmp.replace(target)
    return {"file": target.name, "path": str(target), "previous_saved_as": replaced}


# ---------------------------------------------------------------- Erzeugen

def _image_config(image_ref: str) -> dict[str, Any]:
    """Config des Images - alles daraus ist geerbt und gehoert nicht ins Template."""
    try:
        return (docker_api.client().images.get(image_ref).attrs.get("Config") or {})
    except Exception:  # noqa: BLE001
        return {}


def _add_config(root: ET.Element, *, name: str, target: str, default: str, mode: str,
                description: str, ctype: str, value: str, display: str = "always",
                required: str = "false", mask: str = "false") -> None:
    node = ET.SubElement(root, "Config", {
        "Name": name, "Target": target, "Default": default, "Mode": mode,
        "Description": description, "Type": ctype, "Display": display,
        "Required": required, "Mask": mask,
    })
    node.text = value


def generate_template(attrs: dict[str, Any], *, container_name: str | None = None) -> str:
    """Baut ein Unraid-Template aus einem ``docker inspect``-Datensatz."""
    cfg = attrs.get("Config") or {}
    host = attrs.get("HostConfig") or {}
    netset = attrs.get("NetworkSettings") or {}
    labels = cfg.get("Labels") or {}
    name = container_name or (attrs.get("Name") or "").lstrip("/") or "container"
    image = cfg.get("Image") or ""

    networks = list((netset.get("Networks") or {}).keys())
    network = networks[0] if networks else (host.get("NetworkMode") or "bridge")
    if network.startswith("container:"):
        network = "none"

    root = ET.Element("Container", {"version": "2"})
    ET.SubElement(root, "Name").text = name
    ET.SubElement(root, "Repository").text = image
    ET.SubElement(root, "Registry").text = _registry_url(image)
    ET.SubElement(root, "Network").text = network
    ET.SubElement(root, "MyIP").text = _static_ip(netset, network)
    ET.SubElement(root, "MyMAC")
    ET.SubElement(root, "Shell").text = "sh"
    ET.SubElement(root, "Privileged").text = "true" if host.get("Privileged") else "false"
    ET.SubElement(root, "Support")
    ET.SubElement(root, "Project")
    ET.SubElement(root, "ReadMe")
    ET.SubElement(root, "Overview").text = (
        f"Automatisch von DockVault aus der gesicherten Container-Konfiguration "
        f"erzeugt ({time.strftime('%d.%m.%Y %H:%M')}). "
        f"Image: {image}. Pruefe Ports und Pfade vor dem ersten Start."
    )
    ET.SubElement(root, "Category").text = "Other:"
    ET.SubElement(root, "WebUI").text = labels.get("net.unraid.docker.webui", "")
    ET.SubElement(root, "TemplateURL")
    ET.SubElement(root, "Icon").text = labels.get("net.unraid.docker.icon", "")
    ET.SubElement(root, "ExtraParams").text = _extra_params(host)
    ET.SubElement(root, "PostArgs").text = " ".join(cfg.get("Cmd") or []) if cfg.get("Cmd") else ""
    ET.SubElement(root, "CPUset").text = host.get("CpusetCpus") or ""
    ET.SubElement(root, "DateInstalled").text = str(int(time.time()))
    ET.SubElement(root, "DonateText")
    ET.SubElement(root, "DonateLink")
    ET.SubElement(root, "Requires")

    # --- Ports -----------------------------------------------------------
    port_bindings = host.get("PortBindings") or {}
    index = 0
    for container_port, bindings in sorted(port_bindings.items()):
        if not bindings:
            continue
        port_no, _, proto = container_port.partition("/")
        proto = proto or "tcp"
        host_port = bindings[0].get("HostPort") or port_no
        index += 1
        _add_config(root, name=f"Port {index} ({proto})", target=port_no, default=port_no,
                    mode=proto, description="", ctype="Port", value=host_port,
                    required="false")

    # --- Pfade (Bind-Mounts und benannte Volumes) ------------------------
    index = 0
    for mount in attrs.get("Mounts") or []:
        destination = mount.get("Destination") or ""
        if destination in config.get("exclude_paths", []):
            continue
        source = mount.get("Source") or mount.get("Name") or ""
        if mount.get("Type") == "volume":
            source = mount.get("Name") or source
        index += 1
        _add_config(root, name=f"Pfad {index}", target=destination, default="",
                    mode="rw" if mount.get("RW", True) else "ro", description="",
                    ctype="Path", value=source)

    # --- Environment -----------------------------------------------------
    image_cfg = _image_config(image)
    inherited_env = set(image_cfg.get("Env") or [])
    inherited_labels = image_cfg.get("Labels") or {}
    for entry in cfg.get("Env") or []:
        if entry in inherited_env:
            continue
        key, _, value = entry.partition("=")
        if key in _NOISE_ENV:
            continue
        _add_config(root, name=key, target=key, default="", mode="", description="",
                    ctype="Variable", value=value, display="always")

    # --- Devices ---------------------------------------------------------
    for device in host.get("Devices") or []:
        path_on_host = device.get("PathOnHost") or ""
        if not path_on_host:
            continue
        _add_config(root, name=f"Device {Path(path_on_host).name}", target=path_on_host,
                    default="", mode="", description="", ctype="Device",
                    value=path_on_host, display="advanced")

    # --- Benutzer-Labels -------------------------------------------------
    for key, value in labels.items():
        if key in _UNRAID_LABELS or key.startswith("com.docker.compose"):
            continue
        if inherited_labels.get(key) == value:   # kommt aus dem Image
            continue
        _add_config(root, name=key, target=key, default="", mode="", description="",
                    ctype="Label", value=value, display="advanced")

    ET.SubElement(root, "TailscaleStateDir")
    return _pretty(root)


def _registry_url(image: str) -> str:
    if not image:
        return ""
    ref = image.split(":")[0]
    if ref.startswith("ghcr.io/"):
        return f"https://{ref}"
    if "/" in ref and "." in ref.split("/")[0]:
        return f"https://{ref}"
    return f"https://hub.docker.com/r/{ref}/" if "/" in ref else f"https://hub.docker.com/_/{ref}"


def _static_ip(netset: dict[str, Any], network: str) -> str:
    entry = (netset.get("Networks") or {}).get(network) or {}
    ipam = entry.get("IPAMConfig") or {}
    return ipam.get("IPv4Address") or ""


def _extra_params(host: dict[str, Any]) -> str:
    parts: list[str] = []
    policy = (host.get("RestartPolicy") or {}).get("Name")
    if policy and policy != "no":
        parts.append(f"--restart={policy}")
    if host.get("Runtime") and host["Runtime"] != "runc":
        parts.append(f"--runtime={host['Runtime']}")
    for cap in host.get("CapAdd") or []:
        parts.append(f"--cap-add={cap}")
    for cap in host.get("CapDrop") or []:
        parts.append(f"--cap-drop={cap}")
    if host.get("Memory"):
        parts.append(f"--memory={host['Memory']}")
    for dns in host.get("Dns") or []:
        parts.append(f"--dns={dns}")
    return " ".join(parts)


def _pretty(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode")
    parsed = minidom.parseString(raw)
    pretty = parsed.toprettyxml(indent="  ")
    lines = [line for line in pretty.split("\n") if line.strip()]
    if lines and lines[0].startswith("<?xml"):
        lines[0] = '<?xml version="1.0"?>'
    return "\n".join(lines) + "\n"
