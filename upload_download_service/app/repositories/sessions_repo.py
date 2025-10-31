# app/repositories/sessions_repo.py
# -----------------------------------------------------------------------------
# Repository helpers for managing UploadSession lifecycle.
#
# Context (see "Upload/Download Data Flow Overview"):
# - An UploadSession tracks a client-side multipart upload to blob storage.
# - The service creates a session (status=in_progress), the client uploads parts,
#   and we update 'parts_json' as progress metadata. When complete, we mark
#   the session 'completed'; on failure or user cancel we mark it 'aborted'.
#
# Notes for maintainers:
# - db.flush() is used (not commit) so callers can compose operations within a
#   larger transaction boundary.
# - This file defines TWO functions named `get_session`. In Python, the second
#   definition overrides the first at import time. That means the UUID-validated
#   version below is effectively shadowed by the later legacy `.get()` version.
#   This comment is intentionally explicit but we are not changing code per the
#   request—just be aware of which one actually executes.
# - Query.get() used later is a legacy/2.x-deprecated pattern; Session.get() is
#   the modern API. Kept as-is here for compatibility.
# -----------------------------------------------------------------------------

from uuid import UUID
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
from ..models import UploadSession, UploadStatus

def get_session(db: Session, session_id: str):
    """Fetch a single UploadSession by ID (string-safe for UUID).

    Implementation details:
    - Attempts to parse `session_id` into a UUID first to avoid invalid casts in SQL.
    - If parsing fails, returns None instead of raising.
    - Uses a normal filter/one_or_none() to fetch the row.

    Warning:
    - This definition is later overridden by another `get_session` definition below.
      Leaving this here for historical context and to document the intended behavior.
    """
    try:
        # Validate the provided identifier is a proper UUID to prevent malformed
        # values from reaching the database layer.
        sid = UUID(session_id)
    except Exception:
        # On any parsing error, treat as not found.
        return None
    # Query by the typed UUID value.
    return db.query(UploadSession).filter(UploadSession.id == sid).one_or_none()

def create_session(
    db: Session,
    *,
    temp_key: str,
    owner_id: Optional[str] = None,
    expected_hash: Optional[str] = None,
    parts_json: Optional[Dict[str, Any]] = None,
) -> UploadSession:
    """
    Create and persist a new UploadSession in 'in_progress' state.

    Parameters
    ----------
    temp_key : str
        The temporary object key in blob storage for the multipart upload.
    owner_id : Optional[str]
        Optional user/tenant identifier for RBAC/ownership checks.
    expected_hash : Optional[str]
        Optional full-file hash (e.g., SHA-256) to validate upon completion.
    parts_json : Optional[Dict[str, Any]]
        Arbitrary JSON-safe dict capturing part ETags, sizes, or upload state.

    Returns
    -------
    UploadSession
        The newly created session ORM instance (flushed but not committed).
    """
    # Construct the transient ORM object for the new session.
    sess = UploadSession(
        temp_key=temp_key,
        owner_id=owner_id,
        expected_hash=expected_hash,
        parts_json=parts_json or {},  # default to empty structure for easier updates
        status=UploadStatus.in_progress,
    )
    db.add(sess)
    # Flush to assign PK/DB-generated fields without finalizing the transaction.
    db.flush()
    return sess


def get_session(db: Session, session_id: str) -> Optional[UploadSession]:
    #  This function overrides the earlier get_session defined above.
    # It uses the legacy Query.get() call path (accepts PK directly).
    # If session_id is not a UUID or not found, this returns None.
    return db.query(UploadSession).get(session_id)


def mark_aborted(db: Session, session_id: str) -> Optional[UploadSession]:
    """
    Transition a session to 'aborted'.

    Behavior
    --------
    - No-op if the session does not exist (returns None).
    - Otherwise sets status and flushes to persist state change within the
      current transaction (caller decides when to commit).
    """
    sess = get_session(db, session_id)
    if not sess:
        return None
    # Update the finite-state of the upload session: in_progress -> aborted.
    sess.status = UploadStatus.aborted
    db.flush()
    return sess


def mark_completed(db: Session, session_id: str) -> Optional[UploadSession]:
    """
    Transition a session to 'completed'.

    Typical call site: after verifying parts and assembling the object,
    and after verifying the final digest (if expected_hash is set).
    """
    sess = get_session(db, session_id)
    if not sess:
        return None
    # Update the finite-state of the upload session: in_progress -> completed.
    sess.status = UploadStatus.completed
    db.flush()
    return sess


def update_parts(db: Session, session_id: str, parts_json: Dict[str, Any]) -> Optional[UploadSession]:
    """
    Replace the 'parts_json' metadata for a session.

    Common payload contents:
    - mapping of partNumber -> { etag, size, offset }
    - upload progress markers for resume/retry UX

    Note
    ----
    - This is a full replace; callers should pass the whole desired structure.
    """
    sess = get_session(db, session_id)
    if not sess:
        return None
    # Persist the latest known multipart state (useful for resume and auditing).
    sess.parts_json = parts_json
    db.flush()
    return sess
