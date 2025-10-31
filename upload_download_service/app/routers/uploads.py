# app/routers/uploads.py
# -----------------------------------------------------------------------------
# FastAPI router exposing endpoints to:
# 1) start a new upload (initiate a blob upload + create DB session metadata)
# 2) complete an upload (finalize multipart/object, verify digest, persist Artifact)
# 3) abort an upload (mark session aborted and clean up as needed)
#
# Design notes:
# - Each handler opens a short-lived DB session via `session_scope()` and delegates
#   storage-specific logic to the `upload_service` (service layer).
# - Handlers are `async` to allow the service layer to perform async I/O
#   (e.g., presigning with cloud SDKs, network calls).
# - `expected_hash` is typically a hex-encoded SHA-256 provided by the client;
#   we pass it through to enable server-side verification at completion time.
# - `etag` in `complete_upload` is the provider-returned ETag for the uploaded object
#   (single-part or composed multipart) used to verify integrity / match parts.
# - `owner_id` is a simple placeholder for auth/tenancy; replace with real auth later.
#
# Implementation notes:
# - `Depends` and `Session` are currently unused but kept for parity with other routers
#   and potential future dependency injection.
# - The `complete_upload` endpoint declares `response_model=ArtifactOut` to shape the
#   OpenAPI schema and automatically serialize the ORM object returned by the service.
# -----------------------------------------------------------------------------

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import session_scope
from ..services import upload_service
from ..schemas import ArtifactOut

# Group upload-related routes under /uploads; "Uploads" tag organizes OpenAPI docs.
router = APIRouter(prefix="/uploads", tags=["Uploads"])


@router.post("")
async def start_upload(
    filename: str,
    size_bytes: int,
    mime_type: str = "application/octet-stream",
    expected_hash: str = "",
    owner_id: str = "demo-user",
):
    """
    Initialize a new upload session and return provider-specific parameters
    (e.g., pre-signed URL, temp key, session ID).

    Parameters
    ----------
    filename : str
        Original client filename; used for metadata and suggested download name.
    size_bytes : int
        Declared total size; useful for validation and preflight checks.
    mime_type : str, default "application/octet-stream"
        MIME type hint for storage and downstream consumers.
    expected_hash : str, default ""
        Optional hex-encoded SHA-256 for end-to-end integrity verification.
    owner_id : str, default "demo-user"
        Simple owner/tenant marker for RBAC (placeholder until real auth).

    Returns
    -------
    dict
        Service-defined payload with fields required by the client to upload.
    """
    with session_scope() as db:
        # Delegate to service: create UploadSession, presign upload, return details.
        result = await upload_service.start_upload(db, filename, size_bytes, mime_type, expected_hash, owner_id)
    return result


@router.post("/{upload_id}/complete", response_model=ArtifactOut)
async def complete_upload(upload_id: str, expected_hash: str, etag: str):
    """
    Finalize an upload session:
    - Verify object integrity (compare `expected_hash` and/or provider `etag`)
    - Persist/return the resulting Artifact record

    Parameters
    ----------
    upload_id : str
        Identifier of the UploadSession previously created by start_upload.
    expected_hash : str
        Client's SHA-256 hex to validate server-side after assembly.
    etag : str
        Storage provider ETag of the uploaded object (or composite for multipart).

    Returns
    -------
    ArtifactOut
        Serialized artifact metadata for the newly created (or matched) Artifact.
    """
    with session_scope() as db:
        artifact = await upload_service.complete_upload(db, upload_id, expected_hash, etag)
    return artifact


@router.post("/{upload_id}/abort")
async def abort_upload(upload_id: str):
    """
    Abort an in-progress upload session.

    Parameters
    ----------
    upload_id : str
        Identifier of the UploadSession to abort.

    Returns
    -------
    dict | None
        Service-defined result (e.g., confirmation/status). None if not found.
    """
    with session_scope() as db:
        # Mark the session aborted; service may also attempt best-effort cleanup.
        result = upload_service.abort_upload(db, upload_id)
    return result
