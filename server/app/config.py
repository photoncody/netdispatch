"""Runtime configuration, driven entirely by environment variables."""

from __future__ import annotations

import logging
import secrets
from functools import cached_property
from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("netdispatch.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # --- URLs -----------------------------------------------------------
    # Public URL users reach the UI at (through the reverse proxy). Used for
    # OIDC redirect URIs and as the default boot URL.
    public_url: str = "http://localhost:8000"
    # URL PXE clients use to fetch boot scripts and payloads. Defaults to
    # PUBLIC_URL. Point this at a plain-HTTP LAN address if your proxy uses a
    # certificate iPXE does not trust, or to keep multi-GB WIM transfers off TLS.
    boot_base_url: str = ""
    # Base URL that Linux initramfs downloads use: the {iso_url} and {files_url}
    # kernel cmdline placeholders. Defaults to the boot URL. casper (Ubuntu,
    # Mint) and Debian live-boot fetch with busybox wget, which has no TLS, so
    # point this at a plain-HTTP address of the hub when the boot URL is https.
    # Only /files/iso/* (already unauthenticated) is reached through it.
    linux_fetch_url: str = ""

    # --- Storage --------------------------------------------------------
    data_dir: Path = Path("/data")
    database_url: str = ""  # default: sqlite:///<data_dir>/netdispatch.db

    # --- Sessions -------------------------------------------------------
    secret_key: str = ""  # generated and persisted to <data_dir>/secret_key if empty
    session_max_age: int = 12 * 3600

    # --- OIDC -----------------------------------------------------------
    oidc_enabled: bool = False
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_metadata_url: str = ""
    oidc_scopes: str = "openid email profile"
    oidc_provider_name: str = "SSO"
    oidc_allowed_groups: str = ""  # comma-separated; empty = any authenticated user
    oidc_groups_claim: str = "groups"
    auto_redirect_sso: bool = False

    # --- Local fallback admin -------------------------------------------
    fallback_admin_username: str = "admin"
    # Empty: a random password is generated on first start, written to
    # <data_dir>/initial_admin_password and logged once.
    fallback_admin_password: str = ""
    fallback_admin_enabled: bool = True
    login_max_attempts: int = 10
    login_window_seconds: int = 300

    # --- Relays ---------------------------------------------------------
    # Optional bootstrap key. Relays presenting it are auto-registered.
    # Prefer per-relay keys created in the UI.
    relay_api_key: str = ""
    relay_offline_seconds: int = 90
    relay_build_context: str = "https://github.com/photoncody/netdispatch.git#main:relay"
    relay_image: str = ""  # if set, compose snippets use `image:` instead of `build:`

    # --- Devices / boot loop -------------------------------------------
    wait_poll_seconds: int = 5
    device_offline_seconds: int = 60
    new_device_state: str = "waiting"  # waiting | local

    # --- SMB (Windows Setup media) -------------------------------------
    smb_host: str = ""  # default: host part of BOOT_BASE_URL
    smb_share: str = "netdispatch"
    smb_username: str = "netdispatch"
    smb_password: str = ""

    # --- Bundled boot helpers ------------------------------------------
    wimboot_path: Path = Path("/opt/netdispatch/wimboot")
    memdisk_path: Path = Path("/opt/netdispatch/memdisk")

    @field_validator("public_url", "boot_base_url", "linux_fetch_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.strip().rstrip("/")

    @field_validator("smb_username")
    @classmethod
    def _valid_smb_user(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", v):
            raise ValueError("SMB_USERNAME may only contain letters, digits, '.', '_' and '-'")
        return v

    @field_validator("smb_password")
    @classmethod
    def _valid_smb_password(cls, v: str) -> str:
        if any(c in v for c in '"\r\n'):
            raise ValueError("SMB_PASSWORD may not contain double quotes or newlines")
        return v

    @field_validator("new_device_state")
    @classmethod
    def _valid_state(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"waiting", "local"}:
            raise ValueError("NEW_DEVICE_STATE must be 'waiting' or 'local'")
        return v

    # --- Derived --------------------------------------------------------
    @property
    def boot_url(self) -> str:
        return self.boot_base_url or self.public_url

    @property
    def effective_linux_fetch_url(self) -> str:
        return self.linux_fetch_url or self.boot_url

    @property
    def isos_dir(self) -> Path:
        return self.data_dir / "isos"

    @property
    def extracted_dir(self) -> Path:
        return self.data_dir / "extracted"

    @property
    def configs_dir(self) -> Path:
        return self.data_dir / "configs"

    @property
    def effective_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'netdispatch.db'}"

    @property
    def effective_smb_host(self) -> str:
        return self.smb_host or (urlparse(self.boot_url).hostname or "")

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.lower().startswith("https://")

    @property
    def allowed_groups(self) -> set[str]:
        return {g.strip() for g in self.oidc_allowed_groups.split(",") if g.strip()}

    @cached_property
    def session_secret(self) -> str:
        if self.secret_key:
            return self.secret_key
        return _persisted_secret(self.data_dir / "secret_key", 48, "session secret")

    @cached_property
    def admin_password(self) -> str:
        if not self.fallback_admin_enabled:
            return ""
        if self.fallback_admin_password:
            return self.fallback_admin_password
        path = self.data_dir / "initial_admin_password"
        existed = path.exists()
        pw = _persisted_secret(path, 18, "initial admin password")
        if not existed:
            log.warning(
                "FALLBACK_ADMIN_PASSWORD is not set. Generated a local admin password "
                "for user '%s': %s  (stored in %s)",
                self.fallback_admin_username,
                pw,
                path,
            )
        return pw

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.isos_dir, self.extracted_dir, self.configs_dir):
            d.mkdir(parents=True, exist_ok=True)


def _persisted_secret(path: Path, nbytes: int, what: str) -> str:
    try:
        value = path.read_text().strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    value = secrets.token_urlsafe(nbytes)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    log.info("Generated %s at %s", what, path)
    return value
