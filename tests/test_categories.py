"""GET /v1/categories - shape, filters and counters."""

from __future__ import annotations

CATEGORY_KEYS = ["id", "name", "description", "position", "artwork_count"]
META_KEYS = ["page", "page_size", "total", "has_next"]


async def test_root_listing_matches_contract(client):
    response = await client.get("/v1/categories")
    assert response.status_code == 200
    body = response.json()

    assert list(body) == ["data", "meta"]
    assert list(body["meta"]) == META_KEYS
    assert body["meta"] == {"page": 1, "page_size": 20, "total": 2, "has_next": False}

    anime = body["data"][0]
    assert list(anime) == CATEGORY_KEYS
    assert anime == {
        "id": 1,
        "name": "Anime",
        "description": "Anime wallpapers wonderful styles .",
        "position": 0,
        "artwork_count": 4,
    }


async def test_root_listing_excludes_subcategories(client):
    body = (await client.get("/v1/categories")).json()
    assert [c["name"] for c in body["data"]] == ["Anime", "Nature"]


async def test_parent_filter_returns_live_and_static(client):
    body = (await client.get("/v1/categories", params={"parent_id": 1})).json()
    assert body["meta"]["total"] == 2
    # ordered by position: static (0) then live (1)
    assert [c["name"] for c in body["data"]] == ["Anime Static", "Anime Live"]
    assert [c["artwork_count"] for c in body["data"]] == [3, 1]


async def test_kind_filter_spans_all_roots(client):
    body = (await client.get("/v1/categories", params={"kind": "static"})).json()
    assert [c["name"] for c in body["data"]] == [
        "Anime Static",
        "Nature Static",
    ]

    live = (await client.get("/v1/categories", params={"kind": "live"})).json()
    assert [c["name"] for c in live["data"]] == ["Anime Live"]


async def test_root_artwork_count_rolls_up_children(client):
    body = (await client.get("/v1/categories")).json()
    counts = {c["name"]: c["artwork_count"] for c in body["data"]}
    assert counts == {"Anime": 4, "Nature": 1}


async def test_pagination_reports_total_and_has_next(client):
    first = (await client.get("/v1/categories", params={"page_size": 1})).json()
    assert first["meta"] == {"page": 1, "page_size": 1, "total": 2, "has_next": True}
    assert first["data"][0]["name"] == "Anime"

    second = (await client.get("/v1/categories", params={"page": 2, "page_size": 1})).json()
    assert second["meta"] == {"page": 2, "page_size": 1, "total": 2, "has_next": False}
    assert second["data"][0]["name"] == "Nature"


async def test_page_past_the_end_still_reports_total(client):
    body = (await client.get("/v1/categories", params={"page": 9, "page_size": 20})).json()
    assert body["data"] == []
    assert body["meta"]["total"] == 2
    assert body["meta"]["has_next"] is False


async def test_page_size_is_clamped_to_env_maximum(client, settings):
    body = (await client.get("/v1/categories", params={"page_size": 999})).json()
    assert body["meta"]["page_size"] == settings.PAGE_SIZE_MAX


async def test_invalid_kind_is_rejected(client):
    assert (await client.get("/v1/categories", params={"kind": "nope"})).status_code == 422
