"""Liveness and readiness probes.

/healthz must not touch the database: a load balancer uses it to decide whether
the process is alive, and a slow database would otherwise cause a restart storm.
/readyz is the one that checks dependencies.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.config import Settings, get_settings
from app.db import db
from app.http import cache_stats
from app.schemas import Health

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=Health)
async def healthz(settings: Settings = Depends(get_settings)) -> Health:
    return Health(status="ok", env=settings.ENV)


@router.get("/readyz")
async def readyz(response: Response, settings: Settings = Depends(get_settings)) -> dict:
    ok = await db.healthy()
    response.status_code = 200 if ok else 503
    return {
        "status": "ready" if ok else "unavailable",
        "env": settings.ENV,
        "database": "up" if ok else "down",
        "cache": cache_stats(),
    }
