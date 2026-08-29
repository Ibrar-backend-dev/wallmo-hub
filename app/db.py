"""asyncpg connection pool and the shared paged-fetch helper.

Read endpoints are single-round-trip: the list query carries
`count(*) OVER () AS total_count`, so rows and the grand total arrive together.
The separate COUNT query only runs for an empty page past the end of the set,
where the window function has no row to hang the total on.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import asyncpg

from app.config import Settings

log = logging.getLogger(__name__)


class Database:
    """Owns the process-wide asyncpg pool. One instance per worker."""

    __slots__ = ("_pool", "_settings")

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None
        self._settings: Settings | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database pool is not initialised")
        return self._pool

    async def connect(self, settings: Settings) -> None:
        if self._pool is not None:
            return
        self._settings = settings
        self._pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=settings.DB_POOL_MIN,
            max_size=settings.DB_POOL_MAX,
            command_timeout=settings.DB_COMMAND_TIMEOUT,
            max_inactive_connection_lifetime=settings.DB_MAX_INACTIVE_LIFETIME,
            statement_cache_size=settings.DB_STATEMENT_CACHE_SIZE,
            server_settings={
                # These queries are tiny and run thousands of times; JIT
                # compilation costs more than it saves.
                "jit": "off",
                "application_name": f"wallmo-{settings.ENV}",
                "statement_timeout": str(settings.DB_STATEMENT_TIMEOUT_MS),
            },
        )
        log.info("db pool ready (min=%s max=%s)", settings.DB_POOL_MIN, settings.DB_POOL_MAX)

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ---- query helpers -------------------------------------------------

    async def fetch(self, sql: str, *args: Any) -> list[asyncpg.Record]:
        return await self.pool.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> asyncpg.Record | None:
        return await self.pool.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return await self.pool.fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        return await self.pool.execute(sql, *args)

    async def fetch_page(
        self,
        rows_sql: str,
        count_sql: str,
        filter_args: Sequence[Any],
        limit: int,
        offset: int,
    ) -> tuple[list[asyncpg.Record], int]:
        """Return (rows, total). One round trip unless the page is past the end."""
        async with self.pool.acquire() as con:
            rows = await con.fetch(rows_sql, *filter_args, limit, offset)
            if rows:
                return rows, rows[0]["total_count"]
            if offset == 0:
                return [], 0
            total = await con.fetchval(count_sql, *filter_args)
            return [], int(total or 0)

    async def healthy(self) -> bool:
        try:
            return await self.pool.fetchval("SELECT 1") == 1
        except Exception:  # noqa: BLE001 - readiness must never raise
            log.exception("readiness probe failed")
            return False


db = Database()
