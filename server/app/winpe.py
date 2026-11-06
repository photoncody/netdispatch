"""Files injected into WinPE by wimboot for unattended Windows Setup.

wimboot places every injected file in ``X:\\Windows\\System32``. ``winpeshl.ini``
replaces the default shell with our ``netdispatch.cmd``, which initialises the
network, maps the extracted ISO from the Samba sidecar and starts Setup with
the per-device autounattend.xml.
"""

from __future__ import annotations

from .config import Settings
from .models import IsoImage

WINPESHL_INI = '[LaunchApps]\r\n"netdispatch.cmd"\r\n'


def _batch_literal(value: str) -> str:
    """Escape a value for use inside double quotes in a .cmd script."""
    if '"' in value or "\r" in value or "\n" in value:
        raise ValueError("value may not contain double quotes or newlines")
    return value.replace("%", "%%")


def render_netdispatch_cmd(settings: Settings, iso: IsoImage, has_unattend: bool, mac: str) -> str:
    host = settings.effective_smb_host
    share = f"\\\\{host}\\{settings.smb_share}\\{iso.id}"
    setup = (iso.setup_path or "setup.exe").replace("/", "\\").lstrip("\\")
    user = _batch_literal(settings.smb_username)
    password = _batch_literal(settings.smb_password)
    unattend = "X:\\Windows\\System32\\autounattend.xml"
    lines = [
        "@echo off",
        "title netdispatch - Windows Setup",
        "echo.",
        f"echo netdispatch: preparing Windows Setup for {mac}",
        "echo netdispatch: initialising network (wpeinit)...",
        "wpeinit",
        "wpeutil WaitForNetwork >nul 2>&1",
        f'set "ND_SHARE={_batch_literal(share)}"',
        "set ND_TRIES=0",
        ":connect",
        "net use Z: /delete /y >nul 2>&1",
        f'net use Z: "%ND_SHARE%" /user:{user} "{password}" /persistent:no',
        "if not errorlevel 1 goto connected",
        "set /a ND_TRIES+=1",
        "echo netdispatch: cannot reach %ND_SHARE% (attempt %ND_TRIES%) - retrying in 10 seconds",
        "ping -n 11 127.0.0.1 >nul",
        "goto connect",
        ":connected",
        f'if not exist "Z:\\{setup}" (',
        f"  echo netdispatch: Z:\\{setup} not found on the share - dropping to a command prompt",
        "  cmd /k",
        ")",
    ]
    if has_unattend:
        lines += [
            f"echo netdispatch: starting Z:\\{setup} with {unattend}",
            f'"Z:\\{setup}" /unattend:{unattend}',
        ]
    else:
        lines += [
            f"echo netdispatch: starting Z:\\{setup} (interactive, no unattend bound)",
            f'"Z:\\{setup}"',
        ]
    lines += [
        "echo netdispatch: Setup exited - opening a command prompt",
        "cmd /k",
        "",
    ]
    return "\r\n".join(lines)
