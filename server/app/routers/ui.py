"""Dashboard page and health check."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .. import __version__
from ..config import Settings
from ..iso_tools import seven_zip

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse("/static/favicon.svg")


@router.get("/")
def dashboard(request: Request):
    settings: Settings = request.app.state.settings
    user = request.session.get("user") or {}
    warnings = []
    if not settings.smb_password:
        warnings.append("SMB_PASSWORD is not set - Windows Setup images cannot map their install share.")
    if not settings.wimboot_path.is_file():
        warnings.append(f"wimboot not found at {settings.wimboot_path} - Windows/WinPE images cannot boot.")
    if not seven_zip():
        warnings.append("7-Zip is not installed - uploaded images cannot be extracted.")
    config = {
        "user": user.get("name") or "admin",
        "auth_method": user.get("method"),
        "version": __version__,
        "boot_url": settings.boot_url,
        "public_url": settings.public_url,
        "smb_host": settings.effective_smb_host,
        "smb_share": settings.smb_share,
        "relay_offline_seconds": settings.relay_offline_seconds,
        "device_offline_seconds": settings.device_offline_seconds,
    }
    return request.app.state.templates.TemplateResponse(
        request, "index.html", {"config": config, "warnings": warnings, "version": __version__}
    )
