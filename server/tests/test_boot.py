from __future__ import annotations

import re

MAC = "52-54-00-AB-CD-01"
NORM = "52:54:00:ab:cd:01"
Q = f"/boot?mac={MAC}&ip=10.0.0.42&relay=site-a&arch=x86_64&platform=efi"


def _device(admin):
    return next(d for d in admin.get("/api/devices").json() if d["mac"] == NORM)


def test_unknown_device_enters_wait_loop(client):
    r = client.get(Q)
    assert r.status_code == 200
    s = r.text
    assert s.startswith("#!ipxe")
    assert "Waiting for administrator instructions" in s
    assert NORM in s and "10.0.0.42" in s and "site-a" in s
    # loop re-chains to the boot URL (BOOT_BASE_URL, not the request host) without nesting
    assert "chain --replace --autofree http://10.0.0.5:8000/boot?mac=${netX/mac:hexhyp}" in s
    assert "&relay=site-a&" in s
    assert "sleep 5" in s
    assert r.headers["cache-control"] == "no-store"


def test_wait_screen_redraws_with_poll_counter(client):
    client.get(Q)
    s = client.get(Q + "&seq=3").text
    assert "echo -n ${esc:string}[2J" in s  # clear screen each poll
    assert "Waiting for administrator" in s
    assert "poll 3 :: waiting 15s" in s
    assert "&seq=4" in s


def test_invalid_mac(client):
    s = client.get("/boot?mac=nonsense").text
    assert s.startswith("#!ipxe") and "valid MAC" in s


def test_text_is_sanitised(client, admin):
    client.get(Q)
    dev = _device(admin)
    admin.patch(f"/api/devices/{dev['id']}", json={"label": "evil ${shell} && reboot"})
    s = client.get(Q).text
    assert "${shell}" not in s
    assert "&& reboot" not in s


def test_full_deploy_cycle_windows(client, admin, windows_iso):
    client.get(Q)
    dev = _device(admin)
    assert dev["status"] == "waiting" and dev["relay_id"] == "site-a"

    tpl = admin.post(
        "/api/unattend",
        json={"name": "Win11 Pro", "content": "<unattend><ComputerName>{{ hostname }}</ComputerName><x>{{ mac }}</x></unattend>"},
    )
    assert tpl.status_code == 201, tpl.text
    r = admin.post(
        f"/api/devices/{dev['id']}/deploy",
        json={"iso_id": windows_iso, "unattend_id": tpl.json()["id"], "label": "LAB-PC-07 & company"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deploying"

    script = client.get(Q).text
    assert "kernel http://10.0.0.5:8000/files/boot/wimboot gui" in script
    assert f"/files/iso/{windows_iso}/tree/boot/BCD BCD" in script
    assert f"/files/iso/{windows_iso}/tree/sources/boot.wim boot.wim" in script
    token = re.search(r"/files/d/([A-Za-z0-9_-]+)/autounattend.xml autounattend.xml", script).group(1)
    assert "winpeshl.ini winpeshl.ini" in script and "netdispatch.cmd netdispatch.cmd" in script
    assert _device(admin)["status"] == "booting"

    # wimboot downloads (unauthenticated, token-gated)
    assert client.get(f"/files/iso/{windows_iso}/tree/boot/bcd").content == b"x"  # case-insensitive
    xml = client.get(f"/files/d/{token}/autounattend.xml")
    assert xml.status_code == 200
    assert "<ComputerName>LAB-PC-07 &amp; com</ComputerName>" in xml.text  # 15 chars, XML-escaped
    assert NORM in xml.text
    cmd = client.get(f"/files/d/{token}/netdispatch.cmd").text
    assert f"\\\\10.0.0.5\\netdispatch\\{windows_iso}" in cmd
    assert '"smb-pw%%1"' in cmd  # % escaped for cmd.exe
    assert "/unattend:X:\\Windows\\System32\\autounattend.xml" in cmd
    # a failed share connection prints network diagnostics and offers a prompt
    assert "call :diag" in cmd and "\r\n:diag\r\n" in cmd and "ipconfig" in cmd
    assert "ping -n 1 -w 2000 10.0.0.5" in cmd
    assert "choice /c RC /t 10 /d R" in cmd and "if errorlevel 2 goto shell" in cmd
    assert "\n" not in cmd.replace("\r\n", "")  # CRLF line endings only
    assert client.get(f"/files/d/{token}/winpeshl.ini").text.startswith("[LaunchApps]")
    assert client.get("/files/d/wrongtokenwrongtoken123/autounattend.xml").status_code == 404

    # Setup reboots -> PXE again -> must NOT reinstall; falls through to local disk
    s = client.get(Q).text
    assert "set nd-local 1" in s and "exit 0" in s and "deployment complete" in s
    assert _device(admin)["state"] == "local"
    # token is revoked once the deployment is over
    assert client.get(f"/files/d/{token}/autounattend.xml").status_code == 404
    assert "exit 0" in client.get(Q).text

    # reset returns it to the hold loop
    admin.post(f"/api/devices/{dev['id']}/reset")
    assert "Waiting for administrator" in client.get(Q).text


def test_after_deploy_wait_and_failure(client, admin, windows_iso):
    client.get(Q)
    dev = _device(admin)
    admin.post(f"/api/devices/{dev['id']}/deploy", json={"iso_id": windows_iso, "after_deploy": "wait"})
    assert "wimboot" in client.get(Q).text
    assert "Waiting for administrator" in client.get(Q).text
    assert _device(admin)["state"] == "waiting"

    admin.post(f"/api/devices/{dev['id']}/deploy", json={"iso_id": windows_iso})
    client.get(Q)
    s = client.get(Q + "&result=failed").text
    assert "Waiting for administrator" in s
    assert "failed" in _device(admin)["last_event"]


def test_deploy_requires_ready_image(client, admin):
    client.get(Q)
    dev = _device(admin)
    assert admin.post(f"/api/devices/{dev['id']}/deploy", json={"iso_id": 999}).status_code == 404


def test_preregister_and_new_device_local(client, admin, tmp_path):
    r = admin.post("/api/devices", json={"mac": "AABB.CCDD.EEFF", "label": "staged"})
    assert r.status_code == 201
    assert r.json()["mac"] == "aa:bb:cc:dd:ee:ff"
    assert admin.post("/api/devices", json={"mac": "aa:bb:cc:dd:ee:ff"}).status_code == 409


def test_script_preview(client, admin, windows_iso):
    client.get(Q)
    dev = _device(admin)
    admin.post(f"/api/devices/{dev['id']}/deploy", json={"iso_id": windows_iso})
    s = admin.get(f"/api/devices/{dev['id']}/script").text
    assert "wimboot" in s
    assert _device(admin)["state"] == "deploying"  # preview does not consume the deployment


def test_raw_iso_supports_range(client, windows_iso):
    r = client.get(f"/files/iso/{windows_iso}/raw/win11.iso", headers={"Range": "bytes=1-2"})
    assert r.status_code == 206
    assert r.content == b"SO"
    assert client.get(f"/files/iso/{windows_iso}/raw/other.iso").status_code == 404
    assert client.get(f"/files/iso/{windows_iso}/tree/../../win11.iso").status_code == 404
