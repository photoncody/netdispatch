"""Boot media: /usb/boot relay detection, media scripts, the online-only ISO."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import Settings
from app.usb_media import OnlineIso, missing_requirements
from app.usb_scripts import check_hub_url, render_media_handoff, render_media_script

from .conftest import BOOTSTRAP_KEY, make_settings

HB = {"relay_id": "site-a", "name": "Site A", "ip_address": "10.0.0.2", "subnet": "10.0.0.0/24", "dnsmasq_ok": True}
MAC = "52-54-00-aa-00-07"


def beat(client, **over):
    hb = dict(HB, **over)
    r = client.post("/api/relays/heartbeat", json=hb, headers={"X-Relay-Key": BOOTSTRAP_KEY})
    assert r.status_code == 200, r.text


@pytest.fixture
def usb_tools():
    """The real boot files and tools, as installed in the server image. In the Docker
    test stage NETDISPATCH_REQUIRE_USB_TOOLS=1 turns the skip into a failure."""
    missing = missing_requirements(Settings(_env_file=None))
    if missing:
        if os.environ.get("NETDISPATCH_REQUIRE_USB_TOOLS") == "1":
            pytest.fail(f"boot media tools missing: {missing}")
        pytest.skip(f"boot media tools missing: {missing}")


# ---------------------------------------------------------------- /usb/boot


def test_usb_boot_needs_no_session(client):
    r = client.get(f"/usb/boot?mac={MAC}&ip=10.9.9.9&relay_ip=&media=iso")
    assert r.status_code == 200
    assert r.text.startswith("#!ipxe")
    assert "chain --replace --autofree http://10.0.0.5:8000/boot?mac=${netX/mac:hexhyp}" in r.text
    assert "&relay=-&" in r.text  # no relay on this subnet


def test_usb_boot_picks_relay_by_proxydhcp_server(client):
    beat(client)
    beat(client, relay_id="site-b", name="Site B", ip_address="10.1.0.2", subnet="10.1.0.0/24")
    # The ProxyDHCP server identifies the relay even when the client address doesn't.
    r = client.get(f"/usb/boot?mac={MAC}&ip=192.168.1.50&relay_ip=10.1.0.2")
    assert "&relay=site-b&" in r.text
    assert "relay for this subnet: Site B (site-b)" in r.text


def test_usb_boot_picks_relay_by_subnet(client, admin):
    beat(client)
    beat(client, relay_id="wide", name="Wide", ip_address="10.0.9.9", subnet="10.0.0.0/16")
    beat(client, relay_id="junk", name="Junk", ip_address="10.0.9.8", subnet="not a subnet")
    beat(client, relay_id="all", name="All", ip_address="10.0.9.7", subnet="0.0.0.0/0")
    # most specific subnet wins; a bogus or catch-all subnet never matches
    assert "&relay=site-a&" in client.get(f"/usb/boot?mac={MAC}&ip=10.0.0.77").text
    assert "&relay=wide&" in client.get(f"/usb/boot?mac={MAC}&ip=10.0.200.1").text
    assert "&relay=-&" in client.get(f"/usb/boot?mac={MAC}&ip=172.16.0.1").text
    # disabled relays are skipped
    admin.patch("/api/relays/site-a", json={"enabled": False})
    assert "&relay=wide&" in client.get(f"/usb/boot?mac={MAC}&ip=10.0.0.77").text


def test_usb_boot_sanitises_relay_name(client):
    beat(client, name="Evil ${x} && shell")
    r = client.get(f"/usb/boot?mac={MAC}&relay_ip=10.0.0.2")
    assert "${x}" not in r.text and "&& shell" not in r.text


def test_boot_relay_dash_clears_previous_relay(client, admin):
    beat(client)
    client.get(f"/boot?mac={MAC}&relay=site-a")
    dev = admin.get("/api/devices").json()[0]
    assert dev["relay_id"] == "site-a"
    # The same machine later boots media on a subnet without a relay.
    r = client.get(f"/boot?mac={MAC}&relay=-")
    assert "&relay=&" in r.text  # later polls carry no relay
    assert admin.get("/api/devices").json()[0]["relay_id"] is None


# ---------------------------------------------------------------- media scripts


def test_media_script_uefi():
    s = render_media_script("http://10.0.0.5:8000/", efi=True)
    assert s.startswith("#!ipxe\n")
    assert "set nd-hub http://10.0.0.5:8000\n" in s
    assert "${nd-hub}/usb/boot?mac=${netX/mac:hexhyp}&ip=${netX/ip}&relay_ip=${proxydhcp/dhcp-server}" in s
    # invariant 2: a local boot fails out of the boot entry
    assert "isset ${nd-local} && goto local || goto hub_failed" in s
    assert ":local\necho netdispatch :: booting from local disk\nexit 1\n" in s
    # no firmware network device: chain the native-driver iPXE
    assert "imgfetch -n autoexec.ipxe file:/EFI/BOOT/native/autoexec.ipxe" in s
    assert "chain file:/EFI/BOOT/native/ipxe.efi || goto native_done" in s
    # no trailing || (AGENTS.md gotcha)
    assert not any(line.rstrip().endswith("||") for line in s.splitlines())


def test_media_script_uefi_native_does_not_loop():
    s = render_media_script("http://hub", efi=True, native=True)
    assert "EFI/BOOT/native" not in s
    assert "exit 1" in s


def test_media_script_bios_boots_first_disk():
    s = render_media_script("https://pxe.example.com", efi=False)
    assert "sanboot --no-describe --drive 0x80" in s
    assert "exit 1" not in s and "file:" not in s


@pytest.mark.parametrize("bad", ["ftp://hub", "http://hub/${x}", "http://hub/a b", "http://hub/a&&b", ""])
def test_hub_url_checked(bad):
    with pytest.raises(ValueError):
        check_hub_url(bad)


def test_handoff_without_relay():
    s = render_media_handoff("http://hub", None, None)
    assert "&relay=-&" in s and "none (direct to the hub)" in s


# ---------------------------------------------------------------- online ISO


def test_online_iso_needs_a_session(client):
    r = client.get("/api/usb/online.iso", follow_redirects=False)
    assert r.status_code in (401, 302, 303)


def test_online_iso_missing_tools_is_503(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    from .conftest import ADMIN_PW, HDR

    settings = make_settings(tmp_path, usb_assets_dir=tmp_path / "nothing-here")
    with TestClient(create_app(settings), base_url="https://testserver") as c:
        c.post("/login", data={"username": "admin", "password": ADMIN_PW, "next": "/"})
        c.headers.update(HDR)
        info = c.get("/api/usb/info").json()
        assert info["online_iso"] is False and info["problems"]
        r = c.get("/api/usb/online.iso")
        assert r.status_code == 503 and "can't build the boot ISO" in r.json()["detail"]


def _report(iso: Path) -> str:
    return subprocess.run(
        ["xorriso", "-indev", str(iso), "-report_el_torito", "plain"],
        capture_output=True, text=True, check=True,
    ).stdout


def test_online_iso_build(usb_tools, admin, settings, tmp_path):
    r = admin.get("/api/usb/online.iso")
    assert r.status_code == 200, r.text
    assert "netdispatch-online.iso" in r.headers["content-disposition"]
    iso = tmp_path / "online.iso"
    iso.write_bytes(r.content)
    assert iso.stat().st_size < 16 * 1024 * 1024

    report = _report(iso)
    assert "BIOS" in report and "UEFI" in report
    # El Torito records the EFI image size in 512-byte sectors in 16 bits; 0 there
    # ("whole rest of the disc") is unreadable for EDK2. Ours must be explicit.
    efi_line = next(line for line in report.splitlines() if line.startswith("El Torito boot img :   2"))
    assert int(efi_line.split()[-2]) > 0

    # Pull the files back out and check the EFI image's contents.
    out = tmp_path / "x"
    out.mkdir()
    subprocess.run(["xorriso", "-osirrox", "on", "-indev", str(iso), "-extract", "/", str(out)],
                   capture_output=True, check=True)
    assert (out / "isolinux" / "isolinux.cfg").read_text().count("INITRD /netdispatch.ipxe") == 1
    assert "sanboot --no-describe --drive 0x80" in (out / "netdispatch.ipxe").read_text()
    efi = out / "efiboot.img"
    assert efi.stat().st_size < 32 * 1024 * 1024
    env = {**os.environ, "MTOOLS_SKIP_CHECK": "1"}
    listing = subprocess.run(["mdir", "-/", "-b", "-i", str(efi), "::/"], capture_output=True, text=True,
                             env=env, check=True).stdout.lower()
    for name in ("efi/boot/bootx64.efi", "efi/boot/ipxe.efi", "efi/boot/autoexec.ipxe",
                 "efi/boot/native/ipxe.efi", "efi/boot/native/autoexec.ipxe"):
        assert name in listing, name
    snp = subprocess.run(["mtype", "-i", str(efi), "::/EFI/BOOT/autoexec.ipxe"], capture_output=True, text=True,
                         env=env, check=True).stdout
    assert "set nd-hub http://10.0.0.5:8000" in snp and "EFI/BOOT/native/ipxe.efi" in snp
    # next to the shim: the signed build, byte for byte
    signed = subprocess.run(["mtype", "-i", str(efi), "::/EFI/BOOT/ipxe.efi"], capture_output=True,
                            env=env, check=True).stdout
    assert signed == (Settings(_env_file=None).usb_assets_dir / "ipxe.efi").read_bytes()
    shutil.rmtree(out)


def test_online_iso_is_cached_and_rebuilt_on_change(usb_tools, tmp_path):
    builder = OnlineIso()
    s1 = make_settings(tmp_path, usb_assets_dir=Settings(_env_file=None).usb_assets_dir)
    first = builder.get(s1)
    assert builder.get(s1) == first
    s2 = make_settings(tmp_path, usb_assets_dir=s1.usb_assets_dir, boot_base_url="http://10.0.0.6:8000")
    second = builder.get(s2)
    assert second != first and second.is_file() and not first.exists()

