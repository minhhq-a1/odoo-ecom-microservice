"""FastAPI app entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.api.dependencies import require_admin_token
from src.api.middleware import BodySizeLimitMiddleware, TraceMiddleware
from src.api.routers import admin, config_admin, health, shopee_admin, webhooks
from src.core.config import settings
from src.core.database import close_engines
from src.core.logging import configure_logging, get_logger
from src.core.rate_limit import limiter
from src.core.redis import close_redis

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("api_starting", env=settings.ENVIRONMENT, dry_run=settings.MIDDLEWARE_DRY_RUN)
    yield
    await close_engines()
    await close_redis()
    logger.info("api_stopped")


# Disable FastAPI's default unauthenticated /docs and /openapi.json.
# Production: not served at all. Non-production: served via admin-token-
# gated routes below so the API schema isn't exposed to anyone on the
# network.
app = FastAPI(
    title="Odoo E-Commerce Middleware",
    version="2.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

if settings.ENVIRONMENT != "production":
    from src.api.dependencies import set_admin_cookie

    @app.get("/admin/openapi.json", include_in_schema=False)
    async def _admin_openapi(_: str = Depends(require_admin_token)) -> dict:
        return app.openapi()

    @app.get("/admin/docs", include_in_schema=False)
    async def _admin_docs(_: str = Depends(require_admin_token)):
        # Persist auth via cookie so Swagger UI's follow-up fetch of
        # /admin/openapi.json (which drops the ?token= query) succeeds.
        resp = get_swagger_ui_html(
            openapi_url="/admin/openapi.json",
            title=f"{app.title} – docs",
        )
        set_admin_cookie(resp)
        return resp


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, limiter._rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(TraceMiddleware)

app.include_router(health.router)
app.include_router(webhooks.router)
app.include_router(admin.router)
app.include_router(shopee_admin.router)
app.include_router(config_admin.router)
