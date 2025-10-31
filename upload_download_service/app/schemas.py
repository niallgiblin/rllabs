# app/schemas.py
# -----------------------------------------------------------------------------
# Pydantic schemas (response models) for the API.
#
# Purpose
# - Define the JSON shapes returned by routers/services.
# - Keep these minimal and stable for clients; evolve iteratively as features
#   expand (see Phase 3 notes).
#
# Notes for maintainers
# - `orm_mode = True` lets Pydantic read attributes directly from SQLAlchemy
#   ORM objects (no need to pre-convert to dicts).
# - Favor explicit fields and types to produce a clear OpenAPI spec and help
#   clients generate type-safe bindings.
# -----------------------------------------------------------------------------

from typing import List, Optional
from pydantic import BaseModel, Field

# Response models kept minimal for now — expanded in Phase 3.

class ArtifactOut(BaseModel):
    """Public view of an Artifact row returned to clients.

    Fields
    ------
    id : str
        Primary key of the artifact (typically UUID as string).
    storage_key : str
        Content-addressed key in the blob store (e.g., 'sha256/<hash>').
    content_hash : str
        Hex-encoded SHA-256 digest used for deduplication/integrity.
    size_bytes : int
        Size of the stored object in bytes (may be 0 if not captured yet).
    mime_type : Optional[str]
        Media type hint for clients/UI (if known).
    """
    id: str
    storage_key: str
    content_hash: str
    size_bytes: int
    mime_type: Optional[str] = None

    class Config:
        # Enable reading from ORM objects (e.g., SQLAlchemy models).
        orm_mode = True


class UploadSessionOut(BaseModel):
    """Public view of an upload session's basic state.

    Fields
    ------
    id : str
        Server-assigned identifier for the upload session.
    status : str
        Current lifecycle status (e.g., 'in_progress', 'completed', 'aborted').
    temp_key : str
        Temporary object key used during multipart upload.
    """
    id: str
    status: str
    temp_key: str

    class Config:
        # Allow direct serialization from ORM instances.
        orm_mode = True
