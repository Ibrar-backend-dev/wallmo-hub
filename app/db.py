"""asyncpg connection pool.

  open_pool() / close_pool()  -> called from main.lifespan
  get_pool()                  -> FastAPI dependency
  fetch_page(sql, *args)      -> shared helper: runs a COUNT(*) OVER() query and
                                 returns (rows, total) in one round trip
"""
