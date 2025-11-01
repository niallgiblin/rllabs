# app/services/download_service.py
# -----------------------------------------------------------------------------
# Service layer for generating pre-signed *download* URLs for stored artifacts.
#
# Responsibilities
# - Look up artifacts by ID or by content hash via repository helpers.
# - Authorize the requesting user (stubbed for now).
# - Produce a time-limited, cache-friendly, pre-signed S3 GET URL.
#
# Implementation notes
# - Uses aioboto3's async client for S3-compatible storage (S3, MinIO, etc.).
# - Cache headers: we set "public, immutable, max-age=31536000" on the response
#   so CDN/browser can cache aggressively when the object key is content-addressed.
# - Optional `filename` controls the `Content-Disposition` header on the presigned
#   URL so the browser suggests that name when saving.
# - Security: `_authorize` is a TODO; wire real RBAC/tenant checks before prod.
# - Naming: `Session` below comes from aioboto3, not SQLAlchemy.
# -----------------------------------------------------------------------------

from typing import Optional
from aioboto3 import Session
from ..settings import settings
from ..repositories.artifacts_repo import get_artifact_by_id, get_artifact_by_hash

# TODO: wire an actual RBAC check
async def _authorize(user_id: Optional[str], artifact_id: str) -> bool:
    """
    Placeholder authorization hook. Always returns True in development.

    Parameters
    ----------
    user_id : Optional[str]
        The caller's user/tenant identifier (if available from auth).
    artifact_id : str
        The target artifact primary key.

    Returns
    -------
    bool
        True if access should be granted. Replace with real RBAC logic.
    """
    return True  # allow all in dev

async def presign_download_by_id(db, artifact_id: str, filename: Optional[str] = None):
    """
    Generate a pre-signed download URL for an artifact referenced by DB ID.

    Parameters
    ----------
    db : Session
        Database session used to load the Artifact.
    artifact_id : str
        Primary key of the Artifact.
    filename : Optional[str]
        Optional override for the download filename (Content-Disposition).

    Returns
    -------
    dict | None
        {"url": ..., "key": ..., "ttl": ...} on success, else None if not found.
    """
    # Look up the artifact; return None to the router if not found.
    art = get_artifact_by_id(db, artifact_id)
    if not art:
        return None
    # Delegate to the lower-level presign helper using the artifact's storage key.
    return await _presign_key(art.storage_key, filename)

async def presign_download_by_hash(db, sha256_hex: str, filename: Optional[str] = None):
    """
    Generate a pre-signed download URL by content hash (SHA-256 hex).

    Useful for content-addressed retrieval, where clients only know the hash.

    Parameters
    ----------
    db : Session
        Database session used to resolve the Artifact by hash.
    sha256_hex : str
        Hex-encoded SHA-256 of the artifact content.
    filename : Optional[str]
        Optional override for the download filename.

    Returns
    -------
    dict | None
        {"url": ..., "key": ..., "ttl": ...} on success, else None if not found.
    """
    art = get_artifact_by_hash(db, sha256_hex)
    if not art:
        return None
    return await _presign_key(art.storage_key, filename)

async def _presign_key(storage_key: str, filename: Optional[str] = None):
    """
    Low-level helper that produces a pre-signed S3 GET URL for a given object key.

    Parameters
    ----------
    storage_key : str
        The object key in the bucket (e.g., 'artifacts/aa/bb/<uuid>').
    filename : Optional[str]
        If provided, adds Content-Disposition so browsers download with this name.

    Returns
    -------
    dict
        {
          "url": <pre-signed GET URL>,
          "key": <storage_key>,
          "ttl": <seconds until URL expires>
        }
    """
    # Base parameters for the signed request. Response headers here are *hints* that
    # S3 will include when the client downloads via the pre-signed URL.
    params = {
        "Bucket": settings.S3_BUCKET,
        "Key": storage_key,
        # Encourage long-lived caching for immutable objects (typical for content-hash keys).
        "ResponseCacheControl": "public, immutable, max-age=31536000",
    }
    if filename:
        # Force download with a friendly filename on the client.
        # Using quotes handles spaces/special characters safely.
        params["ResponseContentDisposition"] = f"attachment; filename=\"{filename}\""

    # Create an async S3 client with configured endpoint/credentials/SSL.
    async with Session().client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        use_ssl=settings.S3_USE_SSL,
    ) as s3:
        # Produce a time-limited GET URL for the object.
        url = await s3.generate_presigned_url(
            ClientMethod="get_object",
            Params=params,
            ExpiresIn=settings.PRESIGN_TTL_SECONDS,
        )
        # Return a compact payload for the API layer to forward to clients.
        return {"url": url, "key": storage_key, "ttl": settings.PRESIGN_TTL_SECONDS}
