# wallmo

Wallpaper catalog API. FastAPI + Postgres (asyncpg, raw SQL) + Backblaze B2 for media.
Read-only feed endpoints for the app frontend, plus a key-guarded admin upload path.

## Layout

    app/
      main.py            app factory, lifespan, middleware
      config.py          env settings
      db.py              asyncpg pool + paged-fetch helper
      schemas.py         Pydantic response models (defines JSON key order)
      queries.py         all SQL, one constant per query
      storage.py         B2 client, key/URL builders, image renditions
      routers/
        health.py        /healthz, /readyz
        categories.py    /v1/categories, /v1/categories/{id}/artworks
        artworks.py      /v1/artworks
        admin.py         write endpoints (ADMIN_API_KEY)
    migrations/          numbered .sql, applied by scripts/migrate.py
    scripts/             migrate.py, seed.py
    tests/               contract tests over ASGITransport

## Endpoints

    GET /v1/categories                  ?page &page_size &parent_id &kind
    GET /v1/artworks                    ?page &page_size &kind &is_premium
    GET /v1/categories/{id}/artworks    ?page &page_size
    GET /healthz  /readyz

All list responses use the same envelope:

    { "data": [...], "meta": { "page", "page_size", "total", "has_next" } }

## Model

Categories are a one-level tree: root categories (Anime, Nature, ...) each have
`live` and `static` subcategories. Only subcategories hold artworks, so
`artworks.category_id` always points at a leaf. `categories.artwork_count` is
denormalized and trigger-maintained.

`artworks.storage_key` holds the B2 object key; `file_path` in responses is
built as `MEDIA_BASE_URL + "/" + storage_key`. The API never proxies media
bytes - Cloudflare fronts the public B2 bucket.

## Local dev

    cp .env.example .env
    docker compose up -d db
    uv venv && uv pip install -e ".[dev]"
    python -m scripts.migrate
    python -m scripts.seed
    uvicorn app.main:app --reload

## Deploy

    docker compose up -d --build
