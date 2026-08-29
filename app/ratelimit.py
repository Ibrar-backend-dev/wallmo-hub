"""Per-IP token bucket, off by default.

The real limiter belongs at the edge (Cloudflare / nginx), which sees traffic
before it costs a worker anything. This is defence in depth for a direct-hit
origin, is per-worker rather than global, and is enabled only by
RATE_LIMIT_ENABLED=true. Written as pure ASGI middleware - BaseHTTPMiddleware
adds a task group and a queue per request, which is measurable on a hot read path.
"""

from __future__ import annotations

import time
from collections import OrderedDict

import orjson
from starlette.types import ASGIApp, Receive, Scope, Send

_BODY = orjson.dumps({"error": {"code": "rate_limited", "message": "Too many requests"}})


class RateLimitMiddleware:
    __slots__ = ("app", "_rate", "_burst", "_max_clients", "_buckets")

    def __init__(
        self, app: ASGIApp, per_minute: int, burst: int, max_clients: int = 20_000
    ) -> None:
        self.app = app
        self._rate = per_minute / 60.0
        self._burst = float(max(burst, 1))
        self._max_clients = max_clients
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def _client(self, scope: Scope) -> str:
        for name, value in scope.get("headers", ()):
            if name == b"x-forwarded-for":
                return value.split(b",")[0].strip().decode("latin-1")
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self._burst, now))
        tokens = min(self._burst, tokens + (now - last) * self._rate)
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            self._buckets.move_to_end(key)
            return False
        self._buckets[key] = (tokens - 1.0, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self._max_clients:
            self._buckets.popitem(last=False)
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._allow(self._client(scope)):
            await self.app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_BODY)).encode()),
                    (b"retry-after", b"1"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _BODY})
