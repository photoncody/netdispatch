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
    # both options are off unless ticked in the dashboard
    assert "SECURE_BOOT" not in compose and "RELAY_CACHE" not in compose and "volumes:" not in compose

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


def test_compose_options(admin):
    r = admin.post("/api/relays", json={"id": "site-c", "name": "Site C", "cache": True, "secure_boot": True})
    assert r.status_code == 201
    key, compose = r.json()["key"], r.json()["compose"]
    assert f'RELAY_API_KEY: "{key}"' in compose
    assert '\n      SECURE_BOOT: "true"\n' in compose
    assert '\n      RELAY_CACHE: "true"\n' in compose
    assert "\n    volumes:\n      - ./cache:/cache" in compose
    assert '      # CACHE_RATE_LIMIT: "20M"' in compose  # the finer settings stay optional

    # Without the key (the hub keeps only its hash), each option on its own.
    doc = admin.get("/api/relays/site-c/compose?cache=true").json()
    assert doc["key_placeholder"] in doc["compose"] and key not in doc["compose"]
    assert 'RELAY_CACHE: "true"' in doc["compose"] and "SECURE_BOOT" not in doc["compose"]
    doc = admin.get("/api/relays/site-c/compose?secure_boot=true").json()
    assert 'SECURE_BOOT: "true"' in doc["compose"] and "RELAY_CACHE" not in doc["compose"]
    assert "volumes:" not in doc["compose"]
    assert "RELAY_CACHE" not in admin.get("/api/relays/site-c/compose").json()["compose"]

    rotated = admin.post("/api/relays/site-c/rotate-key?cache=true").json()
    assert rotated["key"] in rotated["compose"] and 'RELAY_CACHE: "true"' in rotated["compose"]
