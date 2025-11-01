# app/routers/downloads.py
# -----------------------------------------------------------------------------
# FastAPI router for generating *download* URLs (typically pre-signed links)
# for stored artifacts. Endpoints delegate to the download_service and
# consistently:
#   - open a short-lived DB session (via session_scope)
#   - ask the service layer to build a pre-signed URL
#   - return 404 if the artifact cannot be found/resolved
#
# Notes for maintainers:
# - Endpoints are async to play nicely with FastAPI; the service functions
#   themselves may perform async I/O (e.g., cloud SDK calls).
# - `filename` is optional; when provided it can influence the `Content-Disposition`
#   header on the pre-signed link (so the browser saves as that name).
# - `artifact_id` is the DB primary key for an Artifact row. The "by-hash" route
#   supports lookups by SHA-256 hex content hash for de-duplication flows.
# - Errors: we intentionally return 404 for "not found" rather than 400/500.
# -----------------------------------------------------------------------------

from fastapi import APIRouter, HTTPException
from typing import Optional
from ..database import session_scope
from ..services import download_service

# Group all routes under /downloads with a "Downloads" tag for docs.
router = APIRouter(prefix="/downloads", tags=["Downloads"])

@router.get("/{artifact_id}")
async def get_download(artifact_id: str, filename: Optional[str] = None):
    """
    Produce a pre-signed download URL for a specific artifact ID.

    Parameters
    ----------
    artifact_id : str
        Primary key of the Artifact (UUID-as-string or similar).
    filename : Optional[str]
        Suggested filename for download (may affect Content-Disposition).

    Returns
    -------
    dict
        Service-defined payload, typically including the pre-signed URL and
        any headers/metadata required by the client to initiate the download.

    Raises
    ------
    HTTPException(404)
        If no artifact exists with the given ID.
    """
    # Use a scoped DB session; it commits/rolls back and closes automatically.
    with session_scope() as db:
        # Delegate to the service layer for storage-provider specifics.
        res = await download_service.presign_download_by_id(db, artifact_id, filename)
    if not res:
        # Standardize not-found behavior for clients.
        raise HTTPException(status_code=404, detail="Artifact not found")
    return res

@router.get("/by-hash/{sha256_hex}")
async def get_download_by_hash(sha256_hex: str, filename: Optional[str] = None):
    """
    Produce a pre-signed download URL by content hash (SHA-256 hex).

    This supports content-addressed retrieval: clients who know the hash of
    the desired file can obtain a download link without a prior artifact ID.

    Parameters
    ----------
    sha256_hex : str
        64-character hex-encoded SHA-256 of the artifact content.
    filename : Optional[str]
        Suggested filename for the resulting download.

    Returns
    -------
    dict
        Service-defined payload including the pre-signed URL.

    Raises
    ------
    HTTPException(404)
        If no artifact exists with the given content hash.
    """
    with session_scope() as db:
        res = await download_service.presign_download_by_hash(db, sha256_hex, filename)
    if not res:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return res
