"""/api/devices - device list, dispatch, reset (admin session required)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..models import (
    BOOT_LINUX,
    ISO_READY,
    STATE_BOOTING,
    STATE_DEPLOYING,
    STATE_LOCAL,
    STATE_WAITING,
    Device,
    IsoImage,
    Relay,
    UnattendConfig,
    utcnow,
)
from ..pxe_engine import (
    https_fetch_warning,
    normalize_mac,
    render_cmdline,
    render_local,
    render_payload,
    render_wait,
)
from ..relay_cache import usable_cache
from ..schemas import DeployIn, DeviceCreate, DeviceUpdate, device_out
from .boot import context_for

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _user(request: Request) -> str:
    return (request.session.get("user") or {}).get("name") or "admin"


def _relays(db: Session) -> dict[str, Relay]:
    return {r.id: r for r in db.query(Relay).all()}


def _get(db: Session, device_id: int) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "device not found")
    return device


def _clean_label(label: str | None) -> str | None:
    label = (label or "").strip()
    return label or None


@router.get("")
def list_devices(request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    relays = _relays(db)
    devices = db.query(Device).order_by(Device.last_seen.desc().nullslast(), Device.id.desc()).all()
    return [device_out(d, settings, relays) for d in devices]


@router.post("", status_code=201)
def create_device(body: DeviceCreate, request: Request, db: Session = Depends(get_db)):
    """Pre-register a MAC so a deployment can be staged before it first boots."""
    settings: Settings = request.app.state.settings
    mac = normalize_mac(body.mac)
    if not mac:
        raise HTTPException(422, "invalid MAC address")
    if db.query(Device).filter(Device.mac == mac).count():
        raise HTTPException(409, "device already exists")
    device = Device(
        mac=mac,
        label=_clean_label(body.label),
        state=STATE_WAITING,
        after_deploy=STATE_LOCAL,
        last_event=f"pre-registered by {_user(request)}",
    )
    db.add(device)
    db.commit()
    return device_out(device, settings, _relays(db))


@router.patch("/{device_id}")
def update_device(device_id: int, body: DeviceUpdate, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    device = _get(db, device_id)
    fields = body.model_fields_set
    if "label" in fields:
        device.label = _clean_label(body.label)
    if "notes" in fields:
        device.notes = body.notes
    if "after_deploy" in fields and body.after_deploy:
        device.after_deploy = body.after_deploy
    db.commit()
    return device_out(device, settings, _relays(db))


@router.post("/{device_id}/deploy")
def deploy(device_id: int, body: DeployIn, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    device = _get(db, device_id)
    iso = db.get(IsoImage, body.iso_id)
    if iso is None:
        raise HTTPException(404, "image not found")
    if iso.status != ISO_READY:
        raise HTTPException(409, f"image is not ready (status: {iso.status})")
    if body.unattend_id is not None:
        if db.get(UnattendConfig, body.unattend_id) is None:
            raise HTTPException(404, "unattend template not found")
    if "label" in body.model_fields_set:
        device.label = _clean_label(body.label)
    device.iso_id = iso.id
    device.unattend_id = body.unattend_id
    device.after_deploy = body.after_deploy
    device.state = STATE_DEPLOYING
    device.deploy_token = secrets.token_urlsafe(24)
    device.dispatched_at = utcnow()
    device.payload_served_at = None
    device.last_event = f"dispatched {iso.name} ({_user(request)})"
    db.commit()
    db.refresh(device)
    out = device_out(device, settings, _relays(db))
    if iso.boot_method == BOOT_LINUX:
        relay = db.get(Relay, device.relay_id) if device.relay_id else None
        warning = https_fetch_warning(render_cmdline(context_for(device, relay, settings), iso))
        if warning:
            out["warning"] = warning
    return out


@router.post("/{device_id}/reset")
def reset_to_wait(device_id: int, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    device = _get(db, device_id)
    device.state = STATE_WAITING
    device.deploy_token = None
    device.last_event = f"reset to hold loop ({_user(request)})"
    db.commit()
    return device_out(device, settings, _relays(db))


@router.post("/{device_id}/local")
def boot_local(device_id: int, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    device = _get(db, device_id)
    device.state = STATE_LOCAL
    device.deploy_token = None
    device.last_event = f"set to local boot ({_user(request)})"
    db.commit()
    return device_out(device, settings, _relays(db))


@router.delete("/{device_id}", status_code=204)
def delete_device(device_id: int, db: Session = Depends(get_db)):
    device = _get(db, device_id)
    db.delete(device)
    db.commit()


@router.get("/{device_id}/script", response_class=PlainTextResponse)
def preview_script(device_id: int, request: Request, db: Session = Depends(get_db)):
    """What the device would receive on its next poll (no state change)."""
    settings: Settings = request.app.state.settings
    device = _get(db, device_id)
    relay = db.get(Relay, device.relay_id) if device.relay_id else None
    ctx = context_for(device, relay, settings)
    if device.state in (STATE_DEPLOYING, STATE_BOOTING) and device.iso and device.deploy_token:
        cache = usable_cache(db, settings, request.app.state.manifests, device.relay_id, device.iso)
        if cache is not None:
            ctx.cache_base_url = cache.cache_url or ""
        script = render_payload(ctx, device.iso, device.deploy_token, device.unattend_id is not None)
        if device.state == STATE_BOOTING:
            script = (
                "# NOTE: payload already served; the next poll returns "
                f"{'the hold loop' if device.after_deploy == 'wait' else 'local boot'}\n" + script
            )
        return script
    if device.state == STATE_LOCAL:
        return render_local(ctx)
    return render_wait(ctx, 0)
