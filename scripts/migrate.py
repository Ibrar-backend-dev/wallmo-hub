"""Apply migrations/*.sql in filename order.

    python -m scripts.migrate            # apply pending
    python -m scripts.migrate --status   # show what is applied

Each file runs in its own transaction, is recorded with a checksum, and is
skipped once applied. A session-level advisory lock makes concurrent deploys
safe: the second one waits, then finds nothing to do.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

import asyncpg

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
LOCK_ID = 0x7A11_0000  # arbitrary, stable across deploys

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def _run(show_status: bool) -> int:
    settings = get_settings()
    con = await asyncpg.connect(settings.DATABASE_URL)
    try:
        await con.execute(BOOTSTRAP)
        applied = {
            r["filename"]: r["checksum"]
            for r in await con.fetch("SELECT filename, checksum FROM schema_migrations")
        }

        if show_status:
            for path in _files():
                recorded = applied.get(path.name)
                current = _checksum(path.read_text(encoding="utf-8"))
                if recorded is None:
                    state = "pending"
                elif recorded != current:
                    state = "APPLIED BUT CHANGED ON DISK"
                else:
                    state = "applied"
                print(f"{path.name:<28} {state}")
            return 0

        await con.execute("SELECT pg_advisory_lock($1)", LOCK_ID)
        try:
            pending = 0
            for path in _files():
                sql = path.read_text(encoding="utf-8")
                checksum = _checksum(sql)
                recorded = applied.get(path.name)

                if recorded == checksum:
                    continue
                if recorded is not None:
                    print(
                        f"! {path.name} is already applied but its contents changed.\n"
                        f"  Migrations are immutable - add a new file instead.",
                        file=sys.stderr,
                    )
                    return 1

                print(f"applying {path.name} ...", flush=True)
                async with con.transaction():
                    await con.execute(sql)
                    await con.execute(
                        "INSERT INTO schema_migrations (filename, checksum) VALUES ($1, $2)",
                        path.name,
                        checksum,
                    )
                pending += 1

            print(f"up to date ({pending} applied this run)")
            return 0
        finally:
            await con.execute("SELECT pg_advisory_unlock($1)", LOCK_ID)
    finally:
        await con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply SQL migrations.")
    parser.add_argument("--status", action="store_true", help="report state and exit")
    args = parser.parse_args()
    return asyncio.run(_run(args.status))


if __name__ == "__main__":
    raise SystemExit(main())
