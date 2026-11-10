"""Database models.

All timestamps are stored as naive UTC datetimes (SQLite has no tz type) and
serialised with a trailing ``Z``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


# Device states (persisted). "offline" is derived from last_seen, never stored.
STATE_WAITING = "waiting"  # hold loop
STATE_DEPLOYING = "deploying"  # payload dispatched, served on next poll
STATE_BOOTING = "booting"  # payload script served, client is booting it
STATE_LOCAL = "local"  # iPXE exits so firmware boots the next device (disk)
DEVICE_STATES = {STATE_WAITING, STATE_DEPLOYING, STATE_BOOTING, STATE_LOCAL}

# ISO boot methods
BOOT_WINDOWS_SETUP = "windows_setup"
BOOT_WINPE = "winpe"
BOOT_LINUX = "linux"
BOOT_SANBOOT = "sanboot"
BOOT_MEMDISK = "memdisk"
BOOT_METHODS = {BOOT_WINDOWS_SETUP, BOOT_WINPE, BOOT_LINUX, BOOT_SANBOOT, BOOT_MEMDISK}

# ISO statuses
ISO_UPLOADING = "uploading"
ISO_EXTRACTING = "extracting"
ISO_READY = "ready"
ISO_ERROR = "error"
ISO_MISSING = "missing"

# Relay cache item statuses (reported by the relay)
CACHE_PENDING = "pending"  # assigned, not downloaded yet
CACHE_SYNCING = "syncing"  # downloading
CACHE_READY = "ready"  # complete copy of the reported etag
CACHE_ERROR = "error"  # e.g. not enough disk space
CACHE_STATUSES = {CACHE_PENDING, CACHE_SYNCING, CACHE_READY, CACHE_ERROR}


class Relay(Base):
    __tablename__ = "relays"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    key_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_registered: Mapped[bool] = mapped_column(Boolean, default=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subnet: Mapped[str | None] = mapped_column(String(128), nullable=True)
    interface: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dnsmasq_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IsoImage(Base):
    __tablename__ = "isos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default=ISO_UPLOADING)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    boot_method: Mapped[str] = mapped_column(String(32), default=BOOT_SANBOOT)
    detected_os: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # linux
    kernel_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    initrd_paths_json: Mapped[str] = mapped_column(Text, default="[]")
    cmdline: Mapped[str | None] = mapped_column(Text, nullable=True)
    # windows (wimboot)
    bcd_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    boot_sdi_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    boot_wim_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    setup_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def initrd_paths(self) -> list[str]:
        try:
            value = json.loads(self.initrd_paths_json or "[]")
        except ValueError:
            return []
        return [str(v) for v in value if v]

    @initrd_paths.setter
    def initrd_paths(self, value: list[str]) -> None:
        self.initrd_paths_json = json.dumps([v for v in value if v])


class RelayCache(Base):
    """A caching relay's last self-report (sent with its heartbeat).

    A separate table rather than columns on ``relays``: there are no
    migrations, and ``create_all()`` only adds missing tables.
    """

    __tablename__ = "relay_caches"

    relay_id: Mapped[str] = mapped_column(
        ForeignKey("relays.id", ondelete="CASCADE"), primary_key=True
    )
    # Validated http://host[:port] the relay serves its cache at; None = invalid.
    cache_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    http_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    smb_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    disk_free: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    disk_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RelayCacheItem(Base):
    """An ISO assigned to a relay's cache, plus the relay's progress on it."""

    __tablename__ = "relay_cache_items"

    relay_id: Mapped[str] = mapped_column(
        ForeignKey("relays.id", ondelete="CASCADE"), primary_key=True
    )
    iso_id: Mapped[int] = mapped_column(ForeignKey("isos.id", ondelete="CASCADE"), primary_key=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Reported by the relay: the manifest etag its copy matches (when ready).
    etag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=CACHE_PENDING)
    bytes_done: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_total: Mapped[int] = mapped_column(BigInteger, default=0)
    message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UnattendConfig(Base):
    __tablename__ = "unattend_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class UnattendGenerated(Base):
    """Generator settings for a template made with the built-in generator.

    The template's file is regenerated from these settings whenever it is saved
    in the generator; templates without a row here are edited as raw XML. A
    separate table rather than columns on ``unattend_configs``: there are no
    migrations. Rows are deleted explicitly with their template.
    """

    __tablename__ = "unattend_generated"

    unattend_id: Mapped[int] = mapped_column(
        ForeignKey("unattend_configs.id", ondelete="CASCADE"), primary_key=True
    )
    settings_json: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(Integer)
    # unattend-generator commit the file was last generated with.
    upstream_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def settings(self) -> dict:
        try:
            value = json.loads(self.settings_json or "{}")
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mac: Mapped[str] = mapped_column(String(17), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relay_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arch: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)

    state: Mapped[str] = mapped_column(String(16), default=STATE_WAITING)
    iso_id: Mapped[int | None] = mapped_column(
        ForeignKey("isos.id", ondelete="SET NULL"), nullable=True
    )
    unattend_id: Mapped[int | None] = mapped_column(
        ForeignKey("unattend_configs.id", ondelete="SET NULL"), nullable=True
    )
    after_deploy: Mapped[str] = mapped_column(String(16), default=STATE_LOCAL)
    deploy_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_event: Mapped[str | None] = mapped_column(String(255), nullable=True)

    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payload_served_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    boot_count: Mapped[int] = mapped_column(Integer, default=0)

    iso: Mapped[IsoImage | None] = relationship(lazy="joined")
    unattend: Mapped[UnattendConfig | None] = relationship(lazy="joined")
