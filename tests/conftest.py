"""Test harness: a throwaway database, migrated and seeded with fixed data.

The suite runs against a real Postgres because every read path is SQL - a mocked
database would test almost nothing here. Point TEST_DATABASE_URL at any server,
or let it derive `<DATABASE_URL>_test` from the normal one. If no server is
reachable the suite skips rather than fails.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.db import db
from app.http import init_response_cache
from app.main import create_app
from app.storage import storage

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "migrations"

MEDIA_BASE_URL = "http://cdn.test/media"

# Fixed fixture data. Timestamps and keys are literal so tests can assert the
# exact bytes the Android client will parse.
FIXTURES = """
INSERT INTO categories (id, name, slug, description, position) VALUES
    (1, 'Anime',  'anime',  'Anime wallpapers wonderful styles .', 0),
    (2, 'Nature', 'nature', 'Landscapes and open water.',          1);

INSERT INTO categories (id, parent_id, kind, name, slug, description, position) VALUES
    (3, 1, 'static', 'Anime Static',  'anime-static',  'Still anime.',  0),
    (4, 1, 'live',   'Anime Live',    'anime-live',    'Moving anime.', 1),
    (5, 2, 'static', 'Nature Static', 'nature-static', 'Still nature.', 0);

SELECT setval('categories_id_seq', 100, false);

INSERT INTO artworks (
    id, category_id, title, media_type, is_premium, storage_key, thumb_key,
    preview_key, original_filename, color_code, width, height, bytes, created_at
) VALUES
    (1, 3, 'anime static 1', 'image', true,
     'wallpaper/anime-static/static/2026/08/aaaa1111.png',
     'wallpaper/anime-static/static/2026/08/aaaa1111_thumb.webp',
     'wallpaper/anime-static/static/2026/08/aaaa1111_preview.webp',
     NULL, '#1b1f3b', 1440, 3120, 2500000, '2026-08-23T10:48:52.245+00'),
    (2, 3, 'anime static 2', 'image', false,
     'wallpaper/anime-static/static/2026/08/aaaa2222.png',
     NULL, NULL, 'anime_002.png', '#33658a', 1080, 1920, 900000,
     '2026-08-22T09:00:00+00'),
    (3, 3, 'anime static 3', 'image', false,
     'wallpaper/anime-static/static/2026/08/aaaa3333.png',
     NULL, NULL, NULL, NULL, NULL, NULL, NULL, '2026-08-21T09:00:00+00'),
    (4, 4, 'anime live 1', 'video', true,
     'wallpaper/anime-live/live/2026/08/bbbb1111.mp4',
     NULL, NULL, NULL, '#f26419', 1080, 1920, 5000000,
     '2026-08-20T09:00:00+00'),
    (5, 5, 'nature static 1', 'image', false,
     'wallpaper/nature-static/static/2026/08/cccc1111.png',
     NULL, NULL, NULL, '#2f4858', 1440, 3120, 1200000,
     '2026-08-19T09:00:00+00');

SELECT setval('artworks_id_seq', 100, false);
"""


def _test_dsn() -> str:
    """TEST_DATABASE_URL if set, else `<DATABASE_URL>_test` from the app settings.

    Reading it through Settings rather than os.environ means a DSN configured in
    .env is picked up, exactly as the running service would see it.
    """
    explicit = os.getenv("TEST_DATABASE_URL")
    if explicit:
        return explicit
    parts = urlsplit(get_settings().DATABASE_URL)
    return urlunsplit(parts._replace(path=parts.path.rstrip("/") + "_test"))


def _admin_dsn(dsn: str) -> tuple[str, str]:
    parts = urlsplit(dsn)
    dbname = parts.path.lstrip("/")
    return urlunsplit(parts._replace(path="/postgres")), dbname


async def _prepare(dsn: str) -> None:
    admin_dsn, dbname = _admin_dsn(dsn)
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()

    con = await asyncpg.connect(dsn)
    try:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            await con.execute(path.read_text(encoding="utf-8"))
        await con.execute(FIXTURES)
        await con.execute("SELECT recount_artwork_counts()")
    finally:
        await con.close()


@pytest.fixture(scope="session")
def dsn() -> str:
    """Build the test database once per session, or skip if none is reachable."""
    target = _test_dsn()
    try:
        asyncio.run(_prepare(target))
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"no Postgres available at {target}: {exc}")
    return target


@pytest.fixture(scope="session")
def settings(dsn: str) -> Settings:
    return Settings(
        ENV="local",
        DATABASE_URL=dsn,
        MEDIA_BASE_URL=MEDIA_BASE_URL,
        DB_POOL_MIN=1,
        DB_POOL_MAX=4,
        CACHE_ENABLED=False,  # per-test isolation; cache behaviour is tested explicitly
        STORAGE_BACKEND="none",
        ADMIN_ENABLED=False,
        RATE_LIMIT_ENABLED=False,
        LOG_LEVEL="WARNING",
    )


@pytest_asyncio.fixture
async def client(settings: Settings):
    """An HTTP client wired straight to the ASGI app - no socket, no server."""
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    init_response_cache(settings)
    await db.connect(settings)
    await storage.start(settings)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http
    finally:
        await storage.stop()
        await db.disconnect()


@pytest_asyncio.fixture
async def cached_client(settings: Settings):
    """Same app with the response cache switched on."""
    cached = settings.model_copy(update={"CACHE_ENABLED": True, "CACHE_TTL_SECONDS": 60})
    app = create_app(cached)
    app.dependency_overrides[get_settings] = lambda: cached

    init_response_cache(cached)
    await db.connect(cached)
    await storage.start(cached)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http
    finally:
        await storage.stop()
        await db.disconnect()
