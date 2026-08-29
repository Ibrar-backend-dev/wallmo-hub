"""Every SQL statement in the service. No SQL lives outside this module.

List queries come in (rows, count) pairs. The rows query always ends with
`LIMIT $n OFFSET $n+1`, so callers pass filter args first and let
`Database.fetch_page` append limit/offset.

The builders are lru_cached, so the process only ever holds a handful of
distinct statement strings and asyncpg's prepared-statement cache stays warm.
"""

from __future__ import annotations

from functools import lru_cache

CATEGORY_COLS = "id, name, description, position, artwork_count"

ARTWORK_COLS = (
    "a.media_type, a.is_premium, a.title, a.id, a.category_id, "
    "a.original_filename, a.storage_key, a.created_at, a.color_code, "
    "a.thumb_key, a.preview_key, a.width, a.height, a.bytes"
)


# --------------------------------------------------------------------------- #
# categories
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=8)
def category_list(has_parent: bool, has_kind: bool) -> tuple[str, str]:
    """(rows_sql, count_sql) for GET /v1/categories.

    With neither filter the endpoint returns root categories only, so the app's
    first screen never mixes roots and subcategories.
    """
    where = ["is_active"]
    n = 0
    if has_parent:
        n += 1
        where.append(f"parent_id = ${n}")
    if has_kind:
        n += 1
        where.append(f"kind = ${n}::category_kind")
    if not has_parent and not has_kind:
        where.append("parent_id IS NULL")
    clause = " AND ".join(where)

    rows = (
        f"SELECT {CATEGORY_COLS}, count(*) OVER () AS total_count "
        f"FROM categories WHERE {clause} "
        f"ORDER BY position, id LIMIT ${n + 1} OFFSET ${n + 2}"
    )
    count = f"SELECT count(*) FROM categories WHERE {clause}"
    return rows, count


CATEGORY_NODE = f"""
SELECT {CATEGORY_COLS}, kind, parent_id
FROM categories
WHERE id = $1 AND is_active
"""

CATEGORY_CHILDREN = f"""
SELECT {CATEGORY_COLS}, kind, parent_id
FROM categories
WHERE parent_id = $1 AND is_active
ORDER BY position, id
"""

CATEGORY_META = """
SELECT id, slug, kind, parent_id FROM categories WHERE id = $1 AND is_active
"""


# --------------------------------------------------------------------------- #
# artworks
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=16)
def artwork_list(has_category: bool, has_kind: bool, has_premium: bool) -> tuple[str, str]:
    """(rows_sql, count_sql) for the artwork feed and per-category listings.

    A category filter matches the category itself *and* its subcategories, so
    /v1/categories/{id}/artworks works whether the client passes a root id or a
    live/static leaf id.
    """
    join = ""
    where = ["a.is_active"]
    n = 0
    if has_category:
        n += 1
        where.append(
            f"a.category_id IN (SELECT id FROM categories "
            f"WHERE is_active AND (id = ${n} OR parent_id = ${n}))"
        )
    if has_kind:
        n += 1
        join = " JOIN categories c ON c.id = a.category_id"
        where.append(f"c.kind = ${n}::category_kind")
    if has_premium:
        n += 1
        where.append(f"a.is_premium = ${n}")
    clause = " AND ".join(where)

    rows = (
        f"SELECT {ARTWORK_COLS}, count(*) OVER () AS total_count "
        f"FROM artworks a{join} WHERE {clause} "
        f"ORDER BY a.created_at DESC, a.id DESC LIMIT ${n + 1} OFFSET ${n + 2}"
    )
    count = f"SELECT count(*) FROM artworks a{join} WHERE {clause}"
    return rows, count


# Whole-category delivery: every subcategory's media in one round trip, capped
# per subcategory so a runaway category can't produce an unbounded response.
BUNDLE_ARTWORKS = f"""
SELECT {ARTWORK_COLS.replace("a.", "t.")}
FROM (
    SELECT {ARTWORK_COLS},
           row_number() OVER (
               PARTITION BY a.category_id
               ORDER BY a.created_at DESC, a.id DESC
           ) AS rn
    FROM artworks a
    WHERE a.is_active AND a.category_id = ANY($1::int[])
) t
WHERE t.rn <= $2
ORDER BY t.category_id, t.rn
"""


# --------------------------------------------------------------------------- #
# writes (admin surface)
# --------------------------------------------------------------------------- #

CATEGORY_INSERT = f"""
INSERT INTO categories (parent_id, kind, name, slug, description, position, is_active)
VALUES ($1, $2::category_kind, $3, $4, $5, $6, $7)
RETURNING {CATEGORY_COLS}, kind, parent_id
"""

CATEGORY_UPDATE = f"""
UPDATE categories SET
    name        = COALESCE($2::text, name),
    description = COALESCE($3::text, description),
    position    = COALESCE($4::int, position),
    is_active   = COALESCE($5::bool, is_active)
WHERE id = $1
RETURNING {CATEGORY_COLS}, kind, parent_id
"""

CATEGORY_SOFT_DELETE = """
UPDATE categories SET is_active = false
WHERE id = $1 AND is_active
RETURNING id
"""

ARTWORK_INSERT = f"""
INSERT INTO artworks (
    category_id, title, media_type, is_premium, storage_key, thumb_key,
    preview_key, original_filename, color_code, width, height, bytes
)
VALUES ($1, $2, $3::media_type, $4, $5, $6, $7, $8, $9, $10, $11, $12)
RETURNING {ARTWORK_COLS.replace("a.", "")}
"""

ARTWORK_SOFT_DELETE = """
UPDATE artworks SET is_active = false
WHERE id = $1 AND is_active
RETURNING id, storage_key, thumb_key, preview_key
"""

ARTWORK_BY_ID = f"""
SELECT {ARTWORK_COLS.replace("a.", "")} FROM artworks WHERE id = $1 AND is_active
"""

RECOUNT_ARTWORKS = "SELECT recount_artwork_counts()"
