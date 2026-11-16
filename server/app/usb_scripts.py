"""iPXE scripts for netdispatch boot media: the online-only ISO and USB sticks.

A media script runs before the hub has been reached, so the hub renders it
when it builds the media (or, for a stick, when the stick's writer asks for
its layout), with the boot URL baked in. It plays the part of the relay's
``netdispatch.ipxe``: get an address, then chain the hub's ``/usb/boot``. That
endpoint works out which relay (if any) serves the client's subnet and hands
over to the normal ``/boot`` state machine.

Local boot follows invariant 2 (AGENTS.md): when the hub's script sets
``nd-local``, a UEFI media script exits with status 1 so the firmware moves on
to its next boot entry. Legacy BIOS iPXE was loaded as a Linux kernel by
ISOLINUX/SYSLINUX and has nothing to return to, so it boots a hard disk instead.

UEFI media start the iPXE-signed ``ipxe.efi`` through the Microsoft-signed
shim, so they boot with Secure Boot on. That build drives NICs through the
firmware's own network drivers (SNP/NII) and has native drivers for only a few
Realtek chips. When the firmware offers no network device - typically its
network stack is switched off in setup - the script chains an unsigned iPXE
with all of iPXE's own NIC drivers. Firmware only runs that with Secure Boot off.

USB sticks boot in two stages, because an iPXE that drives the network also
takes over the USB controllers, and the firmware can lose the stick when it
gets them back:

* The parent, the signed ``snponly.efi`` (``EFI/BOOT/ipxe.efi``, loaded by the
  shim), touches no hardware: it reads the stick through the firmware but has
  no network. It runs ``EFI/BOOT/autoexec.ipxe`` (``render_stick_parent``):
  the menu of the stick's own images and the images it boots.
* For the network it loads everything it needs into memory and chains the
  full signed ``ipxe.efi`` (the child) with ``render_media_script(stick=...)``
  as its script. The child exits 0 for "boot from the local disk" (the parent
  then exits 1, invariant 2) and 1 for "back to the stick" (no network, hub
  unreachable, or L pressed in the hold loop).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import __version__
from .pxe_engine import ipxe_text

# Paths inside the media's EFI system partition (FAT).
EFI_DIR = "EFI/BOOT"
EFI_NATIVE_DIR = "EFI/BOOT/native"
# USB stick boot partition (FAT32, label NDBOOT); the parent reads it by label.
STICK_BOOT_LABEL = "NDBOOT"
STICK_DATA_LABEL = "NDDATA"
STICK_ROOT = f"file://{STICK_BOOT_LABEL}"
STICK_NET_EFI = "EFI/BOOT/net/ipxe.efi"  # signed, full drivers: the network child
STICK_DIR = "netdispatch"  # hub-rendered files on both partitions
# Names under which the parent keeps the child's files in memory. The child
# reads them from the parent's virtual filesystem (file:/<name>).
_MEM_NET_SCRIPT = "autoexec.ipxe"  # the name iPXE looks for at start-up
_MEM_NATIVE_SCRIPT = "native.ipxe"
_MEM_NATIVE_EFI = "native.efi"
_MEM_SHIM = "shim.efi"
_MEM_NET_EFI = "net.efi"
# Legacy BIOS: SYSLINUX loads BIOS iPXE as a kernel. Booted from a stick, the
# stick is BIOS drive 0x80 and the first internal disk 0x81.
BIOS_STICK_LOCAL_DRIVE = "0x81"
BIOS_CD_LOCAL_DRIVE = "0x80"
# Hub retries before a stick gives up and goes back to its own images.
STICK_HUB_TRIES = 3
STICK_DHCP_TRIES = 2

_URL = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+(/[A-Za-z0-9._~\-/]*)?$")
_STICK_ID = re.compile(r"^[a-z0-9]{6,32}$")
# "<iso id>:<8 hex>," per image; a profile holds at most 50 (schemas.UsbProfileIn).
_HAVE = re.compile(r"^[0-9a-f:,]{0,2048}$")


def check_hub_url(url: str) -> str:
    """The hub URL is baked into media scripts as ``set nd-hub <url>``. It comes
    from BOOT_BASE_URL/PUBLIC_URL, but refuse anything iPXE would interpret."""
    url = url.strip().rstrip("/")
    if not _URL.match(url):
        raise ValueError(f"boot URL {url!r} can't be used in boot media (expected http(s)://host[:port][/path])")
    return url


@dataclass(frozen=True)
class StickRef:
    """What a stick's network script tells ``/usb/boot`` about the stick."""

    id: str
    # "<iso id>:<etag prefix>,..." for the images the stick holds; may be empty.
    have: str = ""

    def __post_init__(self):
        if not _STICK_ID.match(self.id):
            raise ValueError(f"bad stick id {self.id!r}")
        if not _HAVE.match(self.have):
            raise ValueError(f"bad image list {self.have!r}")


def render_media_script(
    hub_url: str, *, efi: bool, native: bool = False, media: str = "iso", stick: StickRef | None = None
) -> str:
    """Online boot script for boot media.

    efi=True:              the start script of the signed ipxe.efi (an ISO's EFI/BOOT/autoexec.ipxe,
                           a stick's netdispatch/net.ipxe).
    efi=True, native=True: the native-driver fallback's.
    efi=False:             the legacy BIOS script, loaded by ISOLINUX/SYSLINUX as an initrd.

    With ``stick`` (and efi) the script is a child of the stick's parent iPXE
    and returns to it instead of retrying forever: exit 0 = boot the local
    disk, exit 1 = back to the stick's own images.
    """
    hub = check_hub_url(hub_url)
    media = re.sub(r"[^a-z0-9-]", "", media.lower()) or "media"
    variant = "uefi-native" if efi and native else ("uefi" if efi else "bios")
    child = stick is not None and efi
    url = (
        "${nd-hub}/usb/boot?mac=${netX/mac:hexhyp}&ip=${netX/ip}"
        "&relay_ip=${proxydhcp/dhcp-server}&media=${nd-media}&arch=${buildarch}&platform=${platform}"
    )
    lines = [
        "#!ipxe",
        f"# netdispatch {__version__} boot media ({media}, {variant}) - generated by the hub, do not edit.",
    ]
    if child:
        lines += [
            "# Child of the USB stick's own iPXE: exit 0 = boot the local disk (the stick's",
            "# iPXE then exits 1, AGENTS.md invariant 2), exit 1 = back to the stick's images.",
        ]
    else:
        lines += [
            "# Exits with status 1 for a local boot: UEFI firmware only tries its next",
            "# boot entry when the current one fails (AGENTS.md, invariant 2).",
        ]
    lines += [
        f"set nd-hub {hub}",
        f"set nd-media {media}",
    ]
    if stick is not None:
        lines += [f"set nd-stick {stick.id}", f"set nd-have {stick.have or '-'}"]
        url += "&stick=${nd-stick}&have=${nd-have}"
    if child:
        lines.append("set nd-tries:int32 0")
    lines += [
        "echo netdispatch :: boot media :: iPXE ${version} (${platform}/${buildarch})",
        ":netdev",
        "isset ${net0/mac} || goto no_nic",
        ":dhcp",
        "dhcp || goto dhcp_failed",
        ":hub",
        "echo netdispatch :: contacting ${nd-hub}",
        f"chain --autofree {url} || goto hub_done",
        ":hub_done",
    ]
    if child:
        lines += [
            "isset ${nd-local} && goto local || goto hub_menu",
            ":hub_menu",
            # L pressed in the hold loop (pxe_engine.render_wait)
            "isset ${nd-menu} && goto offline || goto hub_failed",
            ":hub_failed",
            "inc nd-tries",
            f"iseq ${{nd-tries}} {STICK_HUB_TRIES} && goto offline || goto hub_retry",
            ":hub_retry",
            "echo netdispatch :: hub unreachable or returned an error - retrying in 10 seconds (Ctrl-C: this stick's menu)",
            "sleep 10 || goto offline",
            "goto hub",
            ":dhcp_failed",
            "inc nd-tries",
            f"iseq ${{nd-tries}} {STICK_DHCP_TRIES} && goto offline || goto dhcp_retry",
            ":dhcp_retry",
            "echo netdispatch :: no DHCP lease - retrying in 5 seconds (Ctrl-C: this stick's menu)",
            "sleep 5 || goto offline",
            "goto dhcp",
            ":offline",
            "echo netdispatch :: back to the USB stick",
            "exit 1",
            ":local",
            "echo netdispatch :: booting from local disk",
            "exit 0",
            ":no_nic",
        ]
        if not native:
            lines += [
                "echo netdispatch :: the firmware offers no network adapter - trying iPXE's own drivers",
                # The stick's iPXE loaded both into memory: our file:/ is its
                # virtual filesystem, and the grandchild finds its script there
                # under the name it looks for at start-up.
                f"imgfetch -n autoexec.ipxe file:/{_MEM_NATIVE_SCRIPT} || goto offline",
                f"chain file:/{_MEM_NATIVE_EFI} && goto local || goto native_failed",
                ":native_failed",
                "echo netdispatch :: no network through iPXE's own drivers either. With Secure Boot on,",
                "echo netdispatch :: only the firmware's network drivers can be used.",
                "sleep 5",
                "goto offline",
            ]
        else:
            lines += [
                "echo netdispatch :: no usable network adapter found",
                "sleep 5",
                "goto offline",
            ]
        lines.append("")
        return "\n".join(lines)

    lines += [
        "isset ${nd-local} && goto local || goto hub_failed",
        ":hub_failed",
        "echo netdispatch :: hub unreachable or returned an error - retrying in 10 seconds (Ctrl-C for a shell)",
        "sleep 10 || shell",
        "goto hub",
        ":dhcp_failed",
        "echo netdispatch :: no DHCP lease - retrying in 5 seconds (Ctrl-C for a shell)",
        "sleep 5 || shell",
        "goto dhcp",
        ":local",
    ]
    if efi:
        lines += [
            "echo netdispatch :: booting from local disk",
            "exit 1",
        ]
    else:
        # The loader started us as a kernel: there is no boot entry to return
        # to. From a (virtual) CD the first hard disk is 0x80; from a stick
        # the stick itself is 0x80.
        drive = BIOS_STICK_LOCAL_DRIVE if stick is not None else BIOS_CD_LOCAL_DRIVE
        lines += [
            "echo netdispatch :: booting from local disk",
            f"sanboot --no-describe --drive {drive} || goto local_failed",
            ":local_failed",
            "echo netdispatch :: could not boot the local disk - remove the boot media and restart",
            "sleep 30 || shell",
            "goto hub",
        ]
    lines.append(":no_nic")
    if efi and not native:
        lines += [
            "echo netdispatch :: the firmware offers no network adapter - trying iPXE's own drivers",
            # The chained iPXE finds its script in our virtual filesystem under the
            # name it looks for at start-up.
            f"imgfetch -n autoexec.ipxe file:/{EFI_NATIVE_DIR}/autoexec.ipxe || goto no_nic_failed",
            f"chain file:/{EFI_NATIVE_DIR}/ipxe.efi || goto native_done",
            ":native_done",
            # It only returns for a local boot - or never ran, because Secure Boot
            # refuses unsigned code. Either way the firmware should move on.
            "echo netdispatch :: back from iPXE's own drivers. If the machine did not reach the hub:",
            "echo netdispatch :: with Secure Boot on, only the firmware's network drivers can be used -",
            "echo netdispatch :: enable the network stack in firmware setup, or turn Secure Boot off.",
            "sleep 10 || shell",
            "exit 1",
            ":no_nic_failed",
        ]
    lines += [
        "echo netdispatch :: no usable network adapter found",
        "echo netdispatch :: booting from local disk in 15 seconds (Ctrl-C for a shell)",
        "sleep 15 || shell",
        "goto local",
        "",
    ]
    return "\n".join(lines)


def render_media_handoff(
    boot_url: str, relay_id: str | None, relay_name: str | None, *, usb_menu: bool = False
) -> str:
    """``/usb/boot``'s answer: chain the normal ``/boot`` with the relay the hub
    picked for the client's subnet, or ``-`` (no relay: everything from the hub).
    ``usb_menu``: the client is a stick with images of its own, so the hold
    loop offers L to go back to them."""
    relay = relay_id or "-"
    label = f"{ipxe_text(relay_name, 40)} ({ipxe_text(relay_id, 40)})" if relay_id else "none (direct to the hub)"
    return "\n".join(
        [
            "#!ipxe",
            f"# netdispatch {__version__} :: boot media hand-off",
            f"echo netdispatch :: relay for this subnet: {label}",
            f"chain --replace --autofree {boot_url}/boot?mac=${{netX/mac:hexhyp}}&ip=${{netX/ip}}"
            f"&relay={relay}&arch=${{buildarch}}&platform=${{platform}}" + ("&usbmenu=1" if usb_menu else ""),
            "",
        ]
    )


# ---------------------------------------------------------------- USB sticks


@dataclass(frozen=True)
class StickEntry:
    """An image in a stick's own menu (the stick holds a copy of it)."""

    id: int  # profile entry id
    label: str
    iso_id: int
    windows_setup: bool  # else a plain WinPE: no netdispatch.cmd
    bcd: bool
    boot_sdi: bool


def stick_boot_path(iso_id: int, name: str) -> str:
    """Where an image's wimboot files live on the boot partition."""
    return f"{STICK_DIR}/boot/{iso_id}/{name}"


def stick_entry_path(entry_id: int, name: str) -> str:
    return f"{STICK_DIR}/entry/{entry_id}/{name}"


def render_stick_parent(stick_id: str, stick_name: str, entries: list[StickEntry]) -> str:
    """``EFI/BOOT/autoexec.ipxe`` of a stick, run by the signed ``snponly.efi``.

    It has the stick but no network. Everything the network child needs is
    loaded into memory first, so trying the network again never needs the
    stick: some firmware loses the stick once the child has driven the USB
    controllers (QEMU/OVMF does).
    """
    if not _STICK_ID.match(stick_id):
        raise ValueError(f"bad stick id {stick_id!r}")
    root = "${nd-root}"
    lines = [
        "#!ipxe",
        f"# netdispatch {__version__} USB stick {stick_id} - generated by the hub, do not edit.",
        "# This iPXE (snponly) reads the stick through the firmware and has no network.",
        "# The network is the full iPXE in EFI/BOOT/net, chained as a child: it exits 0",
        "# for a local boot and 1 to come back here.",
        f"set nd-root {STICK_ROOT}",
        f"set nd-stick {stick_id}",
        "set esc:hex 1b",
        "echo ${esc:string}[2J${esc:string}[H",
        f"echo netdispatch :: USB stick {ipxe_text(stick_name)}",
        f"imgfetch -n {_MEM_NET_SCRIPT} {root}/{STICK_DIR}/net.ipxe || goto broken",
        f"imgfetch -n {_MEM_NATIVE_SCRIPT} {root}/{STICK_DIR}/native.ipxe || goto broken",
        f"imgfetch -n {_MEM_NATIVE_EFI} {root}/{EFI_NATIVE_DIR}/ipxe.efi || goto broken",
        f"imgfetch -n {_MEM_SHIM} {root}/{EFI_DIR}/BOOTX64.EFI || goto broken",
        f"imgfetch -n {_MEM_NET_EFI} {root}/{STICK_NET_EFI} || goto broken",
    ]
    if entries:
        lines += [
            "prompt --key l --timeout 3000 netdispatch :: press L for the images on this stick,"
            " or wait for the network && goto menu || goto net",
        ]
    lines += [
        ":net",
        # The shim verifies the iPXE-signed child (it is not signed by Microsoft).
        f"shim {_MEM_SHIM} || goto broken",
        f"chain {_MEM_NET_EFI} && goto local || goto back",
        ":back",
        "shim",
        "echo netdispatch :: no network, or the hub can't be reached",
        "goto menu",
        ":local",
        "echo netdispatch :: booting from local disk",
        "exit 1",
        ":menu",
        f"menu netdispatch :: USB stick {ipxe_text(stick_name)}",
    ]
    if entries:
        lines.append("item --gap -- Images on this stick:")
        for e in entries:
            lines.append(f"item e{e.id} {ipxe_text(e.label)}")
        lines.append("item --gap --")
    lines += [
        "item disk Boot from local disk",
        "item net Use the network",
        "item reboot Restart",
        "item shell iPXE shell",
        # A stick left in after Windows Setup reboots must never restart an install.
        "choose --default disk --timeout 15000 nd-sel || goto local",
        "goto m_${nd-sel}",
        ":m_disk",
        "goto local",
        ":m_net",
        "goto net",
        ":m_reboot",
        "reboot",
        ":m_shell",
        "shell",
        "goto menu",
    ]
    for e in entries:
        lines += [
            f":m_e{e.id}",
            f"echo netdispatch :: starting {ipxe_text(e.label)} from this USB stick",
            # Only the payload's files may be in memory: wimboot injects every
            # image iPXE holds into WinPE.
            "imgfree",
            f"kernel {root}/{STICK_DIR}/wimboot gui || goto entry_failed",
        ]
        if e.bcd:
            lines.append(f"initrd {root}/{stick_boot_path(e.iso_id, 'BCD')} BCD || goto entry_failed")
        if e.boot_sdi:
            lines.append(f"initrd {root}/{stick_boot_path(e.iso_id, 'boot.sdi')} boot.sdi || goto entry_failed")
        if e.windows_setup:
            lines += [
                f"initrd {root}/{STICK_DIR}/winpeshl.ini winpeshl.ini || goto entry_failed",
                f"initrd {root}/{stick_entry_path(e.id, 'netdispatch.cmd')} netdispatch.cmd || goto entry_failed",
            ]
        lines += [
            f"initrd {root}/{stick_boot_path(e.iso_id, 'boot.wim')} boot.wim || goto entry_failed",
            "boot || goto entry_failed",
        ]
    lines += [
        ":entry_failed",
        "echo netdispatch :: could not load the image from this stick.",
        ":lost",
        "echo netdispatch :: If the network was tried first, this PC's firmware may have lost the stick:",
        "echo netdispatch :: restart and press L at the netdispatch prompt to use its images.",
        "prompt --key r --timeout 30000 netdispatch :: press R to restart, or wait to boot from local disk"
        " && goto m_reboot || goto local",
        ":broken",
        "echo netdispatch :: this stick's boot files are incomplete - update it with USB Creator.",
        "goto lost",
        "",
    ]
    return "\n".join(lines)


def render_stick_syslinux_cfg() -> str:
    """SYSLINUX loads BIOS iPXE with the stick's BIOS script (online only)."""
    return (
        "DEFAULT netdispatch\n"
        "PROMPT 0\n"
        "TIMEOUT 0\n"
        "LABEL netdispatch\n"
        "  KERNEL /syslinux/ipxe.lkrn\n"
        f"  INITRD /{STICK_DIR}/bios.ipxe\n"
    )
