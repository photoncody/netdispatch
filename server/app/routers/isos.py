"""/api/isos - image upload (streamed), extraction, boot profile editing."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..iso_tools import ExtractionManager, apply_detection, detect_layout, list_files, rescan, sanitize_filename
from ..models import (
    ISO_EXTRACTING,
    ISO_READY,
    ISO_UPLOADING,
    STATE_BOOTING,
    STATE_DEPLOYING,
    STATE_WAITING,
    Device,
    IsoImage,
)
from ..schemas import IsoUpdate, iso_out

log = logging.getLogger("netdispatch.isos")
router = APIRouter(prefix="/api/isos", tags=["isos"])

WRITE_CHUNK = 8 * 1024 * 1024


def _get(db: Session, iso_id: int) -> IsoImage:
    iso = db.get(IsoImage, iso_id)
    if iso is None:
        raise HTTPException(404, "image not found")
    return iso


@router.get("")
def list_isos(db: Session = Depends(get_db)):
    return [iso_out(i) for i in db.query(IsoImage).order_by(IsoImage.name).all()]


@router.post("/upload", status_code=201)
async def upload_iso(request: Request, db: Session = Depends(get_db)):
    """Stream the raw request body to disk.

    Headers: ``X-Filename`` (URL-encoded, required), ``X-Display-Name`` (optional).
    The body is written straight to ``<name>.part`` and renamed when complete,
    so multi-GB images are never held in memory.
    """
    settings: Settings = request.app.state.settings
    manager: ExtractionManager = request.app.state.extractor
    try:
        filename = sanitize_filename(unquote(request.headers.get("x-filename", "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    display = unquote(request.headers.get("x-display-name", "")).strip()[:200] or Path(filename).stem
    dest = settings.isos_dir / filename
    if dest.exists() or db.query(IsoImage).filter(IsoImage.filename == filename).count():
        raise HTTPException(409, f"{filename} already exists")

    iso = IsoImage(name=display, filename=filename, status=ISO_UPLOADING, status_message="uploading...")
    db.add(iso)
    db.commit()

    part = settings.isos_dir / (filename + ".part")
    size = 0
    try:
        with open(part, "wb") as fh:
            buf = bytearray()
            async for chunk in request.stream():
                buf += chunk
                if len(buf) >= WRITE_CHUNK:
                    data = bytes(buf)
                    buf.clear()
                    await run_in_threadpool(fh.write, data)
                    size += len(data)
            if buf:
                await run_in_threadpool(fh.write, bytes(buf))
                size += len(buf)
        if size == 0:
            raise ValueError("empty upload")
        part.rename(dest)
    except (ClientDisconnect, OSError, ValueError) as exc:
        part.unlink(missing_ok=True)
        db.delete(iso)
        db.commit()
        log.warning("upload of %s failed: %s", filename, exc)
        raise HTTPException(400, f"upload failed: {exc}") from exc

    iso.size = size
    iso.status = ISO_EXTRACTING
    iso.status_message = "queued for extraction"
    db.commit()
    manager.start(iso.id)
    log.info("uploaded %s (%d bytes)", filename, size)
    return iso_out(iso)


@router.post("/rescan")
def rescan_isos(request: Request, db: Session = Depends(get_db)):
    return rescan(db, request.app.state.settings, request.app.state.extractor)


@router.get("/{iso_id}")
def get_iso(iso_id: int, db: Session = Depends(get_db)):
    return iso_out(_get(db, iso_id))


@router.get("/{iso_id}/files")
def iso_files(iso_id: int, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    iso = _get(db, iso_id)
    return list_files(settings.extracted_dir / str(iso.id), limit=20000)


@router.patch("/{iso_id}")
def update_iso(iso_id: int, body: IsoUpdate, db: Session = Depends(get_db)):
    iso = _get(db, iso_id)
    for field in body.model_fields_set:
        value = getattr(body, field)
        if field == "initrd_paths":
            iso.initrd_paths = [p.strip() for p in (value or []) if p and p.strip()]
        elif field == "name":
            iso.name = value.strip()
        elif field == "boot_method":
            if value:
                iso.boot_method = value
        else:
            setattr(iso, field, (value or "").strip() or None)
    db.commit()
    return iso_out(iso)


@router.post("/{iso_id}/redetect")
def redetect(iso_id: int, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    iso = _get(db, iso_id)
    if iso.status != ISO_READY:
        raise HTTPException(409, "image is not extracted yet")
    det = detect_layout(settings.extracted_dir / str(iso.id))
    apply_detection(iso, det)
    iso.status_message = f"re-detected: {det.detected_os}"
    db.commit()
    return iso_out(iso)


@router.post("/{iso_id}/reextract")
def reextract(iso_id: int, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    manager: ExtractionManager = request.app.state.extractor
    iso = _get(db, iso_id)
    if manager.is_running(iso.id):
        raise HTTPException(409, "extraction already running")
    if not (settings.isos_dir / iso.filename).is_file():
        raise HTTPException(409, "image file is missing")
    iso.status = ISO_EXTRACTING
    iso.status_message = "queued for extraction"
    db.commit()
    manager.start(iso.id)
    return iso_out(iso)


@router.delete("/{iso_id}", status_code=204)
def delete_iso(iso_id: int, request: Request, delete_file: bool = True, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    manager: ExtractionManager = request.app.state.extractor
    iso = _get(db, iso_id)
    if manager.is_running(iso.id) or iso.status == ISO_UPLOADING:
        raise HTTPException(409, "image is busy (uploading or extracting)")
    for device in db.query(Device).filter(Device.iso_id == iso.id).all():
        if device.state in (STATE_DEPLOYING, STATE_BOOTING):
            device.state = STATE_WAITING
            device.deploy_token = None
            device.last_event = f"image {iso.name} deleted - returned to hold loop"
        device.iso_id = None
    shutil.rmtree(settings.extracted_dir / str(iso.id), ignore_errors=True)
    if delete_file:
        (settings.isos_dir / iso.filename).unlink(missing_ok=True)
    db.delete(iso)
    db.commit()
