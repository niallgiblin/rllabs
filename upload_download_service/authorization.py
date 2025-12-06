"""
Authorization Service for Artifact Access Control
==================================================

Checks if a user has permission to access an artifact before generating presigned URLs.

Authorisation happens in the application layer before generating presigned URLs.
MinIO doesn't need to know about users/models/permissions - it just stores files.

Architecture Decision:
- Presigned URLs are time-limited access tokens (security through expiration)
- RBAC is enforced in our service (where we have business logic)
- MinIO stays simple (just storage)
- Result: Secure + Performant (direct client-to-MinIO transfers)
"""

from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text, and_
import logging
import os
import httpx

from database import UploadSession, UploadStatus

logger = logging.getLogger(__name__)


async def check_download_permission(
    db: Session,
    user_id: Optional[str],
    artifact_id: str,
    training_model_id: Optional[int] = None
) -> Tuple[bool, Optional[str]]:
    """
    Check if user has permission to download an artifact.
    
    Authorization Rules (in order of priority):
    1. Public downloads: If no user_id provided, allow download (public access)
    2. Owner can always download their artifacts (checked via upload session)
    3. Users with access to the model can download (if artifact belongs to model)
    
    Args:
        db: Database session
        user_id: User requesting download (from X-User-Id header, optional for public downloads)
        artifact_id: Content hash (sha256:...) of artifact
        
    Returns:
        Tuple of (is_authorized: bool, error_code: Optional[str])
        - (True, None) if authorized
        - (False, "404") if artifact not found (should return 404)
        - (False, "403") if unauthorized (should return 403)
        - (False, "400") if invalid input (should return 400)
        
    Security Model:
    - Fail closed: Returns False on any error or missing data
    - Logs all authorization attempts for audit trail
    - Distinguishes between "not found" (404) and "forbidden" (403)
    """
    # user_id is optional for public downloads
    # If not provided, we allow public access (no permission check needed)
    
    if not artifact_id:
        logger.warning("Download request without artifact_id - validation error")
        return False, "400"  # Missing required parameter
    
    # Validate artifact_id format (should be sha256:hex)
    if not artifact_id.startswith("sha256:"):
        # Check if it's a valid hex string without prefix
        if len(artifact_id) == 64 and all(c in '0123456789abcdefABCDEF' for c in artifact_id):
            # Valid hex but missing prefix - this is acceptable, normalize it
            artifact_id = f"sha256:{artifact_id}"
        else:
            # Invalid format
            logger.warning(f"Invalid artifact_id format: {artifact_id}")
            return False, "400"  # Invalid format
    
    # Extract hash part (remove sha256: prefix if present)
    hash_part = artifact_id.replace("sha256:", "", 1) if artifact_id.startswith("sha256:") else artifact_id
    
    # Validate hash length (SHA-256 should be 64 hex characters)
    if len(hash_part) != 64:
        logger.warning(f"Invalid artifact_id length: {len(hash_part)} (expected 64)")
        return False, "400"  # Invalid format
    
    try:
        # Strategy: Check ownership via upload sessions
        # When an artifact is uploaded, we create an UploadSession with:
        # - file_hash (the artifact_id/content hash)
        # - user_id (who uploaded it)
        # - model_id (which model it belongs to)
        # - status = COMPLETED or INITIATED (check both for testing)
        
        # Find the upload session that created this artifact
        # Check both COMPLETED and INITIATED (for testing scenarios)
        # Prefer COMPLETED sessions, but also check INITIATED for testing
        from sqlalchemy import case
        
        session = db.query(UploadSession).filter(
            UploadSession.file_hash == artifact_id
        ).order_by(
            # Prefer COMPLETED status, then by creation time (newest first)
            case(
                (UploadSession.status == UploadStatus.COMPLETED, 0),
                else_=1
            ),
            UploadSession.created_at.desc()
        ).first()
        
        if not session:
            # No upload session found, but artifact might exist in storage (e.g., uploaded to different model)
            # For training jobs, if training_model_id is provided, check access to that model
            if training_model_id:
                model_catalog_url = os.getenv("MODEL_CATALOG_URL", "http://model-catalog-service:8000")
                has_model_access = await check_model_access(db, user_id, training_model_id, model_catalog_url)
                if has_model_access:
                    logger.info(
                        f"User {user_id} granted access to artifact {artifact_id} "
                        f"via training model {training_model_id} permissions (no upload session found)"
                    )
                    return True, None
            # No session and no training model access - artifact not found in our system
            logger.info(f"No upload session found for artifact {artifact_id} - artifact not found")
            return False, "404"  # Not found, not forbidden
        
        # Rule 1: Public downloads (no user_id) - allow access
        if not user_id:
            logger.info(f"Public download granted for artifact {artifact_id}")
            return True, None
        
        # Rule 2: Owner can always access their artifacts
        if session.user_id == user_id:
            logger.info(f"User {user_id} is owner of artifact {artifact_id} - access granted")
            return True, None
        
        # Rule 3: Check model-level permissions
        # For training jobs, allow access if user has access to the training job's model
        # Otherwise, check access to the model the artifact was originally uploaded to
        model_to_check = training_model_id if training_model_id else session.model_id
        if model_to_check:
            model_catalog_url = os.getenv("MODEL_CATALOG_URL", "http://model-catalog-service:8000")
            has_model_access = await check_model_access(db, user_id, model_to_check, model_catalog_url)
            if has_model_access:
                if training_model_id and training_model_id != session.model_id:
                    logger.info(
                        f"User {user_id} granted access to artifact {artifact_id} "
                        f"via training model {training_model_id} permissions (artifact originally from model {session.model_id})"
                    )
                else:
                    logger.info(
                        f"User {user_id} granted access to artifact {artifact_id} "
                        f"via model {session.model_id} permissions"
                    )
                return True, None
        
        logger.warning(
            f"User {user_id} denied access to artifact {artifact_id} "
            f"(owner: {session.user_id}, model_id: {session.model_id})"
        )
        return False, "403"  # Forbidden - user doesn't have permission
        
    except Exception as e:
        user_context = f"user {user_id}" if user_id else "public user"
        logger.error(f"Error checking download permission for {user_context}, artifact {artifact_id}: {e}")
        # Fail closed - deny access on error, but return 500 for unexpected errors
        return False, "500"


async def check_upload_permission(
    db: Session,
    user_id: str,
    model_id: int,
    model_catalog_url: str = None
) -> bool:
    """
    Check if user has permission to upload artifacts for a model.
    
    Authorization Rules:
    1. Model owner can upload (checked via Model Catalog Service)
    2. Future: Team members can upload
    3. Future: Public models allow uploads from anyone
    
    Args:
        db: Database session (not used, but kept for consistency)
        user_id: User requesting upload
        model_id: Model ID to upload to
        model_catalog_url: URL of Model Catalog Service (defaults to env var)
        
    Returns:
        True if authorized, False otherwise
        
    Security Model:
    - Fail closed: Returns False on any error
    - Logs all upload permission checks
    """
    if not user_id:
        logger.warning("Upload permission check without user_id")
        return False
    
    if not model_id:
        logger.warning("Upload permission check without model_id")
        return False
    
    # Query Model Catalog Service to check ownership
    # Uses the same pattern as check_model_access
    if not model_catalog_url:
        model_catalog_url = os.getenv("MODEL_CATALOG_URL", "http://model-catalog-service:8000")
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{model_catalog_url}/models/{model_id}/ownership",
                headers={
                    "X-User-Id": user_id,
                    "Content-Type": "application/json"
                }
            )
            
            if response.status_code == 404:
                logger.warning(f"Model {model_id} not found in Model Catalog")
                return False
            
            response.raise_for_status()
            data = response.json()
            
            has_access = data.get("has_access", False)
            is_owner = data.get("is_owner", False)
            
            if has_access:
                logger.info(f"User {user_id} granted upload permission for model {model_id} (owner: {is_owner})")
            else:
                logger.warning(f"User {user_id} denied upload permission for model {model_id}")
            
            return has_access
            
    except httpx.HTTPStatusError as e:
        logger.error(f"Model Catalog returned error for model {model_id}: {e.response.status_code}")
        return False
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to Model Catalog: {e}")
        # Fail closed - if we can't verify, deny access
        return False
    except Exception as e:
        logger.error(f"Error checking upload permission: {e}")
        return False


async def check_model_access(
    db: Session,
    user_id: str,
    model_id: int,
    model_catalog_url: str = None
) -> bool:
    """
    Check if user has access to a model (for downloading artifacts).
    
    Queries Model Catalog Service to verify model ownership/permissions.
    This enables model-level RBAC (not just artifact-level).
    
    Args:
        db: Database session (not used for this check, but kept for consistency)
        user_id: User requesting access
        model_id: Model ID to check
        model_catalog_url: URL of Model Catalog Service (defaults to env var)
        
    Returns:
        True if user has access, False otherwise
        
    Security Model:
    - Fail closed: Returns False on any error
    - Logs all model access checks
    """
    if not model_catalog_url:
        model_catalog_url = os.getenv("MODEL_CATALOG_URL", "http://model-catalog-service:8000")
    
    if not user_id or not model_id:
        logger.warning(f"Invalid parameters for model access check: user_id={user_id}, model_id={model_id}")
        return False
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{model_catalog_url}/models/{model_id}/ownership",
                headers={
                    "X-User-Id": user_id,
                    "Content-Type": "application/json"
                }
            )
            
            if response.status_code == 404:
                logger.warning(f"Model {model_id} not found in Model Catalog")
                return False
            
            response.raise_for_status()
            data = response.json()
            
            has_access = data.get("has_access", False)
            is_owner = data.get("is_owner", False)
            
            if has_access:
                logger.info(f"User {user_id} has access to model {model_id} (owner: {is_owner})")
            else:
                logger.warning(f"User {user_id} denied access to model {model_id}")
            
            return has_access
            
    except httpx.HTTPStatusError as e:
        logger.error(f"Model Catalog returned error for model {model_id}: {e.response.status_code}")
        return False
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to Model Catalog: {e}")
        # Fail closed - if we can't verify, deny access
        return False
    except Exception as e:
        logger.error(f"Error checking model access: {e}")
        return False
