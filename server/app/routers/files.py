"""/files/* - unauthenticated payload downloads for booting clients.

* ``/files/boot/{wimboot,memdisk}``  bundled boot helpers
* ``/files/iso/{id}/raw/{filename}`` the raw image (sanboot / memdisk / casper url=)
* ``/files/iso/{id}/tree/{path}``    files extracted from the image (kernel, WIM, ...)
* ``/files/d/{token}/{name}``        per-deployment generated files (secret-bearing:
  autounattend.xml, the WinPE script with SMB credentials). Valid only while
  the deployment is in progress.

FileResponse honours HTTP Range requests, which iPXE's sanboot relies on.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from jinja2 import TemplateError
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_db
from ..iso_tools import resolve_ci
from ..models import ISO_READY, STATE_BOOTING, STATE_DEPLOYING, Device, IsoImage
from ..unattend import device_context, read_template, render
from ..winpe import WINPESHL_INI, render_netdispatch_cmd

log = logging.getLogger("netdispatch.files")
router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


@router.api_route("/files/boot/{name}", methods=["GET", "HEAD"])
def boot_helper(name: str, request: Request):
    settings: Settings = request.app.state.settings
    paths = {"wimboot": settings.wimboot_path, "memdisk": settings.memdisk_path}
    path = paths.get(name)
    if path is None or not path.is_file():
        raise _not_found()
    return FileResponse(path, media_type="application/octet-stream")


@router.api_route("/files/iso/{iso_id}/raw/{filename}", methods=["GET", "HEAD"])
def raw_iso(iso_id: int, filename: str, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    iso = db.get(IsoImage, iso_id)
    if iso is None or iso.filename != filename:
        raise _not_found()
    path = settings.isos_dir / iso.filename
    if not path.is_file():
        raise _not_found()
    return FileResponse(path, media_type="application/octet-stream")


@router.api_route("/files/iso/{iso_id}/tree/{rel_path:path}", methods=["GET", "HEAD"])
def iso_tree_file(iso_id: int, rel_path: str, request: Request, db: Session = Depends(get_db)):
    settings: Settings = request.app.state.settings
    iso = db.get(IsoImage, iso_id)
    if iso is None or iso.status != ISO_READY:
        raise _not_found()
    path = resolve_ci(settings.extracted_dir / str(iso.id), rel_path)
    if path is None:
        raise _not_found()
    return FileResponse(path, media_type="application/octet-stream")


@router.get("/files/d/{token}/{name}")
def deployment_file(token: str, name: str, request: Request, db: Session = Depends(get_db)) -> Response:
    settings: Settings = request.app.state.settings
    if len(token) < 20:
        raise _not_found()
    device = db.query(Device).filter(Device.deploy_token == token).one_or_none()
    if device is None or device.state not in (STATE_DEPLOYING, STATE_BOOTING) or device.iso is None:
        raise _not_found()
    iso = device.iso

    if name == "winpeshl.ini":
        return PlainTextResponse(WINPESHL_INI, headers=NO_STORE)

    if name == "netdispatch.cmd":
        if not settings.smb_password:
            log.error("SMB_PASSWORD is not configured; WinPE cannot map the install share")
        script = render_netdispatch_cmd(settings, iso, device.unattend_id is not None, device.mac)
        return PlainTextResponse(script, headers=NO_STORE)

    if name == "autounattend.xml":
        if device.unattend is None:
            raise _not_found()
        content = read_template(settings, device.unattend)
        if content is None:
            log.error("unattend template file %s is missing", device.unattend.filename)
            raise _not_found()
        try:
            xml = render(content, device_context(device))
        except TemplateError as exc:
            log.error("rendering unattend %s for %s failed: %s", device.unattend.name, device.mac, exc)
            raise HTTPException(status_code=500, detail="template error") from exc
        return Response(xml, media_type="application/xml", headers=NO_STORE)

    raise _not_found()
