"""Media endpoints: the flat feed, per-category listings and whole-category bundles."""

from __future__ import annotations

from tests.conftest import MEDIA_BASE_URL

CONTRACT_PREFIX = [
    "media_type",
    "is_premium",
    "title",
    "id",
    "category_id",
    "original_filename",
    "file_path",
    "created_at",
    "color_code",
]
ADDITIVE = ["thumb_path", "preview_path", "width", "height", "file_size"]


async def test_feed_matches_contract_exactly(client):
    response = await client.get("/v1/artworks")
    assert response.status_code == 200
    body = response.json()

    assert list(body) == ["data", "meta"]
    assert body["meta"] == {"page": 1, "page_size": 20, "total": 5, "has_next": False}

    newest = body["data"][0]
    # The nine contract keys come first, in the documented order.
    assert list(newest)[: len(CONTRACT_PREFIX)] == CONTRACT_PREFIX
    assert list(newest)[len(CONTRACT_PREFIX) :] == ADDITIVE

    assert newest["media_type"] == "image"
    assert newest["is_premium"] is True
    assert newest["title"] == "anime static 1"
    assert newest["id"] == 1
    assert newest["category_id"] == 3
    assert newest["original_filename"] is None
    assert newest["created_at"] == "2026-08-23T10:48:52.245000"
    assert newest["color_code"] == "#1b1f3b"


async def test_file_path_is_built_from_media_base_url(client):
    body = (await client.get("/v1/artworks")).json()
    newest = body["data"][0]
    assert newest["file_path"] == (
        f"{MEDIA_BASE_URL}/wallpaper/anime-static/static/2026/08/aaaa1111.png"
    )
    assert newest["thumb_path"] == (
        f"{MEDIA_BASE_URL}/wallpaper/anime-static/static/2026/08/aaaa1111_thumb.webp"
    )


async def test_missing_renditions_serialise_as_null(client):
    body = (await client.get("/v1/artworks")).json()
    bare = next(a for a in body["data"] if a["id"] == 3)
    assert bare["thumb_path"] is None
    assert bare["preview_path"] is None
    assert bare["color_code"] is None
    assert bare["width"] is None
    assert bare["file_size"] is None
    # file_path is never null - storage_key is NOT NULL.
    assert bare["file_path"].endswith("aaaa3333.png")


async def test_feed_is_newest_first(client):
    body = (await client.get("/v1/artworks")).json()
    assert [a["id"] for a in body["data"]] == [1, 2, 3, 4, 5]
    stamps = [a["created_at"] for a in body["data"]]
    assert stamps == sorted(stamps, reverse=True)


async def test_feed_filters(client):
    live = (await client.get("/v1/artworks", params={"kind": "live"})).json()
    assert [a["id"] for a in live["data"]] == [4]
    assert live["data"][0]["media_type"] == "video"

    premium = (await client.get("/v1/artworks", params={"is_premium": "true"})).json()
    assert {a["id"] for a in premium["data"]} == {1, 4}

    free = (await client.get("/v1/artworks", params={"is_premium": "false"})).json()
    assert {a["id"] for a in free["data"]} == {2, 3, 5}


async def test_category_listing_accepts_a_subcategory_id(client):
    body = (await client.get("/v1/categories/3/artworks")).json()
    assert body["meta"]["total"] == 3
    assert {a["category_id"] for a in body["data"]} == {3}


async def test_category_listing_accepts_a_root_id_and_includes_children(client):
    body = (await client.get("/v1/categories/1/artworks")).json()
    assert body["meta"]["total"] == 4
    assert {a["category_id"] for a in body["data"]} == {3, 4}


async def test_unknown_category_lists_empty_rather_than_404(client):
    body = (await client.get("/v1/categories/999/artworks")).json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


# --------------------------------------------------------------------------- #
# whole-category bundle
# --------------------------------------------------------------------------- #


async def test_bundle_returns_the_whole_category_in_one_response(client):
    response = await client.get("/v1/categories/1/bundle")
    assert response.status_code == 200
    body = response.json()

    root = body["data"]
    assert root["id"] == 1
    assert root["name"] == "Anime"
    assert root["kind"] is None
    assert root["parent_id"] is None
    # Media hangs off subcategories, never off a root.
    assert root["artworks"] == []

    static, live = root["subcategories"]
    assert (static["name"], static["kind"]) == ("Anime Static", "static")
    assert (live["name"], live["kind"]) == ("Anime Live", "live")
    assert [a["id"] for a in static["artworks"]] == [1, 2, 3]
    assert [a["id"] for a in live["artworks"]] == [4]

    assert body["meta"] == {
        "artworks_returned": 4,
        "limit_per_subcategory": 500,
        "truncated": False,
    }


async def test_bundle_artworks_use_the_same_shape_as_the_feed(client):
    bundle = (await client.get("/v1/categories/1/bundle")).json()
    feed = (await client.get("/v1/artworks")).json()
    first_in_bundle = bundle["data"]["subcategories"][0]["artworks"][0]
    assert first_in_bundle == feed["data"][0]


async def test_bundle_on_a_subcategory_returns_its_media_directly(client):
    body = (await client.get("/v1/categories/3/bundle")).json()
    root = body["data"]
    assert root["kind"] == "static"
    assert root["parent_id"] == 1
    assert root["subcategories"] == []
    assert [a["id"] for a in root["artworks"]] == [1, 2, 3]


async def test_bundle_limit_caps_media_and_flags_truncation(client):
    body = (await client.get("/v1/categories/1/bundle", params={"limit": 1})).json()
    static, live = body["data"]["subcategories"]
    assert [a["id"] for a in static["artworks"]] == [1]
    assert [a["id"] for a in live["artworks"]] == [4]
    assert body["meta"]["artworks_returned"] == 2
    assert body["meta"]["truncated"] is True


async def test_bundle_404s_on_unknown_category(client):
    assert (await client.get("/v1/categories/999/bundle")).status_code == 404


# --------------------------------------------------------------------------- #
# caching
# --------------------------------------------------------------------------- #


async def test_etag_round_trip_returns_304(client):
    first = await client.get("/v1/artworks")
    etag = first.headers["etag"]
    assert etag.startswith('W/"')
    assert "max-age" in first.headers["cache-control"]

    second = await client.get("/v1/artworks", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""


async def test_response_cache_serves_a_repeat_request(cached_client):
    first = await cached_client.get("/v1/categories")
    second = await cached_client.get("/v1/categories")
    assert first.headers["x-cache"] == "MISS"
    assert second.headers["x-cache"] == "HIT"
    assert first.json() == second.json()


async def test_cache_key_includes_the_query_string(cached_client):
    roots = await cached_client.get("/v1/categories")
    children = await cached_client.get("/v1/categories?parent_id=1")
    assert children.headers["x-cache"] == "MISS"
    assert roots.json() != children.json()


async def test_vary_is_sent_once_even_when_gzip_engages(client):
    """GZipMiddleware appends to Vary; the response must still carry one value."""
    response = await client.get("/v1/categories/1/bundle", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert len(response.headers.get_list("vary")) == 1
    assert response.headers["vary"] == "Accept-Encoding"


async def test_every_response_carries_a_request_id(client):
    response = await client.get("/v1/artworks")
    assert response.headers["x-request-id"]


async def test_health_and_readiness(client):
    health = await client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["database"] == "up"


async def test_admin_router_is_absent_when_disabled(client):
    assert (await client.post("/admin/categories", json={"name": "x"})).status_code == 404
