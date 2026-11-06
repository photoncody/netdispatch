from __future__ import annotations

import pytest

from app.iso_tools import detect_layout, resolve_ci, sanitize_filename
from app.models import IsoImage
from app.pxe_engine import BootContext, ipxe_text, normalize_mac, render_payload

from .conftest import make_tree


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("52-54-00-ab-cd-01", "52:54:00:ab:cd:01"),
        ("52:54:00:AB:CD:01", "52:54:00:ab:cd:01"),
        ("5254.00ab.cd01", "52:54:00:ab:cd:01"),
        ("525400abcd01", "52:54:00:ab:cd:01"),
        ("5254", None),
        ("", None),
    ],
)
def test_normalize_mac(raw, expected):
    assert normalize_mac(raw) == expected


def test_ipxe_text_strips_specials():
    assert ipxe_text("a${b}&&c||d#e") == "a??b???c??d?e"
    assert ipxe_text(None) == "-"


def test_sanitize_filename():
    assert sanitize_filename("../../My Win 11.iso") == "My_Win_11.iso"
    with pytest.raises(ValueError):
        sanitize_filename("evil.sh")


def _iso(**kw) -> IsoImage:
    iso = IsoImage(id=7, name="img", filename="img.iso", **kw)
    return iso


CTX_EFI = BootContext(base_url="http://hub", mac="52:54:00:00:00:01", platform="efi", hostname="h1")
CTX_BIOS = BootContext(base_url="http://hub", mac="52:54:00:00:00:01", platform="pcbios")


def test_linux_payload_efi_and_placeholders():
    iso = _iso(boot_method="linux", kernel_path="casper/vmlinuz", cmdline="url={iso_url} host={hostname} x={files_url}")
    iso.initrd_paths = ["casper/initrd"]
    s = render_payload(CTX_EFI, iso, "tok")
    assert "kernel http://hub/files/iso/7/tree/casper/vmlinuz initrd=initrd.magic url=http://hub/files/iso/7/raw/img.iso host=h1 x=http://hub/files/iso/7/tree || goto failed" in s
    assert "initrd http://hub/files/iso/7/tree/casper/initrd || goto failed" in s
    assert "result=failed" in s


def test_linux_payload_bios_has_no_initrd_magic():
    iso = _iso(boot_method="linux", kernel_path="k", cmdline="quiet")
    iso.initrd_paths = ["i"]
    assert "initrd.magic" not in render_payload(CTX_BIOS, iso, "tok")


def test_memdisk_falls_back_to_sanboot_on_efi():
    iso = _iso(boot_method="memdisk")
    assert "files/boot/memdisk iso raw" in render_payload(CTX_BIOS, iso, "tok")
    s = render_payload(CTX_EFI, iso, "tok")
    assert "sanboot --no-describe http://hub/files/iso/7/raw/img.iso" in s


def test_winpe_has_no_setup_files():
    iso = _iso(boot_method="winpe", bcd_path="boot/bcd", boot_sdi_path="boot/boot.sdi", boot_wim_path="sources/boot.wim")
    s = render_payload(CTX_EFI, iso, "tok", has_unattend=True)
    assert "wimboot" in s and "boot.wim" in s
    assert "netdispatch.cmd" not in s and "autounattend" not in s


def test_resolve_ci_and_traversal(tmp_path):
    make_tree(tmp_path / "root", ["Boot/BCD", "sources/boot.wim"])
    (tmp_path / "secret").write_text("s")
    root = tmp_path / "root"
    assert resolve_ci(root, "boot/bcd") == root / "Boot" / "BCD"
    assert resolve_ci(root, "../secret") is None
    assert resolve_ci(root, "/etc/passwd") is None
    assert resolve_ci(root, "boot") is None  # directories are not files


@pytest.mark.parametrize(
    "files,method,os_name,kernel",
    [
        (["boot/bcd", "boot/boot.sdi", "sources/boot.wim", "sources/install.wim", "setup.exe"], "windows_setup", "Windows Setup", None),
        (["boot/bcd", "boot/boot.sdi", "sources/boot.wim", "HBCD_PE.ini"], "winpe", "Hiren's BootCD PE", None),
        (["casper/vmlinuz", "casper/initrd"], "linux", "Ubuntu / casper live", "casper/vmlinuz"),
        (["images/pxeboot/vmlinuz", "images/pxeboot/initrd.img", ".treeinfo"], "linux", "Fedora / RHEL installer", "images/pxeboot/vmlinuz"),
        (["images/pxeboot/vmlinuz", "images/pxeboot/initrd.img", "LiveOS/squashfs.img"], "linux", "Fedora / RHEL live", "images/pxeboot/vmlinuz"),
        (["arch/boot/x86_64/vmlinuz-linux", "arch/boot/x86_64/initramfs-linux.img"], "linux", "Arch Linux", "arch/boot/x86_64/vmlinuz-linux"),
        (["live/vmlinuz-6.1.0-18-amd64", "live/initrd.img-6.1.0-18-amd64", "live/filesystem.squashfs"], "linux", "Debian live", "live/vmlinuz-6.1.0-18-amd64"),
        ([".alpine-release", "boot/vmlinuz-virt", "boot/initramfs-virt", "boot/modloop-virt", "apks/.boot_repository"], "linux", "Alpine Linux (virt)", "boot/vmlinuz-virt"),
        (["README.TXT", "other/thing.bin"], "sanboot", "Unknown (raw ISO via sanboot)", None),
    ],
)
def test_detect_layout(tmp_path, files, method, os_name, kernel):
    make_tree(tmp_path, files)
    det = detect_layout(tmp_path)
    assert det.boot_method == method
    assert det.detected_os == os_name
    assert det.kernel_path == kernel


def test_detect_alpine_cmdline(tmp_path):
    make_tree(tmp_path, [".alpine-release", "boot/vmlinuz-lts", "boot/initramfs-lts", "boot/modloop-lts", "apks/.boot_repository"])
    det = detect_layout(tmp_path)
    assert det.initrd_paths == ["boot/initramfs-lts"]
    assert "alpine_repo={files_url}/apks" in det.cmdline
    assert "modloop={files_url}/boot/modloop-lts" in det.cmdline
