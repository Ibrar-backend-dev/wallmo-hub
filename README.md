# wallmo

Wallpaper catalogue API for the Android client. FastAPI + Postgres (asyncpg, raw
SQL) + Backblaze B2 for media. Public read endpoints, no authentication; a
write surface that is off unless the environment enables it.

## Design in one page

**Media is never proxied.** The database stores an object key; responses return
`MEDIA_BASE_URL + "/" + storage_key`. Serving a category costs one query and one
JSON encode — no outbound call, no signing, no bytes through a worker. Put
Cloudflare in front of the public B2 bucket and egress is free and cached.

**One round trip per request.** Every list query carries
`count(*) OVER () AS total_count`, so rows and the grand total arrive together.
A second COUNT runs only for an empty page past the end of a set.

**Counters are columns, not joins.** `categories.artwork_count` is maintained by
a trigger on both the subcategory and its parent, so listing categories is an
index scan.

**Finished bytes are cached.** A response is encoded once by orjson, hashed into
a weak ETag, and kept in a bounded per-worker TTL cache. A repeat request costs
a dict lookup; a client with the ETag gets a 304.

**No ORM.** All SQL lives in [app/queries.py](app/queries.py), built by
`lru_cache`d functions so the process holds a handful of statement strings and
asyncpg's prepared-statement cache stays warm.

## Data model

Categories are a one-level tree. Root categories (Anime, Nature, …) have no
`kind`; each has a `live` and a `static` subcategory, and **only subcategories
hold media**.

    Anime  (root, kind=NULL, artwork_count=16)
      ├── Anime Static  (kind=static, artwork_count=8)  -> image media
      └── Anime Live    (kind=live,   artwork_count=8)  -> video media

Enforced in the database, not just the application:

- a root has no kind, a subcategory must have one (CHECK)
- at most one `live` and one `static` per parent (partial unique index)
- media cannot attach to a root category (trigger)
- `artwork_count` always matches the active media on both levels (trigger)

Constraint violations map to real status codes — 409 for a conflict, 422 for a
rejected constraint — never a 500.

## Endpoints

    GET /v1/categories                  ?page &page_size &parent_id &kind
    GET /v1/artworks                    ?page &page_size &kind &is_premium
    GET /v1/categories/{id}/artworks    ?page &page_size &is_premium
    GET /v1/categories/{id}/bundle      ?limit
    GET /healthz    liveness, no DB touch
    GET /readyz     readiness, DB ping + cache stats
    GET /docs       OpenAPI (DOCS_ENABLED)

`/v1/categories` with no filter returns roots; `?parent_id=4` returns that
category's live/static subcategories; `?kind=live` returns every live
subcategory. `/v1/categories/{id}/artworks` accepts a root id or a subcategory
id — a root includes all of its subcategories' media.

List responses share one envelope:

    { "data": [...], "meta": { "page", "page_size", "total", "has_next" } }

### Serving a whole category

`GET /v1/categories/{id}/bundle` is what the Android client should call when a
category is opened. It returns the category, its subcategories, and all of their
media in a single response instead of one request per subcategory plus a page
walk:

    {
      "data": {
        "id": 1, "name": "Anime", "description": "...", "position": 0,
        "artwork_count": 16, "kind": null, "parent_id": null,
        "artworks": [],
        "subcategories": [
          { "id": 3, "name": "Anime Static", "kind": "static",
            "artwork_count": 8, "artworks": [ ...8 items... ] },
          { "id": 4, "name": "Anime Live", "kind": "live",
            "artwork_count": 8, "artworks": [ ...8 items... ] }
        ]
      },
      "meta": { "artworks_returned": 16, "limit_per_subcategory": 500,
                "truncated": false }
    }

Media is capped per subcategory by `limit` (bounded by `BUNDLE_LIMIT_MAX`);
`meta.truncated` says whether the cap was hit. Media objects are byte-identical
to the ones in the flat feed.

### Media object

The first nine keys are the established contract, in order. The rest are
additive — older clients ignore unknown keys.

    {
      "media_type": "image", "is_premium": true, "title": "wallpaper",
      "id": 3, "category_id": 4, "original_filename": null,
      "file_path": "https://cdn.example.com/wallpaper/anime-static/static/2026/08/996e.png",
      "created_at": "2026-08-23T10:48:52.245000", "color_code": "#1b1f3b",
      "thumb_path": "..._thumb.webp", "preview_path": "..._preview.webp",
      "width": 1440, "height": 3120, "file_size": 2500000
    }

`created_at` is naive UTC with six fractional digits, matching the existing
client contract. **Use `thumb_path` in grids** — pulling full-size originals
into a scrolling grid is the single biggest cause of a slow-feeling wallpaper
app.

## Write surface

Not mounted unless `ADMIN_ENABLED=true`. `ADMIN_API_KEY` is an optional
`X-API-Key` check, skipped when blank.

    POST   /admin/categories
    PATCH  /admin/categories/{id}
    DELETE /admin/categories/{id}          soft delete
    POST   /admin/artworks                 multipart upload
    DELETE /admin/artworks/{id}            ?purge=true (STORAGE_ALLOW_PURGE)
    POST   /admin/maintenance/recount      rebuild artwork_count

Upload derives dimensions and a dominant colour with Pillow (off the event
loop), generates 400w and 1080w WebP renditions, uploads all three to B2
concurrently, then inserts one row. If the insert fails, the objects are
deleted — nothing is orphaned in the bucket.

## Layout

    app/
      main.py            app factory, lifespan, middleware, error handlers
      config.py          every setting, all environment-driven
      db.py              asyncpg pool + fetch_page helper
      http.py            encoding, ETags, response cache
      cache.py           bounded TTL cache
      schemas.py         response models (docs) + hot-path row mappers
      queries.py         all SQL
      storage.py         B2 client, key/URL builders, image renditions
      middleware.py      request id + access log (pure ASGI)
      ratelimit.py       per-IP token bucket, env-gated
      logging_config.py  JSON or text logs
      routers/           health, categories, artworks, admin
    migrations/          numbered .sql, applied by scripts/migrate.py
    scripts/             migrate.py, seed.py
    tests/               43 tests over ASGITransport, real Postgres

## Local development

    cp .env.example .env
    docker compose up -d db          # publishes Postgres on 5433
    py -3.11 -m venv .venv
    .venv/Scripts/python -m pip install -e ".[dev]"
    .venv/Scripts/python -m scripts.migrate
    .venv/Scripts/python -m scripts.seed --reset --per-kind 8
    .venv/Scripts/python -m uvicorn app.main:app --reload

Compose publishes Postgres on **5433**, not 5432, because a local Postgres
install usually already owns 5432. Override with `DB_PORT`.

    .venv/Scripts/python -m pytest          # builds its own <db>_test database
    .venv/Scripts/python -m ruff check .
    .venv/Scripts/python -m ruff format .

Tests skip rather than fail when no Postgres is reachable.

## Production

    docker compose up -d --build

Set in the environment:

- `ENV=production`, `LOG_JSON=true`, `DOCS_ENABLED=false`
- `DATABASE_URL` pointing at managed Postgres; `DB_STATEMENT_CACHE_SIZE=0` if
  it sits behind pgbouncer in transaction pooling mode
- `MEDIA_BASE_URL` = your CDN hostname
- `STORAGE_BACKEND=b2` plus the `B2_*` credentials, only on the instance that
  accepts uploads
- `CORS_ORIGINS` narrowed from `*`
- `ADMIN_ENABLED=true` **only** on the ingest instance

The container runs as a non-root user, has a healthcheck on `/healthz`, and
starts uvicorn with uvloop + httptools. Scale with `--workers`; each worker owns
its own pool and cache, so size `DB_POOL_MAX` against Postgres `max_connections`
divided by worker count.

Rate limiting belongs at the edge — Cloudflare or nginx sees traffic before it
costs a worker anything. `RATE_LIMIT_ENABLED=true` turns on a per-worker token
bucket as defence in depth for a directly exposed origin.

## Known gaps

- **Premium media is not access-controlled.** `is_premium` is a flag on a
  publicly reachable URL. Protecting it needs Cloudflare signed URLs (which keep
  CDN caching) rather than S3 presigned URLs (which defeat it).
- **Bulk ingest is synchronous.** Pillow runs in a thread per upload, which is
  fine for admin-paced uploads. Sustained bulk loading wants a process pool or an
  out-of-band worker.
