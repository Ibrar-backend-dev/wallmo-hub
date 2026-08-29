-- 001_init.sql -- schema
--
-- Categories are a one-level tree. A root category (parent_id IS NULL) has no
-- kind; its children are the 'live' and 'static' subcategories, and only those
-- children hold media. artwork_count is denormalised so listing categories is
-- an index scan rather than a join and GROUP BY.

CREATE TYPE category_kind AS ENUM ('live', 'static');
CREATE TYPE media_type AS ENUM ('image', 'video');

CREATE TABLE categories (
    id            serial PRIMARY KEY,
    parent_id     int REFERENCES categories (id) ON DELETE CASCADE,
    kind          category_kind,
    name          text NOT NULL,
    slug          text NOT NULL,
    description   text,
    position      int NOT NULL DEFAULT 0,
    artwork_count int NOT NULL DEFAULT 0,
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT categories_slug_key UNIQUE (slug),
    CONSTRAINT categories_no_self_parent CHECK (parent_id IS DISTINCT FROM id),
    -- A root has no kind, a subcategory must have one. This is what keeps the
    -- tree exactly one level deep in practice.
    CONSTRAINT categories_leaf_has_kind CHECK (
        (parent_id IS NULL AND kind IS NULL)
        OR (parent_id IS NOT NULL AND kind IS NOT NULL)
    ),
    CONSTRAINT categories_count_non_negative CHECK (artwork_count >= 0)
);

-- At most one live and one static subcategory per parent.
CREATE UNIQUE INDEX categories_parent_kind_key
    ON categories (parent_id, kind)
    WHERE parent_id IS NOT NULL;

-- Ordering for GET /v1/categories, one index per shape the endpoint serves.
CREATE INDEX categories_roots_idx
    ON categories (position, id)
    WHERE parent_id IS NULL AND is_active;

CREATE INDEX categories_children_idx
    ON categories (parent_id, position, id)
    WHERE is_active;

CREATE INDEX categories_kind_idx
    ON categories (kind, position, id)
    WHERE parent_id IS NOT NULL AND is_active;


CREATE TABLE artworks (
    id                serial PRIMARY KEY,
    category_id       int NOT NULL REFERENCES categories (id) ON DELETE CASCADE,
    title             text NOT NULL,
    media_type        media_type NOT NULL,
    is_premium        boolean NOT NULL DEFAULT false,
    -- Object key in the bucket. Responses return MEDIA_BASE_URL || '/' || key,
    -- so the CDN origin can change without touching a single row.
    storage_key       text NOT NULL,
    thumb_key         text,
    preview_key       text,
    original_filename text,
    color_code        text,
    width             int,
    height            int,
    bytes             bigint,
    is_active         boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT artworks_storage_key_key UNIQUE (storage_key),
    CONSTRAINT artworks_colour_format CHECK (
        color_code IS NULL OR color_code ~ '^#[0-9a-fA-F]{6}$'
    ),
    CONSTRAINT artworks_dimensions_positive CHECK (
        (width IS NULL OR width > 0) AND (height IS NULL OR height > 0)
    )
);

-- Covers the flat feed: ORDER BY created_at DESC, id DESC.
CREATE INDEX artworks_feed_idx
    ON artworks (created_at DESC, id DESC)
    WHERE is_active;

-- Covers per-category listings and the bundle's per-category window.
CREATE INDEX artworks_category_feed_idx
    ON artworks (category_id, created_at DESC, id DESC)
    WHERE is_active;

-- Only useful once premium media is a small slice of the table.
CREATE INDEX artworks_premium_idx
    ON artworks (created_at DESC, id DESC)
    WHERE is_active AND is_premium;


CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$fn$;

CREATE TRIGGER categories_touch_updated_at
    BEFORE UPDATE ON categories
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TRIGGER artworks_touch_updated_at
    BEFORE UPDATE ON artworks
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
