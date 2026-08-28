"""App factory and ASGI entrypoint.

Responsibilities:
  - build FastAPI(default_response_class=ORJSONResponse)
  - lifespan: open/close the asyncpg pool (db.open_pool / db.close_pool)
  - register routers: health, categories, artworks, admin
  - middleware: CORS, rate limit, request-id / structlog
"""
