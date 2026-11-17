"""USB sticks: profiles, pairing USB Creator, and what a stick's writer copies.

A stick has two partitions (made by USB Creator, or ``tests/e2e/make-stick.py``):

* ``NDDATA`` (exFAT, first, fills the stick): full extracted trees of its
  Windows images (``netdispatch/img/<iso>/``), the offline unattend parts
  (``netdispatch/unattend/<entry>/``) and ``netdispatch/stick.json``/``stick.key``.
  Firmware can't read exFAT; WinPE can.
* ``NDBOOT`` (FAT32, active): the boot template (shim, iPXE builds, wimboot,
  SYSLINUX), the hub-rendered scripts, and per image only what iPXE loads
  (``netdispatch/boot/<iso>/BCD|boot.sdi|boot.wim``).

The writers are copy engines. The hub decides everything:

* ``stick_manifest()``: per image, every file with its partition, path on the
  stick, size, mtime and where to download it (the hub's unauthenticated
  ``/files/iso/*``, or the caller's relay cache when it holds a current copy).
* ``render_layout()``: the small files (scripts, offline ``netdispatch.cmd``,
  unattend parts), rendered for the images the stick holds.
* ``BootTemplate``: the boot partition's head, formatted and populated with
  mkfs.fat + mtools + SYSLINUX, so a writer only does a raw write.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import secrets
import shutil
import struct
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from sqlalchemy.orm import Session

from . import __version__
from .auth import hash_key
from .config import Settings
from .iso_tools import resolve_ci
from .models import (
    BOOT_WINDOWS_SETUP,
    BOOT_WINPE,
    ISO_READY,
    STICK_ITEM_ERROR,
    IsoImage,
    UnattendConfig,
    UsbCreator,
    UsbPairing,
    UsbProfile,
    UsbProfileEntry,
    UsbStick,
    UsbStickItem,
    utcnow,
)
from .models import iso as iso_time
from .relay_cache import _walk_tree, ascii_lower
from .unattend import read_template, render
from .usb_media import MediaError, _run
from .usb_scripts import (
    UPDATER_INITRD,
    UPDATER_KERNEL,
    parse_have_ref,
    EFI_DIR,
    EFI_NATIVE_DIR,
    STICK_DIR,
    STICK_NET_EFI,
    StickEntry,
    StickRef,
    check_hub_url,
    render_media_script,
    render_stick_parent,
    render_stick_syslinux_cfg,
    stick_boot_path,
    stick_entry_path,
)
from .winpe import WINPESHL_INI, render_stick_cmd

log = logging.getLogger("netdispatch.usb")

# Images a stick can boot on its own: wimboot payloads.
LOCAL_METHODS = {BOOT_WINDOWS_SETUP, BOOT_WINPE}
PART_BOOT = "boot"
PART_DATA = "data"

PAIRING_TTL = timedelta(minutes=10)
PAIRING_MAX_PENDING = 20
PAIRING_MAX_PER_IP = 5
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I

# Replaces {{ hostname }} when the offline unattend parts are rendered; the
# parts are joined around the name typed in WinPE.
HOSTNAME_MARKER = "NDOFFLINEHOSTNAME7Q4Z"


# ---------------------------------------------------------------- keys and pairing


def new_creator_key() -> str:
    return "ndc_" + secrets.token_urlsafe(32)


def new_stick_key() -> str:
    return "nds_" + secrets.token_urlsafe(32)


def new_stick_id() -> str:
    return secrets.token_hex(6)


def normalize_code(code: str) -> str:
    return "".join(c for c in (code or "").upper() if c.isalnum())


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def start_pairing(db: Session, client_name: str | None, client_ip: str | None) -> tuple[UsbPairing, str]:
    """A new pairing and its poll token (only its hash is stored)."""
    now = utcnow()
    db.query(UsbPairing).filter(UsbPairing.expires_at < now).delete(synchronize_session=False)
    pending = db.query(UsbPairing).all()
    if len(pending) >= PAIRING_MAX_PENDING:
        raise PermissionError("too many pending pairings - approve or reject them first")
    if client_ip and sum(1 for p in pending if p.client_ip == client_ip) >= PAIRING_MAX_PER_IP:
        raise PermissionError("too many pending pairings from this address")
    while True:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        if db.get(UsbPairing, code) is None:
            break
    token = secrets.token_urlsafe(32)
    pairing = UsbPairing(
        code=code,
        poll_hash=hash_key(token),
        client_name=(client_name or "").strip()[:128] or None,
        client_ip=client_ip,
        created_at=now,
        expires_at=now + PAIRING_TTL,
    )
    db.add(pairing)
    return pairing, token


def creator_for_key(db: Session, key: str) -> UsbCreator | None:
    if not key or not key.startswith("ndc_"):
        return None
    creator = db.query(UsbCreator).filter(UsbCreator.key_hash == hash_key(key)).one_or_none()
    return None if creator is None or creator.revoked else creator


def stick_for_key(db: Session, stick_id: str, key: str) -> UsbStick | None:
    if not key or not key.startswith("nds_"):
        return None
    stick = db.get(UsbStick, stick_id)
    if stick is None or stick.revoked or stick.key_hash != hash_key(key):
        return None
    return stick


# ---------------------------------------------------------------- manifests


@dataclass
class StickImage:
    iso_id: int
    etag: str = ""
    files: list[dict] = field(default_factory=list)
    boot_bytes: int = 0
    data_bytes: int = 0
    # Why the image can't go on a stick; files is then empty.
    error: str | None = None


def _stick_file(part: str, dest: str, rel: str, path: Path, iso_id: int) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {
        "part": part,
        "dest": dest,
        "src": f"/files/iso/{iso_id}/tree/{quote(rel, safe='/')}",
        # URL path below a caching relay's URL (relay_cache.build_manifest's
        # dest), encoded like src.
        "cache": quote(f"tree/{iso_id}/{ascii_lower(rel)}", safe="/"),
        "size": st.st_size,
        "mtime": int(st.st_mtime),
    }


def build_stick_image(settings: Settings, iso: IsoImage) -> StickImage:
    if iso.boot_method not in LOCAL_METHODS:
        return StickImage(iso.id, error="only Windows Setup and WinPE images can be copied to a stick")
    root = settings.extracted_dir / str(iso.id)
    if not root.is_dir():
        return StickImage(iso.id, error="the image has no extracted files")
    files: list[dict] = []
    wanted = [("BCD", iso.bcd_path), ("boot.sdi", iso.boot_sdi_path), ("boot.wim", iso.boot_wim_path or "sources/boot.wim")]
    for name, rel in wanted:
        if not rel:
            continue
        path = resolve_ci(root, rel)
        if path is None:
            if name == "boot.wim":
                return StickImage(iso.id, error=f"{rel} not found in the image")
            continue
        entry = _stick_file(PART_BOOT, stick_boot_path(iso.id, name), path.relative_to(root).as_posix(), path, iso.id)
        if entry:
            files.append(entry)
    if iso.boot_method == BOOT_WINDOWS_SETUP:
        tree, problem = _walk_tree(root)
        if problem:
            return StickImage(iso.id, error=problem)
        for rel, path in tree:
            entry = _stick_file(PART_DATA, f"{STICK_DIR}/img/{iso.id}/{rel}", rel, path, iso.id)
            if entry:
                files.append(entry)
    files.sort(key=lambda f: (f["part"], f["dest"]))
    digest = hashlib.sha256(
        json.dumps([[f["part"], f["dest"], f["size"], f["mtime"]] for f in files], separators=(",", ":")).encode()
    ).hexdigest()
    return StickImage(
        iso.id,
        digest,
        files,
        sum(f["size"] for f in files if f["part"] == PART_BOOT),
        sum(f["size"] for f in files if f["part"] == PART_DATA),
    )


class StickManifests:
    """Memoises ``build_stick_image`` like ``relay_cache.ManifestCache``."""

    def __init__(self, ttl: float = 300.0):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._data: dict[int, tuple[tuple, float, StickImage]] = {}

    def get(self, settings: Settings, iso: IsoImage) -> StickImage | None:
        if iso.status != ISO_READY:
            return None
        key = (iso.updated_at, iso.status, iso.boot_method, iso.bcd_path, iso.boot_sdi_path, iso.boot_wim_path)
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(iso.id)
            if hit and hit[0] == key and now - hit[1] < self.ttl:
                return hit[2]
        image = build_stick_image(settings, iso)
        with self._lock:
            self._data[iso.id] = (key, now, image)
        return image

    def forget(self, iso_id: int) -> None:
        with self._lock:
            self._data.pop(iso_id, None)


def profile_entries(db: Session, profile_id: int | None) -> list[tuple[UsbProfileEntry, IsoImage]]:
    if profile_id is None:
        return []
    return (
        db.query(UsbProfileEntry, IsoImage)
        .join(IsoImage, IsoImage.id == UsbProfileEntry.iso_id)
        .filter(UsbProfileEntry.profile_id == profile_id)
        .order_by(UsbProfileEntry.sort, UsbProfileEntry.id)
        .all()
    )


def stick_manifest(
    db: Session, settings: Settings, stick: UsbStick, images: StickManifests, cache_url_for
) -> dict:
    """What the stick should hold. ``cache_url_for(iso)`` returns the caller's
    relay cache URL when it holds a current copy of the image, else None."""
    out: list[dict] = []
    seen: set[int] = set()
    for entry, iso in profile_entries(db, stick.profile_id):
        if iso.id in seen:
            continue
        seen.add(iso.id)
        image = images.get(settings, iso)
        if image is None:
            # Being (re-)extracted: keep what the stick has, nothing to copy now.
            out.append({"iso_id": iso.id, "name": iso.name, "keep": True})
            continue
        if image.error:
            out.append({"iso_id": iso.id, "name": iso.name, "error": image.error})
            continue
        out.append(
            {
                "iso_id": iso.id,
                "name": iso.name,
                "boot_method": iso.boot_method,
                "etag": image.etag,
                "boot_bytes": image.boot_bytes,
                "data_bytes": image.data_bytes,
                "cache_url": cache_url_for(iso),
                "files": image.files,
            }
        )
    return {
        "stick": stick_out(db, stick),
        "hub_url": settings.boot_url,
        "boot_files": boot_files_version(settings),
        "images": out,
    }


# ---------------------------------------------------------------- layout


def offline_unattend_parts(content: str, image_name: str) -> list[str]:
    """The answer file for an offline install, split where the computer name
    goes. WinPE joins the parts around the name typed at the console.

    Only ``{{ hostname }}`` is known offline; the MAC, IP and relay variables
    render empty.
    """
    context = {
        "mac": "",
        "mac_dash": "",
        "mac_plain": "",
        "hostname": HOSTNAME_MARKER,
        "ip": "",
        "relay": "",
        "image": image_name,
    }
    parts = render(content, context).split(HOSTNAME_MARKER)
    try:
        ET.fromstring("ND-SAMPLE".join(parts).encode("utf-8"))
    except ET.ParseError as e:
        raise ValueError(f"the answer file is not well-formed XML offline: {e}") from e
    return parts


def parse_have(have: dict) -> dict[int, str]:
    out: dict[int, str] = {}
    for k, v in (have or {}).items():
        try:
            iso_id = int(k)
        except (TypeError, ValueError):
            continue
        if isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v):
            out[iso_id] = v
    return out


def stick_updates(
    db: Session, settings: Settings, images: StickManifests, stick_id: str, have: str, wants_images: bool = False
) -> list[str]:
    """What a booting stick is behind on, for ``/usb/boot``'s update prompt:
    images whose copy (``have``, as the stick's network script sends it)
    differs from the hub's, and profile images the stick doesn't hold yet.

    Nothing for an online-only stick (``wants_images`` false and no images:
    the updater only adds images where a writer chose to), a revoked or
    unknown stick, or images the hub is still preparing. A stick meant to
    carry images that holds none - an update was interrupted - is offered
    them all. An image the stick last reported as failed (too big, a broken
    file) isn't offered again on every boot."""
    held = parse_have_ref(have)
    if not held and not wants_images:
        return []
    stick = db.get(UsbStick, stick_id)
    if stick is None or stick.revoked:
        return []
    failed = {
        i.iso_id
        for i in db.query(UsbStickItem).filter(UsbStickItem.stick_id == stick.id, UsbStickItem.status == STICK_ITEM_ERROR)
    }
    out: list[str] = []
    seen: set[int] = set()
    for _entry, iso in profile_entries(db, stick.profile_id):
        if iso.id in seen:
            continue
        seen.add(iso.id)
        image = images.get(settings, iso)
        if image is None or image.error:
            continue
        if iso.id in held:
            if held[iso.id] != image.etag[:8]:
                out.append(f"{iso.name} (newer on the hub)")
        elif iso.id not in failed:
            out.append(f"{iso.name} (new)")
    return out


def updater_missing(settings: Settings) -> bool:
    return not all((settings.usb_assets_dir / n).is_file() for n in (UPDATER_KERNEL, UPDATER_INITRD))


def render_layout(
    db: Session, settings: Settings, stick: UsbStick, have: dict[int, str], *, images: bool = False
) -> dict:
    """The small files a writer puts on the stick, for the images it holds
    (``have``: iso id -> etag of the copy). Entries whose image it doesn't
    hold are left out of the menu."""
    hub = check_hub_url(settings.boot_url)
    entries: list[StickEntry] = []
    files: list[dict] = []
    problems: list[str] = []

    def add(part: str, path: str, content: str) -> None:
        files.append({"part": part, "path": path, "content": content})

    for entry, iso in profile_entries(db, stick.profile_id):
        if iso.id not in have or iso.boot_method not in LOCAL_METHODS:
            continue
        windows_setup = iso.boot_method == BOOT_WINDOWS_SETUP
        if windows_setup:
            parts: list[str] = []
            if entry.unattend_id is not None:
                cfg = db.get(UnattendConfig, entry.unattend_id)
                content = read_template(settings, cfg) if cfg else None
                if content is None:
                    problems.append(f"{entry.label}: its answer file is missing")
                    continue
                try:
                    parts = offline_unattend_parts(content, iso.name)
                except Exception as e:  # template errors, bad XML
                    problems.append(f"{entry.label}: {e}")
                    continue
            for n, text in enumerate(parts):
                add(PART_DATA, f"{STICK_DIR}/unattend/{entry.id}/part{n}.xml", text)
            add(PART_BOOT, stick_entry_path(entry.id, "netdispatch.cmd"), render_stick_cmd(iso, entry.id, len(parts)))
        entries.append(
            StickEntry(
                id=entry.id,
                label=entry.label,
                iso_id=iso.id,
                windows_setup=windows_setup,
                bcd=bool(iso.bcd_path),
                boot_sdi=bool(iso.boot_sdi_path),
            )
        )

    ref = StickRef(stick.id, ",".join(f"{i}:{e[:8]}" for i, e in sorted(have.items())), images=images or bool(have))
    name = stick.name
    add(PART_BOOT, f"{EFI_DIR}/autoexec.ipxe", render_stick_parent(stick.id, name, entries))
    add(PART_BOOT, f"{STICK_DIR}/net.ipxe", render_media_script(hub, efi=True, media="usb", stick=ref))
    add(PART_BOOT, f"{STICK_DIR}/native.ipxe", render_media_script(hub, efi=True, native=True, media="usb", stick=ref))
    add(PART_BOOT, f"{STICK_DIR}/bios.ipxe", render_media_script(hub, efi=False, media="usb", stick=ref))
    add(PART_BOOT, f"{STICK_DIR}/winpeshl.ini", WINPESHL_INI)
    add(
        PART_DATA,
        f"{STICK_DIR}/stick.json",
        json.dumps({"id": stick.id, "name": stick.name, "hub_url": hub, "profile_id": stick.profile_id}, indent=2)
        + "\n",
    )
    return {
        "files": files,
        # Directories the writer empties before writing: they only hold files
        # rendered here, and entries can disappear.
        "replace_dirs": [
            {"part": PART_BOOT, "path": f"{STICK_DIR}/entry"},
            {"part": PART_DATA, "path": f"{STICK_DIR}/unattend"},
        ],
        "problems": problems,
    }


# ---------------------------------------------------------------- views


def stick_out(db: Session, stick: UsbStick) -> dict:
    profile = db.get(UsbProfile, stick.profile_id) if stick.profile_id else None
    creator = db.get(UsbCreator, stick.creator_id) if stick.creator_id else None
    return {
        "id": stick.id,
        "name": stick.name,
        "profile_id": stick.profile_id,
        "profile_name": profile.name if profile else None,
        "creator_name": creator.name if creator else None,
        "created_at": iso_time(stick.created_at),
        "last_seen": iso_time(stick.last_seen),
        "last_report_at": iso_time(stick.last_report_at),
    }


def delete_for_iso(db: Session, iso_id: int) -> None:
    db.query(UsbProfileEntry).filter(UsbProfileEntry.iso_id == iso_id).delete(synchronize_session=False)
    db.query(UsbStickItem).filter(UsbStickItem.iso_id == iso_id).delete(synchronize_session=False)


def delete_for_unattend(db: Session, unattend_id: int) -> None:
    db.query(UsbProfileEntry).filter(UsbProfileEntry.unattend_id == unattend_id).update(
        {UsbProfileEntry.unattend_id: None}, synchronize_session=False
    )


def delete_stick(db: Session, stick: UsbStick) -> None:
    db.query(UsbStickItem).filter(UsbStickItem.stick_id == stick.id).delete(synchronize_session=False)
    db.delete(stick)


def delete_profile(db: Session, profile: UsbProfile) -> None:
    db.query(UsbProfileEntry).filter(UsbProfileEntry.profile_id == profile.id).delete(synchronize_session=False)
    db.query(UsbStick).filter(UsbStick.profile_id == profile.id).update(
        {UsbStick.profile_id: None}, synchronize_session=False
    )
    db.delete(profile)


# ---------------------------------------------------------------- boot template

# Static files of every stick's boot partition: Settings.usb_assets_dir names,
# except wimboot (Settings.wimboot_path).
TEMPLATE_FILES = {
    f"{EFI_DIR}/BOOTX64.EFI": "shimx64.efi",  # Microsoft-signed shim
    f"{EFI_DIR}/ipxe.efi": "snponly.efi",  # iPXE-signed, no drivers of its own: the parent
    STICK_NET_EFI: "ipxe.efi",  # iPXE-signed, full: the network child
    f"{EFI_NATIVE_DIR}/ipxe.efi": "ipxe-native.efi",  # unsigned, all native NIC drivers
    "syslinux/ipxe.lkrn": "ipxe.lkrn",  # legacy BIOS
}
MBR_ASSET = "mbr.bin"
TEMPLATE_TOOLS = ("mkfs.fat", "mmd", "mcopy")
# SYSLINUX's installer makes sticks boot on legacy BIOS. Debian only builds it
# for x86, so a hub on arm64 makes UEFI-only sticks.
BIOS_TOOL = "syslinux"
TEMPLATE_MIN_MB = 64
TEMPLATE_MAX_MB = 64 * 1024


def template_missing(settings: Settings) -> list[str]:
    missing = [t for t in TEMPLATE_TOOLS if shutil.which(t) is None]
    for name in [*TEMPLATE_FILES.values(), MBR_ASSET]:
        if not (settings.usb_assets_dir / name).is_file():
            missing.append(str(settings.usb_assets_dir / name))
    if not settings.wimboot_path.is_file():
        missing.append(str(settings.wimboot_path))
    return missing


def template_bios(settings: Settings) -> bool:
    """Whether this hub's stick boot partitions boot on legacy BIOS too."""
    return shutil.which(BIOS_TOOL) is not None


def _asset_stamp(path: Path) -> list[int]:
    st = path.stat()
    return [st.st_size, int(st.st_mtime)]


def boot_files_version(settings: Settings) -> str:
    """Changes whenever a stick's boot template would: writers compare it with
    the version they wrote and rewrite the boot files when it differs."""
    try:
        stamps = {n: _asset_stamp(settings.usb_assets_dir / n) for n in sorted(set(TEMPLATE_FILES.values()))}
        stamps["wimboot"] = _asset_stamp(settings.wimboot_path)
    except OSError:
        return ""
    blob = json.dumps(
        {"files": stamps, "cfg": render_stick_syslinux_cfg(), "bios": template_bios(settings), "v": __version__},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def fat_used_bytes(image: Path) -> int:
    """Bytes from the start of a FAT32 filesystem up to the end of its last
    allocated cluster. Everything after it is free space that a writer may
    leave as it finds it: a FAT driver never reads a free cluster."""
    with open(image, "rb") as f:
        bs = f.read(512)
        bps, spc, reserved, nfats = struct.unpack_from("<HBHB", bs, 11)
        fat_sectors = struct.unpack_from("<I", bs, 36)[0]
        if bps not in (512, 1024, 2048, 4096) or not spc or not fat_sectors:
            raise MediaError("not a FAT32 filesystem")
        f.seek(reserved * bps)
        fat = f.read(fat_sectors * bps)
    entries = struct.unpack_from(f"<{len(fat) // 4}I", fat)
    last = 2
    for i in range(len(entries) - 1, 1, -1):
        if entries[i] & 0x0FFFFFFF:
            last = i
            break
    data_start = (reserved + nfats * fat_sectors) * bps
    return data_start + (last - 1) * spc * bps


class BootTemplate:
    """Builds and caches stick boot partition heads, per size and offset."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def get(self, settings: Settings, size_mb: int, offset: int) -> tuple[Path, dict]:
        """(gzipped head, metadata). ``offset``: the partition's first sector,
        written as the FAT's hidden sectors for SYSLINUX."""
        missing = template_missing(settings)
        if missing:
            raise MediaError("missing: " + ", ".join(missing))
        if not TEMPLATE_MIN_MB <= size_mb <= TEMPLATE_MAX_MB:
            raise ValueError(f"size_mb must be {TEMPLATE_MIN_MB}-{TEMPLATE_MAX_MB}")
        if not 0 <= offset < 2**32:
            raise ValueError("bad offset")
        version = boot_files_version(settings)
        cache = settings.usb_cache_dir
        out = cache / f"boot-{version}-{size_mb}-{offset}.img.gz"
        meta_path = out.with_suffix(".json")
        if out.is_file() and meta_path.is_file():
            return out, json.loads(meta_path.read_text())
        with self._lock:
            if out.is_file() and meta_path.is_file():
                return out, json.loads(meta_path.read_text())
            cache.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=cache, prefix=".build-") as tmp:
                img = Path(tmp) / "boot.img"
                head = self._build(settings, img, size_mb, offset)
                part = Path(tmp) / "boot.img.gz"
                with open(img, "rb") as src, gzip.open(part, "wb", compresslevel=6) as dst:
                    remaining = head
                    while remaining:
                        chunk = src.read(min(remaining, 1 << 20))
                        if not chunk:
                            break
                        dst.write(chunk)
                        remaining -= len(chunk)
                meta = {
                    "version": version,
                    "size_mb": size_mb,
                    "offset": offset,
                    "head_bytes": head,
                    "bios": template_bios(settings),
                }
                os.replace(part, out)
                meta_path.write_text(json.dumps(meta))
            # Keep the current version only (any size): sticks of every size share it.
            for old in cache.glob("boot-*.img.gz"):
                if not old.name.startswith(f"boot-{version}-"):
                    old.unlink(missing_ok=True)
                    old.with_suffix(".json").unlink(missing_ok=True)
            log.info("built stick boot template %s (%d byte head)", out.name, meta["head_bytes"])
            return out, meta

    def _build(self, settings: Settings, img: Path, size_mb: int, offset: int) -> int:
        with open(img, "wb") as f:
            f.truncate(size_mb * 1024 * 1024)
        _run(["mkfs.fat", "-F", "32", "-n", "NDBOOT", "-h", str(offset), str(img)])
        target = ["-i", str(img)]
        dirs = sorted({str(Path(p).parent) for p in TEMPLATE_FILES} | {STICK_DIR, "syslinux"})
        made: set[str] = set()
        for d in dirs:
            parts = Path(d).parts
            for i in range(1, len(parts) + 1):
                sub = "/".join(parts[:i])
                if sub not in made:
                    _run(["mmd", *target, f"::/{sub}"])
                    made.add(sub)
        for dest, name in TEMPLATE_FILES.items():
            _run(["mcopy", *target, str(settings.usb_assets_dir / name), f"::/{dest}"])
        _run(["mcopy", *target, str(settings.wimboot_path), f"::/{STICK_DIR}/wimboot"])
        cfg = img.parent / "syslinux.cfg"
        cfg.write_text(render_stick_syslinux_cfg())
        _run(["mcopy", *target, str(cfg), "::/syslinux/syslinux.cfg"])
        if template_bios(settings):
            # SYSLINUX's mtools installer: ldlinux.sys/.c32 and the boot sector code.
            _run([BIOS_TOOL, "--install", "--directory", "/syslinux", str(img)])
            _sync_backup_boot_sector(img)
        return fat_used_bytes(img)


def _sync_backup_boot_sector(img: Path) -> None:
    """SYSLINUX writes its boot code to the boot sector only; copy it over the
    FAT32 backup boot sector too, so fsck and chkdsk find them equal."""
    with open(img, "r+b") as f:
        bs = f.read(512)
        bps = struct.unpack_from("<H", bs, 11)[0]
        backup = struct.unpack_from("<H", bs, 50)[0]
        if backup:
            f.seek(backup * bps)
            f.write(bs)


def mbr_path(settings: Settings) -> Path:
    return settings.usb_assets_dir / MBR_ASSET
