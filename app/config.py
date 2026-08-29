"""Environment-driven settings. Nothing in this codebase reads os.environ directly.

Every knob, limit, credential and feature check lives here so that behaviour is
changed by redeploying with a different environment, never by editing code.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ---- runtime ----
    ENV: Literal["local", "staging", "production"] = "local"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False
    DEBUG: bool = False
    DOCS_ENABLED: bool = True

    # ---- database ----
    DATABASE_URL: str = "postgresql://wallmo:wallmo@localhost:5432/wallmo"
    DB_POOL_MIN: int = 5
    DB_POOL_MAX: int = 20
    DB_COMMAND_TIMEOUT: float = 10.0
    DB_STATEMENT_TIMEOUT_MS: int = 8_000
    # Set to 0 when running behind pgbouncer in transaction pooling mode.
    DB_STATEMENT_CACHE_SIZE: int = 256
    DB_MAX_INACTIVE_LIFETIME: float = 300.0

    # ---- media delivery ----
    # CDN / public origin prefix. file_path = MEDIA_BASE_URL + "/" + storage_key
    MEDIA_BASE_URL: str = "http://localhost:8000/media"

    # ---- pagination ----
    PAGE_SIZE_DEFAULT: int = 20
    PAGE_SIZE_MAX: int = 200
    # Whole-category bundle: max artworks returned per subcategory.
    BUNDLE_LIMIT_DEFAULT: int = 500
    BUNDLE_LIMIT_MAX: int = 2_000

    # ---- caching ----
    CACHE_ENABLED: bool = True
    CACHE_TTL_SECONDS: int = 30
    CACHE_MAX_ENTRIES: int = 512
    HTTP_MAX_AGE: int = 60
    HTTP_STALE_WHILE_REVALIDATE: int = 300
    GZIP_MIN_SIZE: int = 1_024

    # ---- rate limiting (defence in depth; the CDN/edge is the real limiter) ----
    RATE_LIMIT_ENABLED: bool = False
    RATE_LIMIT_PER_MINUTE: int = 600
    RATE_LIMIT_BURST: int = 120

    # ---- CORS ----
    CORS_ORIGINS: str = "*"

    # ---- storage (Backblaze B2, S3-compatible) ----
    STORAGE_BACKEND: Literal["b2", "none"] = "none"
    B2_ENDPOINT_URL: str = ""
    B2_REGION: str = ""
    B2_BUCKET: str = ""
    B2_KEY_ID: str = ""
    B2_APP_KEY: str = ""
    B2_UPLOAD_CONCURRENCY: int = 4

    # ---- uploads ----
    UPLOAD_MAX_BYTES: int = 64 * 1024 * 1024
    UPLOAD_IMAGE_MIMES: str = "image/png,image/jpeg,image/webp"
    UPLOAD_VIDEO_MIMES: str = "video/mp4,video/webm"
    THUMB_WIDTH: int = 400
    PREVIEW_WIDTH: int = 1_080
    WEBP_QUALITY: int = 82
    # A 'static' subcategory takes images, a 'live' one takes video.
    ENFORCE_KIND_MEDIA_MATCH: bool = True
    # Permanently removing objects from the bucket is opt-in.
    STORAGE_ALLOW_PURGE: bool = False

    # ---- admin (write) surface ----
    # The public read API is unauthenticated by design. The write surface is off
    # unless ADMIN_ENABLED=true; ADMIN_API_KEY is an optional extra check and is
    # skipped entirely when left blank.
    ADMIN_ENABLED: bool = False
    ADMIN_API_KEY: str = ""

    # ---- observability ----
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0

    @field_validator("MEDIA_BASE_URL")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("DATABASE_URL")
    @classmethod
    def _normalise_dsn(cls, v: str) -> str:
        # Accept SQLAlchemy-style DSNs; asyncpg wants the bare scheme.
        return v.replace("postgresql+asyncpg://", "postgresql://").replace(
            "postgres://", "postgresql://"
        )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def image_mimes(self) -> frozenset[str]:
        return frozenset(m.strip() for m in self.UPLOAD_IMAGE_MIMES.split(",") if m.strip())

    @property
    def video_mimes(self) -> frozenset[str]:
        return frozenset(m.strip() for m in self.UPLOAD_VIDEO_MIMES.split(",") if m.strip())

    @property
    def cache_control(self) -> str:
        return (
            f"public, max-age={self.HTTP_MAX_AGE}, "
            f"stale-while-revalidate={self.HTTP_STALE_WHILE_REVALIDATE}"
        )

    def clamp_page_size(self, requested: int | None) -> int:
        if requested is None:
            return self.PAGE_SIZE_DEFAULT
        return max(1, min(requested, self.PAGE_SIZE_MAX))

    def clamp_bundle_limit(self, requested: int | None) -> int:
        if requested is None:
            return self.BUNDLE_LIMIT_DEFAULT
        return max(1, min(requested, self.BUNDLE_LIMIT_MAX))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
