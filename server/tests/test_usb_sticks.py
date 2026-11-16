"""USB sticks: pairing USB Creator, profiles, stick manifests and layouts, the
boot template, and the stick's boot flow through /usb/boot and /boot."""

from __future__ import annotations

import gzip
import os
import re
import subprocess
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.models import ISO_READY, IsoImage, UsbPairing, utcnow
from app.unattend import render
from app.usb_scripts import StickEntry, StickRef, render_media_script, render_stick_parent
from app.usb_sticks import HOSTNAME_MARKER, fat_used_bytes, offline_unattend_parts, template_missing
from app.winpe import render_stick_cmd

from .conftest import make_tree

APP = "/api/usb/app"
UA = """<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
  <settings pass="specialize"><component name="Microsoft-Windows-Shell-Setup">
    <ComputerName>{{ hostname }}</ComputerName>
    <RegisteredOwner>{{ hostname }} on {{ image }}</RegisteredOwner>
    <Mac>{{ mac }}</Mac>
  </component></settings>
</unattend>
"""


# ---------------------------------------------------------------- helpers


def pair(client, admin, name="Desk PC") -> str:
    r = client.post(f"{APP}/pair/start", json={"name": "USB Creator on DESK"})
    assert r.status_code == 200, r.text
    code, token = r.json()["code"], r.json()["poll_token"]
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", code)
    assert client.post(f"{APP}/pair/poll", json={"poll_token": token}).json() == {"status": "pending"}
    r = admin.post(f"/api/usb/pairings/{code.lower()}/approve", json={"name": name})
    assert r.status_code == 200, r.text
    r = client.post(f"{APP}/pair/poll", json={"poll_token": token})
    assert r.json()["status"] == "approved"
    return r.json()["creator_key"]


def winpe_iso(app, settings) -> int:
    with app.state.sessionmaker() as db:
        row = IsoImage(
            name="Rescue PE", filename="pe.iso", size=1, status=ISO_READY, boot_method="winpe",
            boot_wim_path="sources/boot.wim",
        )
        db.add(row)
        db.commit()
        iso_id = row.id
    make_tree(settings.extracted_dir / str(iso_id), ["sources/boot.wim", "tools/x.exe"])
    return iso_id


def unattend(admin, content=UA, name="Offline") -> int:
    r = admin.post("/api/unattend", json={"name": name, "content": content})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def profile(admin, entries, name="Lab") -> dict:
    r = admin.post("/api/usb/profiles", json={"name": name, "entries": entries})
    assert r.status_code == 201, r.text
    return r.json()


def stick(client, ckey, profile_id, name="Stick 1") -> tuple[str, str]:
    r = client.post(f"{APP}/sticks", json={"name": name, "profile_id": profile_id}, headers={"X-Creator-Key": ckey})
    assert r.status_code == 201, r.text
    return r.json()["stick"]["id"], r.json()["stick_key"]


@pytest.fixture
def setup(client, admin, windows_iso):
    ua = unattend(admin)
    prof = profile(admin, [{"iso_id": windows_iso, "unattend_id": ua, "label": "Windows 11"}])
    ckey = pair(client, admin)
    sid, skey = stick(client, ckey, prof["id"])
    return {"ua": ua, "profile": prof, "ckey": ckey, "sid": sid, "skey": skey, "iso": windows_iso}


# ---------------------------------------------------------------- pairing


def test_pairing_hands_out_the_key_once(client, admin):
    r = client.post(f"{APP}/pair/start", json={})
    token = r.json()["poll_token"]
    code = r.json()["code"]
    listed = admin.get("/api/usb/creators").json()["pairings"]
    assert [p["code"] for p in listed] == [code] and not listed[0]["approved"]
    admin.post(f"/api/usb/pairings/{code}/approve", json={"name": "Laptop"})
    key = client.post(f"{APP}/pair/poll", json={"poll_token": token}).json()["creator_key"]
    assert key.startswith("ndc_")
    # Once collected, the pairing is gone.
    assert client.post(f"{APP}/pair/poll", json={"poll_token": token}).status_code == 404
    creators = admin.get("/api/usb/creators").json()
    assert creators["pairings"] == [] and [c["name"] for c in creators["creators"]] == ["Laptop"]
    assert client.get(f"{APP}/profiles", headers={"X-Creator-Key": key}).status_code == 200


def test_pairing_expires_and_is_limited(client, admin, app):
    token = client.post(f"{APP}/pair/start", json={}).json()["poll_token"]
    with app.state.sessionmaker() as db:
        for p in db.query(UsbPairing):
            p.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.post(f"{APP}/pair/poll", json={"poll_token": token}).status_code == 404
    codes = [client.post(f"{APP}/pair/start", json={}) for _ in range(6)]
    assert [r.status_code for r in codes] == [200] * 5 + [429]  # per-address limit
    assert admin.post("/api/usb/pairings/ZZZZ-ZZZZ/approve", json={"name": "x"}).status_code == 404


def test_app_endpoints_need_keys(client, admin, setup):
    sid = setup["sid"]
    assert client.get(f"{APP}/profiles").status_code == 401
    assert client.get(f"{APP}/profiles", headers={"X-Creator-Key": "ndc_wrong"}).status_code == 401
    assert client.get(f"{APP}/sticks/{sid}/manifest").status_code == 401
    assert client.get(f"{APP}/sticks/{sid}/manifest", headers={"X-Stick-Key": setup["skey"]}).status_code == 200
    assert client.get(f"{APP}/sticks/{sid}/manifest", headers={"X-Creator-Key": setup["ckey"]}).status_code == 200
    # A stick's key opens only that stick.
    sid2, _ = stick(client, setup["ckey"], setup["profile"]["id"], "Stick 2")
    assert client.get(f"{APP}/sticks/{sid2}/manifest", headers={"X-Stick-Key": setup["skey"]}).status_code == 401
    # The session doesn't stand in for a key, and the admin API needs a session.
    assert admin.get(f"{APP}/sticks/{sid}/manifest").status_code == 401
    anon = TestClient(client.app, base_url="https://testserver")
    assert anon.get("/api/usb/profiles").status_code == 401
    # Revoking the creator locks it out; its sticks keep their own keys.
    cid = admin.get("/api/usb/creators").json()["creators"][0]["id"]
    assert admin.delete(f"/api/usb/creators/{cid}").status_code == 204
    assert client.get(f"{APP}/profiles", headers={"X-Creator-Key": setup["ckey"]}).status_code == 401
    assert client.get(f"{APP}/sticks/{sid}/manifest", headers={"X-Stick-Key": setup["skey"]}).status_code == 200
    # Revoking the stick locks it out too.
    assert admin.delete(f"/api/usb/sticks/{sid}").status_code == 204
    assert client.get(f"{APP}/sticks/{sid}/manifest", headers={"X-Stick-Key": setup["skey"]}).status_code == 401


# ---------------------------------------------------------------- profiles


def test_profile_validation(admin, app, settings, windows_iso):
    pe = winpe_iso(app, settings)
    ua = unattend(admin)
    with app.state.sessionmaker() as db:
        linux = IsoImage(name="Debian", filename="d.iso", status=ISO_READY, boot_method="linux", kernel_path="k")
        db.add(linux)
        db.commit()
        linux_id = linux.id
    bad = [
        [{"iso_id": linux_id, "label": "Debian"}],
        [{"iso_id": pe, "unattend_id": ua, "label": "PE"}],
        [{"iso_id": windows_iso, "unattend_id": 999, "label": "W"}],
        [{"iso_id": 999, "label": "x"}],
    ]
    for entries in bad:
        assert admin.post("/api/usb/profiles", json={"name": "P", "entries": entries}).status_code == 422
    p = profile(admin, [{"iso_id": windows_iso, "unattend_id": ua, "label": "W11"}, {"iso_id": pe, "label": "PE"}])
    assert [(e["label"], e["boot_method"]) for e in p["entries"]] == [("W11", "windows_setup"), ("PE", "winpe")]
    assert p["entries"][0]["data_bytes"] > 0 and p["entries"][1]["data_bytes"] == 0
    assert admin.post("/api/usb/profiles", json={"name": "Lab", "entries": []}).status_code == 409
    r = admin.put(f"/api/usb/profiles/{p['id']}", json={"name": "Lab 2", "entries": [{"iso_id": pe, "label": "PE"}]})
    assert [e["label"] for e in r.json()["entries"]] == ["PE"]
    assert admin.delete(f"/api/usb/profiles/{p['id']}").status_code == 204
    assert admin.get("/api/usb/profiles").json() == []


# ---------------------------------------------------------------- manifest


def test_manifest_lists_boot_and_data_files(client, setup, settings):
    m = client.get(f"{APP}/sticks/{setup['sid']}/manifest", headers={"X-Stick-Key": setup["skey"]}).json()
    assert m["hub_url"] == "http://10.0.0.5:8000"
    assert m["stick"]["name"] == "Stick 1" and m["boot_files"]
    (img,) = m["images"]
    iso_id = setup["iso"]
    boot = {f["dest"]: f for f in img["files"] if f["part"] == "boot"}
    data = {f["dest"] for f in img["files"] if f["part"] == "data"}
    assert set(boot) == {f"netdispatch/boot/{iso_id}/{n}" for n in ("BCD", "boot.sdi", "boot.wim")}
    assert boot[f"netdispatch/boot/{iso_id}/boot.wim"]["src"] == f"/files/iso/{iso_id}/tree/sources/boot.wim"
    assert f"netdispatch/img/{iso_id}/sources/install.wim" in data
    assert img["cache_url"] is None and img["boot_bytes"] == 3 and img["data_bytes"] == 5
    assert len(img["etag"]) == 64
    assert "smb" not in str(m).lower()  # never the share password


def test_manifest_points_at_a_current_relay_cache(client, admin, setup, app):
    key = admin.post("/api/relays", json={"id": "site-a", "name": "Site A"}).json()["key"]
    admin.put("/api/relays/site-a/cache", json={"iso_ids": [setup["iso"]]})
    relay_doc = client.get("/api/relays/cache/manifest?relay_id=site-a", headers={"X-Relay-Key": key}).json()
    entry = relay_doc["isos"][0]
    body = {
        "relay_id": "site-a", "name": "Site A", "ip_address": "10.0.0.2", "subnet": "10.0.0.0/24",
        "dnsmasq_ok": True,
        "cache": {
            "url": "http://10.0.0.2:8080", "http_ok": True, "smb_ok": True,
            "items": [{"iso_id": setup["iso"], "etag": entry["etag"], "status": "ready",
                       "bytes_done": entry["total_bytes"], "bytes_total": entry["total_bytes"]}],
        },
    }
    assert client.post("/api/relays/heartbeat", json=body, headers={"X-Relay-Key": key}).status_code == 200
    with TestClient(app, base_url="https://testserver", client=("10.0.0.77", 50000)) as lan:
        img = lan.get(f"{APP}/sticks/{setup['sid']}/manifest", headers={"X-Stick-Key": setup["skey"]}).json()
    img = img["images"][0]
    assert img["cache_url"] == "http://10.0.0.2:8080"
    wim = next(f for f in img["files"] if f["dest"] == f"netdispatch/boot/{setup['iso']}/boot.wim")
    assert wim["cache"] == f"tree/{setup['iso']}/sources/boot.wim"
    # Elsewhere (no relay on the caller's subnet) everything comes from the hub.
    with TestClient(app, base_url="https://testserver", client=("192.168.9.9", 50000)) as away:
        img = away.get(f"{APP}/sticks/{setup['sid']}/manifest", headers={"X-Stick-Key": setup["skey"]}).json()
    assert img["images"][0]["cache_url"] is None


# ---------------------------------------------------------------- layout


def _layout(client, setup, have):
    r = client.post(f"{APP}/sticks/{setup['sid']}/layout", json={"have": have}, headers={"X-Stick-Key": setup["skey"]})
    assert r.status_code == 200, r.text
    doc = r.json()
    return doc, {(f["part"], f["path"]): f["content"] for f in doc["files"]}


def test_layout_without_local_images_is_online_only(client, setup):
    doc, files = _layout(client, setup, {})
    parent = files[("boot", "EFI/BOOT/autoexec.ipxe")]
    assert "Images on this stick" not in parent and "prompt --key l" not in parent
    assert not any(p.startswith("netdispatch/entry/") for _, p in files)
    net = files[("boot", "netdispatch/net.ipxe")]
    assert f"set nd-stick {setup['sid']}" in net and "set nd-have -" in net
    assert "sanboot --no-describe --drive 0x81" in files[("boot", "netdispatch/bios.ipxe")]
    stick_json = files[("data", "netdispatch/stick.json")]
    assert setup["sid"] in stick_json and "nds_" not in stick_json
    assert {d["path"] for d in doc["replace_dirs"]} == {"netdispatch/entry", "netdispatch/unattend"}


def test_layout_with_a_local_windows_image(client, setup):
    m = client.get(f"{APP}/sticks/{setup['sid']}/manifest", headers={"X-Stick-Key": setup["skey"]}).json()
    etag = m["images"][0]["etag"]
    iso_id = setup["iso"]
    doc, files = _layout(client, setup, {str(iso_id): etag})
    assert doc["problems"] == []
    parent = files[("boot", "EFI/BOOT/autoexec.ipxe")]
    eid = setup["profile"]["entries"][0]["id"]
    assert f"item e{eid} Windows 11" in parent and "prompt --key l" in parent
    assert f"initrd ${{nd-root}}/netdispatch/boot/{iso_id}/boot.wim boot.wim" in parent
    assert f"initrd ${{nd-root}}/netdispatch/entry/{eid}/netdispatch.cmd netdispatch.cmd" in parent
    assert f"set nd-have {iso_id}:{etag[:8]}" in files[("boot", "netdispatch/net.ipxe")]
    cmd = files[("boot", f"netdispatch/entry/{eid}/netdispatch.cmd")]
    assert "\r\n" in cmd and "net use" not in cmd and "smb-pw" not in cmd
    # The answer file, split around the computer name, joins back to what a
    # network deployment would render with that name.
    parts = [files[("data", f"netdispatch/unattend/{eid}/part{n}.xml")] for n in range(3)]
    assert ("data", f"netdispatch/unattend/{eid}/part3.xml") not in files
    ctx = {"mac": "", "mac_dash": "", "mac_plain": "", "hostname": "LAB-PC-7", "ip": "", "relay": "",
           "image": "Windows 11 24H2"}
    assert "LAB-PC-7".join(parts) == render(UA, ctx)
    assert "part0.xml\"+X:\\nd-name.txt+\"" in cmd and "part2.xml\" X:\\autounattend.xml" in cmd


def test_layout_reports_a_broken_answer_file(client, admin, setup, settings):
    (settings.configs_dir / "offline.xml").write_text("<unattend>{{ hostname }")
    doc, files = _layout(client, setup, {str(setup["iso"]): "0" * 64})
    assert doc["problems"] and "Windows 11" in doc["problems"][0]
    assert "item e" not in files[("boot", "EFI/BOOT/autoexec.ipxe")]


def test_offline_parts_without_hostname():
    parts = offline_unattend_parts("<a>{{ image }}</a>", "Img")
    assert parts == ["<a>Img</a>"]
    with pytest.raises(ValueError):
        offline_unattend_parts("<a>{{ hostname }}<b></a>", "Img")
    assert HOSTNAME_MARKER not in "".join(offline_unattend_parts(UA, "x"))


# ---------------------------------------------------------------- reports


def test_report_and_dashboard_status(client, admin, setup):
    sid, h = setup["sid"], {"X-Stick-Key": setup["skey"]}
    etag = client.get(f"{APP}/sticks/{sid}/manifest", headers=h).json()["images"][0]["etag"]
    (row,) = admin.get("/api/usb/sticks").json()
    assert row["items"][0]["state"] == "missing" and row["profile_name"] == "Lab"
    item = {"iso_id": setup["iso"], "etag": etag, "status": "ready", "bytes_done": 8, "bytes_total": 8}
    assert client.post(f"{APP}/sticks/{sid}/report", json={"items": [item]}, headers=h).status_code == 200
    assert admin.get("/api/usb/sticks").json()[0]["items"][0]["state"] == "current"
    item["etag"] = "f" * 64
    client.post(f"{APP}/sticks/{sid}/report", json={"items": [item]}, headers=h)
    assert admin.get("/api/usb/sticks").json()[0]["items"][0]["state"] == "outdated"
    client.post(f"{APP}/sticks/{sid}/report", json={"items": [dict(item, status="bogus")]}, headers=h)
    assert admin.get("/api/usb/sticks").json()[0]["items"][0]["state"] == "error"
    client.post(f"{APP}/sticks/{sid}/report", json={"items": []}, headers=h)
    assert admin.get("/api/usb/sticks").json()[0]["items"][0]["state"] == "missing"


def test_deleting_image_or_answer_file_updates_profiles(client, admin, setup):
    assert admin.delete(f"/api/unattend/{setup['ua']}").status_code in (200, 204)
    (p,) = admin.get("/api/usb/profiles").json()
    assert p["entries"][0]["unattend_id"] is None
    assert admin.delete(f"/api/isos/{setup['iso']}").status_code in (200, 204)
    assert admin.get("/api/usb/profiles").json()[0]["entries"] == []


# ---------------------------------------------------------------- boot flow


def test_stick_boot_offers_l_in_the_hold_loop(client):
    mac = "52-54-00-aa-00-0b"
    r = client.get(f"/usb/boot?mac={mac}&ip=10.9.9.9&stick=a1b2c3d4e5f6&have=3:abcdef12&platform=efi")
    assert "&relay=-&arch=${buildarch}&platform=${platform}&usbmenu=1" in r.text
    # BIOS sticks, sticks without images and junk get the plain hand-off.
    for q in ("&have=3:abcdef12&platform=pcbios", "&have=-&platform=efi"):
        assert "usbmenu" not in client.get(f"/usb/boot?mac={mac}&stick=a1b2c3d4e5f6{q}").text
    assert "usbmenu" not in client.get(f"/usb/boot?mac={mac}&stick=BAD$&have=3:ab&platform=efi").text
    w = client.get(f"/boot?mac={mac}&ip=10.9.9.9&relay=-&arch=x86_64&platform=efi&usbmenu=1").text
    assert "prompt --key l --timeout 5000 && goto usbmenu || goto next" in w
    assert "set nd-menu 1\nexit 0" in w and "&usbmenu=1&seq=1" in w
    plain = client.get(f"/boot?mac={mac}&ip=10.9.9.9&relay=-&arch=x86_64&platform=efi").text
    assert "prompt --key l" not in plain and "sleep 5" in plain


def test_stick_child_script_returns_to_the_stick():
    s = render_media_script("http://hub", efi=True, media="usb", stick=StickRef("a1b2c3d4e5f6", "3:abcdef12"))
    assert "&stick=${nd-stick}&have=${nd-have}" in s
    assert ":local\necho netdispatch :: booting from local disk\nexit 0" in s
    assert ":offline\necho netdispatch :: back to the USB stick\nexit 1" in s
    assert "isset ${nd-menu} && goto offline || goto hub_failed" in s
    assert "chain file:/native.efi && goto local || goto native_failed" in s
    assert "shell" not in s  # Ctrl-C goes back to the stick's menu
    native = render_media_script("http://hub", efi=True, native=True, media="usb", stick=StickRef("a1b2c3d4e5f6"))
    assert "native.efi" not in native and "set nd-have -" in native
    for bad in ("../x", "A1B2C3D4E5F6"):
        with pytest.raises(ValueError):
            StickRef(bad)
    with pytest.raises(ValueError):
        StickRef("a1b2c3d4e5f6", "3:${x}")


def test_stick_parent_script():
    s = render_stick_parent("a1b2c3d4e5f6", "Lab ${evil} stick", [StickEntry(5, "Win|dows", 2, True, True, False)])
    assert "${evil}" not in s and "Win?dows" in s
    # Everything the network needs is in memory before the first chain.
    assert s.index("imgfetch -n net.efi") < s.index("chain net.efi")
    assert "chain net.efi && goto local || goto back" in s and ":local\necho netdispatch :: booting from local disk\nexit 1" in s
    assert "choose --default disk --timeout 15000" in s
    assert "boot.sdi" not in s and "imgfree\nkernel ${nd-root}/netdispatch/wimboot gui" in s
    winpe = render_stick_parent("a1b2c3d4e5f6", "x", [StickEntry(6, "PE", 3, False, False, False)])
    assert "netdispatch.cmd" not in winpe and "winpeshl" not in winpe


def test_stick_cmd():
    row = IsoImage(id=7, name="W", filename="w.iso", boot_method="windows_setup", setup_path="setup.exe")
    cmd = render_stick_cmd(row, 4, 3)
    assert cmd.startswith("@echo off\r\nif /i not \"%~f0\"==\"X:\\netdispatch.cmd\"")
    assert 'if exist "%%d:\\netdispatch\\stick.json" if exist "%%d:\\netdispatch\\img\\7\\setup.exe"' in cmd
    assert 'set /p "ND_NAME=' in cmd and "/unattend:X:\\autounattend.xml" in cmd
    assert render_stick_cmd(row, 4, 1).count("set /p") == 0  # no {{ hostname }}: no prompt
    interactive = render_stick_cmd(row, 4, 0)
    assert "/unattend" not in interactive and '"!ND_DATA!\\netdispatch\\img\\7\\setup.exe"' in interactive


# ---------------------------------------------------------------- boot template


@pytest.fixture
def template_tools(settings):
    """The real boot files and tools, as installed in the server image."""
    missing = template_missing(settings)
    if missing:
        if os.environ.get("NETDISPATCH_REQUIRE_USB_TOOLS") == "1":
            pytest.fail(f"stick template tools missing: {missing}")
        pytest.skip(f"stick template tools missing: {missing}")


def test_boot_template(client, setup, settings, tmp_path, template_tools):
    h = {"X-Stick-Key": setup["skey"], "X-Stick-Id": setup["sid"]}
    assert client.get(f"{APP}/boot-template?size_mb=64&offset=2048").status_code == 401
    assert client.get(f"{APP}/boot-template?size_mb=1&offset=2048", headers=h).status_code == 422
    r = client.get(f"{APP}/boot-template?size_mb=64&offset=2048", headers=h)
    assert r.status_code == 200, r.text
    head = gzip.decompress(r.content)
    assert len(head) == int(r.headers["x-nd-head-bytes"]) < 64 * 2**20
    # Written to the start of a partition full of old data, it is a clean FAT32.
    img = tmp_path / "part.img"
    img.write_bytes(head + b"\xa5" * (64 * 2**20 - len(head)))
    assert fat_used_bytes(img) == len(head)
    fsck = subprocess.run(["fsck.fat", "-n", str(img)], capture_output=True, text=True)
    assert fsck.returncode == 0, fsck.stdout + fsck.stderr
    listing = subprocess.run(
        ["mdir", "-i", str(img), "-a", "-/", "-b", "::/"], capture_output=True, text=True, env={"MTOOLS_SKIP_CHECK": "1"}
    ).stdout
    for path in ("EFI/BOOT/BOOTX64.EFI", "EFI/BOOT/ipxe.efi", "EFI/BOOT/net/ipxe.efi", "EFI/BOOT/native/ipxe.efi",
                 "syslinux/ldlinux.sys", "syslinux/ldlinux.c32", "syslinux/ipxe.lkrn", "syslinux/syslinux.cfg", "netdispatch/wimboot"):
        assert f"::/{path}" in listing, listing
    # The parent is the signed snponly build, the child the signed full one.
    got = tmp_path / "parent.efi"
    subprocess.run(["mcopy", "-i", str(img), "::/EFI/BOOT/ipxe.efi", str(got)], check=True,
                   env={"MTOOLS_SKIP_CHECK": "1"})
    assert got.read_bytes() == (settings.usb_assets_dir / "snponly.efi").read_bytes()
    # Hidden sectors = the partition offset, for SYSLINUX.
    assert int.from_bytes(head[28:32], "little") == 2048
    # Cached per size and offset.
    again = client.get(f"{APP}/boot-template?size_mb=64&offset=2048", headers=h)
    assert again.content == r.content
    mbr = client.get(f"{APP}/mbr", headers={"X-Creator-Key": setup["ckey"]})
    assert mbr.status_code == 200 and len(mbr.content) == 440


def test_usb_info_lists_stick_problems(admin, tmp_path):
    info = admin.get("/api/usb/info").json()
    assert "sticks" in info and isinstance(info["stick_problems"], list)

