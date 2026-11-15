"""Boot media built by the hub: the online-only boot ISO.

The ISO is for virtual media (iDRAC, iLO, IP KVMs) and optical drives. It holds
no images and no secrets: it only gets the machine onto the network and into
the hub's ``/boot`` loop, like a PXE client. It is a few MB, built on request
and cached under ``<data_dir>/usb-cache`` until its inputs change.

Layout:
* BIOS: El Torito no-emulation ISOLINUX, which loads ``ipxe.lkrn`` with the
  BIOS media script as its initrd.
* UEFI: an El Torito FAT image holding the Microsoft-signed iPXE shim as
  ``EFI/BOOT/BOOTX64.EFI``, the iPXE-signed ``ipxe.efi`` it loads, its
  ``autoexec.ipxe``, and in ``EFI/BOOT/native/`` an unsigned iPXE with all of
  iPXE's own NIC drivers (fallback, Secure Boot off only). The FAT image stays well under 32 MB: El Torito records
  its size in 512-byte sectors in a 16-bit field, and EDK2 can't read a larger
  image whose size is recorded as 0.

The ISO is deliberately not isohybrid. Written to a USB stick, firmware treats
a disk with an El Torito catalog as a CD; sticks are made with USB Creator.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .config import Settings
from .usb_scripts import EFI_DIR, EFI_NATIVE_DIR, render_media_script

log = logging.getLogger("netdispatch.usb")

# Files in Settings.usb_assets_dir (see server/Dockerfile).
ASSETS = {
    "shim": "shimx64.efi",  # iPXE project's shim, signed by Microsoft
    "ipxe": "ipxe.efi",  # iPXE-signed: firmware network drivers (SNP/NII) + a few native
    "native": "ipxe-native.efi",  # built from source, all native NIC drivers, unsigned
    "lkrn": "ipxe.lkrn",  # legacy BIOS iPXE, built from source
    "isolinux": "isolinux.bin",
    "ldlinux": "ldlinux.c32",
}
TOOLS = ("xorriso", "mkfs.fat", "mmd", "mcopy")
EFI_IMAGE_KIB = 8 * 1024
ISO_VOLUME_ID = "NETDISPATCH"

ISOLINUX_CFG = """DEFAULT netdispatch
PROMPT 0
TIMEOUT 0
LABEL netdispatch
  KERNEL /ipxe.lkrn
  INITRD /netdispatch.ipxe
"""

README = """netdispatch online boot media
=============================

This disc boots a machine into netdispatch over the network, like a PXE client:
it gets an address by DHCP, detects a netdispatch relay on the subnet (if any)
and waits for instructions from the hub at:

    {hub}

It holds no images and no credentials. Rebuild it from the dashboard
(Boot media) if the hub's boot URL changes.
"""


class MediaError(RuntimeError):
    pass


def missing_requirements(settings: Settings) -> list[str]:
    """Tools and boot files the ISO builder needs that this hub lacks."""
    missing = [t for t in TOOLS if shutil.which(t) is None]
    missing += [
        str(settings.usb_assets_dir / name)
        for name in ASSETS.values()
        if not (settings.usb_assets_dir / name).is_file()
    ]
    return missing


def _run(args: list[str], **kw) -> None:
    env = {**os.environ, "MTOOLS_SKIP_CHECK": "1"}
    proc = subprocess.run(args, capture_output=True, text=True, timeout=300, env=env, **kw)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        raise MediaError(f"{args[0]} failed: {' / '.join(detail) or proc.returncode}")


class OnlineIso:
    """Builds and caches the online-only ISO. One build at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def scripts(self, settings: Settings) -> dict[str, str]:
        hub = settings.boot_url
        return {
            "uefi": render_media_script(hub, efi=True, media="iso"),
            "uefi-native": render_media_script(hub, efi=True, native=True, media="iso"),
            "bios": render_media_script(hub, efi=False, media="iso"),
        }

    def _key(self, settings: Settings, scripts: dict[str, str]) -> str:
        assets = {}
        for name in sorted(ASSETS.values()):
            st = (settings.usb_assets_dir / name).stat()
            assets[name] = [st.st_size, int(st.st_mtime)]
        blob = json.dumps({"scripts": scripts, "assets": assets, "v": 1}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def get(self, settings: Settings) -> Path:
        """Path of a current ISO, building it if needed."""
        missing = missing_requirements(settings)
        if missing:
            raise MediaError("missing: " + ", ".join(missing))
        scripts = self.scripts(settings)
        cache = settings.usb_cache_dir
        out = cache / f"online-{self._key(settings, scripts)}.iso"
        if out.is_file():
            return out
        with self._lock:
            if out.is_file():
                return out
            cache.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=cache, prefix=".build-") as tmp:
                part = Path(tmp) / "online.iso"
                self._build(settings, scripts, Path(tmp), part)
                os.replace(part, out)
            for old in cache.glob("online-*.iso"):
                if old != out:
                    old.unlink(missing_ok=True)
            log.info("built online boot ISO %s (%d bytes)", out.name, out.stat().st_size)
            return out

    def _build(self, settings: Settings, scripts: dict[str, str], work: Path, out: Path) -> None:
        assets = {k: settings.usb_assets_dir / v for k, v in ASSETS.items()}
        tree = work / "tree"
        (tree / "isolinux").mkdir(parents=True)
        shutil.copyfile(assets["isolinux"], tree / "isolinux" / "isolinux.bin")
        shutil.copyfile(assets["ldlinux"], tree / "isolinux" / "ldlinux.c32")
        (tree / "isolinux" / "isolinux.cfg").write_text(ISOLINUX_CFG)
        shutil.copyfile(assets["lkrn"], tree / "ipxe.lkrn")
        (tree / "netdispatch.ipxe").write_text(scripts["bios"])
        (tree / "README.TXT").write_text(README.format(hub=settings.boot_url).replace("\n", "\r\n"))

        # The UEFI El Torito image: a small FAT filesystem built without mounting.
        efi_img = tree / "efiboot.img"
        _run(["mkfs.fat", "-C", "-n", "NDBOOT", str(efi_img), str(EFI_IMAGE_KIB)])
        staged = work / "efi"
        staged.mkdir()
        files = {
            f"{EFI_DIR}/BOOTX64.EFI": assets["shim"],
            f"{EFI_DIR}/ipxe.efi": assets["ipxe"],
            f"{EFI_NATIVE_DIR}/ipxe.efi": assets["native"],
        }
        scripts_out = {
            f"{EFI_DIR}/autoexec.ipxe": scripts["uefi"],
            f"{EFI_NATIVE_DIR}/autoexec.ipxe": scripts["uefi-native"],
        }
        img = ["-i", str(efi_img)]
        _run(["mmd", *img, "::/EFI", f"::/{EFI_DIR}", f"::/{EFI_NATIVE_DIR}"])
        for dest, src in files.items():
            _run(["mcopy", *img, str(src), f"::/{dest}"])
        for n, (dest, text) in enumerate(scripts_out.items()):
            p = staged / f"script{n}"
            p.write_text(text)
            _run(["mcopy", *img, str(p), f"::/{dest}"])

        _run(
            [
                "xorriso", "-as", "mkisofs", "-quiet",
                "-o", str(out),
                "-V", ISO_VOLUME_ID,
                "-J", "-R",
                "-b", "isolinux/isolinux.bin",
                "-c", "isolinux/boot.cat",
                "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table",
                "-eltorito-alt-boot",
                "-e", "efiboot.img",
                "-no-emul-boot",
                str(tree),
            ]
        )
