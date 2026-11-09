"""LINUX_FETCH_URL: plain-HTTP base for initramfs downloads ({iso_url} / {files_url})."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.iso_tools import detect_layout
from app.main import create_app
from app.models import ISO_READY, IsoImage
from app.pxe_engine import BootContext, https_fetch_warning, render_payload

from .conftest import ADMIN_PW, HDR, make_settings, make_tree

MAC = "52-54-00-AB-CD-09"
NORM = "52:54:00:ab:cd:09"
Q = f"/boot?mac={MAC}&ip=10.0.0.42&relay=site-a&arch=x86_64&platform=efi"
CASPER_FILES = ["casper/vmlinuz", "casper/initrd", "casper/filesystem.squashfs", "md5sum.txt"]


def _iso(cmdline: str) -> IsoImage:
    iso = IsoImage(id=7, name="mint", filename="mint.iso", boot_method="linux", kernel_path="casper/vmlinuz", cmdline=cmdline)
    iso.initrd_paths = ["casper/initrd"]
    return iso


# --- settings -----------------------------------------------------------------


def test_fetch_url_defaults_to_boot_url(tmp_path):
    s = make_settings(tmp_path, boot_base_url="https://pxe.example.com/")
    assert s.linux_fetch_url == ""
    assert s.effective_linux_fetch_url == "https://pxe.example.com"


def test_fetch_url_override_strips_slash(tmp_path):
    s = make_settings(tmp_path, linux_fetch_url=" http://10.0.0.5:8000/ ")
    assert s.effective_linux_fetch_url == "http://10.0.0.5:8000"
    assert s.boot_url == "http://10.0.0.5:8000"  # unchanged by the new setting


# --- engine -------------------------------------------------------------------


def test_placeholders_use_fetch_url_but_ipxe_downloads_do_not():
    ctx = BootContext(
        base_url="https://pxe.example.com",
        fetch_base_url="http://10.0.0.5:8000",
        mac="52:54:00:00:00:01",
        platform="efi",
    )
    iso = _iso("boot=casper url={iso_url} x={files_url} b={base_url}")
    s = render_payload(ctx, iso, "tok")
    assert (
        "kernel https://pxe.example.com/files/iso/7/tree/casper/vmlinuz initrd=initrd.magic boot=casper "
        "url=http://10.0.0.5:8000/files/iso/7/raw/mint.iso x=http://10.0.0.5:8000/files/iso/7/tree "
        "b=https://pxe.example.com || goto failed"
    ) in s
    assert "initrd https://pxe.example.com/files/iso/7/tree/casper/initrd || goto failed" in s
    assert "chain --replace --autofree https://pxe.example.com/boot?" in s
    assert "WARNING" not in s


def test_fetch_url_falls_back_to_base_url():
    ctx = BootContext(base_url="http://hub", mac="52:54:00:00:00:01")
    assert ctx.fetch_url == "http://hub"
    assert "url=http://hub/files/iso/7/raw/mint.iso" in render_payload(ctx, _iso("boot=casper url={iso_url}"), "t")


@pytest.mark.parametrize(
    "cmdline,warns",
    [
        ("boot=casper ip=dhcp url=https://hub/files/iso/1/raw/x.iso", True),
        ("BOOT=casper URL=HTTPS://hub/x.iso", True),
        ("boot=casper ip=dhcp url=http://hub/files/iso/1/raw/x.iso", False),
        ("boot=live components fetch=https://hub/files/iso/1/tree/live/filesystem.squashfs", True),
        ("boot=live components fetch=http://hub/files/iso/1/tree/live/filesystem.squashfs", False),
        # dracut / archiso download with curl, which has TLS
        ("root=live:https://hub/files/iso/1/tree/LiveOS/squashfs.img rd.live.image", False),
        ("archisobasedir=arch archiso_http_srv=https://hub/files/iso/1/tree/", False),
        # url= alone is not casper
        ("url=https://hub/x.iso", False),
    ],
)
def test_https_fetch_warning(cmdline, warns):
    assert (https_fetch_warning(cmdline) is not None) is warns


def test_warning_echoed_in_script_when_casper_fetches_https():
    ctx = BootContext(base_url="https://pxe.example.com", mac="52:54:00:00:00:01")
    s = render_payload(ctx, _iso("boot=casper url={iso_url}"), "tok")
    warn = next(line for line in s.splitlines() if "WARNING" in line)
    assert warn.startswith("echo netdispatch: WARNING: casper downloads url= with busybox wget")
    assert "LINUX_FETCH_URL" in warn
    assert not any(c in warn for c in "${}&|#\"'")
    assert s.index(warn) < s.index("kernel ")


# --- end to end through /boot ---------------------------------------------------


def _client(tmp_path, **overrides):
    settings = make_settings(tmp_path, boot_base_url="https://pxe.example.com", **overrides)
    app = create_app(settings)
    return settings, app


def _casper_iso(app, settings) -> int:
    src = settings.data_dir / "casper-src"
    make_tree(src, CASPER_FILES)
    det = detect_layout(src)
    with app.state.sessionmaker() as db:
        iso = IsoImage(
            name="Linux Mint 22.3",
            filename="linuxmint-22.3-cinnamon-64bit.iso",
            size=10,
            status=ISO_READY,
            boot_method=det.boot_method,
            kernel_path=det.kernel_path,
            cmdline=det.cmdline,
        )
        iso.initrd_paths = det.initrd_paths
        db.add(iso)
        db.commit()
        iso_id = iso.id
    make_tree(settings.extracted_dir / str(iso_id), CASPER_FILES)
    return iso_id


def _deploy(c: TestClient, iso_id: int) -> dict:
    c.get(Q)
    r = c.post("/login", data={"username": "admin", "password": ADMIN_PW, "next": "/"}, follow_redirects=False)
    assert r.status_code == 303
    c.headers.update(HDR)
    dev = next(d for d in c.get("/api/devices").json() if d["mac"] == NORM)
    r = c.post(f"/api/devices/{dev['id']}/deploy", json={"iso_id": iso_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_casper_cmdline_uses_http_fetch_url(tmp_path):
    settings, app = _client(tmp_path, linux_fetch_url="http://10.0.0.5:8000")
    iso_id = _casper_iso(app, settings)
    with TestClient(app, base_url="https://testserver") as c:
        out = _deploy(c, iso_id)
        assert "warning" not in out
        preview = c.get(f"/api/devices/{out['id']}/script").text
        s = c.get(Q).text
    assert preview == s
    kernel = next(line for line in s.splitlines() if line.startswith("kernel "))
    assert kernel.startswith(f"kernel https://pxe.example.com/files/iso/{iso_id}/tree/casper/vmlinuz ")
    assert f"url=http://10.0.0.5:8000/files/iso/{iso_id}/raw/linuxmint-22.3-cinnamon-64bit.iso" in kernel
    assert f"initrd https://pxe.example.com/files/iso/{iso_id}/tree/casper/initrd" in s
    assert "WARNING" not in s
    # the plain-HTTP base only ever points at the unauthenticated image routes
    for chunk in s.split("http://10.0.0.5:8000")[1:]:
        assert chunk.startswith(f"/files/iso/{iso_id}/")


def test_casper_over_https_warns_on_deploy_and_in_script(tmp_path):
    settings, app = _client(tmp_path)
    iso_id = _casper_iso(app, settings)
    with TestClient(app, base_url="https://testserver") as c:
        out = _deploy(c, iso_id)
        s = c.get(Q).text
    assert "busybox wget" in out["warning"] and "LINUX_FETCH_URL" in out["warning"]
    assert f"url=https://pxe.example.com/files/iso/{iso_id}/raw/" in s
    assert "echo netdispatch: WARNING: casper downloads url=" in s


def test_windows_deploy_has_no_fetch_warning(admin, client, windows_iso):
    client.get(Q)
    dev = next(d for d in admin.get("/api/devices").json() if d["mac"] == NORM)
    out = admin.post(f"/api/devices/{dev['id']}/deploy", json={"iso_id": windows_iso}).json()
    assert "warning" not in out
