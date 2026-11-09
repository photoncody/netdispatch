"""GET /boot - the endpoint every PXE client polls (unauthenticated)."""

from __future__ import annotations

import ipaddress
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..models import (
    ISO_READY,
    STATE_BOOTING,
    STATE_DEPLOYING,
    STATE_LOCAL,
    STATE_WAITING,
    Device,
    Relay,
    utcnow,
)
from ..pxe_engine import (
    BootContext,
    normalize_mac,
    render_local,
    render_message,
    render_payload,
    render_wait,
    safe_slug,
)

log = logging.getLogger("netdispatch.boot")
router = APIRouter()


def ipxe_response(script: str) -> PlainTextResponse:
    return PlainTextResponse(script, headers={"Cache-Control": "no-store"})


def _clean_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def context_for(device: Device, relay: Relay | None, settings: Settings) -> BootContext:
    return BootContext(
        base_url=settings.boot_url,
        fetch_base_url=settings.effective_linux_fetch_url,
        mac=device.mac,
        ip=device.ip,
        relay_id=device.relay_id,
        relay_name=relay.name if relay else None,
        platform=device.platform,
        arch=device.arch,
        hostname=device.label,
        poll_seconds=settings.wait_poll_seconds,
    )


@router.get("/boot", response_class=PlainTextResponse)
def boot(
    request: Request,
    mac: str = "",
    ip: str = "",
    relay: str = "",
    arch: str = "",
    platform: str = "",
    seq: int = 0,
    result: str = "",
    db: Session = Depends(get_db),
):
    settings: Settings = request.app.state.settings
    norm = normalize_mac(mac)
    if not norm:
        ctx = BootContext(base_url=settings.boot_url, mac="unknown", relay_id=safe_slug(relay))
        return ipxe_response(render_message(ctx, "the client did not report a valid MAC address"))

    now = utcnow()
    device = db.query(Device).filter(Device.mac == norm).one_or_none()
    if device is None:
        device = Device(
            mac=norm,
            first_seen=now,
            state=settings.new_device_state,
            after_deploy=STATE_LOCAL,
            boot_count=0,
            last_event="first seen",
        )
        db.add(device)
        log.info("new device %s via relay %s", norm, relay or "-")
    if seq == 0 and not result:
        device.boot_count = (device.boot_count or 0) + 1
    device.last_seen = now
    if ip and (clean := _clean_ip(ip)):
        device.ip = clean
    elif request.client and not device.ip:
        device.ip = request.client.host
    if safe_slug(relay):
        device.relay_id = safe_slug(relay)
    if safe_slug(arch):
        device.arch = safe_slug(arch)[:32]
    if safe_slug(platform):
        device.platform = safe_slug(platform)[:32]

    relay_obj = db.get(Relay, device.relay_id) if device.relay_id else None
    ctx = context_for(device, relay_obj, settings)

    if result == "failed" and device.state == STATE_BOOTING:
        device.state = STATE_WAITING
        device.deploy_token = None
        device.last_event = "payload failed to load on the client"
        log.warning("device %s reported payload failure", norm)

    if device.state == STATE_DEPLOYING:
        iso = device.iso
        if iso is None or iso.status != ISO_READY or not device.deploy_token:
            device.state = STATE_WAITING
            device.deploy_token = None
            device.last_event = "assigned image is not available - returned to hold loop"
            script = render_message(ctx, "assigned image is not available - returning to hold loop")
        else:
            script = render_payload(ctx, iso, device.deploy_token, has_unattend=device.unattend_id is not None)
            device.state = STATE_BOOTING
            device.payload_served_at = now
            device.last_event = f"payload served: {iso.name}"
            log.info("serving %s (%s) to %s", iso.name, iso.boot_method, norm)
    elif device.state == STATE_BOOTING:
        # The client came back to PXE after booting its payload (e.g. Windows
        # Setup rebooting). Never re-serve the payload: that would loop forever.
        device.deploy_token = None
        if device.after_deploy == "wait":
            device.state = STATE_WAITING
            device.last_event = "returned after deployment - hold loop"
            script = render_wait(ctx, 0)
        else:
            device.state = STATE_LOCAL
            device.last_event = "returned after deployment - local boot"
            script = render_local(ctx, "deployment complete")
    elif device.state == STATE_LOCAL:
        script = render_local(ctx)
    else:
        device.state = STATE_WAITING
        script = render_wait(ctx, max(seq, 0))

    db.commit()
    return ipxe_response(script)
