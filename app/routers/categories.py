"""GET /v1/categories                     ?page &page_size &parent_id &kind
   GET /v1/categories/{id}/artworks       ?page &page_size

   No params        -> top-level categories
   ?parent_id=<id>  -> that category's live + static subcategories
   ?kind=live       -> every live leaf across all categories

   Sets Cache-Control + ETag; served through the in-process TTL cache.
"""
