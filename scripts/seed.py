"""Seed development data.

    python -m scripts.seed
    python -m scripts.seed --reset      # truncate first
    python -m scripts.seed --per-kind 40

Creates root categories, a live and a static subcategory under each, and media
rows pointing at object keys that follow the real layout. No bytes are uploaded,
so file_path values 404 until the same keys exist in the bucket - that is fine
for exercising the API and the Android client's parsing.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from app.config import get_settings

ROOTS: list[tuple[str, str, str]] = [
    ("Anime", "anime", "Anime wallpapers wonderful styles ."),
    ("Nature", "nature", "Landscapes, forests and open water."),
    ("Abstract", "abstract", "Gradients, shapes and generative art."),
    ("Minimal", "minimal", "Clean, low-detail backgrounds."),
]

COLOURS = ["#1b1f3b", "#2f4858", "#33658a", "#86bbd8", "#f6ae2d", "#f26419", "#0f3057"]
EXT_BY_KIND = {"static": "png", "live": "mp4"}
MIME_BY_KIND = {"static": "image", "live": "video"}


async def _seed(reset: bool, per_kind: int) -> None:
    settings = get_settings()
    con = await asyncpg.connect(settings.DATABASE_URL)
    rng = random.Random(20260828)
    try:
        async with con.transaction():
            if reset:
                await con.execute("TRUNCATE artworks, categories RESTART IDENTITY CASCADE")
                print("truncated artworks + categories")

            now = datetime.now(timezone.utc)
            total_media = 0

            for position, (name, slug, description) in enumerate(ROOTS):
                root_id = await con.fetchval(
                    """
                    INSERT INTO categories (name, slug, description, position)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (slug) DO UPDATE SET description = EXCLUDED.description
                    RETURNING id
                    """,
                    name,
                    slug,
                    description,
                    position,
                )

                for kind_position, kind in enumerate(("static", "live")):
                    child_slug = f"{slug}-{kind}"
                    child_id = await con.fetchval(
                        """
                        INSERT INTO categories
                            (parent_id, kind, name, slug, description, position)
                        VALUES ($1, $2::category_kind, $3, $4, $5, $6)
                        ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                        RETURNING id
                        """,
                        root_id,
                        kind,
                        f"{name} {kind.capitalize()}",
                        child_slug,
                        f"{kind.capitalize()} {name.lower()} wallpapers.",
                        kind_position,
                    )

                    ext = EXT_BY_KIND[kind]
                    rows = []
                    for i in range(per_kind):
                        created = now - timedelta(hours=rng.randint(0, 24 * 90))
                        key = (
                            f"wallpaper/{child_slug}/{kind}/{created:%Y}/{created:%m}/"
                            f"{uuid.uuid4().hex}.{ext}"
                        )
                        stem = key.rsplit(".", 1)[0]
                        is_image = kind == "static"
                        rows.append(
                            (
                                child_id,
                                f"{name} {kind} {i + 1:03d}",
                                MIME_BY_KIND[kind],
                                rng.random() < 0.3,
                                key,
                                f"{stem}_thumb.webp" if is_image else None,
                                f"{stem}_preview.webp" if is_image else None,
                                f"{slug}_{i + 1:03d}.{ext}",
                                rng.choice(COLOURS),
                                1440 if is_image else 1080,
                                3120 if is_image else 1920,
                                rng.randint(400_000, 6_000_000),
                                created,
                            )
                        )

                    await con.executemany(
                        """
                        INSERT INTO artworks (
                            category_id, title, media_type, is_premium, storage_key,
                            thumb_key, preview_key, original_filename, color_code,
                            width, height, bytes, created_at
                        )
                        VALUES ($1, $2, $3::media_type, $4, $5, $6, $7, $8, $9,
                                $10, $11, $12, $13)
                        ON CONFLICT (storage_key) DO NOTHING
                        """,
                        rows,
                    )
                    total_media += len(rows)

            await con.execute("SELECT recount_artwork_counts()")

        summary = await con.fetch(
            """
            SELECT c.name, c.kind, c.artwork_count
            FROM categories c
            ORDER BY c.parent_id NULLS FIRST, c.position, c.id
            """
        )
        print(f"seeded {total_media} media rows")
        for row in summary:
            label = row["kind"] or "root"
            print(f"  {row['name']:<20} {label:<7} {row['artwork_count']}")
    finally:
        await con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed development data.")
    parser.add_argument("--reset", action="store_true", help="truncate before seeding")
    parser.add_argument("--per-kind", type=int, default=12, help="media per subcategory")
    args = parser.parse_args()
    asyncio.run(_seed(args.reset, max(1, args.per_kind)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
