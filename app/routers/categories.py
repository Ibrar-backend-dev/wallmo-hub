"""Category listings, per-category media, and whole-category bundles.

The bundle endpoint is what the Android client should use to open a category:
it returns the category, its live/static subcategories, and all of their media
in a single response, so opening a category is one request instead of one per
subcategory plus a page walk.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app import queries as q
from app.config import Settings, get_settings
from app.db import db
from app.http import serve_json
from app.schemas import (
    Artwork,
    BundleResponse,
    Category,
    CategoryKind,
    Page,
    artwork_to_dict,
    build_meta,
    category_to_dict,
)

router = APIRouter(prefix="/v1/categories", tags=["categories"])


@router.get(
    "",
    responses={200: {"model": Page[Category]}},
    summary="List categories",
    description=(
        "No filter returns root categories. `parent_id` returns that category's "
        "live/static subcategories. `kind` returns every subcategory of that kind."
    ),
)
async def list_categories(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=1000),
    parent_id: int | None = Query(None, ge=1),
    kind: CategoryKind | None = Query(None),
    settings: Settings = Depends(get_settings),
) -> Response:
    size = settings.clamp_page_size(page_size)
    rows_sql, count_sql = q.category_list(parent_id is not None, kind is not None)

    filters: list[Any] = []
    if parent_id is not None:
        filters.append(parent_id)
    if kind is not None:
        filters.append(kind)

    async def build() -> dict[str, Any]:
        rows, total = await db.fetch_page(rows_sql, count_sql, filters, size, (page - 1) * size)
        return {
            "data": [category_to_dict(r) for r in rows],
            "meta": build_meta(page, size, total),
        }

    return await serve_json(request, settings, build)


@router.get(
    "/{category_id}/artworks",
    responses={200: {"model": Page[Artwork]}},
    summary="List media in a category",
    description=(
        "Accepts a root id or a subcategory id. A root id includes the media of "
        "all its subcategories."
    ),
)
async def list_category_artworks(
    request: Request,
    category_id: int,
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=1000),
    is_premium: bool | None = Query(None),
    settings: Settings = Depends(get_settings),
) -> Response:
    size = settings.clamp_page_size(page_size)
    rows_sql, count_sql = q.artwork_list(True, False, is_premium is not None)

    filters: list[Any] = [category_id]
    if is_premium is not None:
        filters.append(is_premium)

    async def build() -> dict[str, Any]:
        rows, total = await db.fetch_page(rows_sql, count_sql, filters, size, (page - 1) * size)
        base = settings.MEDIA_BASE_URL
        return {
            "data": [artwork_to_dict(r, base) for r in rows],
            "meta": build_meta(page, size, total),
        }

    return await serve_json(request, settings, build)


def _node(row: Any) -> dict[str, Any]:
    node = category_to_dict(row)
    node["kind"] = row["kind"]
    node["parent_id"] = row["parent_id"]
    node["artworks"] = []
    node["subcategories"] = []
    return node


@router.get(
    "/{category_id}/bundle",
    responses={200: {"model": BundleResponse}},
    summary="Whole category in one response",
    description=(
        "The category, its subcategories, and all of their media in a single "
        "payload. Media is capped per subcategory by `limit` "
        "(BUNDLE_LIMIT_DEFAULT / BUNDLE_LIMIT_MAX); `meta.truncated` reports "
        "whether the cap was hit."
    ),
)
async def category_bundle(
    request: Request,
    category_id: int,
    limit: int | None = Query(None, ge=1, le=5000, description="Max media per subcategory"),
    settings: Settings = Depends(get_settings),
) -> Response:
    per_category = settings.clamp_bundle_limit(limit)

    async def build() -> dict[str, Any]:
        root_row = await db.fetchrow(q.CATEGORY_NODE, category_id)
        if root_row is None:
            raise HTTPException(status_code=404, detail="category_not_found")

        child_rows = await db.fetch(q.CATEGORY_CHILDREN, category_id)
        root = _node(root_row)
        children = [_node(r) for r in child_rows]
        nodes = {n["id"]: n for n in (root, *children)}

        rows = await db.fetch(q.BUNDLE_ARTWORKS, list(nodes), per_category)
        base = settings.MEDIA_BASE_URL
        for row in rows:
            nodes[row["category_id"]]["artworks"].append(artwork_to_dict(row, base))

        root["subcategories"] = children
        truncated = any(n["artwork_count"] > per_category for n in nodes.values())
        return {
            "data": root,
            "meta": {
                "artworks_returned": len(rows),
                "limit_per_subcategory": per_category,
                "truncated": truncated,
            },
        }

    return await serve_json(request, settings, build)
