"""Relay ISO caching: manifests, cache reports, boot routing, WinPE share."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from app.models import ISO_EXTRACTING, ISO_READY, IsoImage, Relay, RelayCache, RelayCacheItem, utcnow
from app.relay_cache import valid_cache_url

from .conftest import make_tree

MAC = "52-54-00-AB-CD-01"
NORM = "52:54:00:ab:cd:01"
Q = f"/boot?mac={MAC}&ip=10.0.0.42&relay=site-a&arch=x86_64&platform=efi"
CACHE_URL = "http://10.0.0.2:8080"


@pytest.fixture
def relay_key(admin):
    r = admin.post("/api/relays", json={"id": "site-a", "name": "Site A"})
    assert r.status_code == 201, r.text
    return r.json()["key"]


def _manifest(client, key, relay_id="site-a"):
    return client.get(f"/api/relays/cache/manifest?relay_id={relay_id}", headers={"X-Relay-Key": key})


def _assign(admin, *iso_ids, relay_id="site-a"):
    r = admin.put(f"/api/relays/{relay_id}/cache", json={"iso_ids": list(iso_ids)})
    assert r.status_code == 200, r.text
    return r.json()


def _heartbeat(client, key, cache=None, relay_id="site-a"):
    body = {"relay_id": relay_id, "name": "Site A", "ip_address": "10.0.0.2", "dnsmasq_ok": True}
    if cache is not None:
        body["cache"] = cache
    r = client.post("/api/relays/heartbeat", json=body, headers={"X-Relay-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


def _report_ready(
    client, key, iso_id, etag=None, url=CACHE_URL, smb_ok=True, http_ok=True, status="ready", short=0
):
    entry = next((i for i in _manifest(client, key).json()["isos"] if i["id"] == iso_id), {})
    etag = etag or entry.get("etag")
    total = entry.get("total_bytes", 5)
    return _heartbeat(
        client,
        key,
        {
            "url": url,
            "http_ok": http_ok,
            "smb_ok": smb_ok,
            "disk_free": 50 * 2**30,
            "disk_total": 100 * 2**30,
            "items": [
                {"iso_id": iso_id, "etag": etag, "status": status, "bytes_done": total - short, "bytes_total": total}
            ],
        },
    )


def _device(admin):
    return next(d for d in admin.get("/api/devices").json() if d["mac"] == NORM)


def _deploy(client, admin, iso_id, **body):
    client.get(Q)
    dev = _device(admin)
    r = admin.post(f"/api/devices/{dev['id']}/deploy", json={"iso_id": iso_id, **body})
    assert r.status_code == 200, r.text
    return client.get(Q).text


def _add_iso(app, settings, **fields) -> int:
    with app.state.sessionmaker() as db:
        iso = IsoImage(size=10, status=ISO_READY, **fields)
        db.add(iso)
        db.commit()
        return iso.id


# ------------------------------------------------------------------ manifest
def test_manifest_requires_a_valid_relay_key(client, relay_key):
    assert _manifest(client, "wrong").status_code == 401
    assert _manifest(client, relay_key, relay_id="other").status_code == 401
    assert client.get("/api/relays/cache/manifest?relay_id=site-a").status_code == 401
    r = _manifest(client, relay_key)
    assert r.status_code == 200
    assert r.json()["isos"] == [] and r.json()["smb"] is None


def test_manifest_lists_assigned_windows_tree_lowercased(client, admin, relay_key, windows_iso):
    _assign(admin, windows_iso)
    doc = _manifest(client, relay_key).json()
    assert re.fullmatch(r"[0-9a-f]{64}", doc["etag"])
    [iso] = doc["isos"]
    assert iso["id"] == windows_iso and re.fullmatch(r"[0-9a-f]{64}", iso["etag"])
    files = {f["dest"]: f for f in iso["files"]}
    # stored in lower case on the relay; downloaded from the exact hub path
    assert files[f"tree/{windows_iso}/boot/bcd"]["src"] == f"/files/iso/{windows_iso}/tree/boot/BCD"
    assert f"tree/{windows_iso}/sources/install.wim" in files
    assert not any(d.startswith("raw/") for d in files)  # Windows Setup needs the tree only
    assert iso["total_bytes"] == sum(f["size"] for f in iso["files"]) == 5
    # Windows Setup on the relay needs the share credentials
    assert doc["smb"] == {"share": "netdispatch", "username": "netdispatch", "password": "smb-pw%1"}
    # the manifest downloads are the existing unauthenticated routes
    assert client.get(files[f"tree/{windows_iso}/boot/bcd"]["src"]).content == b"x"


def test_manifest_layout_follows_boot_method(client, admin, app, settings, relay_key):
    san = _add_iso(app, settings, name="Tools", filename="Tools.ISO", boot_method="sanboot")
    (settings.isos_dir / "Tools.ISO").write_bytes(b"RAW")
    live = _add_iso(
        app, settings, name="Mint", filename="mint.iso", boot_method="linux",
        kernel_path="casper/vmlinuz", cmdline="boot=casper url={iso_url}",
    )
    (settings.isos_dir / "mint.iso").write_bytes(b"MINT")
    make_tree(settings.extracted_dir / str(live), ["casper/vmlinuz", "casper/initrd"])
    _assign(admin, san, live)
    doc = _manifest(client, relay_key).json()
    by_id = {i["id"]: [f["dest"] for f in i["files"]] for i in doc["isos"]}
    assert by_id[san] == [f"raw/{san}/tools.iso"]
    assert sorted(by_id[live]) == [f"raw/{live}/mint.iso", f"tree/{live}/casper/initrd", f"tree/{live}/casper/vmlinuz"]
    assert doc["smb"] is None  # no Windows Setup image assigned


def test_manifest_etag_tracks_content(client, admin, app, settings, relay_key, windows_iso):
    _assign(admin, windows_iso)
    first = _manifest(client, relay_key).json()
    assert _manifest(client, relay_key).json()["etag"] == first["etag"]

    # Editing metadata keeps the image etag (no re-download)...
    admin.patch(f"/api/isos/{windows_iso}", json={"name": "Renamed"})
    assert _manifest(client, relay_key).json()["isos"][0]["etag"] == first["isos"][0]["etag"]

    # ...re-extraction makes the relay keep its copy but the hub stop using it...
    with app.state.sessionmaker() as db:
        db.get(IsoImage, windows_iso).status = ISO_EXTRACTING
        db.commit()
    assert _manifest(client, relay_key).json()["isos"] == [{"id": windows_iso, "keep": True}]

    # ...and changed files produce a new etag once it's ready again.
    (settings.extracted_dir / str(windows_iso) / "sources" / "boot.wim").write_bytes(b"new boot wim")
    with app.state.sessionmaker() as db:
        db.get(IsoImage, windows_iso).status = ISO_READY
        db.commit()
    assert _manifest(client, relay_key).json()["isos"][0]["etag"] != first["isos"][0]["etag"]


# ------------------------------------------------------------------ reports & admin API
def test_heartbeat_cache_report_and_admin_view(client, admin, relay_key, windows_iso):
    plain = _heartbeat(client, relay_key)
    assert "cache_manifest_etag" not in plain  # relay without RELAY_CACHE
    assert admin.get("/api/relays").json()[0]["cache"]["enabled"] is False

    before = _report_ready(client, relay_key, windows_iso, etag="0" * 64, status="syncing")["cache_manifest_etag"]
    _assign(admin, windows_iso)
    hb = _report_ready(client, relay_key, windows_iso, status="syncing")
    assert hb["cache_manifest_etag"] != before  # wakes the relay's sync loop
    assert hb["cache_manifest_etag"] == _manifest(client, relay_key).json()["etag"]

    view = admin.get("/api/relays/site-a/cache").json()
    cache = view["relay"]["cache"]
    assert cache["enabled"] and cache["url"] == CACHE_URL and cache["http_ok"] and cache["disk_free"] == 50 * 2**30
    item = next(i for i in view["items"] if i["iso_id"] == windows_iso)
    assert item["assigned"] and item["status"] == "syncing" and not item["usable"] and item["cache_bytes"] == 5

    _report_ready(client, relay_key, windows_iso)
    item = next(i for i in admin.get("/api/relays/site-a/cache").json()["items"] if i["iso_id"] == windows_iso)
    assert item["usable"] and not item["stale"]
    assert admin.get("/api/relays").json()[0]["cache"]["usable"] == 1

    # a restarted relay that hasn't re-checked its copy yet must not inherit "ready"
    _heartbeat(client, relay_key, {"url": CACHE_URL, "http_ok": True, "smb_ok": True, "items": []})
    item = next(i for i in admin.get("/api/relays/site-a/cache").json()["items"] if i["iso_id"] == windows_iso)
    assert item["status"] == "pending" and not item["usable"]
    _report_ready(client, relay_key, windows_iso)

    # a relay restarted without RELAY_CACHE drops its cache report, keeps assignments
    _heartbeat(client, relay_key)
    summary = admin.get("/api/relays").json()[0]["cache"]
    assert summary["enabled"] is False and summary["assigned"] == 1


def test_lenient_report_never_breaks_the_heartbeat(client, admin, relay_key, windows_iso):
    _assign(admin, windows_iso)
    _heartbeat(
        client,
        relay_key,
        {"url": "http://x", "items": [
            {"iso_id": windows_iso, "etag": "not-hex", "status": "bogus", "message": "m" * 1000},
            {"iso_id": 9999, "status": "ready"},  # not assigned: ignored
        ]},
    )
    item = next(i for i in admin.get("/api/relays/site-a/cache").json()["items"] if i["iso_id"] == windows_iso)
    assert item["status"] == "error" and len(item["message"]) == 255


@pytest.mark.parametrize(
    "url,ok",
    [
        ("http://10.0.0.2:8080", True),
        ("http://relay.site.example/", True),
        ("http://[fd00::2]:8080", True),
        ("https://10.0.0.2:8080", False),
        ("http://10.0.0.2:8080/path", False),
        ("http://user@10.0.0.2", False),
        ("http://10.0.0.2:99999", False),
        ("http://10.0.0.2 && reboot", False),
        ("http://${evil}", False),
    ],
)
def test_cache_url_validation(url, ok):
    assert (valid_cache_url(url) is not None) is ok


def test_assignment_api_validation(client, admin, relay_key, windows_iso):
    assert admin.put("/api/relays/site-a/cache", json={"iso_ids": [windows_iso, 9999]}).status_code == 422
    assert admin.put("/api/relays/nope/cache", json={"iso_ids": []}).status_code == 404
    # CSRF guard and session auth apply, unlike the relay-facing manifest
    r = client.put("/api/relays/site-a/cache", json={"iso_ids": []}, headers={"X-Requested-With": ""})
    assert r.status_code == 403
    assert admin.post("/api/relays", json={"id": "cache", "name": "x"}).status_code == 422
    view = _assign(admin, windows_iso)
    assert [i["iso_id"] for i in view["items"] if i["assigned"]] == [windows_iso]
    view = _assign(admin)
    assert not any(i["assigned"] for i in view["items"])


def test_unauthenticated_admin_cache_api(app, relay_key):
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="https://testserver") as anon:
        assert anon.get("/api/relays/site-a/cache").status_code == 401


def test_deleting_iso_or_relay_removes_cache_rows(client, admin, app, relay_key, windows_iso):
    _assign(admin, windows_iso)
    _report_ready(client, relay_key, windows_iso)
    assert admin.delete(f"/api/isos/{windows_iso}").status_code == 204
    with app.state.sessionmaker() as db:
        assert db.query(RelayCacheItem).count() == 0
    assert _manifest(client, relay_key).json()["isos"] == []
    assert admin.delete("/api/relays/site-a").status_code == 204
    with app.state.sessionmaker() as db:
        assert db.query(RelayCache).count() == 0


# ------------------------------------------------------------------ boot routing
def test_windows_payload_from_relay_cache(client, admin, relay_key, windows_iso):
    _assign(admin, windows_iso)
    _report_ready(client, relay_key, windows_iso)
    script = _deploy(client, admin, windows_iso)
    # image files from the relay...
    assert f"initrd {CACHE_URL}/tree/{windows_iso}/boot/BCD BCD" in script
    assert f"initrd {CACHE_URL}/tree/{windows_iso}/sources/boot.wim boot.wim" in script
    assert "relay cache at http://10.0.0.2:8080" in script
    # ...wimboot and the secret-bearing files from the hub
    assert "kernel http://10.0.0.5:8000/files/boot/wimboot gui" in script
    assert "initrd http://10.0.0.5:8000/files/d/" in script
    assert f"{CACHE_URL}/files/d/" not in script
    assert _device(admin)["last_event"].endswith("(relay cache)")

    token = re.search(r"/files/d/([A-Za-z0-9_-]+)/netdispatch.cmd", script).group(1)
    cmd = client.get(f"/files/d/{token}/netdispatch.cmd").text
    # relay share first, hub share as the fallback
    assert f'set "ND_SHARE=\\\\10.0.0.2\\netdispatch\\{windows_iso}"' in cmd
    assert f'set "ND_HUB_SHARE=\\\\10.0.0.5\\netdispatch\\{windows_iso}"' in cmd
    assert "if %ND_TRIES%==3 if defined ND_HUB_SHARE goto use_hub" in cmd
    assert "\r\n:use_hub\r\n" in cmd and 'set "ND_SHARE=%ND_HUB_SHARE%"' in cmd
    assert "ping -n 1 -w 2000 %ND_HOST%" in cmd
    assert cmd.index("call :diag") < cmd.index("goto use_hub")  # diagnose the relay before switching
    assert "\n" not in cmd.replace("\r\n", "")


def test_payload_preview_matches_routing(client, admin, relay_key, windows_iso):
    _assign(admin, windows_iso)
    _report_ready(client, relay_key, windows_iso)
    client.get(Q)
    dev = _device(admin)
    admin.post(f"/api/devices/{dev['id']}/deploy", json={"iso_id": windows_iso})
    preview = admin.get(f"/api/devices/{dev['id']}/script").text
    assert f"{CACHE_URL}/tree/{windows_iso}/sources/boot.wim" in preview


@pytest.mark.parametrize(
    "case",
    [
        "not-assigned", "syncing", "stale-etag", "short-bytes", "http-down", "bad-url",
        "offline", "disabled", "other-relay",
    ],
)
def test_payload_falls_back_to_hub(case, client, admin, app, relay_key, windows_iso):
    if case != "not-assigned":
        _assign(admin, windows_iso)
    _report_ready(
        client,
        relay_key,
        windows_iso,
        etag="0" * 64 if case in ("stale-etag", "not-assigned") else None,
        status="syncing" if case == "syncing" else "ready",
        http_ok=case != "http-down",
        url="http://10.0.0.2:8080/x" if case == "bad-url" else CACHE_URL,
        short=1 if case == "short-bytes" else 0,
    )
    if case == "offline":
        with app.state.sessionmaker() as db:
            db.get(Relay, "site-a").last_seen = utcnow() - timedelta(minutes=10)
            db.commit()
    if case == "disabled":
        admin.patch("/api/relays/site-a", json={"enabled": False})
    q = Q.replace("relay=site-a", "relay=site-b") if case == "other-relay" else Q
    client.get(q)
    dev = _device(admin)
    admin.post(f"/api/devices/{dev['id']}/deploy", json={"iso_id": windows_iso})
    script = client.get(q).text
    assert f"initrd http://10.0.0.5:8000/files/iso/{windows_iso}/tree/sources/boot.wim boot.wim" in script
    assert CACHE_URL not in script
    token = re.search(r"/files/d/([A-Za-z0-9_-]+)/netdispatch.cmd", script).group(1)
    cmd = client.get(f"/files/d/{token}/netdispatch.cmd").text
    assert "ND_HUB_SHARE" not in cmd and f"\\\\10.0.0.5\\netdispatch\\{windows_iso}" in cmd


def test_smb_down_keeps_boot_files_on_relay_but_uses_hub_share(client, admin, relay_key, windows_iso):
    _assign(admin, windows_iso)
    _report_ready(client, relay_key, windows_iso, smb_ok=False)  # CACHE_SMB=false, or smbd down
    script = _deploy(client, admin, windows_iso)
    assert f"initrd {CACHE_URL}/tree/{windows_iso}/sources/boot.wim boot.wim" in script
    token = re.search(r"/files/d/([A-Za-z0-9_-]+)/netdispatch.cmd", script).group(1)
    cmd = client.get(f"/files/d/{token}/netdispatch.cmd").text
    assert "ND_HUB_SHARE" not in cmd
    assert f'set "ND_SHARE=\\\\10.0.0.5\\netdispatch\\{windows_iso}"' in cmd


def test_linux_placeholders_point_at_relay(client, admin, app, settings, relay_key):
    live = _add_iso(
        app, settings, name="Mint", filename="mint.iso", boot_method="linux",
        kernel_path="casper/vmlinuz", initrd_paths_json='["casper/initrd"]',
        cmdline="boot=casper url={iso_url} x={files_url}/casper",
    )
    (settings.isos_dir / "mint.iso").write_bytes(b"MINT")
    make_tree(settings.extracted_dir / str(live), ["casper/vmlinuz", "casper/initrd"])
    _assign(admin, live)
    _report_ready(client, relay_key, live, smb_ok=False)  # no SMB needed for Linux
    script = _deploy(client, admin, live)
    assert f"kernel {CACHE_URL}/tree/{live}/casper/vmlinuz initrd=initrd.magic boot=casper" in script
    assert f"url={CACHE_URL}/raw/{live}/mint.iso" in script
    assert f"x={CACHE_URL}/tree/{live}/casper" in script
    assert f"initrd {CACHE_URL}/tree/{live}/casper/initrd" in script


def test_sanboot_from_relay_cache(client, admin, app, settings, relay_key):
    san = _add_iso(app, settings, name="Tools", filename="Tools.ISO", boot_method="sanboot")
    (settings.isos_dir / "Tools.ISO").write_bytes(b"RAW")
    _assign(admin, san)
    _report_ready(client, relay_key, san, smb_ok=False)
    assert f"sanboot --no-describe {CACHE_URL}/raw/{san}/Tools.ISO" in _deploy(client, admin, san)
