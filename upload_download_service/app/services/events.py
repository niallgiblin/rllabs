# app/services/events.py
# -----------------------------------------------------------------------------
# Outbound event notifications to external services.
#
# This module currently exposes a single helper used to notify a separate
# "Model Catalog" service that a new artifact was created. The notification
# is sent as a short HTTP POST with minimal metadata.
#
# Design notes:
# - The base URL and API key are configured via settings; if no base URL is
#   provided, the call is skipped (useful for local/dev environments).
# - Uses httpx.AsyncClient with a small timeout to avoid blocking request flow.
# - Function is async to fit naturally into async service/router code.
# - Return value is a simple dict describing what happened (status/response,
#   skipped=true, or error message). This makes it easy for callers to log.
# -----------------------------------------------------------------------------

import httpx
from ..settings import settings

async def notify_model_catalog(artifact_id: str, content_hash: str):
    """
    Notify the external Model Catalog that an artifact is available.

    Parameters
    ----------
    artifact_id : str
        The primary key/identifier of the artifact in this service.
    content_hash : str
        Hex-encoded content digest (e.g., SHA-256) for the artifact.

    Behavior
    --------
    - If MODEL_CATALOG_BASE_URL is not set, returns {"skipped": True}.
    - Otherwise, POSTs to '<BASE_URL>/artifacts/register' with JSON payload:
        { "artifact_id": <id>, "content_hash": <hash> }
      and includes 'x-api-key' header if configured.

    Returns
    -------
    dict
        One of:
          - {"skipped": True} when not configured
          - {"status_code": <int>, "response": <text>} on success/failure HTTP response
          - {"error": <str>} if an exception occurs (timeout, network error, etc.)
    """
    # No-op in environments where the external service is not configured.
    if not settings.MODEL_CATALOG_BASE_URL:
        return {"skipped": True}

    # Compose the target endpoint URL.
    url = f"{settings.MODEL_CATALOG_BASE_URL}/artifacts/register"

    # Create a short-lived async HTTP client with a conservative timeout so we
    # don't hang the request pipeline if the external service is slow/unavailable.
    async with httpx.AsyncClient(timeout=5.0) as client:
        # Minimal data needed by the catalog to reference and validate the artifact.
        payload = {"artifact_id": artifact_id, "content_hash": content_hash}
        # Use API key auth via a custom header; empty string if not set.
        headers = {"x-api-key": settings.MODEL_CATALOG_API_KEY or ""}

        try:
            # Fire-and-forget style call; caller can decide how to handle non-2xx.
            resp = await client.post(url, json=payload, headers=headers)
            # Return raw status and body text for logging/diagnostics.
            return {"status_code": resp.status_code, "response": resp.text}
        except Exception as exc:
            # Surface exception info to the caller; avoid raising to keep uploads robust.
            return {"error": str(exc)}
