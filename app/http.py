"""Response construction: one place that owns encoding, ETags and caching.

A handler builds a plain dict; this module encodes it once with orjson, hashes
the bytes into a weak ETag, stores the finished bytes in the per-worker TTL
cache, and answers a matching If-None-Match with 304. A repeat request for the
same URL therefore costs a dict lookup and a header write - no query, no encode.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from hashlib import blake2b
from typing import Any

import orjson
from fastapi import Request, Response

from app.cache import TTLCache
from app.config import Settings

_JSON = "application/json"

# Populated during app startup so the TTL and bound come from the environment.
_cache: TTLCache[tuple[bytes, str]] = TTLCache(maxsize=1, ttl=0.0)


def init_response_cache(settings: Settings) -> None:
    global _cache
    _cache = TTLCache(maxsize=settings.CACHE_MAX_ENTRIES, ttl=settings.CACHE_TTL_SECONDS)


def clear_response_cache() -> None:
    """Called after every write so the next read reflects it immediately."""
    _cache.clear()


def cache_stats() -> dict[str, int]:
    return _cache.stats()


def _etag(body: bytes) -> str:
    # Weak validator: gzip further down the stack changes the bytes on the wire
    # without changing the representation this ETag identifies.
    return f'W/"{blake2b(body, digest_size=16).hexdigest()}"'


def _headers(settings: Settings, etag: str, cache_state: str) -> dict[str, str]:
    return {
        "ETag": etag,
        "Cache-Control": settings.cache_control,
        "Vary": "Accept-Encoding",
        "X-Cache": cache_state,
    }


async def serve_json(
    request: Request,
    settings: Settings,
    build: Callable[[], Awaitable[dict[str, Any]]],
    *,
    cacheable: bool = True,
) -> Response:
    use_cache = cacheable and settings.CACHE_ENABLED
    key = f"{request.url.path}?{request.url.query}" if use_cache else ""

    entry = _cache.get(key) if use_cache else None
    state = "HIT"
    if entry is None:
        state = "MISS"
        body = orjson.dumps(await build())
        entry = (body, _etag(body))
        if use_cache:
            _cache.set(key, entry)

    body, etag = entry
    headers = _headers(settings, etag, state)

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)

    return Response(content=body, media_type=_JSON, headers=headers)


def json_error(status_code: int, code: str, message: str) -> Response:
    return Response(
        content=orjson.dumps({"error": {"code": code, "message": message}}),
        status_code=status_code,
        media_type=_JSON,
    )
