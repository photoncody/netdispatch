"""Relay ISO caching: manifests, readiness and relay cache reports.

A caching relay (``RELAY_CACHE=true``) keeps local copies of the ISOs an
admin assigned to it and serves them on its LAN: plain HTTP for iPXE and
initramfs downloads, a read-only SMB share for Windows Setup. The hub stays in
charge:

* ``build_manifest()`` says which files of an image a relay needs and where to
  download them (the existing, unauthenticated ``/files/iso/*`` routes).
* The relay reports per image the manifest ``etag`` its copy matches.
* ``usable_cache()`` decides per deployment whether the boot script points at
  the relay or, as before, at the hub. Anything short of a complete, current
  copy on a healthy, online relay falls back to the hub. WinPE maps the
  relay's share only if its smbd is up too, and falls back to the hub's.

Relays store every path in lower case (ASCII only) and serve it
case-insensitively (lighttpd ``force-lowercase-filenames``, Samba
``default case = lower``), matching the hub's case-insensitive ``resolve_ci``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    BOOT_LINUX,
    BOOT_MEMDISK,
    BOOT_SANBOOT,
    BOOT_WINDOWS_SETUP,
    BOOT_WINPE,
    CACHE_PENDING,
    CACHE_READY,
    CACHE_STATUSES,
    ISO_READY,
    IsoImage,
    Relay,
    RelayCache,
    RelayCacheItem,
    utcnow,
)

from .pxe_engine import uses_raw_iso_initrd

log = logging.getLogger("netdispatch.relay_cache")

TREE_METHODS = {BOOT_WINDOWS_SETUP, BOOT_WINPE, BOOT_LINUX}
RAW_METHODS = {BOOT_SANBOOT, BOOT_MEMDISK}

# What a relay may report as its cache address: http://host[:port], nothing
# else. It ends up verbatim in iPXE scripts and WinPE's share path.
_CACHE_URL = re.compile(r"^http://([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\])(?::(\d{1,5}))?$")
_ETAG = re.compile(r"^[0-9a-f]{64}$")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def ascii_lower(value: str) -> str:
    """Lower-case A-Z only, like lighttpd and busybox ``tr`` on the relay."""
    return value.translate(_ASCII_LOWER)


def valid_cache_url(value: str | None) -> str | None:
    """Return the URL without a trailing slash if it is a plain http://host[:port]."""
    if not value:
        return None
    value = value.strip().rstrip("/")
    m = _CACHE_URL.match(value)
    if not m:
        return None
    if m.group(2) is not None and not 0 < int(m.group(2)) < 65536:
        return None
    return value


# ---------------------------------------------------------------- manifests
@dataclass
class IsoManifest:
    iso_id: int
    etag: str
    files: list[dict] = field(default_factory=list)
    total_bytes: int = 0
    # Set when the image can't be cached at all; ``files`` is then empty, so
    # no relay copy ever counts as current and clients use the hub.
    error: str | None = None


def _needs_tree(iso: IsoImage) -> bool:
    return iso.boot_method in TREE_METHODS


def _needs_raw(iso: IsoImage) -> bool:
    if iso.boot_method in RAW_METHODS:
        return True
    if iso.boot_method != BOOT_LINUX:
        return False
    return "{iso_url}" in (iso.cmdline or "") or uses_raw_iso_initrd(iso)


def _file_entry(dest: str, src: str, path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"dest": dest, "src": src, "size": st.st_size, "mtime": int(st.st_mtime)}


def _case_clash(files: list[dict]) -> str | None:
    """Why the lower-cased destinations can't all exist on a relay, or None.

    The hub serves ``kernel`` and ``KERNEL`` as two files, but a relay stores
    both at one lower-case path, and a file ``a`` can't sit next to a
    directory ``A/``. Caching such an image would serve one file's bytes for
    the other, so the whole image stays on the hub.
    """
    seen: dict[str, str] = {}
    dirs: set[str] = set()
    for f in files:
        dest = f["dest"]
        if dest in seen:
            first, second = sorted((_shown(seen[dest]), _shown(f["src"])))
            return f"{first} and {second} differ only in letter case"
        seen[dest] = f["src"]
        parts = dest.split("/")
        dirs.update("/".join(parts[:i]) for i in range(1, len(parts)))
    for dest, src in seen.items():
        if dest in dirs:
            return f"{_shown(src)} clashes with a directory of the same name in another letter case"
    return None


def _shown(src: str) -> str:
    """A manifest ``src`` as the path inside the image, for messages."""
    return unquote(src.split("/", 5)[-1])


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _walk_tree(root: Path) -> tuple[list[tuple[str, Path]], str | None]:
    """Every path the hub's ``resolve_ci()`` serves below ``root``, as
    ``(relative path, file)`` pairs, or the reason a relay can't copy them.

    Symlinks inside the image are followed like the hub follows them, so a
    path through ``boot -> actual`` is copied under ``boot/`` too. Links that
    leave the image are skipped: the hub refuses those paths as well. A
    directory link back to one of its own parents (``debian -> .``) gives the
    hub endless valid paths that no copy can hold, so the image isn't cached.
    """
    real_root = root.resolve()
    found: list[tuple[str, Path]] = []
    for dirpath, dirs, names in os.walk(root, followlinks=True):
        here = Path(dirpath)
        rel_here = here.relative_to(root)
        # Real directories on the way down to here: following a link to one
        # of them would never end.
        chain = {real_root}
        for i in range(1, len(rel_here.parts) + 1):
            chain.add(Path(root, *rel_here.parts[:i]).resolve())
        for name in list(dirs):
            sub = here / name
            if not sub.is_symlink():
                continue
            target = sub.resolve()
            if not _inside(target, real_root):
                dirs.remove(name)
            elif target in chain:
                rel = sub.relative_to(root).as_posix()
                return [], f"the directory link {rel} points back to a directory that contains it"
        for name in names:
            path = here / name
            if path.is_symlink():
                target = path.resolve()
                if not _inside(target, real_root) or not target.is_file():
                    continue
            found.append((path.relative_to(root).as_posix(), path))
    return found, None


def build_manifest(settings: Settings, iso: IsoImage) -> IsoManifest:
    """List the files a relay needs to serve ``iso`` from its cache.

    ``dest`` is the path below the relay's CACHE_DIR (lower case), ``src`` the
    hub path to download it from. The relay prefixes ``src`` with its own
    CENTRAL_SERVER_URL: the address it already reaches the hub at.
    """
    files: list[dict] = []
    if _needs_tree(iso):
        root = settings.extracted_dir / str(iso.id)
        if root.is_dir():
            tree, problem = _walk_tree(root)
            if problem:
                log.warning("image %s can't be cached on relays: %s", iso.id, problem)
                return IsoManifest(iso.id, "", [], 0, problem)
            for rel, path in tree:
                entry = _file_entry(
                    f"tree/{iso.id}/{ascii_lower(rel)}",
                    f"/files/iso/{iso.id}/tree/{quote(rel, safe='/')}",
                    path,
                )
                if entry:
                    files.append(entry)
    if _needs_raw(iso):
        entry = _file_entry(
            f"raw/{iso.id}/{ascii_lower(iso.filename)}",
            f"/files/iso/{iso.id}/raw/{quote(iso.filename)}",
            settings.isos_dir / iso.filename,
        )
        if entry:
            files.append(entry)
    files.sort(key=lambda f: f["dest"])
    clash = _case_clash(files)
    if clash:
        log.warning("image %s can't be cached on relays: %s", iso.id, clash)
        return IsoManifest(iso.id, "", [], 0, clash)
    # The etag covers content identity only, so editing the image's name or
    # boot settings doesn't force relays to re-download.
    digest = hashlib.sha256(
        json.dumps([[f["dest"], f["size"], f["mtime"]] for f in files], separators=(",", ":")).encode()
    ).hexdigest()
    return IsoManifest(iso.id, digest, files, sum(f["size"] for f in files))


class ManifestCache:
    """Memoises manifests: heartbeats and /boot would otherwise walk the tree.

    Keyed on the image's row state, so re-extraction (which rewrites
    ``status`` and therefore ``updated_at``) or a boot-method change rebuilds
    it; the TTL catches files changed behind the hub's back.
    """

    def __init__(self, ttl: float = 300.0):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._data: dict[int, tuple[tuple, float, IsoManifest]] = {}

    def get(self, settings: Settings, iso: IsoImage) -> IsoManifest | None:
        if iso.status != ISO_READY:
            return None
        key = (iso.updated_at, iso.status, iso.boot_method, iso.cmdline, iso.filename)
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(iso.id)
            if hit and hit[0] == key and now - hit[1] < self.ttl:
                return hit[2]
        manifest = build_manifest(settings, iso)
        with self._lock:
            self._data[iso.id] = (key, now, manifest)
        return manifest

    def forget(self, iso_id: int) -> None:
        with self._lock:
            self._data.pop(iso_id, None)


def relay_manifest(db: Session, settings: Settings, cache: ManifestCache, relay_id: str) -> dict:
    """The document a relay syncs its cache to (``GET /api/relays/cache/manifest``).

    Assigned images that aren't ready on the hub (e.g. being re-extracted)
    are listed with ``keep: true`` and no files: the relay keeps what it has
    but the hub won't use it until the new etag is reported.
    """
    rows = (
        db.query(RelayCacheItem, IsoImage)
        .join(IsoImage, IsoImage.id == RelayCacheItem.iso_id)
        .filter(RelayCacheItem.relay_id == relay_id)
        .order_by(IsoImage.id)
        .all()
    )
    isos: list[dict] = []
    needs_smb = False
    for _item, iso in rows:
        manifest = cache.get(settings, iso)
        if manifest is None:
            isos.append({"id": iso.id, "keep": True})
            continue
        if manifest.error:
            # The relay drops its copy and reports the reason back.
            isos.append({"id": iso.id, "name": iso.name, "error": manifest.error})
            continue
        isos.append(
            {
                "id": iso.id,
                "name": iso.name,
                "etag": manifest.etag,
                "total_bytes": manifest.total_bytes,
                "files": manifest.files,
            }
        )
        needs_smb = needs_smb or iso.boot_method == BOOT_WINDOWS_SETUP
    smb = None
    if needs_smb and settings.smb_password:
        smb = {"share": settings.smb_share, "username": settings.smb_username, "password": settings.smb_password}
    doc = {"isos": isos, "smb": smb}
    doc["etag"] = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return doc


# ---------------------------------------------------------------- readiness
def is_current(item: RelayCacheItem | None, manifest: IsoManifest | None) -> bool:
    """The relay reports a complete copy of exactly this version of the image.

    The byte count is a backstop: a relay that says "ready" with fewer bytes
    than the manifest lists must not get clients sent to it.
    """
    return bool(
        item is not None
        and manifest is not None
        and manifest.files
        and item.status == CACHE_READY
        and item.etag
        and item.etag == manifest.etag
        and (item.bytes_done or 0) >= manifest.total_bytes
    )


def relay_online(relay: Relay, settings: Settings) -> bool:
    if not relay.enabled or relay.last_seen is None:
        return False
    return (utcnow() - relay.last_seen).total_seconds() <= settings.relay_offline_seconds


def usable_cache(
    db: Session, settings: Settings, cache: ManifestCache, relay_id: str | None, iso: IsoImage
) -> RelayCache | None:
    """The relay's cache if it can serve ``iso``'s files over HTTP right now,
    else None (use the hub). WinPE additionally maps the relay's share only
    when ``report.smb_ok``; otherwise it uses the hub's."""
    if not relay_id:
        return None
    relay = db.get(Relay, relay_id)
    if relay is None or not relay_online(relay, settings):
        return None
    report = db.get(RelayCache, relay_id)
    if report is None or not report.cache_url or not report.http_ok:
        return None
    item = db.get(RelayCacheItem, (relay_id, iso.id))
    if item is None or not is_current(item, cache.get(settings, iso)):
        return None
    return report


# ---------------------------------------------------------------- reports
def apply_report(db: Session, relay: Relay, report) -> None:
    """Store a heartbeat's ``cache`` object (``schemas.CacheReportIn`` or None)."""
    row = db.get(RelayCache, relay.id)
    if report is None:
        # The relay runs without RELAY_CACHE: forget its cache, keep the
        # assignments so turning it back on resumes where it left off.
        if row is not None:
            db.delete(row)
        return
    if row is None:
        row = RelayCache(relay_id=relay.id)
        db.add(row)
    url = valid_cache_url(report.url)
    if report.url and url is None:
        log.warning("relay %s reported an invalid cache URL %r; not using its cache", relay.id, report.url[:200])
    row.cache_url = url
    row.http_ok = bool(report.http_ok)
    row.smb_ok = bool(report.smb_ok)
    row.disk_free = report.disk_free
    row.disk_total = report.disk_total
    row.reported_at = utcnow()

    items = {i.iso_id: i for i in db.query(RelayCacheItem).filter(RelayCacheItem.relay_id == relay.id)}
    reported_ids = {r.iso_id for r in report.items}
    for item in items.values():
        # The relay reports every image it has checked. One it doesn't mention
        # (just assigned, or the relay restarted and hasn't re-verified its
        # copy yet) must not keep an old "ready" from an earlier report.
        if item.iso_id not in reported_ids and (item.status != CACHE_PENDING or item.etag):
            item.status = CACHE_PENDING
            item.etag = None
            item.bytes_done = 0
            item.message = "not checked by the relay yet"
            item.updated_at = utcnow()
    for reported in report.items:
        item = items.get(reported.iso_id)
        if item is None:  # not (or no longer) assigned
            continue
        status = reported.status if reported.status in CACHE_STATUSES else "error"
        item.status = status
        item.etag = reported.etag if reported.etag and _ETAG.match(reported.etag) else None
        item.bytes_done = max(0, reported.bytes_done or 0)
        item.bytes_total = max(0, reported.bytes_total or 0)
        item.message = (reported.message or "")[:255] or None
        item.updated_at = utcnow()


def delete_for_iso(db: Session, iso_id: int) -> None:
    db.query(RelayCacheItem).filter(RelayCacheItem.iso_id == iso_id).delete(synchronize_session=False)


def delete_for_relay(db: Session, relay_id: str) -> None:
    db.query(RelayCacheItem).filter(RelayCacheItem.relay_id == relay_id).delete(synchronize_session=False)
    db.query(RelayCache).filter(RelayCache.relay_id == relay_id).delete(synchronize_session=False)


# ---------------------------------------------------------------- admin views
def item_out(item: RelayCacheItem | None, iso: IsoImage, manifest: IsoManifest | None) -> dict:
    usable = is_current(item, manifest)
    uncacheable = manifest.error if manifest else None
    return {
        "iso_id": iso.id,
        "iso_name": iso.name,
        "iso_status": iso.status,
        "boot_method": iso.boot_method,
        "assigned": item is not None,
        "cache_bytes": manifest.total_bytes if manifest else None,
        "status": "error" if item and uncacheable else (item.status if item else None),
        # ready on the relay but for an older version of the image
        "stale": bool(item and item.status == CACHE_READY and not usable and not uncacheable),
        "usable": usable,
        "bytes_done": item.bytes_done if item else 0,
        "bytes_total": item.bytes_total if item else 0,
        "message": uncacheable or (item.message if item else None),
        "updated_at": item.updated_at.isoformat(timespec="seconds") + "Z" if item and item.updated_at else None,
    }


def cache_summary(
    report: RelayCache | None, items: list[RelayCacheItem], settings: Settings, cache: ManifestCache, isos: dict
) -> dict:
    usable = 0
    for item in items:
        iso = isos.get(item.iso_id)
        if iso is not None and is_current(item, cache.get(settings, iso)):
            usable += 1
    return {
        "enabled": report is not None,
        "url": report.cache_url if report else None,
        "http_ok": bool(report and report.http_ok),
        "smb_ok": bool(report and report.smb_ok),
        "disk_free": report.disk_free if report else None,
        "disk_total": report.disk_total if report else None,
        "assigned": len(items),
        "usable": usable,
        "syncing": sum(1 for i in items if i.status == "syncing"),
        "errors": sum(1 for i in items if i.status == "error"),
    }
