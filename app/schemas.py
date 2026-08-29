"""Response contracts.

The Pydantic models below document the API (they drive /docs and the OpenAPI
schema). The hot path does not validate through them: `category_to_dict` and
`artwork_to_dict` map an asyncpg Record straight to the output dict, which is
then encoded once by orjson. Field order in the models and in the mappers is
kept identical, and matches the contract the Android client already parses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

CategoryKind = Literal["live", "static"]
MediaType = Literal["image", "video"]

DataT = TypeVar("DataT")


def iso_naive_utc(value: datetime) -> str:
    """Render a timestamp as naive UTC ISO-8601 with fixed microseconds.

    Timestamps are stored as timestamptz. The client contract carries no offset
    and always six fractional digits, e.g. 2026-08-23T10:48:52.245000.
    """
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")


# --------------------------------------------------------------------------- #
# documentation models
# --------------------------------------------------------------------------- #


class Meta(BaseModel):
    page: int = Field(examples=[1])
    page_size: int = Field(examples=[20])
    total: int = Field(examples=[1])
    has_next: bool = Field(examples=[False])


class Page(BaseModel, Generic[DataT]):
    data: list[DataT]
    meta: Meta


class Category(BaseModel):
    id: int
    name: str
    description: str | None
    position: int
    artwork_count: int


class Artwork(BaseModel):
    media_type: MediaType
    is_premium: bool
    title: str
    id: int
    category_id: int
    original_filename: str | None
    file_path: str
    created_at: str
    color_code: str | None
    # Additive fields. Older clients ignore unknown keys; new clients should use
    # thumb_path for grids - pulling full-size originals into a scrolling grid is
    # the single biggest cause of a slow-feeling wallpaper app.
    thumb_path: str | None
    preview_path: str | None
    width: int | None
    height: int | None
    file_size: int | None


class BundleNode(BaseModel):
    """A category with its media inlined. Roots carry subcategories, leaves carry artworks."""

    id: int
    name: str
    description: str | None
    position: int
    artwork_count: int
    kind: CategoryKind | None
    parent_id: int | None
    artworks: list[Artwork]
    subcategories: list[BundleNode]


class BundleMeta(BaseModel):
    artworks_returned: int
    limit_per_subcategory: int
    truncated: bool


class BundleResponse(BaseModel):
    data: BundleNode
    meta: BundleMeta


class Health(BaseModel):
    status: str
    env: str


# --------------------------------------------------------------------------- #
# hot-path mappers
# --------------------------------------------------------------------------- #


def category_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "position": row["position"],
        "artwork_count": row["artwork_count"],
    }


def artwork_to_dict(row: Any, media_base: str) -> dict[str, Any]:
    thumb = row["thumb_key"]
    preview = row["preview_key"]
    return {
        "media_type": row["media_type"],
        "is_premium": row["is_premium"],
        "title": row["title"],
        "id": row["id"],
        "category_id": row["category_id"],
        "original_filename": row["original_filename"],
        "file_path": f"{media_base}/{row['storage_key']}",
        "created_at": iso_naive_utc(row["created_at"]),
        "color_code": row["color_code"],
        "thumb_path": f"{media_base}/{thumb}" if thumb else None,
        "preview_path": f"{media_base}/{preview}" if preview else None,
        "width": row["width"],
        "height": row["height"],
        "file_size": row["bytes"],
    }


def build_meta(page: int, page_size: int, total: int) -> dict[str, Any]:
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": page * page_size < total,
    }
