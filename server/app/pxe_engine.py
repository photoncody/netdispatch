"""Dynamic iPXE script generation.

Every script returned to a client is plain text starting with ``#!ipxe``.
All absolute URLs are built from ``Settings.boot_url`` (BOOT_BASE_URL or
PUBLIC_URL) - never from the request's Host header.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from . import __version__
from .models import (
    BOOT_LINUX,
    BOOT_MEMDISK,
    BOOT_SANBOOT,
    BOOT_WINDOWS_SETUP,
    BOOT_WINPE,
    IsoImage,
)

_UNSAFE_TEXT = re.compile(r"[^A-Za-z0-9 ._:/@()\-+,=]")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$", re.IGNORECASE)
_HEX = re.compile(r"[^0-9a-fA-F]")


def normalize_mac(raw: str | None) -> str | None:
    """Accept aa:bb.., aa-bb-.., aabb.ccdd.eeff, AABBCCDDEEFF -> 'aa:bb:cc:dd:ee:ff'."""
    if not raw:
        return None
    hexchars = _HEX.sub("", raw)
    if len(hexchars) != 12:
        return None
    hexchars = hexchars.lower()
    return ":".join(hexchars[i : i + 2] for i in range(0, 12, 2))


def safe_slug(value: str | None) -> str:
    value = (value or "").strip()
    return value if _SLUG.match(value) else ""


def ipxe_text(value: object, maxlen: int = 64) -> str:
    """Make arbitrary text safe to place after `echo` in an iPXE script.

    Strips characters iPXE would interpret (``$``, ``{``, ``&``, ``|``, ``#``, quotes...).
    """
    text = _UNSAFE_TEXT.sub("?", str(value if value is not None else "")).strip()
    return (text[:maxlen] or "-") if text else "-"


def qpath(path: str) -> str:
    return quote(path.strip("/"), safe="/")


@dataclass
class BootContext:
    base_url: str
    mac: str
    ip: str | None = None
    relay_id: str | None = None
    relay_name: str | None = None
    platform: str | None = None
    arch: str | None = None
    hostname: str | None = None
    poll_seconds: int = 5

    @property
    def is_efi(self) -> bool:
        return (self.platform or "").lower() == "efi"

    def poll_url(self, seq: int | None = None, result: str | None = None) -> str:
        """URL the client chains to for its next poll (uses iPXE variables)."""
        url = (
            f"{self.base_url}/boot?mac=${{netX/mac:hexhyp}}&ip=${{netX/ip}}"
            f"&relay={safe_slug(self.relay_id)}&arch=${{buildarch}}&platform=${{platform}}"
        )
        if seq is not None:
            url += f"&seq={seq}"
        if result:
            url += f"&result={result}"
        return url

    def placeholders(self, iso: IsoImage) -> dict[str, str]:
        return {
            "{iso_url}": raw_iso_url(self.base_url, iso),
            "{files_url}": files_url(self.base_url, iso),
            "{base_url}": self.base_url,
            "{mac}": self.mac,
            "{hostname}": self.hostname or "",
            "{ip}": self.ip or "",
            "{relay}": self.relay_id or "",
        }


def files_url(base_url: str, iso: IsoImage) -> str:
    return f"{base_url}/files/iso/{iso.id}/tree"


def raw_iso_url(base_url: str, iso: IsoImage) -> str:
    return f"{base_url}/files/iso/{iso.id}/raw/{quote(iso.filename)}"


def token_url(base_url: str, token: str, name: str) -> str:
    return f"{base_url}/files/d/{token}/{name}"


def _header(ctx: BootContext, what: str) -> list[str]:
    return [
        "#!ipxe",
        f"# netdispatch {__version__} :: {what} :: {ctx.mac}",
    ]


def _relay_label(ctx: BootContext) -> str:
    if ctx.relay_name and ctx.relay_id and ctx.relay_name != ctx.relay_id:
        return f"{ipxe_text(ctx.relay_name, 40)} ({ipxe_text(ctx.relay_id, 40)})"
    return ipxe_text(ctx.relay_name or ctx.relay_id or "direct / unknown")


def _duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def render_wait(ctx: BootContext, seq: int = 0) -> str:
    """Hold-loop script: show a status banner, sleep, then re-poll /boot.

    ``chain --replace`` replaces the running script instead of nesting it, so
    the loop can run for days without exhausting iPXE's memory.
    """
    next_seq = seq + 1
    waited = seq * int(ctx.poll_seconds)
    lines = _header(ctx, "wait") + [
        # ANSI escapes: every poll clears the screen and redraws the banner, so
        # the console shows a static status page instead of scrolling output.
        "set esc:hex 1b",
        "set nd-b ${esc:string}[1m",
        "set nd-c ${esc:string}[36m",
        "set nd-y ${esc:string}[33m",
        "set nd-g ${esc:string}[90m",
        "set nd-r ${esc:string}[0m",
        "echo -n ${esc:string}[2J${esc:string}[H",
        "echo ${nd-c}================================================================${nd-r}",
        "echo ${nd-b}${nd-c} netdispatch${nd-r} :: network boot hold",
        "echo ${nd-c}================================================================${nd-r}",
        "echo",
        f"echo   MAC address : ${{nd-b}}{ipxe_text(ctx.mac)}${{nd-r}}",
        f"echo   IP address  : {ipxe_text(ctx.ip)}",
        f"echo   Relay/site  : {_relay_label(ctx)}",
        f"echo   Firmware    : {ipxe_text(ctx.platform)} / {ipxe_text(ctx.arch)}",
    ]
    if ctx.hostname:
        lines.append(f"echo   Label       : {ipxe_text(ctx.hostname)}")
    lines += [
        "echo",
        "echo   ${nd-b}${nd-y}Waiting for administrator instructions...${nd-r}",
        "echo",
        f"echo   ${{nd-g}}poll {seq} :: waiting {_duration(waited)} :: next check in {int(ctx.poll_seconds)}s${{nd-r}}",
        "echo",
    ]
    lines += [
        ":poll",
        f"sleep {int(ctx.poll_seconds)}",
        f"chain --replace --autofree {ctx.poll_url(seq=next_seq)} || goto unreachable",
        ":unreachable",
        "echo",
        "echo  netdispatch hub unreachable - retrying",
        "goto poll",
        "",
    ]
    return "\n".join(lines)


def render_local(ctx: BootContext, reason: str = "no deployment pending") -> str:
    """Exit iPXE so the firmware continues with the next boot device."""
    return "\n".join(
        _header(ctx, "local")
        + [
            f"echo netdispatch: {ipxe_text(reason)} for {ipxe_text(ctx.mac)} - booting from local disk",
            "sleep 2",
            "exit 0",
            "",
        ]
    )


def render_message(ctx: BootContext, message: str, retry_seconds: int = 15) -> str:
    """Show an error then return to the poll loop."""
    return "\n".join(
        _header(ctx, "message")
        + [
            f"echo netdispatch: {ipxe_text(message, 120)}",
            f"sleep {int(retry_seconds)}",
            f"chain --replace --autofree {ctx.poll_url(seq=0)}",
            "",
        ]
    )


def _failure_tail(ctx: BootContext) -> list[str]:
    return [
        ":failed",
        "echo",
        "echo netdispatch: failed to load the payload - returning to the hold loop in 15s",
        "sleep 15",
        f"chain --replace --autofree {ctx.poll_url(seq=0, result='failed')}",
        "",
    ]


def render_payload(
    ctx: BootContext,
    iso: IsoImage,
    token: str,
    has_unattend: bool = False,
) -> str:
    method = iso.boot_method
    lines = _header(ctx, f"deploy {method}") + [
        f"echo netdispatch: deploying {ipxe_text(iso.name)} ({method}) to {ipxe_text(ctx.mac)}",
    ]
    if method in (BOOT_WINDOWS_SETUP, BOOT_WINPE):
        lines += _wimboot_lines(ctx, iso, token, has_unattend and method == BOOT_WINDOWS_SETUP)
    elif method == BOOT_LINUX:
        lines += _linux_lines(ctx, iso)
    elif method == BOOT_MEMDISK and not ctx.is_efi:
        lines += [
            f"kernel {ctx.base_url}/files/boot/memdisk iso raw || goto failed",
            f"initrd {raw_iso_url(ctx.base_url, iso)} || goto failed",
            "boot || goto failed",
        ]
    else:  # sanboot, or memdisk requested on UEFI (memdisk is BIOS-only)
        if method == BOOT_MEMDISK:
            lines.append("echo netdispatch: memdisk is BIOS-only, using sanboot on UEFI")
        lines += [f"sanboot --no-describe {raw_iso_url(ctx.base_url, iso)} || goto failed"]
    lines += _failure_tail(ctx)
    return "\n".join(lines)


def _wimboot_lines(ctx: BootContext, iso: IsoImage, token: str, with_unattend: bool) -> list[str]:
    base = files_url(ctx.base_url, iso)
    lines = [f"kernel {ctx.base_url}/files/boot/wimboot gui || goto failed"]
    if iso.bcd_path:
        lines.append(f"initrd {base}/{qpath(iso.bcd_path)} BCD || goto failed")
    if iso.boot_sdi_path:
        lines.append(f"initrd {base}/{qpath(iso.boot_sdi_path)} boot.sdi || goto failed")
    if iso.boot_method == BOOT_WINDOWS_SETUP:
        lines.append(f"initrd {token_url(ctx.base_url, token, 'winpeshl.ini')} winpeshl.ini || goto failed")
        lines.append(f"initrd {token_url(ctx.base_url, token, 'netdispatch.cmd')} netdispatch.cmd || goto failed")
        if with_unattend:
            lines.append(
                f"initrd {token_url(ctx.base_url, token, 'autounattend.xml')} autounattend.xml || goto failed"
            )
    lines.append(f"initrd {base}/{qpath(iso.boot_wim_path or 'sources/boot.wim')} boot.wim || goto failed")
    lines.append("boot || goto failed")
    return lines


def render_cmdline(ctx: BootContext, iso: IsoImage) -> str:
    cmdline = iso.cmdline or ""
    for key, value in ctx.placeholders(iso).items():
        cmdline = cmdline.replace(key, value)
    return " ".join(cmdline.split())


def _linux_lines(ctx: BootContext, iso: IsoImage) -> list[str]:
    base = files_url(ctx.base_url, iso)
    if not iso.kernel_path:
        return ["echo netdispatch: this image has no kernel path configured", "goto failed"]
    args = render_cmdline(ctx, iso)
    if ctx.is_efi and iso.initrd_paths:
        # Lets the EFI stub of older kernels find iPXE's combined initrd.
        args = f"initrd=initrd.magic {args}".strip()
    lines = [f"kernel {base}/{qpath(iso.kernel_path)} {args}".rstrip() + " || goto failed"]
    for path in iso.initrd_paths:
        lines.append(f"initrd {base}/{qpath(path)} || goto failed")
    lines.append("boot || goto failed")
    return lines
