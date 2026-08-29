"""Write surface. Ingest lives here; the public read API stays unauthenticated.

The whole router is mounted only when ADMIN_ENABLED=true, so a production read
deployment exposes no write path at all. ADMIN_API_KEY is an optional extra
check and is skipped when blank - both are environment decisions, not code ones.
"""

from __future__ import annotations

import re
import secrets
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from app import queries as q
from app.config import Settings, get_settings
from app.db import db
from app.http import clear_response_cache
from app.schemas import CategoryKind, artwork_to_dict, category_to_dict
from app.storage import Storage, make_renditions, storage

router = APIRouter(prefix="/admin", tags=["admin"])

_CHUNK = 1 << 20


async def require_admin(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.ADMIN_ENABLED:
        raise HTTPException(status_code=404, detail="not_found")
    if settings.ADMIN_API_KEY and not secrets.compare_digest(
        x_api_key or "", settings.ADMIN_API_KEY
    ):
        raise HTTPException(status_code=403, detail="forbidden")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


# --------------------------------------------------------------------------- #
# categories
# --------------------------------------------------------------------------- #


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: int | None = None
    kind: CategoryKind | None = None
    slug: str | None = None
    description: str | None = None
    position: int = 0
    is_active: bool = True


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    position: int | None = None
    is_active: bool | None = None


def _category_out(row: Any) -> dict[str, Any]:
    out = category_to_dict(row)
    out["kind"] = row["kind"]
    out["parent_id"] = row["parent_id"]
    return out


@router.post(
    "/categories",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_category(body: CategoryCreate) -> dict[str, Any]:
    # A root category has no kind; a subcategory must declare live or static.
    if (body.parent_id is None) != (body.kind is None):
        raise HTTPException(
            status_code=422,
            detail="a root category has no kind; a subcategory requires kind=live|static",
        )
    row = await db.fetchrow(
        q.CATEGORY_INSERT,
        body.parent_id,
        body.kind,
        body.name,
        body.slug or _slugify(body.name),
        body.description,
        body.position,
        body.is_active,
    )
    clear_response_cache()
    return {"data": _category_out(row)}


@router.patch("/categories/{category_id}", dependencies=[Depends(require_admin)])
async def update_category(category_id: int, body: CategoryUpdate) -> dict[str, Any]:
    row = await db.fetchrow(
        q.CATEGORY_UPDATE,
        category_id,
        body.name,
        body.description,
        body.position,
        body.is_active,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="category_not_found")
    clear_response_cache()
    return {"data": _category_out(row)}


@router.delete("/categories/{category_id}", dependencies=[Depends(require_admin)])
async def delete_category(category_id: int) -> dict[str, Any]:
    row = await db.fetchrow(q.CATEGORY_SOFT_DELETE, category_id)
    if row is None:
        raise HTTPException(status_code=404, detail="category_not_found")
    clear_response_cache()
    return {"data": {"id": row["id"], "is_active": False}}


# --------------------------------------------------------------------------- #
# artworks
# --------------------------------------------------------------------------- #


async def _read_upload(file: UploadFile, limit: int) -> bytes:
    buf = bytearray()
    while chunk := await file.read(_CHUNK):
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(status_code=413, detail=f"file exceeds UPLOAD_MAX_BYTES ({limit})")
    if not buf:
        raise HTTPException(status_code=422, detail="empty_file")
    return bytes(buf)


@router.post(
    "/artworks",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    summary="Upload one media file into a subcategory",
)
async def create_artwork(
    category_id: int = Form(...),
    title: str = Form(...),
    file: UploadFile = File(...),
    is_premium: bool = Form(False),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not storage.enabled:
        raise HTTPException(status_code=503, detail="storage_not_configured")

    mime = (file.content_type or "").lower()
    if mime in settings.image_mimes:
        media_type = "image"
    elif mime in settings.video_mimes:
        media_type = "video"
    else:
        raise HTTPException(status_code=415, detail=f"unsupported content type: {mime}")

    category = await db.fetchrow(q.CATEGORY_META, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="category_not_found")
    if category["parent_id"] is None:
        raise HTTPException(
            status_code=422,
            detail="media attaches to a live/static subcategory, not a root category",
        )
    kind = category["kind"]
    if settings.ENFORCE_KIND_MEDIA_MATCH:
        expected = "image" if kind == "static" else "video"
        if media_type != expected:
            raise HTTPException(
                status_code=422,
                detail=f"a {kind} subcategory takes {expected} media, got {media_type}",
            )

    raw = await _read_upload(file, settings.UPLOAD_MAX_BYTES)

    key = Storage.build_key(category["slug"], kind, Storage.extension_for(mime))
    stem = key.rsplit(".", 1)[0]
    uploads: list[tuple[str, bytes, str]] = [(key, raw, mime)]
    thumb_key: str | None = None
    preview_key: str | None = None
    width: int | None = None
    height: int | None = None
    color_code: str | None = None

    if media_type == "image":
        rendition = await make_renditions(raw, settings)
        width = rendition.width
        height = rendition.height
        color_code = rendition.color_code
        if rendition.thumb:
            thumb_key = f"{stem}_thumb.webp"
            uploads.append((thumb_key, rendition.thumb, "image/webp"))
        if rendition.preview:
            preview_key = f"{stem}_preview.webp"
            uploads.append((preview_key, rendition.preview, "image/webp"))

    await storage.put_many(uploads)

    try:
        row = await db.fetchrow(
            q.ARTWORK_INSERT,
            category_id,
            title,
            media_type,
            is_premium,
            key,
            thumb_key,
            preview_key,
            file.filename,
            color_code,
            width,
            height,
            len(raw),
        )
    except Exception:
        # Never leave objects in the bucket that no row points at.
        await storage.delete([k for k, _, _ in uploads])
        raise

    clear_response_cache()
    return {"data": artwork_to_dict(row, settings.MEDIA_BASE_URL)}


@router.delete("/artworks/{artwork_id}", dependencies=[Depends(require_admin)])
async def delete_artwork(
    artwork_id: int,
    purge: bool = False,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    row = await db.fetchrow(q.ARTWORK_SOFT_DELETE, artwork_id)
    if row is None:
        raise HTTPException(status_code=404, detail="artwork_not_found")

    purged = False
    if purge and settings.STORAGE_ALLOW_PURGE and storage.enabled:
        keys = [row[k] for k in ("storage_key", "thumb_key", "preview_key") if row[k]]
        await storage.delete(keys)
        purged = True

    clear_response_cache()
    return {"data": {"id": row["id"], "is_active": False, "objects_purged": purged}}


@router.post("/maintenance/recount", dependencies=[Depends(require_admin)])
async def recount() -> dict[str, Any]:
    """Rebuild categories.artwork_count from the artworks table.

    The counters are trigger-maintained; this exists for after a bulk load that
    bypassed them, or to check they have not drifted.
    """
    await db.execute(q.RECOUNT_ARTWORKS)
    clear_response_cache()
    return {"data": {"status": "recounted"}}
