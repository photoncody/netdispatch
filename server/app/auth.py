"""Authentication: OIDC (Authlib) with optional auto-SSO, a local emergency
admin, the session guard middleware, and relay key verification."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import quote, urlencode

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings

log = logging.getLogger("netdispatch.auth")

# Paths reachable without a browser session. Boot clients and relays never
# hold a session; relays authenticate with X-Relay-Key instead.
EXEMPT_EXACT = {
    "/health",
    "/boot",
    "/login",
    "/logout",
    "/favicon.ico",
    "/api/relays/heartbeat",
    "/api/relays/cache/manifest",
}
EXEMPT_PREFIXES = ("/files/", "/static/", "/auth/")

CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "netdispatch"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def is_exempt(path: str) -> bool:
    return path in EXEMPT_EXACT or path.startswith(EXEMPT_PREFIXES)


def safe_next(value: str | None) -> str:
    """Only allow same-site relative redirect targets."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_relay_key() -> str:
    return "ndr_" + secrets.token_urlsafe(32)


class AuthMiddleware:
    """Pure-ASGI session guard (does not buffer request/response bodies, which
    matters for multi-GB ISO uploads and downloads).

    Must run *inside* Starlette's SessionMiddleware.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if is_exempt(path):
            await self.app(scope, receive, send)
            return

        session = scope.get("session") or {}
        user = session.get("user")
        method = scope["method"]

        if user:
            if path.startswith("/api/") and method in UNSAFE_METHODS:
                headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
                if headers.get(CSRF_HEADER) != CSRF_VALUE:
                    resp = JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
                    await resp(scope, receive, send)
                    return
            await self.app(scope, receive, send)
            return

        if path.startswith("/api/"):
            resp: Response = JSONResponse({"detail": "not authenticated"}, status_code=401)
        else:
            target = path
            if scope.get("query_string"):
                target += "?" + scope["query_string"].decode("latin-1")
            resp = RedirectResponse(self.login_redirect(target), status_code=302)
        await resp(scope, receive, send)

    def login_redirect(self, next_path: str) -> str:
        nxt = quote(safe_next(next_path), safe="/")
        if self.settings.oidc_enabled and self.settings.auto_redirect_sso:
            return f"/auth/oidc/login?next={nxt}"
        return f"/login?next={nxt}"


class LoginRateLimiter:
    def __init__(self, max_attempts: int, window: int):
        self.max_attempts = max_attempts
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return len(q) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        self._hits[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


def build_oauth(settings: Settings) -> OAuth | None:
    if not settings.oidc_enabled:
        return None
    missing = [
        n
        for n, v in (
            ("OIDC_CLIENT_ID", settings.oidc_client_id),
            ("OIDC_METADATA_URL", settings.oidc_metadata_url),
        )
        if not v
    ]
    if missing:
        log.error("OIDC_ENABLED=true but %s not set; SSO disabled", ", ".join(missing))
        return None
    oauth = OAuth()
    oauth.register(
        name="oidc",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret or None,
        server_metadata_url=settings.oidc_metadata_url,
        client_kwargs={"scope": settings.oidc_scopes, "code_challenge_method": "S256"},
    )
    return oauth


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


router = APIRouter()


def _login_page(request: Request, *, error: str | None = None, status_code: int = 200, force_local=False):
    settings: Settings = request.app.state.settings
    templates = request.app.state.templates
    sso_available = request.app.state.oauth is not None
    local_available = bool(settings.admin_password)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": error,
            "next": safe_next(request.query_params.get("next")),
            "sso_available": sso_available,
            "sso_name": settings.oidc_provider_name,
            "local_available": local_available,
            "show_local": local_available and (force_local or not sso_available),
            "username": settings.fallback_admin_username,
        },
        status_code=status_code,
    )


@router.get("/login")
async def login_page(request: Request):
    settings: Settings = request.app.state.settings
    params = request.query_params
    if request.session.get("user"):
        return RedirectResponse(safe_next(params.get("next")), status_code=302)
    force_local = params.get("local", "").lower() in ("1", "true", "yes")
    if (
        request.app.state.oauth is not None
        and settings.auto_redirect_sso
        and not force_local
        and not params.get("error")
        and not params.get("logged_out")
    ):
        nxt = quote(safe_next(params.get("next")), safe="/")
        return RedirectResponse(f"/auth/oidc/login?next={nxt}", status_code=302)
    error = {
        "sso_unavailable": "The SSO provider could not be reached. Use the local admin login.",
        "sso_failed": "Single sign-on failed. Try again or use the local admin login.",
        "forbidden": "Your account is not a member of an allowed group.",
    }.get(params.get("error", ""))
    return _login_page(request, error=error, force_local=force_local or bool(error))


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    settings: Settings = request.app.state.settings
    limiter: LoginRateLimiter = request.app.state.login_limiter
    ip = _client_ip(request)
    if not settings.admin_password:
        return _login_page(request, error="Local login is disabled.", status_code=403, force_local=True)
    if limiter.blocked(ip):
        return _login_page(
            request, error="Too many failed attempts. Try again later.", status_code=429, force_local=True
        )
    ok_user = hmac.compare_digest(username.encode(), settings.fallback_admin_username.encode())
    ok_pass = hmac.compare_digest(password.encode(), settings.admin_password.encode())
    if not (ok_user and ok_pass):
        limiter.record_failure(ip)
        log.warning("failed local login for %r from %s", username, ip)
        return _login_page(request, error="Invalid username or password.", status_code=401, force_local=True)
    limiter.reset(ip)
    request.session.clear()
    request.session["user"] = {"name": username, "method": "local"}
    log.info("local admin %r logged in from %s", username, ip)
    return RedirectResponse(safe_next(next), status_code=303)


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login?logged_out=1", status_code=303)


@router.get("/auth/oidc/login")
async def oidc_login(request: Request):
    settings: Settings = request.app.state.settings
    oauth: OAuth | None = request.app.state.oauth
    if oauth is None:
        return RedirectResponse("/login?local=true", status_code=302)
    request.session["oidc_next"] = safe_next(request.query_params.get("next"))
    redirect_uri = f"{settings.public_url}/auth/oidc/callback"
    try:
        return await oauth.oidc.authorize_redirect(request, redirect_uri)
    except Exception:  # noqa: BLE001 - provider unreachable / bad metadata
        log.exception("could not start OIDC login")
        return RedirectResponse("/login?" + urlencode({"error": "sso_unavailable"}), status_code=302)


@router.get("/auth/oidc/callback")
async def oidc_callback(request: Request):
    settings: Settings = request.app.state.settings
    oauth: OAuth | None = request.app.state.oauth
    if oauth is None:
        return RedirectResponse("/login?local=true", status_code=302)
    try:
        token = await oauth.oidc.authorize_access_token(request)
        claims = dict(token.get("userinfo") or {})
        if not claims:
            claims = dict(await oauth.oidc.userinfo(token=token))
    except Exception:  # noqa: BLE001
        log.exception("OIDC callback failed")
        return RedirectResponse("/login?error=sso_failed", status_code=302)

    allowed = settings.allowed_groups
    if allowed:
        groups = claims.get(settings.oidc_groups_claim) or []
        if isinstance(groups, str):
            groups = [groups]
        if not allowed.intersection(str(g) for g in groups):
            log.warning("OIDC user %s rejected: not in %s", claims.get("sub"), sorted(allowed))
            return RedirectResponse("/login?error=forbidden", status_code=302)

    nxt = safe_next(request.session.pop("oidc_next", "/"))
    request.session.clear()
    request.session["user"] = {
        "name": claims.get("name") or claims.get("preferred_username") or claims.get("email") or claims.get("sub"),
        "email": claims.get("email"),
        "sub": claims.get("sub"),
        "method": "oidc",
    }
    log.info("OIDC login: %s", request.session["user"]["name"])
    return RedirectResponse(nxt, status_code=302)
