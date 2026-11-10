"""/api/unattend - autounattend.xml template management.

Templates are either raw XML edited by hand, or made with the built-in generator
(``/api/unattend/generator``): those have a ``UnattendGenerated`` row with their
settings, their file is regenerated on every save, and raw edits are refused
until the template is converted to raw XML (``/detach``).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from jinja2 import TemplateError
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..models import Device, UnattendConfig, UnattendGenerated, utcnow
from ..schemas import GeneratorIn, GeneratorPreviewIn, UnattendIn, unattend_out
from ..unattend import (
    TEMPLATE_VARIABLES,
    read_template,
    render,
    sample_context,
    slug_filename,
    sync_from_disk,
    template_path,
    validate,
    write_template,
)
from ..unattend import device_context as _device_context
from ..unattend_generator import (
    NEW_TEMPLATE_SETTINGS,
    SCHEMA_VERSION,
    TOKENS,
    GeneratorError,
    GeneratorUnavailable,
    UnattendGenerator,
)

router = APIRouter(prefix="/api/unattend", tags=["unattend"])

GENERATED_READ_ONLY = (
    "this template is made with the generator: change its settings in the generator, "
    "or convert it to raw XML first"
)


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


def _check_name(db: Session, name: str, exclude_id: int | None = None) -> str:
    name = name.strip()
    clash = db.query(UnattendConfig).filter(UnattendConfig.name == name)
    if exclude_id is not None:
        clash = clash.filter(UnattendConfig.id != exclude_id)
    if clash.count():
        raise HTTPException(409, "a template with this name already exists")
    return name


def _generator(request: Request) -> UnattendGenerator:
    return request.app.state.unattend_generator


def _engine_commit(request: Request) -> str | None:
    """The installed generator's upstream commit, or None if it is not available."""
    try:
        return _generator(request).upstream_commit
    except GeneratorUnavailable:
        return None


def _build(request: Request, gen_settings: dict) -> tuple[str, list[str]]:
    try:
        return _generator(request).build_template(gen_settings)
    except GeneratorError as exc:
        raise HTTPException(422, "\n".join(exc.errors)) from exc
    except GeneratorUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


def _context(db: Session, device_id: int | None) -> dict[str, str]:
    if device_id is None:
        return sample_context()
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "device not found")
    return _device_context(device)


@router.get("")
def list_templates(request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    sync_from_disk(db, settings)
    generated = {g.unattend_id: g for g in db.query(UnattendGenerated).all()}
    commit = _engine_commit(request) if generated else None
    out = []
    for cfg in db.query(UnattendConfig).order_by(UnattendConfig.name).all():
        item = unattend_out(cfg, generated=generated.get(cfg.id), engine_commit=commit)
        item["missing"] = not template_path(settings, cfg).is_file()
        out.append(item)
    return out


@router.get("/variables")
def variables():
    return TEMPLATE_VARIABLES


# --------------------------------------------------------------- generator
# Declared before the /{cfg_id} routes they would otherwise be read as.
@router.get("/generator/schema")
def generator_schema(request: Request):
    try:
        schema = _generator(request).schema()
    except GeneratorUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {**schema, "newTemplate": NEW_TEMPLATE_SETTINGS, "tokens": TOKENS}


@router.post("/generator/preview")
def generator_preview(body: GeneratorPreviewIn, request: Request, db: Session = Depends(get_db)):
    content, warnings = _build(request, body.settings)
    ctx = _context(db, body.device_id)
    try:
        rendered = render(content, ctx)
    except TemplateError as exc:  # pragma: no cover - build_template already rendered it
        raise HTTPException(422, f"template error: {exc}") from exc
    return {"rendered": rendered, "context": ctx, "warnings": warnings}


@router.post("/generator", status_code=201)
def create_generated(body: GeneratorIn, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    name = _check_name(db, body.name)
    content, warnings = _build(request, body.settings)
    commit = _engine_commit(request)
    cfg = UnattendConfig(name=name, filename=_unique_filename(db, name), description=body.description)
    db.add(cfg)
    db.flush()
    row = UnattendGenerated(
        unattend_id=cfg.id,
        settings_json=json.dumps(body.settings),
        schema_version=SCHEMA_VERSION,
        upstream_commit=commit,
    )
    db.add(row)
    write_template(settings, cfg, content)
    db.commit()
    out = unattend_out(cfg, content, row, commit)
    out.update(settings=row.settings, warnings=warnings)
    return out


@router.put("/generator/{cfg_id}")
def update_generated(cfg_id: int, body: GeneratorIn, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    cfg = _get(db, cfg_id)
    row = db.get(UnattendGenerated, cfg.id)
    if row is None:
        raise HTTPException(409, "this template is raw XML, not made with the generator")
    name = _check_name(db, body.name, exclude_id=cfg.id)
    content, warnings = _build(request, body.settings)
    commit = _engine_commit(request)
    cfg.name = name
    cfg.description = body.description
    cfg.updated_at = utcnow()
    row.settings_json = json.dumps(body.settings)
    row.schema_version = SCHEMA_VERSION
    row.upstream_commit = commit
    write_template(settings, cfg, content)
    db.commit()
    out = unattend_out(cfg, content, row, commit)
    out.update(settings=row.settings, warnings=warnings)
    return out


# --------------------------------------------------------------- templates
@router.get("/{cfg_id}")
def get_template(cfg_id: int, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    cfg = _get(db, cfg_id)
    row = db.get(UnattendGenerated, cfg.id)
    out = unattend_out(cfg, read_template(settings, cfg) or "", row, _engine_commit(request) if row else None)
    if row is not None:
        out["settings"] = row.settings
    return out


@router.post("", status_code=201)
def create_template(body: UnattendIn, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    name = _check_name(db, body.name)
    if err := validate(body.content):
        raise HTTPException(422, err)
    cfg = UnattendConfig(name=name, filename=_unique_filename(db, name), description=body.description)
    write_template(settings, cfg, body.content)
    db.add(cfg)
    db.commit()
    return unattend_out(cfg, body.content)


@router.put("/{cfg_id}")
def update_template(cfg_id: int, body: UnattendIn, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    cfg = _get(db, cfg_id)
    if db.get(UnattendGenerated, cfg.id) is not None:
        raise HTTPException(409, GENERATED_READ_ONLY)
    name = _check_name(db, body.name, exclude_id=cfg.id)
    if err := validate(body.content):
        raise HTTPException(422, err)
    content = body.content
    # The browser's editor uses LF; keep CRLF for a file that had it (such as a
    # template converted from the generator, whose embedded .cmd scripts need it).
    old = read_template(settings, cfg)
    if old and "\r\n" in old and "\r" not in content:
        content = content.replace("\n", "\r\n")
    cfg.name = name
    cfg.description = body.description
    write_template(settings, cfg, content)
    db.commit()
    return unattend_out(cfg, content)


@router.post("/{cfg_id}/preview")
def preview(cfg_id: int, request: Request, device_id: int | None = None, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    cfg = _get(db, cfg_id)
    content = read_template(settings, cfg)
    if content is None:
        raise HTTPException(404, "template file is missing")
    ctx = _context(db, device_id)
    try:
        rendered = render(content, ctx)
    except TemplateError as exc:
        raise HTTPException(422, f"template error: {exc}") from exc
    return {"rendered": rendered, "context": ctx}


@router.post("/{cfg_id}/detach")
def detach(cfg_id: int, request: Request, db: Session = Depends(get_db)):
    """Convert a generated template to raw XML: keep the file, drop the settings."""
    settings: Settings = request.app.state.settings
    cfg = _get(db, cfg_id)
    row = db.get(UnattendGenerated, cfg.id)
    if row is None:
        raise HTTPException(409, "this template is already raw XML")
    db.delete(row)
    cfg.updated_at = utcnow()
    db.commit()
    return unattend_out(cfg, read_template(settings, cfg) or "")


@router.delete("/{cfg_id}", status_code=204)
def delete_template(cfg_id: int, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    cfg = _get(db, cfg_id)
    for device in db.query(Device).filter(Device.unattend_id == cfg.id).all():
        device.unattend_id = None
    db.query(UnattendGenerated).filter(UnattendGenerated.unattend_id == cfg.id).delete()
    template_path(settings, cfg).unlink(missing_ok=True)
    db.delete(cfg)
    db.commit()
