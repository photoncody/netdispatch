"""autounattend.xml template storage and rendering.

Templates live as files in ``<data_dir>/configs`` (so they can be edited or
dropped in directly) and are indexed in the database. They are rendered with
a sandboxed Jinja2 environment; values are XML-escaped.

Line endings are kept: a template with CRLF line endings (such as one from the
generator, whose embedded .cmd scripts need them) renders with CRLF.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy.orm import Session

from .config import Settings
from .models import Device, UnattendConfig

_env = SandboxedEnvironment(autoescape=True, keep_trailing_newline=True)
_env_crlf = SandboxedEnvironment(autoescape=True, keep_trailing_newline=True, newline_sequence="\r\n")
_SLUG = re.compile(r"[^a-z0-9]+")
_HOSTNAME_INVALID = re.compile(r"[^A-Za-z0-9-]+")

TEMPLATE_VARIABLES = {
    "mac": "aa:bb:cc:dd:ee:ff",
    "mac_dash": "AA-BB-CC-DD-EE-FF",
    "mac_plain": "AABBCCDDEEFF",
    "hostname": "device label as a valid computer name (letters, digits, hyphens; max 15 chars), "
    "or ND-<last 6 MAC hex digits>",
    "ip": "client IP reported at boot",
    "relay": "relay ID the client booted through",
    "image": "name of the dispatched image",
}


def slug_filename(name: str) -> str:
    slug = _SLUG.sub("-", name.lower()).strip("-")[:80] or "unattend"
    return f"{slug}.xml"


def template_path(settings: Settings, cfg: UnattendConfig) -> Path:
    return settings.configs_dir / Path(cfg.filename).name


def read_template(settings: Settings, cfg: UnattendConfig) -> str | None:
    try:
        # newline="": keep CRLF as written.
        with template_path(settings, cfg).open(encoding="utf-8", newline="") as f:
            return f.read()
    except FileNotFoundError:
        return None


def write_template(settings: Settings, cfg: UnattendConfig, content: str) -> None:
    with template_path(settings, cfg).open("w", encoding="utf-8", newline="") as f:
        f.write(content)


def computer_name(label: str | None, mac: str) -> str:
    """A valid Windows computer name for a device.

    The label with every run of other characters turned into a hyphen, cut to 15
    characters. Falls back to ND-<last 6 MAC hex digits> when nothing usable is
    left or the result is all digits (Windows rejects both).
    """
    name = _HOSTNAME_INVALID.sub("-", label or "").strip("-")[:15].rstrip("-")
    if not name or name.isdigit():
        name = f"ND-{mac.replace(':', '').upper()[-6:]}"
    return name


def device_context(device: Device) -> dict[str, str]:
    hexmac = device.mac.replace(":", "").upper()
    hostname = computer_name(device.label, device.mac)
    return {
        "mac": device.mac,
        "mac_dash": "-".join(hexmac[i : i + 2] for i in range(0, 12, 2)),
        "mac_plain": hexmac,
        "hostname": hostname,
        "ip": device.ip or "",
        "relay": device.relay_id or "",
        "image": device.iso.name if device.iso else "",
    }


def sample_context() -> dict[str, str]:
    return {
        "mac": "52:54:00:12:34:56",
        "mac_dash": "52-54-00-12-34-56",
        "mac_plain": "525400123456",
        "hostname": "ND-123456",
        "ip": "192.0.2.10",
        "relay": "sample-relay",
        "image": "Sample image",
    }


def render(content: str, context: dict[str, str]) -> str:
    env = _env_crlf if "\r\n" in content else _env
    return env.from_string(content).render(**context)


def validate(content: str) -> str | None:
    """Return an error message, or None if the template renders to valid XML."""
    try:
        rendered = render(content, sample_context())
    except TemplateError as exc:
        return f"template error: {exc}"
    try:
        ET.fromstring(rendered.encode("utf-8"))
    except ET.ParseError as exc:
        return f"rendered output is not well-formed XML: {exc}"
    return None


def sync_from_disk(db: Session, settings: Settings) -> None:
    """Index *.xml files that were placed in the configs dir by hand."""
    if not settings.configs_dir.is_dir():
        return
    known = {c.filename for c in db.query(UnattendConfig).all()}
    names = {c.name for c in db.query(UnattendConfig).all()}
    changed = False
    for path in sorted(settings.configs_dir.glob("*.xml")):
        if path.name in known:
            continue
        name = path.stem
        while name in names:
            name += "-1"
        db.add(UnattendConfig(name=name, filename=path.name, description="imported from configs directory"))
        names.add(name)
        changed = True
    if changed:
        db.commit()
