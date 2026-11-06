from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import ADMIN_PW, make_settings


def test_ui_redirects_to_login_when_anonymous(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login?next=/")


def test_api_returns_401_when_anonymous(client):
    assert client.get("/api/devices").status_code == 401
    assert client.post("/api/relays", json={"id": "x", "name": "x"}).status_code == 401


def test_exempt_paths(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/boot?mac=52:54:00:00:00:01").text.startswith("#!ipxe")
    assert client.get("/files/boot/wimboot").content == b"WIMBOOT"
    # exempt from the session guard, but still authenticated by relay key
    assert client.post("/api/relays/heartbeat", json={"relay_id": "r1"}).status_code == 401


def test_local_login_flow(client):
    r = client.post("/login", data={"username": "admin", "password": "wrong"}, follow_redirects=False)
    assert r.status_code == 401
    r = client.post("/login", data={"username": "admin", "password": ADMIN_PW, "next": "/#relays"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/#relays"
    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/api/devices").status_code == 200
    client.get("/logout")
    assert client.get("/api/devices").status_code == 401


def test_open_redirect_is_blocked(client):
    r = client.post(
        "/login", data={"username": "admin", "password": ADMIN_PW, "next": "//evil.example"}, follow_redirects=False
    )
    assert r.headers["location"] == "/"


def test_csrf_header_required_for_mutations(client):
    client.post("/login", data={"username": "admin", "password": ADMIN_PW})
    r = client.post("/api/relays", json={"id": "site-a", "name": "Site A"})
    assert r.status_code == 403
    r = client.post("/api/relays", json={"id": "site-a", "name": "Site A"}, headers={"X-Requested-With": "netdispatch"})
    assert r.status_code == 201


def test_login_rate_limit(tmp_path):
    app = create_app(make_settings(tmp_path, login_max_attempts=3))
    with TestClient(app, base_url="https://testserver") as c:
        for _ in range(3):
            assert c.post("/login", data={"username": "admin", "password": "x"}).status_code == 401
        assert c.post("/login", data={"username": "admin", "password": ADMIN_PW}).status_code == 429


def test_auto_sso_redirects(tmp_path):
    settings = make_settings(
        tmp_path,
        oidc_enabled=True,
        auto_redirect_sso=True,
        oidc_client_id="netdispatch",
        oidc_client_secret="s",
        oidc_metadata_url="http://127.0.0.1:9/.well-known/openid-configuration",
    )
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver") as c:
        r = c.get("/", follow_redirects=False)
        assert r.headers["location"].startswith("/auth/oidc/login")
        r = c.get("/login", follow_redirects=False)
        assert r.headers["location"].startswith("/auth/oidc/login")
        # ?local=true never auto-redirects
        r = c.get("/login?local=true", follow_redirects=False)
        assert r.status_code == 200
        assert 'name="password"' in r.text
        # provider unreachable -> fall back to the local login page
        r = c.get("/auth/oidc/login", follow_redirects=False)
        assert r.status_code == 302
        assert "error=sso_unavailable" in r.headers["location"]
        r = c.get(r.headers["location"])
        assert 'name="password"' in r.text


def test_generated_admin_password(tmp_path):
    settings = make_settings(tmp_path, fallback_admin_password="")
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver") as c:
        pw = (settings.data_dir / "initial_admin_password").read_text().strip()
        assert len(pw) >= 16
        r = c.post("/login", data={"username": "admin", "password": pw}, follow_redirects=False)
        assert r.status_code == 303
