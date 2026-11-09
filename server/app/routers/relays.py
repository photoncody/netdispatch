"""/api/relays - relay heartbeat (X-Relay-Key) and relay management (admin)."""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .. import relay_cache
from ..auth import hash_key, new_relay_key
from ..config import Settings
from ..database import get_db
from ..models import IsoImage, Relay, RelayCache, RelayCacheItem, utcnow
from ..schemas import CacheAssignIn, HeartbeatIn, RelayCreate, RelayUpdate, relay_out

log = logging.getLogger("netdispatch.relays")
router = APIRouter(prefix="/api/relays", tags=["relays"])

RESERVED_IDS = {"heartbeat", "cache"}


KEY_PLACEHOLDER = "<paste the relay key here>"


def compose_snippet(
    settings: Settings,
    relay_id: str,
    name: str,
    key: str | None,
    cache: bool = False,
    secure_boot: bool = False,
) -> str:
    """The relay's docker-compose.yml. ``cache`` and ``secure_boot`` are the
    dashboard's checkboxes: they switch on RELAY_CACHE (plus its volume) and
    SECURE_BOOT, so nobody has to uncomment YAML by hand."""
    source = (
        f"    image: {settings.relay_image}\n"
        if settings.relay_image
        else f"    build: {settings.relay_build_context}\n"
    )
    key_line = key or KEY_PLACEHOLDER
    safe_name = name.replace('"', "'")
    return (
        "# docker-compose.yml for a netdispatch PXE relay.\n"
        "# Run on a Linux host attached to the target subnet/VLAN (host networking\n"
        "# is required to receive DHCP broadcasts; Docker Desktop will not work).\n"
        "services:\n"
        "  netdispatch-relay:\n"
        f"{source}"
        "    container_name: netdispatch-relay\n"
        "    network_mode: host\n"
        "    cap_add: [NET_ADMIN]\n"
        "    restart: unless-stopped\n"
        "    environment:\n"
        f'      CENTRAL_SERVER_URL: "{settings.boot_url}"\n'
        f'      RELAY_ID: "{relay_id}"\n'
        f'      RELAY_NAME: "{safe_name}"\n'
        f'      RELAY_API_KEY: "{key_line}"\n'
        "      # RELAY_INTERFACE: eth0              # default: interface of the default route\n"
        "      # SUBNET_RANGE: 192.168.20.0,proxy   # default: derived from RELAY_INTERFACE\n"
        "      # UEFI_BOOTFILE: snponly.efi         # use firmware NIC drivers on UEFI\n"
        "      # IPXE_CLIENTS: direct               # let NIC-ROM iPXE skip the netdispatch iPXE build\n"
        '      # RELAY_LOG_DHCP: "false"            # quiet the logs once everything works\n'
        + (
            "      # Boot UEFI machines with Secure Boot on (the iPXE project's signed binaries).\n"
            '      SECURE_BOOT: "true"\n'
            if secure_boot
            else ""
        )
        + (
            "      # Keep local copies of the ISOs assigned in the hub (Relays -> Cache) and serve\n"
            "      # them on this LAN: HTTP on port 8080, SMB on 445 for Windows Setup.\n"
            '      RELAY_CACHE: "true"\n'
            '      # CACHE_RATE_LIMIT: "20M"            # cap downloads from the hub (curl --limit-rate)\n'
            '      # CACHE_SYNC_WINDOW: "22:00-06:00"   # only start downloads in this window (TZ)\n'
            "      # TZ: Europe/Berlin\n"
            "    volumes:\n"
            "      - ./cache:/cache                     # needs room for the assigned images\n"
            if cache
            else ""
        )
    )


def _verify_key(relay: Relay | None, key: str, settings: Settings) -> str | None:
    """Return 'relay' / 'bootstrap' for a valid key, else None."""
    if not key:
        return None
    if relay is not None and relay.key_hash and hmac.compare_digest(hash_key(key), relay.key_hash):
        return "relay"
    if settings.relay_api_key and hmac.compare_digest(key.encode(), settings.relay_api_key.encode()):
        return "bootstrap"
    return None


@router.post("/heartbeat")
def heartbeat(
    body: HeartbeatIn,
    request: Request,
    x_relay_key: str = Header(default=""),
    db: Session = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    relay = db.get(Relay, body.relay_id)
    method = _verify_key(relay, x_relay_key, settings)
    if method is None:
        log.warning("rejected heartbeat for relay %r from %s", body.relay_id, request.client)
        raise HTTPException(401, "invalid relay key")
    if relay is None:
        if body.relay_id in RESERVED_IDS:
            raise HTTPException(422, "reserved relay id")
        relay = Relay(id=body.relay_id, name=body.name or body.relay_id, auto_registered=True, enabled=True)
        db.add(relay)
        log.info("auto-registered relay %s via bootstrap key", body.relay_id)
    if not relay.enabled:
        db.commit()
        raise HTTPException(403, "relay is disabled")
    if body.name and relay.auto_registered:
        relay.name = body.name
    relay.ip_address = body.ip_address
    relay.subnet = body.subnet
    relay.interface = body.interface
    relay.version = body.version
    relay.dnsmasq_ok = body.dnsmasq_ok
    relay.last_seen = utcnow()
    relay_cache.apply_report(db, relay, body.cache)
    db.commit()
    out = {"ok": True, "relay_id": relay.id, "boot_url": settings.boot_url}
    if body.cache is not None:
        # Lets the relay's sync loop notice new assignments without polling.
        manifest = relay_cache.relay_manifest(db, settings, request.app.state.manifests, relay.id)
        out["cache_manifest_etag"] = manifest["etag"]
    return out


@router.get("/cache/manifest")
def cache_manifest(
    request: Request,
    relay_id: str = "",
    x_relay_key: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """What a caching relay should hold. Authenticated by X-Relay-Key (exempt
    from the session guard, like the heartbeat)."""
    settings: Settings = request.app.state.settings
    relay = db.get(Relay, relay_id) if relay_id else None
    if relay is None or _verify_key(relay, x_relay_key, settings) is None:
        raise HTTPException(401, "invalid relay key")
    if not relay.enabled:
        raise HTTPException(403, "relay is disabled")
    return relay_cache.relay_manifest(db, settings, request.app.state.manifests, relay.id)


def _cache_view(db: Session, settings: Settings, manifests: relay_cache.ManifestCache, relay: Relay) -> dict:
    report = db.get(RelayCache, relay.id)
    items = {i.iso_id: i for i in db.query(RelayCacheItem).filter(RelayCacheItem.relay_id == relay.id)}
    isos = db.query(IsoImage).order_by(IsoImage.name).all()
    rows = [relay_cache.item_out(items.get(iso.id), iso, manifests.get(settings, iso)) for iso in isos]
    summary = relay_cache.cache_summary(report, list(items.values()), settings, manifests, {i.id: i for i in isos})
    summary["assigned_bytes"] = sum(r["cache_bytes"] or 0 for r in rows if r["assigned"])
    summary["reported_at"] = report.reported_at.isoformat(timespec="seconds") + "Z" if report and report.reported_at else None
    return {"relay": relay_out(relay, settings, summary), "items": rows}


@router.get("/{relay_id}/cache")
def get_cache(relay_id: str, request: Request, db: Session = Depends(get_db)):
    relay = db.get(Relay, relay_id)
    if relay is None:
        raise HTTPException(404, "relay not found")
    return _cache_view(db, request.app.state.settings, request.app.state.manifests, relay)


@router.put("/{relay_id}/cache")
def set_cache(relay_id: str, body: CacheAssignIn, request: Request, db: Session = Depends(get_db)):
    """Replace the set of images this relay keeps in its cache."""
    relay = db.get(Relay, relay_id)
    if relay is None:
        raise HTTPException(404, "relay not found")
    wanted = set(body.iso_ids)
    known = {i for (i,) in db.query(IsoImage.id).filter(IsoImage.id.in_(wanted))} if wanted else set()
    if wanted - known:
        raise HTTPException(422, f"unknown image id(s): {', '.join(map(str, sorted(wanted - known)))}")
    current = {i.iso_id: i for i in db.query(RelayCacheItem).filter(RelayCacheItem.relay_id == relay.id)}
    for iso_id, item in current.items():
        if iso_id not in wanted:
            db.delete(item)
    for iso_id in wanted - current.keys():
        db.add(RelayCacheItem(relay_id=relay.id, iso_id=iso_id, assigned_at=utcnow(), status="pending"))
    db.commit()
    log.info("relay %s now caches image(s) %s", relay.id, sorted(wanted) or "none")
    return _cache_view(db, request.app.state.settings, request.app.state.manifests, relay)


@router.get("")
def list_relays(request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    manifests = request.app.state.manifests
    reports = {c.relay_id: c for c in db.query(RelayCache)}
    items: dict[str, list[RelayCacheItem]] = {}
    for item in db.query(RelayCacheItem):
        items.setdefault(item.relay_id, []).append(item)
    isos = {i.id: i for i in db.query(IsoImage)} if items else {}
    return [
        relay_out(
            r,
            settings,
            relay_cache.cache_summary(reports.get(r.id), items.get(r.id, []), settings, manifests, isos),
        )
        for r in db.query(Relay).order_by(Relay.id).all()
    ]


@router.post("", status_code=201)
def create_relay(body: RelayCreate, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    if body.id in RESERVED_IDS:
        raise HTTPException(422, "reserved relay id")
    if db.get(Relay, body.id) is not None:
        raise HTTPException(409, "a relay with this id already exists")
    key = new_relay_key()
    relay = Relay(id=body.id, name=body.name.strip(), key_hash=hash_key(key), enabled=True)
    db.add(relay)
    db.commit()
    return {
        "relay": relay_out(relay, settings),
        "key": key,
        "compose": compose_snippet(settings, relay.id, relay.name, key, body.cache, body.secure_boot),
    }


@router.patch("/{relay_id}")
def update_relay(relay_id: str, body: RelayUpdate, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    relay = db.get(Relay, relay_id)
    if relay is None:
        raise HTTPException(404, "relay not found")
    if body.name is not None:
        relay.name = body.name.strip()
        relay.auto_registered = False
    if body.enabled is not None:
        relay.enabled = body.enabled
    db.commit()
    return relay_out(relay, settings)


@router.post("/{relay_id}/rotate-key")
def rotate_key(
    relay_id: str,
    request: Request,
    cache: bool = False,
    secure_boot: bool = False,
    db: Session = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    relay = db.get(Relay, relay_id)
    if relay is None:
        raise HTTPException(404, "relay not found")
    key = new_relay_key()
    relay.key_hash = hash_key(key)
    relay.auto_registered = False
    db.commit()
    return {
        "relay": relay_out(relay, settings),
        "key": key,
        "compose": compose_snippet(settings, relay.id, relay.name, key, cache, secure_boot),
    }


@router.get("/{relay_id}/compose")
def relay_compose(
    relay_id: str,
    request: Request,
    cache: bool = False,
    secure_boot: bool = False,
    db: Session = Depends(get_db),
):
    """The snippet without the key (the hub only stores its hash). The
    dashboard fills in a key it was just shown with ``key_placeholder``."""
    settings: Settings = request.app.state.settings
    relay = db.get(Relay, relay_id)
    if relay is None:
        raise HTTPException(404, "relay not found")
    return {
        "compose": compose_snippet(settings, relay.id, relay.name, None, cache, secure_boot),
        "key_placeholder": KEY_PLACEHOLDER,
    }


@router.delete("/{relay_id}", status_code=204)
def delete_relay(relay_id: str, db: Session = Depends(get_db)):
    relay = db.get(Relay, relay_id)
    if relay is None:
        raise HTTPException(404, "relay not found")
    relay_cache.delete_for_relay(db, relay.id)
    db.delete(relay)
    db.commit()
