"""/api/relays - relay heartbeat (X-Relay-Key) and relay management (admin)."""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import hash_key, new_relay_key
from ..config import Settings
from ..database import get_db
from ..models import Relay, utcnow
from ..schemas import HeartbeatIn, RelayCreate, RelayUpdate, relay_out

log = logging.getLogger("netdispatch.relays")
router = APIRouter(prefix="/api/relays", tags=["relays"])

RESERVED_IDS = {"heartbeat"}


def compose_snippet(settings: Settings, relay_id: str, name: str, key: str | None) -> str:
    source = (
        f"    image: {settings.relay_image}\n"
        if settings.relay_image
        else f"    build: {settings.relay_build_context}\n"
    )
    key_line = key or "<paste the relay key here>"
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
    db.commit()
    return {"ok": True, "relay_id": relay.id, "boot_url": settings.boot_url}


@router.get("")
def list_relays(request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    return [relay_out(r, settings) for r in db.query(Relay).order_by(Relay.id).all()]


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
        "compose": compose_snippet(settings, relay.id, relay.name, key),
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
def rotate_key(relay_id: str, request: Request, db: Session = Depends(get_db)):
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
        "compose": compose_snippet(settings, relay.id, relay.name, key),
    }


@router.get("/{relay_id}/compose")
def relay_compose(relay_id: str, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    relay = db.get(Relay, relay_id)
    if relay is None:
        raise HTTPException(404, "relay not found")
    return {"compose": compose_snippet(settings, relay.id, relay.name, None)}


@router.delete("/{relay_id}", status_code=204)
def delete_relay(relay_id: str, db: Session = Depends(get_db)):
    relay = db.get(Relay, relay_id)
    if relay is None:
        raise HTTPException(404, "relay not found")
    db.delete(relay)
    db.commit()
