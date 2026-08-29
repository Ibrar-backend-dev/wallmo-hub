-- 002_triggers.sql -- integrity and denormalised counters
--
-- Two invariants are enforced in the database rather than the application, so a
-- bulk load or a psql session cannot break them:
--   1. media attaches only to a subcategory, never to a root;
--   2. categories.artwork_count always matches the active artworks, on both the
--      subcategory and its parent.

CREATE OR REPLACE FUNCTION artworks_require_subcategory() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM categories
        WHERE id = NEW.category_id AND parent_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'artworks.category_id must reference a live/static subcategory, got %',
            NEW.category_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$fn$;

CREATE TRIGGER artworks_require_subcategory
    BEFORE INSERT OR UPDATE OF category_id ON artworks
    FOR EACH ROW EXECUTE FUNCTION artworks_require_subcategory();


-- Applies a delta to a subcategory and, in the same statement, its parent.
CREATE OR REPLACE FUNCTION adjust_artwork_count(target_id int, delta int)
RETURNS void
LANGUAGE sql AS $fn$
    UPDATE categories
    SET artwork_count = artwork_count + delta
    WHERE id = target_id
       OR id = (SELECT parent_id FROM categories WHERE id = target_id);
$fn$;


CREATE OR REPLACE FUNCTION artworks_sync_counts() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    remove_from int := NULL;
    add_to      int := NULL;
BEGIN
    -- Only active rows are counted, so a soft delete decrements just like a
    -- hard delete and a reactivation increments again.
    IF TG_OP = 'INSERT' THEN
        IF NEW.is_active THEN add_to := NEW.category_id; END IF;
    ELSIF TG_OP = 'DELETE' THEN
        IF OLD.is_active THEN remove_from := OLD.category_id; END IF;
    ELSE
        IF OLD.is_active THEN remove_from := OLD.category_id; END IF;
        IF NEW.is_active THEN add_to := NEW.category_id; END IF;
    END IF;

    IF remove_from IS NOT NULL AND remove_from IS DISTINCT FROM add_to THEN
        PERFORM adjust_artwork_count(remove_from, -1);
    END IF;
    IF add_to IS NOT NULL AND add_to IS DISTINCT FROM remove_from THEN
        PERFORM adjust_artwork_count(add_to, 1);
    END IF;

    RETURN NULL;
END;
$fn$;

CREATE TRIGGER artworks_sync_counts
    AFTER INSERT OR DELETE OR UPDATE OF category_id, is_active ON artworks
    FOR EACH ROW EXECUTE FUNCTION artworks_sync_counts();


-- Rebuilds every counter from scratch. Used by POST /admin/maintenance/recount
-- and after any load that bypassed the triggers (COPY, restore, manual fixes).
CREATE OR REPLACE FUNCTION recount_artwork_counts() RETURNS void
LANGUAGE sql AS $fn$
    WITH leaf AS (
        SELECT c.id, count(a.id) AS n
        FROM categories c
        LEFT JOIN artworks a ON a.category_id = c.id AND a.is_active
        WHERE c.parent_id IS NOT NULL
        GROUP BY c.id
    ), rolled AS (
        SELECT c.id, COALESCE(sum(leaf.n), 0) AS n
        FROM categories c
        LEFT JOIN categories child ON child.parent_id = c.id
        LEFT JOIN leaf ON leaf.id = child.id
        WHERE c.parent_id IS NULL
        GROUP BY c.id
    ), totals AS (
        SELECT id, n FROM leaf
        UNION ALL
        SELECT id, n FROM rolled
    )
    UPDATE categories c
    SET artwork_count = totals.n
    FROM totals
    WHERE c.id = totals.id AND c.artwork_count <> totals.n;
$fn$;
