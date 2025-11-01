#app/utils/observability.py
# -----------------------------------------------------------------------------
# Lightweight observability utilities.
#
# This module provides a simple ASGI/Starlette/FastAPI middleware-like function
# that logs every incoming request, its duration, and the resulting status code.
# Intended use:
#   app.middleware("http")(log_requests)
#
# Notes for maintainers:
# - Logging is configured at import time with a basic INFO-level formatter.
#   If your application sets up logging elsewhere, ensure this doesn't conflict.
# - We use time.time() for wall-clock duration (sufficient for request timing).
# - logger name "upload_download_service" matches the rest of the codebase so
#   all logs share a single stream/formatter.
# -----------------------------------------------------------------------------

import logging
import time
from fastapi import Request

# Module-level logger and default configuration.
logger = logging.getLogger("upload_download_service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def log_requests(request: Request, call_next):
    """
    HTTP middleware-like handler that logs method, path, duration, and status.

    Parameters
    ----------
    request : fastapi.Request
        The incoming HTTP request object.
    call_next : Callable
        The downstream handler; when awaited it returns a Response.

    Behavior
    --------
    - Captures start time, awaits downstream, computes duration in seconds
      to three decimal places, and logs a single-line summary:
        "<METHOD> <PATH> completed in <duration>s -> <status_code>"
    - Returns the original response untouched.
    """
    start = time.time()
    response = await call_next(request)
    duration = round(time.time() - start, 3)
    logger.info(f"{request.method} {request.url.path} completed in {duration}s -> {response.status_code}")
    return response
