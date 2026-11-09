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

# Failed share connections before the first network diagnostics are printed
# (DHCP can still be settling on the first attempts); repeated every
# DIAG_EVERY attempts after that.
DIAG_FIRST = 3
DIAG_EVERY = 10


def _batch_literal(value: str) -> str:
    """Escape a value for use inside double quotes in a .cmd script."""
    if '"' in value or "\r" in value or "\n" in value:
        raise ValueError("value may not contain double quotes or newlines")
    return value.replace("%", "%%")


def _diagnostics(host: str) -> list[str]:
    """``:diag`` subroutine: show what WinPE's network looks like.

    The hints match English ``ipconfig`` output; on other WinPE languages they
    may be wrong, but the raw lines printed above them still tell the story.
    """
    return [
        ":diag",
        "echo.",
        "echo netdispatch: ---- network diagnostics ----",
        'ipconfig | findstr /i /c:"adapter" /c:"IPv4" /c:"Media State"',
        'ipconfig | findstr /i /c:"adapter" >nul || echo netdispatch: WinPE sees NO network adapter. It has no driver for this'
        " network card: on VMs use e1000e instead of VirtIO, otherwise add the driver to boot.wim.",
        'ipconfig | findstr /r /c:"^ *IPv4 Address" >nul || echo netdispatch: no IPv4 address from DHCP. Check the cable,'
        " VLAN and DHCP server.",
        f"ping -n 1 -w 2000 {host} | findstr /i /c:\"{host}\" /c:\"TTL=\" /c:\"timed out\" /c:\"unreachable\"",
        f"echo netdispatch: the share needs TCP 445 from this machine to {host}.",
        "echo netdispatch: -----------------------------",
        "echo.",
        "goto :eof",
    ]


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
        "echo netdispatch: cannot reach %ND_SHARE% (attempt %ND_TRIES%)",
        f"if %ND_TRIES%=={DIAG_FIRST} call :diag",
        f"set /a ND_MOD=ND_TRIES %% {DIAG_EVERY}",
        "if %ND_MOD%==0 call :diag",
        # choice gives a way out to a prompt (winpeshl has no Shift+F10). If it is
        # missing or has no console it returns >= 255: fall back to a plain wait.
        'choice /c RC /t 10 /d R /n /m "netdispatch: retrying in 10 seconds - press C for a command prompt " 2>nul',
        "if errorlevel 255 goto wait",
        "if errorlevel 2 goto shell",
        "if errorlevel 1 goto connect",
        ":wait",
        "ping -n 11 127.0.0.1 >nul",
        "goto connect",
        ":shell",
        "echo netdispatch: type EXIT to leave this prompt and retry the share.",
        "cmd /k",
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
        "goto :eof",
        *_diagnostics(host),
        "",
    ]
    return "\r\n".join(lines)
