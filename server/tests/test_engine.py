from __future__ import annotations

from pathlib import Path

import pytest

from app import iso_tools
from app.iso_tools import detect_layout, extract_iso, resolve_ci, sanitize_filename
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
        ([".pve-cd-id.txt", "boot/linux26", "boot/initrd.img"], "linux", "Proxmox installer", "boot/linux26"),
        (["boot/linux26", "boot/initrd.img"], "sanboot", "Unknown (raw ISO via sanboot)", None),
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


def test_detect_proxmox_loads_the_iso_into_the_initramfs(tmp_path):
    make_tree(tmp_path, [".pve-cd-id.txt", "boot/linux26", "boot/initrd.img"])
    (tmp_path / ".cd-info").write_text("RELEASE='9.2'\nISONAME='proxmox-ve'\nPRODUCTLONG='Proxmox VE'\n")
    det = detect_layout(tmp_path)
    assert det.detected_os == "Proxmox VE 9.2 installer"
    assert det.initrd_paths == ["boot/initrd.img", "{iso} /proxmox.iso"]
    assert "rootfstype=ramfs" in det.cmdline


def test_linux_payload_raw_iso_initrd():
    iso = _iso(boot_method="linux", kernel_path="boot/linux26", cmdline="quiet")
    iso.initrd_paths = ["boot/initrd.img", "{iso} /proxmox.iso", "{iso} a${b}&&c.iso", "{iso}"]
    s = render_payload(CTX_EFI, iso, "tok")
    assert "initrd http://hub/files/iso/7/tree/boot/initrd.img || goto failed" in s
    assert "initrd http://hub/files/iso/7/raw/img.iso /proxmox.iso || goto failed" in s
    assert "initrd http://hub/files/iso/7/raw/img.iso abc.iso || goto failed" in s  # name is sanitised
    assert "initrd http://hub/files/iso/7/raw/img.iso /image.iso || goto failed" in s
    cached = BootContext(base_url="http://hub", mac="52:54:00:00:00:01", cache_base_url="http://relay:8080")
    assert "initrd http://relay:8080/raw/7/img.iso /proxmox.iso || goto failed" in render_payload(cached, iso, "tok")


class _Fake7z:
    """Stands in for 7-Zip on an ISO whose symbolic links it refuses."""

    def __init__(self, links, extra_stderr=""):
        self.links = links
        self.extra_stderr = extra_stderr

    def __call__(self, cmd, **_kw):
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        res = Result()
        if cmd[1] == "l":
            res.stdout = "".join(
                f"Path = {path}\nFolder = -\nSize = 0\nSymbolic Link = {target}\n\n" for path, target in self.links
            ) + "Path = boot/linux26\nFolder = -\nSymbolic Link = \n\n"
            return res
        out = Path(next(a for a in cmd if a.startswith("-o"))[2:])
        make_tree(out, ["boot/linux26", "proxmox/packages/a.deb", "dists/pve/Packages"])
        (out / "empty-on-purpose").write_bytes(b"")
        for path, target in self.links:
            (out / path).parent.mkdir(parents=True, exist_ok=True)
            (out / path).write_bytes(b"")  # the placeholder 7-Zip leaves behind
            res.stderr += f"ERROR: Dangerous link path was ignored : {path} : {target}\n"
        res.stderr += self.extra_stderr
        res.returncode = 2 if res.stderr else 0
        return res


def test_extract_restores_links_7zip_refuses(tmp_path, monkeypatch):
    (tmp_path / "secret").write_text("s")
    links = [
        ("dists/pve/a.deb", "../../proxmox/packages/a.deb"),
        ("chain.deb", "dists/pve/./a.deb"),  # a link to a link listed before its target exists
        ("debian", "./"),  # a directory that contains itself
        ("dists/up", ".."),
        ("dangling", "./missing"),
        ("escape", "../secret"),
        ("absolute", "/etc/passwd"),
    ]
    monkeypatch.setattr(iso_tools, "seven_zip", lambda: "7z")
    monkeypatch.setattr(iso_tools.subprocess, "run", _Fake7z(links))
    dest = tmp_path / "out" / "7"
    dest.parent.mkdir()
    assert extract_iso(tmp_path / "x.iso", dest) == 6
    assert (dest / "dists/pve/a.deb").is_symlink()
    assert resolve_ci(dest, "dists/pve/a.deb").read_bytes() == b"x"
    assert resolve_ci(dest, "chain.deb").read_bytes() == b"x"
    for gone in ("debian", "dists/up", "dangling", "escape", "absolute"):
        assert not (dest / gone).is_symlink() and not (dest / gone).exists(), gone
    assert (dest / "empty-on-purpose").is_file()  # only placeholders are removed


def test_extract_still_fails_on_other_7zip_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(iso_tools, "seven_zip", lambda: "7z")
    monkeypatch.setattr(
        iso_tools.subprocess, "run",
        _Fake7z([("a.deb", "../x")], extra_stderr="ERROR: Data Error : boot/linux26\n"),
    )
    dest = tmp_path / "out" / "7"
    dest.parent.mkdir()
    with pytest.raises(iso_tools.ExtractionError):
        extract_iso(tmp_path / "x.iso", dest)
    assert not dest.exists()
