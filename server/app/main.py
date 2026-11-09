"""netdispatch application factory.

Run with:  uvicorn app.main:create_app --factory --proxy-headers
(uvicorn reads FORWARDED_ALLOW_IPS from the environment to decide which
proxies may set X-Forwarded-For / X-Forwarded-Proto.)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .auth import AuthMiddleware, LoginRateLimiter, build_oauth
from .auth import router as auth_router
from .config import Settings
from .database import init_db, make_engine, make_sessionmaker
from .iso_tools import ExtractionManager, recover_on_startup
from .relay_cache import ManifestCache
from .routers import boot, devices, files, isos, relays, ui, unattend
from .unattend import sync_from_disk

APP_DIR = Path(__file__).resolve().parent
log = logging.getLogger("netdispatch")


def create_app(settings: Settings | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or Settings()
    settings.ensure_dirs()

    engine = make_engine(settings.effective_database_url)
    init_db(engine)
    session_factory = make_sessionmaker(engine)
    extractor = ExtractionManager(settings, session_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _ = settings.admin_password  # generates + logs the initial password if needed
        with session_factory() as db:
            recover_on_startup(db, settings, extractor)
            sync_from_disk(db, settings)
        log.info(
            "netdispatch %s ready - public URL %s, boot URL %s, OIDC %s",
            __version__,
            settings.public_url,
            settings.boot_url,
            "enabled" if app.state.oauth else "disabled",
        )
        yield
        engine.dispose()

    app = FastAPI(
        title="netdispatch",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = session_factory
    app.state.extractor = extractor
    app.state.manifests = ManifestCache()
    app.state.templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
    # Cache-buster for /static assets: changes whenever the image is rebuilt.
    css = APP_DIR / "static" / "app.css"
    app.state.templates.env.globals["asset_version"] = (
        str(int(css.stat().st_mtime)) if css.exists() else __version__
    )
    app.state.oauth = build_oauth(settings)
    app.state.login_limiter = LoginRateLimiter(settings.login_max_attempts, settings.login_window_seconds)

    # Middleware added last runs first: SessionMiddleware must wrap AuthMiddleware.
    app.add_middleware(AuthMiddleware, settings=settings)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="netdispatch_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.secure_cookies,
    )

    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static"), check_dir=False), name="static")
    for r in (auth_router, ui.router, boot.router, files.router, devices.router, relays.router, isos.router, unattend.router):
        app.include_router(r)
    return app
