from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import ISO_READY, IsoImage

# The unattend generator program, installed in the server image (see server/unattendgen).
UNATTENDGEN = Path(os.environ.get("UNATTENDGEN_PATH", "/opt/netdispatch/netdispatch-unattendgen"))

ADMIN_PW = "test-admin-pw"
BOOTSTRAP_KEY = "bootstrap-relay-key"
HDR = {"X-Requested-With": "netdispatch"}


def make_settings(tmp_path: Path, **overrides) -> Settings:
    wimboot = tmp_path / "wimboot"
    wimboot.write_bytes(b"WIMBOOT")
    values = dict(
        data_dir=tmp_path / "data",
        public_url="https://pxe.example.com",
        boot_base_url="http://10.0.0.5:8000",
        secret_key="test-secret",
        fallback_admin_username="admin",
        fallback_admin_password=ADMIN_PW,
        relay_api_key=BOOTSTRAP_KEY,
        smb_password="smb-pw%1",
        wimboot_path=wimboot,
        memdisk_path=tmp_path / "memdisk-missing",
        unattendgen_path=UNATTENDGEN,
        _env_file=None,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def unattendgen():
    """Skip unless the real unattend generator is installed. In the Docker test stage
    NETDISPATCH_REQUIRE_UNATTENDGEN=1 turns the skip into a failure."""
    if not (UNATTENDGEN.is_file() and os.access(UNATTENDGEN, os.X_OK)):
        if os.environ.get("NETDISPATCH_REQUIRE_UNATTENDGEN") == "1":
            pytest.fail(f"unattend generator missing at {UNATTENDGEN}")
        pytest.skip(f"unattend generator not installed at {UNATTENDGEN}")
    return UNATTENDGEN


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def admin(client):
    r = client.post("/login", data={"username": "admin", "password": ADMIN_PW, "next": "/"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    client.headers.update(HDR)
    return client


def make_tree(root: Path, files: list[str]) -> None:
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


@pytest.fixture
def windows_iso(app, settings):
    """A 'ready' Windows Setup image with a fake extracted tree."""
    with app.state.sessionmaker() as db:
        iso = IsoImage(
            name="Windows 11 24H2",
            filename="win11.iso",
            size=10,
            status=ISO_READY,
            boot_method="windows_setup",
            bcd_path="boot/BCD",
            boot_sdi_path="boot/boot.sdi",
            boot_wim_path="sources/boot.wim",
            setup_path="setup.exe",
        )
        db.add(iso)
        db.commit()
        iso_id = iso.id
    (settings.isos_dir / "win11.iso").write_bytes(b"ISO")
    make_tree(
        settings.extracted_dir / str(iso_id),
        ["boot/BCD", "boot/boot.sdi", "sources/boot.wim", "sources/install.wim", "setup.exe"],
    )
    return iso_id
