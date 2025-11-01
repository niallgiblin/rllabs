# app/main.py
# -----------------------------------------------------------------------------
# FastAPI application bootstrap.
#
# Responsibilities
# - Configure app metadata (title/version) from settings/environment.
# - Initialize database schema at startup (best-effort) and record any init error.
# - Register HTTP middleware for request logging.
# - Mount upload/download routers.
# - Expose a lightweight /health endpoint combining DB init + S3 health.
#
# Notes for maintainers:
# - We use FastAPI's async lifespan context to perform startup work. Any exception
#   during DB initialization is captured on app.state.db_init_error so the service
#   can still start (in a degraded state) and report diagnostics via /health.
# - log_requests middleware emits a single line per request with duration & status.
# - s3_health() returns None when healthy or a short error string otherwise;
#   we surface that directly in the /health payload and degrade status if present.
# - APP_VERSION is read from env at import time; default "0.0.1" if not set.
# - For more robust schema management, prefer migrations (e.g., Alembic) rather
#   than create_db_and_tables(), which is convenient for demos/dev environments.
# -----------------------------------------------------------------------------

from fastapi import FastAPI
from contextlib import asynccontextmanager
import os
from .settings import settings
from .database import create_db_and_tables
from .routers import uploads, downloads
from .storage.s3_client import s3_health
from .utils.observability import log_requests

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan hook.

    Startup:
      - Attempt to create DB tables. On failure, store the error string on
        app.state.db_init_error so /health can report "degraded".
    Shutdown:
      - No explicit cleanup is required here; yielding control returns after
        shutdown where you'd place teardown if needed.
    """
    try:
        create_db_and_tables()
    except Exception as exc:
        app.state.db_init_error = str(exc)
    else:
        app.state.db_init_error = None
    yield

# Instantiate FastAPI with title/version and lifespan management.
app = FastAPI(title=settings.APP_NAME, version=os.getenv("APP_VERSION", "0.0.1"), lifespan=lifespan)

# Register a simple request-logging middleware early so it wraps all routes.
app.middleware("http")(log_requests)

# Mount feature routers: uploads and downloads APIs.
app.include_router(uploads.router)
app.include_router(downloads.router)

@app.get("/health")
async def health():
    """
    Basic health endpoint.

    Returns JSON with:
      - service: app name
      - env: environment name (from settings)
      - db_init_error: str or None, set at startup if DB init failed
      - s3_error: str or None, from s3_health check
      - status: "ok" if no issues, otherwise "degraded"

    This is suitable for readiness probes and dashboards.
    """
    s3_err = s3_health(settings.S3_BUCKET)
    degraded = any([
        getattr(app.state, "db_init_error", None),
        s3_err,
    ])
    return {
        "service": settings.APP_NAME,
        "env": settings.APP_ENV,
        "db_init_error": getattr(app.state, "db_init_error", None),
        "s3_error": s3_err,
        "status": "ok" if not degraded else "degraded",
    }
