"""/api/unattend - autounattend.xml template management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from jinja2 import TemplateError
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..models import Device, UnattendConfig
from ..schemas import UnattendIn, unattend_out
from ..unattend import (
    TEMPLATE_VARIABLES,
    read_template,
    render,
    sample_context,
    slug_filename,
    sync_from_disk,
    template_path,
    validate,
)
from ..unattend import device_context as _device_context

router = APIRouter(prefix="/api/unattend", tags=["unattend"])


def _get(db: Session, cfg_id: int) -> UnattendConfig:
    cfg = db.get(UnattendConfig, cfg_id)
    if cfg is None:
        raise HTTPException(404, "template not found")
    return cfg


def _unique_filename(db: Session, name: str, exclude_id: int | None = None) -> str:
    base = slug_filename(name)
    stem = base[:-4]
    candidate, n = base, 1
    while True:
        clash = db.query(UnattendConfig).filter(UnattendConfig.filename == candidate)
        if exclude_id is not None:
            clash = clash.filter(UnattendConfig.id != exclude_id)
        if not clash.count():
            return candidate
        n += 1
        candidate = f"{stem}-{n}.xml"


@router.get("")
def list_templates(request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    sync_from_disk(db, settings)
    out = []
    for cfg in db.query(UnattendConfig).order_by(UnattendConfig.name).all():
        item = unattend_out(cfg)
        item["missing"] = not template_path(settings, cfg).is_file()
        out.append(item)
    return out


@router.get("/variables")
def variables():
    return TEMPLATE_VARIABLES


@router.get("/{cfg_id}")
def get_template(cfg_id: int, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    cfg = _get(db, cfg_id)
    return unattend_out(cfg, read_template(settings, cfg) or "")


@router.post("", status_code=201)
def create_template(body: UnattendIn, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    name = body.name.strip()
    if db.query(UnattendConfig).filter(UnattendConfig.name == name).count():
        raise HTTPException(409, "a template with this name already exists")
    if err := validate(body.content):
        raise HTTPException(422, err)
    cfg = UnattendConfig(name=name, filename=_unique_filename(db, name), description=body.description)
    template_path(settings, cfg).write_text(body.content, encoding="utf-8")
    db.add(cfg)
    db.commit()
    return unattend_out(cfg, body.content)


@router.put("/{cfg_id}")
def update_template(cfg_id: int, body: UnattendIn, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    cfg = _get(db, cfg_id)
    name = body.name.strip()
    if db.query(UnattendConfig).filter(UnattendConfig.name == name, UnattendConfig.id != cfg.id).count():
        raise HTTPException(409, "a template with this name already exists")
    if err := validate(body.content):
        raise HTTPException(422, err)
    cfg.name = name
    cfg.description = body.description
    template_path(settings, cfg).write_text(body.content, encoding="utf-8")
    db.commit()
    return unattend_out(cfg, body.content)


@router.post("/{cfg_id}/preview")
def preview(cfg_id: int, request: Request, device_id: int | None = None, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    cfg = _get(db, cfg_id)
    content = read_template(settings, cfg)
    if content is None:
        raise HTTPException(404, "template file is missing")
    ctx = sample_context()
    if device_id is not None:
        device = db.get(Device, device_id)
        if device is None:
            raise HTTPException(404, "device not found")
        ctx = _device_context(device)
    try:
        rendered = render(content, ctx)
    except TemplateError as exc:
        raise HTTPException(422, f"template error: {exc}") from exc
    return {"rendered": rendered, "context": ctx}


@router.delete("/{cfg_id}", status_code=204)
def delete_template(cfg_id: int, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    cfg = _get(db, cfg_id)
    for device in db.query(Device).filter(Device.unattend_id == cfg.id).all():
        device.unattend_id = None
    template_path(settings, cfg).unlink(missing_ok=True)
    db.delete(cfg)
    db.commit()
