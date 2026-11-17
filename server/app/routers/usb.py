"""Boot media: ``/usb/boot`` (unauthenticated, polled by media scripts) and the
admin download of the online-only boot ISO."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..pxe_engine import normalize_mac
from ..relay_cache import relay_for_client
from ..usb_media import MediaError, missing_requirements
from ..models import STATE_BOOTING, STATE_DEPLOYING, Device
from ..usb_sticks import stick_updates, template_bios, template_missing, updater_missing
from ..usb_scripts import StickUpdate, check_hub_url, render_media_handoff
from .boot import _clean_ip, ipxe_response

log = logging.getLogger("netdispatch.usb")
_STICK_ID = re.compile(r"^[a-z0-9]{6,32}$")
router = APIRouter()


@router.get("/usb/boot", response_class=PlainTextResponse)
def usb_boot(
    request: Request,
    mac: str = "",
    ip: str = "",
    relay_ip: str = "",
    media: str = "",
    stick: str = "",
    have: str = "",
    platform: str = "",
    db: Session = Depends(get_db),
):
    """First stop of a media script: pick the relay serving the client's subnet
    from the ProxyDHCP server that answered it (``relay_ip``) or, failing that,
    from the client's address, then hand over to ``/boot``."""
    settings: Settings = request.app.state.settings
    client_ip = _clean_ip(ip) or (request.client.host if request.client else None)
    relay = relay_for_client(db, settings, _clean_ip(relay_ip), client_ip)
    stick_id = stick if _STICK_ID.match(stick) else ""
    # The hold loop offers L to go back to the stick's own images (UEFI only).
    usb_menu = bool(stick_id) and platform == "efi" and have not in ("", "-")
    update = None
    if stick_id and not updater_missing(settings):
        device = db.query(Device).filter(Device.mac == normalize_mac(mac)).one_or_none() if normalize_mac(mac) else None
        # A deployment in progress (dispatched, or Windows Setup rebooting) goes first.
        if device is None or device.state not in (STATE_DEPLOYING, STATE_BOOTING):
            changed = stick_updates(db, settings, request.app.state.stick_images, stick_id, have)
            if changed:
                update = StickUpdate(stick_id, changed)
    log.info(
        "boot media (%s%s) %s at %s: relay %s%s",
        (media or "?")[:16],
        f" {stick_id}" if stick_id else "",
        normalize_mac(mac) or "?",
        client_ip or "?",
        relay.id if relay else "none",
        f", offering an update ({len(update.images)} image(s))" if update else "",
    )
    return ipxe_response(
        render_media_handoff(
            settings.boot_url,
            relay.id if relay else None,
            relay.name if relay else None,
            usb_menu=usb_menu,
            update=update,
        )
    )


@router.get("/api/usb/info")
def usb_info(request: Request):
    settings: Settings = request.app.state.settings
    problems = missing_requirements(settings)
    try:
        check_hub_url(settings.boot_url)
    except ValueError as e:
        problems.append(str(e))
    stick_problems = template_missing(settings)
    try:
        check_hub_url(settings.boot_url)
    except ValueError as e:
        stick_problems.append(str(e))
    return {
        "boot_url": settings.boot_url,
        "online_iso": not problems,
        "problems": problems,
        "sticks": not stick_problems,
        "stick_problems": stick_problems,
        "stick_bios": template_bios(settings),
        "creator": (settings.usb_assets_dir / CREATOR_EXE).is_file(),
        "updater": not updater_missing(settings),
    }


CREATOR_EXE = "netdispatch-usb-creator.exe"


@router.get("/api/usb/creator")
def usb_creator(request: Request):
    """USB Creator for Windows, built into the hub image (server/usbcreator)."""
    path = request.app.state.settings.usb_assets_dir / CREATOR_EXE
    if not path.is_file():
        raise HTTPException(status_code=404, detail="USB Creator is not part of this hub's image")
    return FileResponse(path, media_type="application/vnd.microsoft.portable-executable", filename=CREATOR_EXE)


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
