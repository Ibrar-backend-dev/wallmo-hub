"""All SQL as module-level constants. No SQL anywhere else.

  CATEGORIES_TOP        -> parent_id IS NULL
  CATEGORIES_BY_PARENT  -> parent_id = $1
  CATEGORIES_BY_KIND    -> kind = $1
  ARTWORKS_FEED         -> all active, ORDER BY created_at DESC, id DESC
  ARTWORKS_BY_CATEGORY  -> category_id = $1 OR parent_id = $1
  ARTWORK_INSERT

Every list query carries `count(*) OVER () AS total_count`.
"""
