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

# netdispatch.cmd copies itself here and runs the copy (see render_netdispatch_cmd).
RUN_COPY = "X:\\netdispatch.cmd"

# Failed share connections before the first network diagnostics are printed
# (DHCP can still be settling on the first attempts); repeated every
# DIAG_EVERY attempts after that.
DIAG_FIRST = 3
DIAG_EVERY = 10
# Failed attempts on a relay's cache share before switching to the hub's.
RELAY_TRIES = 3


def _batch_literal(value: str) -> str:
    """Escape a value for use inside double quotes in a .cmd script."""
    if '"' in value or "\r" in value or "\n" in value:
        raise ValueError("value may not contain double quotes or newlines")
    return value.replace("%", "%%")


def _diagnostics(host: str) -> list[str]:
    """``:diag`` subroutine: show what WinPE's network looks like.

    Windows Setup's boot.wim has ``find`` but no ``findstr``, ``choice`` or
    ``timeout``. So the raw ``ipconfig`` and ``ping`` output is printed, and the
    hints use only ``find`` (they're skipped where even that is missing). They
    match English ``ipconfig`` output; on other WinPE languages they may be
    wrong, but the raw output above them still tells the story.
    """
    return [
        ":diag",
        "echo.",
        "echo netdispatch: ---- network diagnostics ----",
        "ipconfig",
        "if not exist %SystemRoot%\\System32\\find.exe goto diagping",
        'ipconfig | find /i "adapter" >nul || echo netdispatch: WinPE sees NO network adapter. It has no driver for this'
        " network card: on VMs use e1000e instead of VirtIO, otherwise add the driver to boot.wim.",
        'ipconfig | find "IPv4 Address" >nul || echo netdispatch: no IPv4 address from DHCP. Check the cable,'
        " VLAN and DHCP server.",
        ":diagping",
        f"ping -n 1 -w 2000 {host}",
        f"echo netdispatch: the share needs TCP 445 from this machine to {host}.",
        "echo netdispatch: -----------------------------",
        "echo.",
        "goto :eof",
    ]


def render_netdispatch_cmd(
    settings: Settings, iso: IsoImage, has_unattend: bool, mac: str, relay_smb_host: str | None = None
) -> str:
    """``relay_smb_host``: the device's relay holds the image in its cache, so
    map that relay's share first and fall back to the hub's after a few tries."""
    hub_host = settings.effective_smb_host
    hub_share = f"\\\\{hub_host}\\{settings.smb_share}\\{iso.id}"
    share = f"\\\\{relay_smb_host}\\{settings.smb_share}\\{iso.id}" if relay_smb_host else hub_share
    # Diagnostics ping whichever host the script is currently trying.
    host = "%ND_HOST%" if relay_smb_host else hub_host
    setup = (iso.setup_path or "setup.exe").replace("/", "\\").lstrip("\\")
    user = _batch_literal(settings.smb_username)
    password = _batch_literal(settings.smb_password)
    unattend = "X:\\Windows\\System32\\autounattend.xml"
    lines = [
        "@echo off",
        # cmd.exe re-reads a batch file from disk before every line. Read back from
        # the wimboot-injected copy in X:\Windows\System32, this script goes wrong a
        # few seconds into WinPE: a line comes back cut short, then the file ends
        # early. It shows up when a long-running command such as wpeinit, net use or
        # ping returns. The script then ends, and winpeshl, whose only app it is,
        # reboots WinPE, so a share that was down never got past the first retries.
        # Run from a copy on the RAM disk instead. The copy is made while the first
        # lines still read back correctly, and a batch file started without CALL
        # replaces this one for good. If the copy fails, carry on from here.
        f'if /i not "%~f0"=="{RUN_COPY}" (',
        f'  copy /y "%~f0" {RUN_COPY} >nul && {RUN_COPY}',
        ")",
        "title netdispatch - Windows Setup",
        "echo.",
        f"echo netdispatch: preparing Windows Setup for {mac}",
        "echo netdispatch: initialising network (wpeinit)...",
        "wpeinit",
        "wpeutil WaitForNetwork >nul 2>&1",
        f'set "ND_SHARE={_batch_literal(share)}"',
    ]
    if relay_smb_host:
        lines += [
            f'set "ND_HOST={_batch_literal(relay_smb_host)}"',
            f'set "ND_HUB_SHARE={_batch_literal(hub_share)}"',
            "echo netdispatch: using the install share cached on this site's relay",
        ]
    lines += [
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
    ]
    if relay_smb_host:
        lines.append(f"if %ND_TRIES%=={RELAY_TRIES} if defined ND_HUB_SHARE goto use_hub")
    lines += [
        # choice gives a way out to a prompt (winpeshl has no Shift+F10). If it is
        # missing or has no console it returns >= 255: fall back to a plain wait.
        'choice /c RC /t 10 /d R /n /m "netdispatch: retrying in 10 seconds - press C for a command prompt " 2>nul',
        "if errorlevel 255 goto wait",
        "if errorlevel 2 goto shell",
        "if errorlevel 1 goto connect",
        ":wait",
        "echo netdispatch: retrying in 10 seconds",
        "ping -n 11 127.0.0.1 >nul",
        "goto connect",
        ":shell",
        "echo netdispatch: type EXIT to leave this prompt and retry the share.",
        "cmd /k",
        "goto connect",
    ]
    if relay_smb_host:
        lines += [
            ":use_hub",
            "echo netdispatch: the relay's share is not answering - switching to the hub's share",
            'set "ND_SHARE=%ND_HUB_SHARE%"',
            f'set "ND_HOST={_batch_literal(hub_host)}"',
            'set "ND_HUB_SHARE="',
            "set ND_TRIES=0",
            "goto connect",
        ]
    lines += [
        ":connected",
        f'if not exist "Z:\\{setup}" (',
        f"  echo netdispatch: Z:\\{setup} not found on the share - dropping to a command prompt",
        "  cmd /k",
        ")",
    ]
    if has_unattend:
        lines += [
            # Setup gets the file via /unattend, but scripts inside the answer file
            # may look for it themselves. The schneegans.de generator's pe.cmd
            # checks HKLM\System\Setup!UnattendFile (Setup's own first implicit
            # search location) and <drive>:\autounattend.xml, never System32.
            f"reg add HKLM\\System\\Setup /v UnattendFile /t REG_SZ /d {unattend} /f >nul",
            f"copy /y {unattend} X:\\autounattend.xml >nul",
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
