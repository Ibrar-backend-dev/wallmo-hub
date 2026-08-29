"""Write surface: the env-driven guard, category CRUD, and the DB invariants.

These tests run against their own database so their writes cannot perturb the
exact totals the read-path tests assert.
"""

from __future__ import annotations

import asyncio
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
from tests.conftest import MEDIA_BASE_URL, _prepare

API_KEY = "test-admin-key"


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    parts = urlsplit(get_settings().DATABASE_URL)
    target = urlunsplit(parts._replace(path=parts.path.rstrip("/") + "_admin"))
    try:
        asyncio.run(_prepare(target))
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"no Postgres available at {target}: {exc}")
    return target


@pytest.fixture(scope="session")
def admin_settings(admin_dsn: str) -> Settings:
    return Settings(
        ENV="local",
        DATABASE_URL=admin_dsn,
        MEDIA_BASE_URL=MEDIA_BASE_URL,
        DB_POOL_MIN=1,
        DB_POOL_MAX=4,
        CACHE_ENABLED=False,
        STORAGE_BACKEND="none",
        ADMIN_ENABLED=True,
        ADMIN_API_KEY=API_KEY,
        LOG_LEVEL="WARNING",
    )


@pytest_asyncio.fixture
async def admin(admin_settings: Settings):
    app = create_app(admin_settings)
    app.dependency_overrides[get_settings] = lambda: admin_settings

    init_response_cache(admin_settings)
    await db.connect(admin_settings)
    await storage.start(admin_settings)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": API_KEY},
        ) as http:
            yield http
    finally:
        await storage.stop()
        await db.disconnect()


# --------------------------------------------------------------------------- #
# the guard is entirely environment-driven
# --------------------------------------------------------------------------- #


async def test_missing_key_is_rejected_when_a_key_is_configured(admin):
    response = await admin.post(
        "/admin/categories", json={"name": "Nope"}, headers={"X-API-Key": ""}
    )
    assert response.status_code == 403


async def test_wrong_key_is_rejected(admin):
    response = await admin.post(
        "/admin/categories", json={"name": "Nope"}, headers={"X-API-Key": "wrong"}
    )
    assert response.status_code == 403


async def test_blank_configured_key_means_no_check(admin_settings):
    """ADMIN_API_KEY="" is the documented way to run the write path open."""
    open_settings = admin_settings.model_copy(update={"ADMIN_API_KEY": ""})
    app = create_app(open_settings)
    app.dependency_overrides[get_settings] = lambda: open_settings
    init_response_cache(open_settings)
    await db.connect(open_settings)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.patch("/admin/categories/999", json={"name": "x"})
        # 404 from the handler, not 403 from the guard: the key check was skipped.
        assert response.status_code == 404
    finally:
        await db.disconnect()


# --------------------------------------------------------------------------- #
# categories
# --------------------------------------------------------------------------- #


async def test_create_root_then_subcategory(admin):
    root = await admin.post(
        "/admin/categories", json={"name": "Cars", "description": "Fast things"}
    )
    assert root.status_code == 201
    root_body = root.json()["data"]
    assert root_body["kind"] is None
    assert root_body["parent_id"] is None
    assert root_body["artwork_count"] == 0

    child = await admin.post(
        "/admin/categories",
        json={"name": "Cars Live", "parent_id": root_body["id"], "kind": "live"},
    )
    assert child.status_code == 201
    assert child.json()["data"]["kind"] == "live"
    assert child.json()["data"]["parent_id"] == root_body["id"]

    listed = await admin.get("/v1/categories", params={"parent_id": root_body["id"]})
    assert [c["name"] for c in listed.json()["data"]] == ["Cars Live"]


async def test_root_with_a_kind_is_rejected(admin):
    response = await admin.post("/admin/categories", json={"name": "Bad", "kind": "live"})
    assert response.status_code == 422


async def test_subcategory_without_a_kind_is_rejected(admin):
    response = await admin.post("/admin/categories", json={"name": "Bad", "parent_id": 1})
    assert response.status_code == 422


async def test_slug_is_derived_and_must_stay_unique(admin):
    first = await admin.post("/admin/categories", json={"name": "Space Shots"})
    assert first.status_code == 201
    assert first.json()["data"]["name"] == "Space Shots"

    duplicate = await admin.post("/admin/categories", json={"name": "Space Shots"})
    # A unique violation is a client problem, so it must be a 409 - not a 500.
    assert duplicate.status_code == 409
    body = duplicate.json()
    assert body["detail"] == "conflict"
    assert body["constraint"] == "categories_slug_key"


async def test_a_parent_gets_at_most_one_subcategory_per_kind(admin):
    """Enforced by the partial unique index on (parent_id, kind)."""
    response = await admin.post(
        "/admin/categories",
        json={"name": "Sneaky", "parent_id": 1, "kind": "static"},
    )
    # Category 3 already occupies (parent 1, static).
    assert response.status_code == 409
    assert response.json()["constraint"] == "categories_parent_kind_key"


async def test_patch_updates_only_supplied_fields(admin):
    created = (await admin.post("/admin/categories", json={"name": "Retro"})).json()["data"]
    patched = await admin.patch(f"/admin/categories/{created['id']}", json={"position": 7})
    assert patched.status_code == 200
    body = patched.json()["data"]
    assert body["position"] == 7
    assert body["name"] == "Retro"  # untouched


async def test_soft_delete_removes_it_from_public_listings(admin):
    created = (await admin.post("/admin/categories", json={"name": "Temporary"})).json()["data"]
    before = (await admin.get("/v1/categories", params={"page_size": 200})).json()
    assert created["id"] in {c["id"] for c in before["data"]}

    deleted = await admin.delete(f"/admin/categories/{created['id']}")
    assert deleted.status_code == 200

    after = (await admin.get("/v1/categories", params={"page_size": 200})).json()
    assert created["id"] not in {c["id"] for c in after["data"]}
    # Deleting twice is not an error the client should retry against.
    assert (await admin.delete(f"/admin/categories/{created['id']}")).status_code == 404


async def test_patch_unknown_category_is_404(admin):
    assert (await admin.patch("/admin/categories/99999", json={"name": "x"})).status_code == 404


# --------------------------------------------------------------------------- #
# uploads and database invariants
# --------------------------------------------------------------------------- #


async def test_upload_requires_a_configured_storage_backend(admin):
    response = await admin.post(
        "/admin/artworks",
        data={"category_id": "3", "title": "x"},
        files={"file": ("x.png", b"not-a-real-png", "image/png")},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "storage_not_configured"


async def test_media_cannot_attach_to_a_root_category(admin):
    """Enforced by a trigger, so a bulk load cannot break it either."""
    with pytest.raises(asyncpg.PostgresError) as excinfo:
        await db.execute(
            """
            INSERT INTO artworks (category_id, title, media_type, storage_key)
            VALUES (1, 'bad', 'image', 'wallpaper/bad/static/2026/08/deadbeef.png')
            """
        )
    assert "subcategory" in str(excinfo.value)


async def test_counters_follow_inserts_and_soft_deletes(admin):
    async def counts() -> tuple[int, int]:
        root = await db.fetchval("SELECT artwork_count FROM categories WHERE id = 1")
        leaf = await db.fetchval("SELECT artwork_count FROM categories WHERE id = 3")
        return root, leaf

    root_before, leaf_before = await counts()

    artwork_id = await db.fetchval(
        """
        INSERT INTO artworks (category_id, title, media_type, storage_key)
        VALUES (3, 'counted', 'image', 'wallpaper/anime-static/static/2026/08/counted.png')
        RETURNING id
        """
    )
    assert await counts() == (root_before + 1, leaf_before + 1)

    await db.execute("UPDATE artworks SET is_active = false WHERE id = $1", artwork_id)
    assert await counts() == (root_before, leaf_before)

    await db.execute("UPDATE artworks SET is_active = true WHERE id = $1", artwork_id)
    assert await counts() == (root_before + 1, leaf_before + 1)

    await db.execute("DELETE FROM artworks WHERE id = $1", artwork_id)
    assert await counts() == (root_before, leaf_before)


async def test_recount_repairs_drifted_counters(admin):
    await db.execute("UPDATE categories SET artwork_count = 999 WHERE id = 3")
    response = await admin.post("/admin/maintenance/recount")
    assert response.status_code == 200
    assert await db.fetchval("SELECT artwork_count FROM categories WHERE id = 3") == 3
