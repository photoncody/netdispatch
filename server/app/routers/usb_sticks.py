"""USB sticks.

Admin (dashboard session): profiles, USB Creator pairing, sticks.

``/api/usb/app/*`` is what USB Creator and a stick's own updater call. It is
exempt from the session guard (``auth.EXEMPT_PREFIXES``) and authenticated
here: ``X-Creator-Key`` (a paired USB Creator) or ``X-Stick-Key`` (one stick).
Pairing itself needs no key: the app shows a code, an admin approves it, and
only the app holds the poll token that collects the key.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..models import (
    BOOT_WINDOWS_SETUP,
    STICK_ITEM_ERROR,
    STICK_ITEM_READY,
    STICK_ITEM_STATUSES,
    IsoImage,
    UnattendConfig,
    UsbCreator,
    UsbPairing,
    UsbProfile,
    UsbProfileEntry,
    UsbStick,
    UsbStickItem,
    iso,
    utcnow,
)
from ..relay_cache import relay_for_client, usable_cache
from ..schemas import (
    UsbApproveIn,
    UsbLayoutIn,
    UsbPairPollIn,
    UsbPairStartIn,
    UsbProfileIn,
    UsbReportIn,
    UsbStickCreateIn,
)
from ..auth import hash_key
from ..usb_media import MediaError
from ..usb_sticks import (
    LOCAL_METHODS,
    creator_for_key,
    delete_profile,
    delete_stick,
    format_code,
    mbr_path,
    new_creator_key,
    new_stick_id,
    new_stick_key,
    normalize_code,
    parse_have,
    profile_entries,
    render_layout,
    start_pairing,
    stick_for_key,
    stick_manifest,
    stick_out,
)

log = logging.getLogger("netdispatch.usb")
router = APIRouter()
APP = "/api/usb/app"


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ---------------------------------------------------------------- views


def _profile_out(db: Session, request: Request, profile: UsbProfile) -> dict:
    settings: Settings = request.app.state.settings
    images = request.app.state.stick_images
    entries = []
    for entry, image_row in profile_entries(db, profile.id):
        image = images.get(settings, image_row)
        ua = db.get(UnattendConfig, entry.unattend_id) if entry.unattend_id else None
        entries.append(
            {
                "id": entry.id,
                "iso_id": image_row.id,
                "iso_name": image_row.name,
                "iso_status": image_row.status,
                "boot_method": image_row.boot_method,
                "unattend_id": entry.unattend_id,
                "unattend_name": ua.name if ua else None,
                "label": entry.label,
                "boot_bytes": image.boot_bytes if image and not image.error else None,
                "data_bytes": image.data_bytes if image and not image.error else None,
                "error": image.error if image else None,
            }
        )
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "entries": entries,
        "sticks": db.query(UsbStick).filter(UsbStick.profile_id == profile.id).count(),
        "updated_at": iso(profile.updated_at),
    }


def _stick_status(db: Session, request: Request, stick: UsbStick) -> dict:
    """The stick with, per profile image, how its copy compares to the hub's."""
    settings: Settings = request.app.state.settings
    images = request.app.state.stick_images
    reported = {i.iso_id: i for i in db.query(UsbStickItem).filter(UsbStickItem.stick_id == stick.id)}
    items = []
    seen: set[int] = set()
    for _entry, image_row in profile_entries(db, stick.profile_id):
        if image_row.id in seen:
            continue
        seen.add(image_row.id)
        image = images.get(settings, image_row)
        item = reported.get(image_row.id)
        if item is None:
            state = "missing"
        elif item.status == STICK_ITEM_ERROR:
            state = "error"
        elif item.status != STICK_ITEM_READY:
            state = "syncing"
        elif image is not None and not image.error and item.etag != image.etag:
            state = "outdated"
        else:
            state = "current"
        items.append(
            {
                "iso_id": image_row.id,
                "iso_name": image_row.name,
                "state": state,
                "bytes_done": item.bytes_done if item else 0,
                "bytes_total": item.bytes_total if item else 0,
                "message": item.message if item else None,
                "updated_at": iso(item.updated_at) if item else None,
            }
        )
    return {**stick_out(db, stick), "items": items}


def _validate_entries(db: Session, body: UsbProfileIn) -> None:
    for e in body.entries:
        image_row = db.get(IsoImage, e.iso_id)
        if image_row is None:
            raise HTTPException(422, f"image {e.iso_id} does not exist")
        if image_row.boot_method not in LOCAL_METHODS:
            raise HTTPException(422, f"{image_row.name}: only Windows Setup and WinPE images can go on a stick")
        if e.unattend_id is not None:
            if image_row.boot_method != BOOT_WINDOWS_SETUP:
                raise HTTPException(422, f"{image_row.name}: an answer file only applies to Windows Setup images")
            if db.get(UnattendConfig, e.unattend_id) is None:
                raise HTTPException(422, f"answer file {e.unattend_id} does not exist")


def _set_entries(db: Session, profile: UsbProfile, body: UsbProfileIn) -> None:
    db.query(UsbProfileEntry).filter(UsbProfileEntry.profile_id == profile.id).delete(synchronize_session=False)
    for n, e in enumerate(body.entries):
        db.add(
            UsbProfileEntry(
                profile_id=profile.id, iso_id=e.iso_id, unattend_id=e.unattend_id, label=e.label.strip(), sort=n
            )
        )


# ---------------------------------------------------------------- admin: profiles


@router.get("/api/usb/profiles")
def list_profiles(request: Request, db: Session = Depends(get_db)):
    return [_profile_out(db, request, p) for p in db.query(UsbProfile).order_by(UsbProfile.name)]


@router.post("/api/usb/profiles", status_code=201)
def create_profile(body: UsbProfileIn, request: Request, db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.query(UsbProfile).filter(UsbProfile.name == name).count():
        raise HTTPException(409, "a profile with this name already exists")
    _validate_entries(db, body)
    profile = UsbProfile(name=name, description=(body.description or "").strip() or None)
    db.add(profile)
    db.flush()
    _set_entries(db, profile, body)
    db.commit()
    return _profile_out(db, request, profile)


@router.put("/api/usb/profiles/{profile_id}")
def update_profile(profile_id: int, body: UsbProfileIn, request: Request, db: Session = Depends(get_db)):
    profile = db.get(UsbProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "profile not found")
    name = body.name.strip()
    if db.query(UsbProfile).filter(UsbProfile.name == name, UsbProfile.id != profile.id).count():
        raise HTTPException(409, "a profile with this name already exists")
    _validate_entries(db, body)
    profile.name = name
    profile.description = (body.description or "").strip() or None
    profile.updated_at = utcnow()
    _set_entries(db, profile, body)
    db.commit()
    return _profile_out(db, request, profile)


@router.delete("/api/usb/profiles/{profile_id}", status_code=204)
def remove_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(UsbProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "profile not found")
    delete_profile(db, profile)
    db.commit()


# ---------------------------------------------------------------- admin: creators


@router.get("/api/usb/creators")
def list_creators(db: Session = Depends(get_db)):
    now = utcnow()
    pairings = (
        db.query(UsbPairing).filter(UsbPairing.expires_at >= now).order_by(UsbPairing.created_at.desc()).all()
    )
    creators = db.query(UsbCreator).order_by(UsbCreator.name).all()
    return {
        "pairings": [
            {
                "code": format_code(p.code),
                "client_name": p.client_name,
                "client_ip": p.client_ip,
                "created_at": iso(p.created_at),
                "expires_at": iso(p.expires_at),
                "approved": p.creator_name is not None,
            }
            for p in pairings
        ],
        "creators": [
            {
                "id": c.id,
                "name": c.name,
                "created_at": iso(c.created_at),
                "last_seen": iso(c.last_seen),
                "last_ip": c.last_ip,
                "sticks": db.query(UsbStick).filter(UsbStick.creator_id == c.id).count(),
            }
            for c in creators
        ],
    }


@router.post("/api/usb/pairings/{code}/approve")
def approve_pairing(code: str, body: UsbApproveIn, request: Request, db: Session = Depends(get_db)):
    pairing = db.get(UsbPairing, normalize_code(code))
    if pairing is None or pairing.expires_at < utcnow():
        raise HTTPException(404, "no pending pairing with this code (they expire after 10 minutes)")
    user = (request.session.get("user") or {}).get("name") or (request.session.get("user") or {}).get("email")
    pairing.creator_name = body.name.strip()
    pairing.approved_by = (user or "admin")[:128]
    db.commit()
    log.info("USB Creator pairing %s approved as %r", pairing.code, pairing.creator_name)
    return {"ok": True}


@router.delete("/api/usb/pairings/{code}", status_code=204)
def reject_pairing(code: str, db: Session = Depends(get_db)):
    pairing = db.get(UsbPairing, normalize_code(code))
    if pairing is not None:
        db.delete(pairing)
        db.commit()


@router.delete("/api/usb/creators/{creator_id}", status_code=204)
def revoke_creator(creator_id: int, db: Session = Depends(get_db)):
    creator = db.get(UsbCreator, creator_id)
    if creator is None:
        raise HTTPException(404, "not found")
    # Its sticks keep their own keys.
    db.query(UsbStick).filter(UsbStick.creator_id == creator.id).update(
        {UsbStick.creator_id: None}, synchronize_session=False
    )
    db.delete(creator)
    db.commit()


# ---------------------------------------------------------------- admin: sticks


@router.get("/api/usb/sticks")
def list_sticks(request: Request, db: Session = Depends(get_db)):
    sticks = db.query(UsbStick).order_by(UsbStick.created_at.desc()).all()
    return [_stick_status(db, request, s) for s in sticks]


@router.delete("/api/usb/sticks/{stick_id}", status_code=204)
def revoke_stick(stick_id: str, db: Session = Depends(get_db)):
    stick = db.get(UsbStick, stick_id)
    if stick is None:
        raise HTTPException(404, "not found")
    delete_stick(db, stick)
    db.commit()


# ---------------------------------------------------------------- app: auth


def _creator(request: Request, db: Session) -> UsbCreator:
    creator = creator_for_key(db, request.headers.get("x-creator-key", ""))
    if creator is None:
        raise HTTPException(401, "invalid creator key")
    creator.last_seen = utcnow()
    creator.last_ip = _ip(request)
    return creator


def _stick(request: Request, db: Session, stick_id: str) -> UsbStick:
    """A stick's own key, or any paired USB Creator's."""
    stick = stick_for_key(db, stick_id, request.headers.get("x-stick-key", ""))
    if stick is None and creator_for_key(db, request.headers.get("x-creator-key", "")) is not None:
        stick = db.get(UsbStick, stick_id)
        if stick is not None and stick.revoked:
            stick = None
    if stick is None:
        raise HTTPException(401, "invalid stick or creator key")
    stick.last_seen = utcnow()
    stick.last_ip = _ip(request)
    return stick


def _any_key(request: Request, db: Session) -> None:
    if creator_for_key(db, request.headers.get("x-creator-key", "")) is not None:
        return
    stick_id = request.headers.get("x-stick-id", "")
    if stick_for_key(db, stick_id, request.headers.get("x-stick-key", "")) is not None:
        return
    raise HTTPException(401, "invalid stick or creator key")


# ---------------------------------------------------------------- app: pairing


@router.post(f"{APP}/pair/start")
def pair_start(body: UsbPairStartIn, request: Request, db: Session = Depends(get_db)):
    try:
        pairing, token = start_pairing(db, body.name, _ip(request))
    except PermissionError as e:
        raise HTTPException(429, str(e)) from e
    db.commit()
    log.info("USB Creator pairing %s requested from %s", pairing.code, _ip(request))
    return {
        "code": format_code(pairing.code),
        "poll_token": token,
        "expires_in": int((pairing.expires_at - pairing.created_at).total_seconds()),
    }


@router.post(f"{APP}/pair/poll")
def pair_poll(body: UsbPairPollIn, db: Session = Depends(get_db)):
    pairing = db.query(UsbPairing).filter(UsbPairing.poll_hash == hash_key(body.poll_token)).one_or_none()
    if pairing is None or pairing.expires_at < utcnow():
        raise HTTPException(404, "unknown or expired pairing - start again")
    if pairing.creator_name is None:
        return {"status": "pending"}
    key = new_creator_key()
    creator = UsbCreator(name=pairing.creator_name, key_hash=hash_key(key), revoked=False, created_at=utcnow())
    db.add(creator)
    db.delete(pairing)  # the key is handed out exactly once
    db.commit()
    log.info("USB Creator %r paired (id %s)", creator.name, creator.id)
    return {"status": "approved", "creator_id": creator.id, "name": creator.name, "creator_key": key}


# ---------------------------------------------------------------- app: sticks


@router.get(f"{APP}/profiles")
def app_profiles(request: Request, db: Session = Depends(get_db)):
    _creator(request, db)
    out = [_profile_out(db, request, p) for p in db.query(UsbProfile).order_by(UsbProfile.name)]
    db.commit()
    return {"hub_url": request.app.state.settings.boot_url, "profiles": out}


@router.post(f"{APP}/sticks", status_code=201)
def create_stick(body: UsbStickCreateIn, request: Request, db: Session = Depends(get_db)):
    creator = _creator(request, db)
    if db.get(UsbProfile, body.profile_id) is None:
        raise HTTPException(422, "profile not found")
    key = new_stick_key()
    stick = UsbStick(
        id=new_stick_id(),
        name=body.name.strip(),
        profile_id=body.profile_id,
        creator_id=creator.id,
        key_hash=hash_key(key),
        revoked=False,
        created_at=utcnow(),
    )
    db.add(stick)
    db.commit()
    log.info("USB stick %s (%r) created by USB Creator %r", stick.id, stick.name, creator.name)
    return {"stick": stick_out(db, stick), "stick_key": key, "hub_url": request.app.state.settings.boot_url}


@router.get(f"{APP}/sticks/{{stick_id}}/manifest")
def app_manifest(stick_id: str, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    stick = _stick(request, db, stick_id)
    relay = relay_for_client(db, settings, None, _ip(request))

    def cache_url_for(image_row: IsoImage) -> str | None:
        if relay is None:
            return None
        cache = usable_cache(db, settings, request.app.state.manifests, relay.id, image_row)
        return cache.cache_url if cache else None

    doc = stick_manifest(db, settings, stick, request.app.state.stick_images, cache_url_for)
    db.commit()
    return doc


@router.post(f"{APP}/sticks/{{stick_id}}/layout")
def app_layout(stick_id: str, body: UsbLayoutIn, request: Request, db: Session = Depends(get_db)):
    stick = _stick(request, db, stick_id)
    try:
        doc = render_layout(db, request.app.state.settings, stick, parse_have(body.have))
    except ValueError as e:
        raise HTTPException(503, str(e)) from e
    db.commit()
    return doc


@router.post(f"{APP}/sticks/{{stick_id}}/report")
def app_report(stick_id: str, body: UsbReportIn, request: Request, db: Session = Depends(get_db)):
    stick = _stick(request, db, stick_id)
    now = utcnow()
    items = {i.iso_id: i for i in db.query(UsbStickItem).filter(UsbStickItem.stick_id == stick.id)}
    reported: set[int] = set()
    for r in body.items:
        if db.get(IsoImage, r.iso_id) is None or r.iso_id in reported:
            continue
        reported.add(r.iso_id)
        item = items.get(r.iso_id)
        if item is None:
            item = UsbStickItem(stick_id=stick.id, iso_id=r.iso_id)
            db.add(item)
        item.status = r.status if r.status in STICK_ITEM_STATUSES else STICK_ITEM_ERROR
        etag = (r.etag or "").lower()
        item.etag = etag if len(etag) == 64 and all(c in "0123456789abcdef" for c in etag) else None
        item.bytes_done = max(0, r.bytes_done)
        item.bytes_total = max(0, r.bytes_total)
        item.message = (r.message or "")[:255] or None
        item.updated_at = now
    # The writer reports every image the stick holds: the rest are gone.
    for iso_id, item in items.items():
        if iso_id not in reported:
            db.delete(item)
    stick.last_report_at = now
    db.commit()
    return {"ok": True}


@router.get(f"{APP}/boot-template")
def app_boot_template(request: Request, size_mb: int, offset: int, db: Session = Depends(get_db)):
    """The stick boot partition's head (gzip): write it at the partition's
    start; the rest of the partition can stay as it is."""
    _any_key(request, db)
    db.commit()
    settings: Settings = request.app.state.settings
    try:
        path, meta = request.app.state.boot_template.get(settings, size_mb, offset)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except MediaError as e:
        log.error("stick boot template: %s", e)
        raise HTTPException(503, f"can't build the boot template: {e}") from e
    return FileResponse(
        path,
        media_type="application/gzip",
        filename=f"netdispatch-boot-{size_mb}.img.gz",
        headers={"X-ND-Head-Bytes": str(meta["head_bytes"]), "X-ND-Boot-Files": meta["version"]},
    )


@router.get(f"{APP}/mbr")
def app_mbr(request: Request, db: Session = Depends(get_db)):
    """SYSLINUX's MBR boot code (440 bytes) for legacy BIOS."""
    _any_key(request, db)
    db.commit()
    path = mbr_path(request.app.state.settings)
    if not path.is_file():
        raise HTTPException(503, "the MBR boot code is missing from this hub")
    return FileResponse(path, media_type="application/octet-stream", filename="mbr.bin")

