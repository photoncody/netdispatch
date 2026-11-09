from __future__ import annotations

from .conftest import BOOTSTRAP_KEY

HB = {"relay_id": "site-a", "name": "Site A", "ip_address": "10.0.0.2", "subnet": "10.0.0.0/24", "dnsmasq_ok": True}


def test_heartbeat_rejects_bad_key(client):
    r = client.post("/api/relays/heartbeat", json=HB, headers={"X-Relay-Key": "nope"})
    assert r.status_code == 401


def test_bootstrap_key_auto_registers(client, admin):
    r = client.post("/api/relays/heartbeat", json=HB, headers={"X-Relay-Key": BOOTSTRAP_KEY})
    assert r.status_code == 200
    assert r.json()["boot_url"] == "http://10.0.0.5:8000"
    relays = admin.get("/api/relays").json()
    assert relays[0]["id"] == "site-a" and relays[0]["online"] and relays[0]["auto_registered"]


def test_per_relay_key_lifecycle(client, admin):
    r = admin.post("/api/relays", json={"id": "site-b", "name": "Site B"})
    assert r.status_code == 201
    key = r.json()["key"]
    compose = r.json()["compose"]
    assert key in compose and "network_mode: host" in compose and "NET_ADMIN" in compose
    assert 'CENTRAL_SERVER_URL: "http://10.0.0.5:8000"' in compose
    assert '# SECURE_BOOT: "true"' in compose

    hb = dict(HB, relay_id="site-b")
    assert client.post("/api/relays/heartbeat", json=hb, headers={"X-Relay-Key": key}).status_code == 200

    new_key = admin.post("/api/relays/site-b/rotate-key").json()["key"]
    assert client.post("/api/relays/heartbeat", json=hb, headers={"X-Relay-Key": key}).status_code == 401
    assert client.post("/api/relays/heartbeat", json=hb, headers={"X-Relay-Key": new_key}).status_code == 200

    admin.patch("/api/relays/site-b", json={"enabled": False})
    assert client.post("/api/relays/heartbeat", json=hb, headers={"X-Relay-Key": new_key}).status_code == 403

    assert admin.delete("/api/relays/site-b").status_code == 204


def test_relay_id_validation(admin):
    assert admin.post("/api/relays", json={"id": "Bad ID!", "name": "x"}).status_code == 422
    assert admin.post("/api/relays", json={"id": "heartbeat", "name": "x"}).status_code == 422
