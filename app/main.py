"""Application factory and ASGI entrypoint.

Run locally:   uvicorn app.main:app --reload
In a container: see the CMD in the Dockerfile.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asyncpg.exceptions import (
    IntegrityConstraintViolationError,
    UniqueViolationError,
)
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.db import db
from app.http import init_response_cache
from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware
from app.ratelimit import RateLimitMiddleware
from app.routers import admin, artworks, categories, health
from app.storage import storage

log = logging.getLogger("wallmo")

TITLE = "wallmo"
VERSION = "0.1.0"
DESCRIPTION = """
Wallpaper catalogue for the Android client. Public read endpoints only.

Categories are a one-level tree: root categories (Anime, Nature, ...) each have
a `live` and a `static` subcategory, and only subcategories hold media.

To open a category, prefer `GET /v1/categories/{id}/bundle` - it returns the
category, its subcategories and all of their media in a single response.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    init_response_cache(settings)
    await db.connect(settings)
    await storage.start(settings)
    log.info("wallmo up (env=%s)", settings.ENV)
    try:
        yield
    finally:
        await storage.stop()
        await db.disconnect()
        log.info("wallmo down")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    if settings.SENTRY_DSN:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENV,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        )

    # The list endpoints return pre-encoded orjson bytes directly (see
    # app/http.py), so they bypass response serialisation entirely. Everything
    # else uses FastAPI's own JSON encoder, which is now the fastest path.
    app = FastAPI(
        title=TITLE,
        version=VERSION,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if settings.DOCS_ENABLED else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.DOCS_ENABLED else None,
    )
    app.state.settings = settings

    # Middleware runs outermost-last, so the request-id/access log wraps
    # everything and rate limiting rejects before any work is done.
    app.add_middleware(GZipMiddleware, minimum_size=settings.GZIP_MIN_SIZE)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["*"],
        max_age=86_400,
    )
    if settings.RATE_LIMIT_ENABLED:
        app.add_middleware(
            RateLimitMiddleware,
            per_minute=settings.RATE_LIMIT_PER_MINUTE,
            burst=settings.RATE_LIMIT_BURST,
        )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(categories.router)
    app.include_router(artworks.router)
    if settings.ADMIN_ENABLED:
        app.include_router(admin.router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, object]:
        return {
            "service": TITLE,
            "version": VERSION,
            "docs": "/docs" if settings.DOCS_ENABLED else None,
            "endpoints": [
                "/v1/categories",
                "/v1/categories/{id}/artworks",
                "/v1/categories/{id}/bundle",
                "/v1/artworks",
            ],
        }

    @app.exception_handler(IntegrityConstraintViolationError)
    async def integrity_violation(
        request: Request, exc: IntegrityConstraintViolationError
    ) -> JSONResponse:
        """Turn database constraints into meaningful status codes.

        The schema is the authority on what is valid - unique slugs, the
        root-has-no-kind rule, media only on subcategories. A violation is a bad
        request, not a server fault, so it must not surface as a 500.
        """
        conflict = isinstance(exc, UniqueViolationError)
        log.warning(
            "integrity violation on %s %s: %s",
            request.method,
            request.url.path,
            exc,
            extra={"request_id": request.scope.get("request_id", "-")},
        )
        return JSONResponse(
            status_code=409 if conflict else 422,
            content={
                "detail": "conflict" if conflict else "constraint_violation",
                "constraint": getattr(exc, "constraint_name", None),
                "message": getattr(exc, "message", None) or str(exc),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception(
            "unhandled error on %s %s",
            request.method,
            request.url.path,
            extra={"request_id": request.scope.get("request_id", "-")},
        )
        return JSONResponse(status_code=500, content={"detail": "internal_error"})

    return app


app = create_app()
