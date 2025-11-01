# app/main.py
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
    try:
        create_db_and_tables()
    except Exception as exc:
        app.state.db_init_error = str(exc)
    else:
        app.state.db_init_error = None
    yield

app = FastAPI(title=settings.APP_NAME, version=os.getenv("APP_VERSION", "0.0.1"), lifespan=lifespan)
app.middleware("http")(log_requests)
app.include_router(uploads.router)
app.include_router(downloads.router)

@app.get("/health")
async def health():
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