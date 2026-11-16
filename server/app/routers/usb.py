"""Boot media: ``/usb/boot`` (unauthenticated, polled by media scripts) and the
admin download of the online-only boot ISO."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..pxe_engine import normalize_mac
from ..relay_cache import relay_for_client
from ..usb_media import MediaError, missing_requirements
from ..usb_scripts import check_hub_url, render_media_handoff
from .boot import _clean_ip, ipxe_response

log = logging.getLogger("netdispatch.usb")
router = APIRouter()


@router.get("/usb/boot", response_class=PlainTextResponse)
def usb_boot(
    request: Request,
    mac: str = "",
    ip: str = "",
    relay_ip: str = "",
    media: str = "",
    db: Session = Depends(get_db),
):
    """First stop of a media script: pick the relay serving the client's subnet
    from the ProxyDHCP server that answered it (``relay_ip``) or, failing that,
    from the client's address, then hand over to ``/boot``."""
    settings: Settings = request.app.state.settings
    client_ip = _clean_ip(ip) or (request.client.host if request.client else None)
    relay = relay_for_client(db, settings, _clean_ip(relay_ip), client_ip)
    log.info(
        "boot media (%s) %s at %s: relay %s",
        (media or "?")[:16],
        normalize_mac(mac) or "?",
        client_ip or "?",
        relay.id if relay else "none",
    )
    return ipxe_response(
        render_media_handoff(settings.boot_url, relay.id if relay else None, relay.name if relay else None)
    )


@router.get("/api/usb/info")
def usb_info(request: Request):
    settings: Settings = request.app.state.settings
    problems = missing_requirements(settings)
    try:
        check_hub_url(settings.boot_url)
    except ValueError as e:
        problems.append(str(e))
    return {"boot_url": settings.boot_url, "online_iso": not problems, "problems": problems}


@router.get("/api/usb/online.iso")
def online_iso(request: Request):
    settings: Settings = request.app.state.settings
    try:
        path = request.app.state.online_iso.get(settings)
    except (MediaError, ValueError) as e:
        log.error("online boot ISO: %s", e)
        raise HTTPException(status_code=503, detail=f"can't build the boot ISO: {e}") from e
    return FileResponse(
        path,
        media_type="application/x-iso9660-image",
        filename="netdispatch-online.iso",
        headers={"Cache-Control": "no-store"},
    )
