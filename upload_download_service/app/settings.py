# app/settings.py
# -----------------------------------------------------------------------------
# Centralized configuration using Pydantic BaseSettings.
#
# How it works
# ------------
# - Each attribute becomes a setting that can be overridden via environment
#   variables. Pydantic reads from the environment and (optionally) a .env file.
# - Types enforce parsing/validation (e.g., AnyHttpUrl for URLs, bool/int, etc.).
# - `settings = Settings()` creates a singleton-like object imported elsewhere.
#
# Conventions
# -----------
# - Defaults here are development-friendly. For staging/prod, override via env.
# - Sensitive values (secrets/keys) should be set via environment, not committed.
# - APP_ENV is a simple string switch used by /health and logging; expand as needed.
#
# Notes for maintainers
# ---------------------
# - `env_file = ".env"` enables local overrides without exporting variables.
# - `case_sensitive = True` aligns with typical container/runtime expectations.
# - If you introduce nested config or complex types, prefer explicit validators.
# - Changing DATABASE_URL or S3_* requires service restart to pick up new values.
# -----------------------------------------------------------------------------

from pydantic import BaseSettings, AnyHttpUrl
from typing import Optional


class Settings(BaseSettings):
    # Service
    APP_NAME: str = "upload_download_service"
    APP_ENV: str = "dev" # dev|staging|prod
    PORT: int = 8081

    # Database (metadata store for artifacts)
    # Example Postgres DSN format:
    #   postgresql://<user>:<password>@<host>[:port]/<database>
    # For Docker Compose, host is the service name (e.g., "postgres").
    DATABASE_URL: str = "postgresql://rllabs:rllabs_password@postgres/ud_metadata"

    # Object Storage (MinIO / S3)
    # If targeting AWS S3 directly, set S3_ENDPOINT_URL to the AWS endpoint
    # (or omit and let boto pick defaults), set S3_USE_SSL=True, and update keys.
    S3_ENDPOINT_URL: str = "http://minio:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "rllabs-artifacts"
    S3_USE_SSL: bool = False

    # Presigned URL defaults
    # Time-to-live (seconds) for generated pre-signed URLs.
    # Clients should complete uploads/downloads within this window.
    PRESIGN_TTL_SECONDS: int = 300

    # Redis (idempotency/rate-limit) — wired later phases
    # Placeholder connection string for future middleware/features.
    REDIS_URL: str = "redis://redis:6379/0"

    # Optional: model catalog endpoint for later notification
    # If MODEL_CATALOG_BASE_URL is unset/None, notifications will be skipped.
    MODEL_CATALOG_BASE_URL: Optional[AnyHttpUrl] = None
    MODEL_CATALOG_API_KEY: Optional[str] = None

    class Config:
        # Load values from a local .env file in addition to the process environment.
        env_file = ".env"
        # Treat environment variable names as case-sensitive.
        case_sensitive = True

# Instantiate the settings object to be imported throughout the app.
settings = Settings()
