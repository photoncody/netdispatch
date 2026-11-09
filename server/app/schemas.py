"""Request bodies and response serialisers."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .config import Settings
from .models import (
    Device,
    IsoImage,
    Relay,
    UnattendConfig,
    iso,
    utcnow,
)

RELAY_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,62}$"


# ---------------------------------------------------------------- requests
class CacheItemIn(BaseModel):
    """One image in a caching relay's report. Lenient: a bad value must not
    make the whole heartbeat fail (relay_cache.apply_report cleans it up)."""

    iso_id: int
    etag: str | None = None
    status: str = "pending"
    bytes_done: int = 0
    bytes_total: int = 0
    message: str | None = None


class CacheReportIn(BaseModel):
    url: str | None = None
    http_ok: bool = False
    smb_ok: bool = False
    disk_free: int | None = Field(default=None, ge=0)
    disk_total: int | None = Field(default=None, ge=0)
    items: list[CacheItemIn] = Field(default_factory=list, max_length=2000)


class HeartbeatIn(BaseModel):
    relay_id: str = Field(pattern=RELAY_ID_PATTERN)
    name: str | None = Field(default=None, max_length=128)
    ip_address: str | None = Field(default=None, max_length=64)
    subnet: str | None = Field(default=None, max_length=128)
    interface: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=64)
    dnsmasq_ok: bool | None = None
    # Only sent by relays running with RELAY_CACHE=true.
    cache: CacheReportIn | None = None


class CacheAssignIn(BaseModel):
    iso_ids: list[int] = Field(default_factory=list, max_length=2000)


class RelayCreate(BaseModel):
    id: str = Field(pattern=RELAY_ID_PATTERN)
    name: str = Field(min_length=1, max_length=128)


class RelayUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None


class DeviceCreate(BaseModel):
    mac: str
    label: str | None = Field(default=None, max_length=128)


class DeviceUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=4000)
    after_deploy: str | None = Field(default=None, pattern="^(local|wait)$")


class DeployIn(BaseModel):
    iso_id: int
    unattend_id: int | None = None
    label: str | None = Field(default=None, max_length=128)
    after_deploy: str = Field(default="local", pattern="^(local|wait)$")


class IsoUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    boot_method: str | None = Field(
        default=None, pattern="^(windows_setup|winpe|linux|sanboot|memdisk)$"
    )
    kernel_path: str | None = None
    initrd_paths: list[str] | None = None
    cmdline: str | None = None
    bcd_path: str | None = None
    boot_sdi_path: str | None = None
    boot_wim_path: str | None = None
    setup_path: str | None = None


class UnattendIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    content: str = Field(min_length=1, max_length=2_000_000)


# --------------------------------------------------------------- responses
def _age(dt: datetime | None) -> float | None:
    return (utcnow() - dt).total_seconds() if dt else None


def relay_out(r: Relay, settings: Settings, cache: dict | None = None) -> dict:
    age = _age(r.last_seen)
    out = {
        "id": r.id,
        "name": r.name,
        "enabled": r.enabled,
        "auto_registered": r.auto_registered,
        "has_key": bool(r.key_hash),
        "ip_address": r.ip_address,
        "subnet": r.subnet,
        "interface": r.interface,
        "version": r.version,
        "dnsmasq_ok": r.dnsmasq_ok,
        "last_seen": iso(r.last_seen),
        "online": age is not None and age <= settings.relay_offline_seconds and r.enabled,
        "created_at": iso(r.created_at),
    }
    if cache is not None:
        out["cache"] = cache
    return out


def device_status(d: Device, settings: Settings) -> str:
    age = _age(d.last_seen)
    stale = age is None or age > settings.device_offline_seconds
    # Only devices in the hold loop are expected to keep polling; deploying,
    # booting and local-boot devices are legitimately silent.
    if d.state == "waiting" and stale:
        return "offline"
    return d.state  # waiting | deploying | booting | local


def device_out(d: Device, settings: Settings, relays: dict[str, Relay]) -> dict:
    relay = relays.get(d.relay_id or "")
    return {
        "id": d.id,
        "mac": d.mac,
        "label": d.label,
        "notes": d.notes,
        "ip": d.ip,
        "relay_id": d.relay_id,
        "relay_name": relay.name if relay else None,
        "arch": d.arch,
        "platform": d.platform,
        "state": d.state,
        "status": device_status(d, settings),
        "after_deploy": d.after_deploy,
        "iso_id": d.iso_id,
        "iso_name": d.iso.name if d.iso else None,
        "unattend_id": d.unattend_id,
        "unattend_name": d.unattend.name if d.unattend else None,
        "last_event": d.last_event,
        "dispatched_at": iso(d.dispatched_at),
        "payload_served_at": iso(d.payload_served_at),
        "first_seen": iso(d.first_seen),
        "last_seen": iso(d.last_seen),
        "boot_count": d.boot_count,
    }


def iso_out(i: IsoImage) -> dict:
    return {
        "id": i.id,
        "name": i.name,
        "filename": i.filename,
        "size": i.size,
        "status": i.status,
        "status_message": i.status_message,
        "boot_method": i.boot_method,
        "detected_os": i.detected_os,
        "kernel_path": i.kernel_path,
        "initrd_paths": i.initrd_paths,
        "cmdline": i.cmdline,
        "bcd_path": i.bcd_path,
        "boot_sdi_path": i.boot_sdi_path,
        "boot_wim_path": i.boot_wim_path,
        "setup_path": i.setup_path,
        "created_at": iso(i.created_at),
        "updated_at": iso(i.updated_at),
    }


def unattend_out(u: UnattendConfig, content: str | None = None) -> dict:
    out = {
        "id": u.id,
        "name": u.name,
        "filename": u.filename,
        "description": u.description,
        "created_at": iso(u.created_at),
        "updated_at": iso(u.updated_at),
    }
    if content is not None:
        out["content"] = content
    return out
