"""Access logging and request correlation, as pure ASGI middleware.

Starlette's BaseHTTPMiddleware allocates a task group and an anyio stream per
request; on a read path that otherwise costs one dict lookup that overhead is
the dominant cost, so these are written against the raw ASGI interface.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("wallmo.access")


class RequestContextMiddleware:
    """Assigns a request id, echoes it back, and logs one line per request."""

    __slots__ = ("app", "_slow_ms")

    def __init__(self, app: ASGIApp, slow_ms: float = 500.0) -> None:
        self.app = app
        self._slow_ms = slow_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex[:16]
        scope["request_id"] = request_id
        started = time.perf_counter()
        status = 500
        cache_state = "-"

        async def send_wrapper(message: Message) -> None:
            nonlocal status, cache_state
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = message.setdefault("headers", [])
                _merge_vary(headers)
                headers.append((b"x-request-id", request_id.encode("ascii")))
                for name, value in headers:
                    if name == b"x-cache":
                        cache_state = value.decode("latin-1")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            query = scope.get("query_string", b"").decode("latin-1")
            log.log(
                logging.WARNING if duration_ms >= self._slow_ms else logging.INFO,
                "%s %s%s -> %s in %.1fms",
                scope.get("method", "-"),
                scope.get("path", "-"),
                f"?{query}" if query else "",
                status,
                duration_ms,
                extra={
                    "request_id": request_id,
                    "status": status,
                    "duration_ms": round(duration_ms, 2),
                    "cache": cache_state,
                },
            )


def _merge_vary(headers: list[tuple[bytes, bytes]]) -> None:
    """Collapse repeated Vary headers into one deduplicated value.

    GZipMiddleware appends `Vary: Accept-Encoding` rather than replacing it, so a
    compressed response would otherwise go out with the token twice. This runs in
    the outermost middleware, which is the only place that sees the final headers.
    """
    positions = [i for i, (name, _) in enumerate(headers) if name == b"vary"]
    if not positions:
        return

    tokens: list[bytes] = []
    seen: set[bytes] = set()
    for index in positions:
        for raw in headers[index][1].split(b","):
            token = raw.strip()
            key = token.lower()
            if token and key not in seen:
                seen.add(key)
                tokens.append(token)

    merged = b", ".join(tokens)
    if len(positions) == 1 and headers[positions[0]][1] == merged:
        return
    for index in reversed(positions[1:]):
        del headers[index]
    headers[positions[0]] = (b"vary", merged)


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", ()):
        if name == b"x-request-id":
            return value.decode("latin-1")[:64]
    return None
