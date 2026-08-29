"""GET /v1/artworks - the flat, newest-first media feed."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response

from app import queries as q
from app.config import Settings, get_settings
from app.db import db
from app.http import serve_json
from app.schemas import Artwork, CategoryKind, Page, artwork_to_dict, build_meta

router = APIRouter(prefix="/v1/artworks", tags=["artworks"])


@router.get(
    "",
    responses={200: {"model": Page[Artwork]}},
    summary="List all media, newest first",
)
async def list_artworks(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=1000),
    kind: CategoryKind | None = Query(None, description="Filter by subcategory kind"),
    is_premium: bool | None = Query(None),
    settings: Settings = Depends(get_settings),
) -> Response:
    size = settings.clamp_page_size(page_size)
    rows_sql, count_sql = q.artwork_list(False, kind is not None, is_premium is not None)

    filters: list[Any] = []
    if kind is not None:
        filters.append(kind)
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
