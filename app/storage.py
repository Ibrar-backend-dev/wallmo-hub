"""Backblaze B2 over its S3-compatible endpoint.

Only the write path touches B2. Read endpoints return
`MEDIA_BASE_URL + "/" + storage_key` and nothing else, so serving a category of
media never makes an outbound call, never signs anything, and never streams
bytes through a worker - the CDN in front of the bucket does all of that.

The S3 client is created once during startup and reused; building one per
request costs a TLS handshake and endpoint resolution each time.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.config import Settings

log = logging.getLogger(__name__)

_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
}


@dataclass(slots=True)
class ImageRenditions:
    width: int
    height: int
    color_code: str
    thumb: bytes | None
    preview: bytes | None


class Storage:
    """S3-compatible object storage. Inert when STORAGE_BACKEND=none."""

    __slots__ = ("_client", "_stack", "_settings", "_semaphore")

    def __init__(self) -> None:
        self._client: Any | None = None
        self._stack: AsyncExitStack | None = None
        self._settings: Settings | None = None
        self._semaphore: asyncio.Semaphore | None = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def start(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.B2_UPLOAD_CONCURRENCY)
        if settings.STORAGE_BACKEND != "b2":
            log.info("storage backend disabled (STORAGE_BACKEND=%s)", settings.STORAGE_BACKEND)
            return
        missing = [
            name
            for name in ("B2_ENDPOINT_URL", "B2_BUCKET", "B2_KEY_ID", "B2_APP_KEY")
            if not getattr(settings, name)
        ]
        if missing:
            raise RuntimeError(f"STORAGE_BACKEND=b2 but missing: {', '.join(missing)}")

        import aioboto3  # imported lazily: read-only deployments do not need it
        from botocore.config import Config

        self._stack = AsyncExitStack()
        session = aioboto3.Session()
        self._client = await self._stack.enter_async_context(
            session.client(
                "s3",
                endpoint_url=settings.B2_ENDPOINT_URL,
                region_name=settings.B2_REGION or None,
                aws_access_key_id=settings.B2_KEY_ID,
                aws_secret_access_key=settings.B2_APP_KEY,
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                    max_pool_connections=settings.B2_UPLOAD_CONCURRENCY * 4,
                ),
            )
        )
        log.info("storage ready (bucket=%s)", settings.B2_BUCKET)

    async def stop(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._client = None

    # ---- keys and urls -------------------------------------------------

    @staticmethod
    def extension_for(mime: str) -> str:
        return _EXT_BY_MIME.get(mime, "bin")

    @staticmethod
    def build_key(slug: str, kind: str, ext: str, suffix: str = "") -> str:
        now = datetime.now(timezone.utc)
        return f"wallpaper/{slug}/{kind}/{now:%Y}/{now:%m}/{uuid.uuid4().hex}{suffix}.{ext}"

    def url(self, key: str) -> str:
        assert self._settings is not None
        return f"{self._settings.MEDIA_BASE_URL}/{key}"

    # ---- objects -------------------------------------------------------

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        if self._client is None:
            raise RuntimeError("storage backend is not configured")
        assert self._settings is not None and self._semaphore is not None
        async with self._semaphore:
            await self._client.put_object(
                Bucket=self._settings.B2_BUCKET,
                Key=key,
                Body=data,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )

    async def put_many(self, items: list[tuple[str, bytes, str]]) -> None:
        await asyncio.gather(*(self.put(k, b, ct) for k, b, ct in items))

    async def delete(self, keys: list[str]) -> None:
        if self._client is None or not keys:
            return
        assert self._settings is not None
        await self._client.delete_objects(
            Bucket=self._settings.B2_BUCKET,
            Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True},
        )


storage = Storage()


# --------------------------------------------------------------------------- #
# image processing
# --------------------------------------------------------------------------- #


def _renditions_sync(raw: bytes, settings: Settings) -> ImageRenditions:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(raw)) as img:
        img.load()
        width, height = img.size
        rgb = img.convert("RGB")

    # A 1x1 downscale is the average colour, which is what a placeholder wants.
    color = rgb.resize((1, 1), Image.Resampling.LANCZOS).getpixel((0, 0))
    color_code = "#{:02x}{:02x}{:02x}".format(*color)  # type: ignore[misc]

    def encode(target_width: int) -> bytes | None:
        if width <= target_width:
            return None
        ratio = target_width / width
        out = rgb.resize((target_width, max(1, round(height * ratio))), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="WEBP", quality=settings.WEBP_QUALITY, method=4)
        return buf.getvalue()

    return ImageRenditions(
        width=width,
        height=height,
        color_code=color_code,
        thumb=encode(settings.THUMB_WIDTH),
        preview=encode(settings.PREVIEW_WIDTH),
    )


async def make_renditions(raw: bytes, settings: Settings) -> ImageRenditions:
    """Resize off the event loop. Pillow is CPU-bound and would block every
    concurrent reader otherwise. For sustained bulk ingest, move this to a
    ProcessPoolExecutor or an out-of-band worker."""
    return await asyncio.to_thread(_renditions_sync, raw, settings)
