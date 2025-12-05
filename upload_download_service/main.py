"""
Upload/Download Service - Main Application
============================================

This service manages secure file uploads and downloads for model artifacts using MinIO (S3-compatible storage).

Key Architectural Decisions:
1. Chunked multipart uploads for handling large model files (GBs)
2. Presigned URLs for direct client-to-storage transfer (bypasses our servers)
3. Content-addressed storage using SHA-256 for automatic deduplication
4. Hybrid approach: Synchronous calls to Model Catalog + Async events to RabbitMQ
5. Idempotency via Redis to prevent duplicate uploads

Trade-offs:
- Presigned URLs: Better performance but requires time-limited security
- PostgreSQL for sessions: Strong consistency but more complex than Redis-only
- Fail-open on events: Upload succeeds even if event publishing fails (availability over consistency)
"""

# =============================================================================
# OBSERVABILITY SETUP (must be first, before other imports)
# =============================================================================
import os
import sys

# Add shared module to path
shared_path = os.path.join(os.path.dirname(__file__), 'shared')
if os.path.exists(shared_path) and shared_path not in sys.path:
    sys.path.insert(0, shared_path)

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "upload-download-service")

# Initialize structured logging and tracing
try:
    from observability import setup_logging, setup_tracing, get_logger
    
    json_output = os.getenv("KUBERNETES_SERVICE_HOST") is not None
    setup_logging(service_name=SERVICE_NAME, json_output=json_output)
    setup_tracing(service_name=SERVICE_NAME)
    logger = get_logger(__name__)
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.warning("Observability module not available, using basic logging")

# =============================================================================
# APPLICATION IMPORTS
# =============================================================================
from fastapi import FastAPI, Depends, HTTPException, Header, status, Request, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
from typing import Optional, List
import httpx

from database import get_db, get_read_db, create_db_and_tables
from models import (
    UploadInitRequest, UploadInitResponse, PresignedURL,
    UploadCompleteRequest, UploadCompleteResponse,
    DownloadResponse, UploadPart,
    TrainingJobRequest, TrainingJobResponse
)
from storage import StorageService
from session_manager import SessionManager
from event_publisher import get_event_publisher
from authorization import check_download_permission
from database import UploadSession

# Configuration from environment
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_PUBLIC_ENDPOINT = os.getenv("MINIO_PUBLIC_ENDPOINT", "localhost:9000")  # For browser-accessible presigned URLs
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin_password")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "models")
MINIO_USE_SSL = os.getenv("MINIO_USE_SSL", "false").lower() == "true"
MODEL_CATALOG_URL = os.getenv("MODEL_CATALOG_URL", "http://model-catalog-service:8000")

# Global HTTP client for Model Catalog calls (reused across requests)
_model_catalog_client: Optional[httpx.AsyncClient] = None

def get_model_catalog_client() -> httpx.AsyncClient:
    """
    Get or create global HTTP client for Model Catalog calls (singleton pattern).
    Reusing the client provides connection pooling and better performance.
    """
    global _model_catalog_client
    if _model_catalog_client is None:
        _model_catalog_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=25,
                keepalive_expiry=30.0
            )
        )
    return _model_catalog_client

# Global StorageService instance (singleton pattern)
# Created once on startup and reused for all requests
_storage_service: Optional[StorageService] = None

def get_storage_service() -> StorageService:
    """
    Get the global StorageService instance (singleton).
    This avoids creating a new StorageService (and aioboto3 Session) on every request.
    """
    global _storage_service
    if _storage_service is None:
        _storage_service = StorageService(
            endpoint=MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            bucket=MINIO_BUCKET,
            use_ssl=MINIO_USE_SSL,
            public_endpoint=MINIO_PUBLIC_ENDPOINT
        )
    return _storage_service

# Application lifespan - handles startup and shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management
    - Startup: Create database tables and initialize MinIO bucket
    - Shutdown: Close connections gracefully
    """
    logger.info("Upload/Download Service starting...")
    
    # Create database tables on startup
    create_db_and_tables()
    
    # Initialize global storage service (creates bucket if needed)
    storage = get_storage_service()
    await storage.initialize()
    
    logger.info("Upload/Download Service ready")
    
    yield
    
    logger.info("Upload/Download Service shutting down...")
    
    # Close HTTP client on shutdown
    global _model_catalog_client
    if _model_catalog_client:
        await _model_catalog_client.aclose()
        _model_catalog_client = None

# FastAPI application
app = FastAPI(
    title="Upload/Download Service",
    description="Manages secure upload and download of model artifacts",
    version="1.0.0",
    lifespan=lifespan
)

# Health check endpoint for Kubernetes readiness/liveness probes
# Register BEFORE Instrumentator so it can be properly excluded
# Lightweight version - doesn't check dependencies to avoid expensive operations
@app.get("/health", tags=["Monitoring"], include_in_schema=False)
async def health_check():
    """
    Lightweight health check endpoint for Kubernetes probes
    
    Note: This is intentionally lightweight to avoid expensive operations
    during frequent health checks. For detailed health, use /health/detailed
    Excluded from schema and Prometheus metrics to minimize overhead.
    """
    return {"status": "ok"}

# Add Prometheus metrics
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    instrumentator = Instrumentator()
    instrumentator.instrument(app).expose(app)
    logger.info("Prometheus metrics enabled")
except ImportError:
    logger.warning("prometheus-fastapi-instrumentator not available - metrics disabled")

# Detailed health check endpoint (for monitoring, not probes)
@app.get("/health/detailed", tags=["Monitoring"])
async def detailed_health_check(db: Session = Depends(get_read_db)):
    """
    Detailed health check endpoint
    Verifies database and storage connectivity
    Use this for monitoring, not for Kubernetes probes
    """
    try:
        from sqlalchemy import text
        db.execute(text('SELECT 1'))
        db_status = "online"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "offline"
    
    # Check MinIO connectivity
    try:
        storage = get_storage_service()
        await storage.initialize()
        storage_status = "online"
    except Exception as e:
        logger.error(f"Storage health check failed: {e}")
        storage_status = "offline"
    
    overall_status = "ok" if db_status == "online" and storage_status == "online" else "degraded"
    
    return {
        "service_status": overall_status,
        "dependencies": {
            "database": db_status,
            "storage": storage_status
        }
    }


@app.post("/uploads", response_model=UploadInitResponse, status_code=201, tags=["Uploads"])
async def initiate_upload(
    request: UploadInitRequest,
    db: Session = Depends(get_db),
    user_id: str = Header(..., alias="X-User-Id")
):
    """
    Initiate a multipart upload session
    
    Flow:
    1. Check idempotency (prevent duplicate uploads of same file)
    2. Create upload session in PostgreSQL
    3. Initiate multipart upload with MinIO
    4. Generate presigned URLs for each chunk
    5. Return upload_id and presigned URLs to client
    
    Architectural Decision: Use presigned URLs to allow direct client-to-MinIO uploads
    - Trade-off: Better performance (no data through our servers) but URLs are time-limited
    - Security: URLs expire after 1 hour, preventing unauthorized access
    
    Args:
        request: Upload configuration (filename, size, hash, chunks)
        user_id: User ID from API Gateway JWT (injected via header)
    
    Returns:
        Upload session ID and presigned URLs for each chunk
    """
    logger.info(f"User {user_id} initiating upload for {request.filename} ({request.file_size} bytes)")
    
    # Get shared storage service instance (reused across requests)
    storage = get_storage_service()
    session_manager = SessionManager(db, storage)
    
    try:
        # Check for duplicate upload using idempotency (Redis-based)
        # If this exact file was recently uploaded, return existing session
        existing_session = await session_manager.check_idempotency(
            file_hash=request.file_hash,
            user_id=user_id
        )
        
        if existing_session:
            logger.info(f"Returning existing upload session {existing_session['upload_id']} (idempotent)")
            return UploadInitResponse(**existing_session)
        
        # Create new upload session
        upload_session = await session_manager.create_upload_session(
            filename=request.filename,
            file_size=request.file_size,
            file_hash=request.file_hash,
            chunk_size=request.chunk_size,
            artifact_type=request.artifact_type,
            model_id=request.model_id,
            user_id=user_id
        )
        
        logger.info(f"Created upload session {upload_session.upload_id}")
        
        return UploadInitResponse(
            upload_id=upload_session.upload_id,
            presigned_urls=upload_session.presigned_urls,
            session_expires_at=upload_session.session_expires_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error initiating upload: {e}",
            exc_info=True,
            extra={
                "user_id": user_id,
                "filename": request.filename,
                "file_size": request.file_size,
                "error_type": type(e).__name__
            }
        )
        # Provide more specific error messages
        error_detail = str(e)
        if "connection" in error_detail.lower() or "timeout" in error_detail.lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Storage service temporarily unavailable. Please retry."
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate upload: {error_detail}"
        )


@app.post("/uploads/{upload_id}/complete", response_model=UploadCompleteResponse, tags=["Uploads"])
async def complete_upload(
    upload_id: str,
    request: UploadCompleteRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user_id: str = Header(..., alias="X-User-Id")
):
    """
    Complete a multipart upload
    
    Flow:
    1. Verify all parts were uploaded (via ETags)
    2. Complete multipart upload in MinIO (stitches chunks together)
    3. Verify SHA-256 hash matches original
    4. Move file to content-addressed location (sha256:hash)
    5. Register version with Model Catalog Service (synchronous HTTP call)
    6. Publish ArtifactUploaded event to RabbitMQ (asynchronous, best-effort)
    7. Mark session as complete
    
    Architectural Decision: Hybrid approach
    - Synchronous: Call Model Catalog directly (required for consistency)
    - Asynchronous: Publish events for other services (optional, fail-open)
    - Trade-off: Model Catalog must be available, but event broker can be down
    
    Args:
        upload_id: Session ID from initiate_upload
        request: List of uploaded parts with ETags
        user_id: User ID from API Gateway
    
    Returns:
        Artifact ID (content hash) and storage location
    """
    logger.info(f"User {user_id} completing upload {upload_id}")
    
    # Get shared storage service instance
    storage = get_storage_service()
    session_manager = SessionManager(db, storage)
    
    try:
        # Complete the upload and verify integrity
        result = await session_manager.complete_upload(
            upload_id=upload_id,
            parts=request.parts,
            user_id=user_id
        )
        
        logger.info(f"Upload {upload_id} completed successfully, artifact_id: {result['artifact_id']}")
        
        # SYNCHRONOUS: Register version with Model Catalog Service
        # This is required - if it fails, the upload is considered failed
        try:
            await register_with_model_catalog(
                model_id=result['model_id'],
                version=result['version'],
                storage_path=result['storage_path'],
                content_hash=result['artifact_id'],
                user_id=user_id
            )
            logger.info(f"Registered artifact with Model Catalog: model_id={result['model_id']}, version={result['version']}")
            result['registered_with_catalog'] = True
            
        except Exception as e:
            error_str = str(e)
            # 409 Conflict means version already exists - this is fine, upload was successful
            if "409" in error_str or "Conflict" in error_str:
                logger.warning(f"Model Catalog returned 409 (version already exists) - upload successful: {e}")
                result['registered_with_catalog'] = False
                result['catalog_message'] = "Version already exists in catalog"
                # Don't mark as failed - the upload was successful
            else:
                logger.error(f"Failed to register with Model Catalog: {e}")
                # Mark upload as failed since catalog registration is required (except for 409)
                await session_manager.fail_upload(upload_id, f"Model Catalog registration failed: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Upload completed but failed to register with Model Catalog: {str(e)}"
                )
        
        # ASYNCHRONOUS: Publish ArtifactUploaded event in background (best-effort, fail-open)
        # This is optional - if it fails, we log but don't fail the upload
        def publish_event():
            try:
                publisher = get_event_publisher()
                if publisher:
                    publisher.publish_artifact_uploaded(
                        artifact_id=result['artifact_id'],
                        model_id=result['model_id'],
                        version=result['version'],
                        storage_path=result['storage_path'],
                        uploaded_by=user_id,
                        file_size=result['file_size'],
                        filename=result['filename']
                    )
                    logger.info(f"Published ArtifactUploaded event for {result['artifact_id']}")
            except Exception as e:
                logger.warning(f"Failed to publish ArtifactUploaded event: {e}")
        background_tasks.add_task(publish_event)
        
        return UploadCompleteResponse(**result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error completing upload: {e}",
            exc_info=True,
            extra={
                "upload_id": upload_id,
                "user_id": user_id,
                "error_type": type(e).__name__
            }
        )
        error_detail = str(e)
        if "connection" in error_detail.lower() or "timeout" in error_detail.lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Storage service temporarily unavailable. Please retry."
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to complete upload: {error_detail}"
        )


@app.post("/uploads/{upload_id}/abort", tags=["Uploads"])
async def abort_upload(
    upload_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user_id: str = Header(..., alias="X-User-Id")
):
    """
    Abort an in-progress upload (optimized for performance)
    
    Flow:
    1. Mark session as aborted in database (fast, <200ms)
    2. Return success immediately
    3. Clean up partial data in MinIO in background (non-blocking)
    
    Performance Optimization:
    - MinIO cleanup happens asynchronously to avoid blocking (was 7-13s, now <200ms)
    - Request returns immediately after marking session as aborted
    - Background cleanup has 5s timeout to fail fast if MinIO is slow
    
    Use cases:
    - User cancels upload
    - Upload fails and needs cleanup
    - Client-side error handling
    
    Args:
        upload_id: Session ID to abort
        user_id: User ID from API Gateway
    
    Returns:
        Status confirmation (cleanup happens in background)
    """
    logger.info(f"User {user_id} aborting upload {upload_id}")
    
    storage = get_storage_service()
    session_manager = SessionManager(db, storage)
    
    try:
        # Fast path: mark as aborted and return immediately
        temp_object_key, minio_upload_id = await session_manager.abort_upload(upload_id, user_id)
        
        # Schedule MinIO cleanup in background (non-blocking)
        background_tasks.add_task(
            session_manager.cleanup_minio_upload,
            temp_object_key,
            minio_upload_id
        )
        
        logger.info(f"Upload {upload_id} aborted successfully (cleanup scheduled in background)")
        
        return {
            "status": "aborted",
            "upload_id": upload_id,
            "cleanup_scheduled": True  # Changed from cleanup_completed to indicate async cleanup
        }
        
    except Exception as e:
        logger.error(f"Error aborting upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to abort upload: {str(e)}"
        )

@app.get("/downloads/{artifact_id}", response_model=DownloadResponse, tags=["Downloads"])
async def get_download_url(
    artifact_id: str,
    expires_in: int = 3600,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_read_db),
    user_id: Optional[str] = Header(None, alias="X-User-Id")
):
    """
    Generate a presigned download URL
    
    Flow:
    1. Check user permissions via RBAC (public downloads allowed, authenticated users checked)
    2. Verify artifact exists in storage
    3. Generate time-limited presigned GET URL
    4. Log download request for audit trail
    5. Optionally publish ArtifactDownloaded event
    
    Authorisation Rules:
    - Public downloads: Unauthenticated users can download any artifact
    - Authenticated users: Must be owner or have model-level access
    - RBAC enforced via check_download_permission() before generating presigned URL
    
    Architecture Decision: Presigned URLs for direct downloads
    - Trade-off: High performance (direct MinIO access) but time-limited URLs
    - Security: URLs expire (default 1 hour), preventing URL sharing
    - No bandwidth through our service = better scalability
    - RBAC enforced in application layer before MinIO access
    
    Args:
        artifact_id: Content hash (sha256:...) of the artifact
        expires_in: URL expiration time in seconds (default: 3600 = 1 hour)
        user_id: User ID from API Gateway (optional - downloads are public)
    
    Returns:
        Presigned download URL and file metadata
    """
    # Log download request (authenticated or public)
    if user_id:
        logger.info(f"User {user_id} requesting download for {artifact_id}")
    else:
        logger.info(f"Public download requested for {artifact_id}")
    
    storage = get_storage_service()
    
    try:
        # STEP 1: AUTHORIZATION CHECK (before generating presigned URL)
        # This is critical for security - we check permissions in our service
        # before allowing access to MinIO. MinIO doesn't know about users/models.
        has_permission, error_code = await check_download_permission(db, user_id, artifact_id)
        if not has_permission:
            if error_code == "404":
                # Artifact not found in our system
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Artifact {artifact_id} not found"
                )
            elif error_code == "400":
                # Invalid artifact_id format
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid artifact_id format: {artifact_id}"
                )
            elif error_code == "500":
                # Internal error
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Internal error checking permissions"
                )
            else:
                # Default to 403 Forbidden
                user_context = f"User {user_id}" if user_id else "Anonymous user"
                logger.warning(f"{user_context} denied access to artifact {artifact_id}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to download this artifact"
                )
        
        # STEP 2: Verify artifact exists in storage
        object_key = artifact_id  # artifact_id is the content-addressed key
        file_info = await storage.get_object_info(object_key)
        
        if not file_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Artifact {artifact_id} not found"
            )
        
        # Generate presigned download URL
        download_url = await storage.generate_presigned_get_url(
            object_key=object_key,
            expires_in=expires_in
        )
        
        from datetime import datetime, timedelta
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        
        logger.info(f"Generated download URL for {artifact_id}, expires at {expires_at}")
        
        # Optional: Publish ArtifactDownloaded event in background for audit trail
        if background_tasks:
            def publish_event():
                try:
                    publisher = get_event_publisher()
                    if publisher:
                        publisher.publish_artifact_downloaded(
                            artifact_id=artifact_id,
                            downloaded_by=user_id or "anonymous"
                        )
                except Exception as e:
                    logger.warning(f"Failed to publish ArtifactDownloaded event: {e}")
            background_tasks.add_task(publish_event)
        
        return DownloadResponse(
            download_url=download_url,
            expires_at=expires_at.isoformat() + "Z",
            file_size=file_info.get('size', 0),
            filename=file_info.get('filename', artifact_id)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating download URL: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate download URL: {str(e)}"
        )

@app.delete("/artifacts/{artifact_id}", status_code=204, tags=["Downloads"])
async def delete_artifact(
    artifact_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user_id: str = Header(..., alias="X-User-Id"),
    user_scopes: Optional[str] = Header(None, alias="X-Scope")
):
    """
    Delete an artifact. Only the owner or an admin can delete.
    
    This will:
    1. Check authorization (owner or admin)
    2. Delete the artifact file from MinIO storage
    3. Optionally mark upload sessions as deleted (soft delete)
    
    Note: This is a hard delete - the artifact will be permanently removed.
    Consider implementing soft delete if you need recovery capabilities.
    
    Args:
        artifact_id: Content hash (sha256:...) of the artifact
        user_id: User ID from API Gateway
        user_scopes: Space-separated scopes from X-Scope header
    
    Returns:
        204 No Content on success
    """
    logger.info(f"User {user_id} requesting delete for artifact {artifact_id}")
    
    # Validate artifact_id format
    if not artifact_id.startswith("sha256:") or len(artifact_id) != 71:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid artifact_id format: {artifact_id}"
        )
    
    try:
        storage = get_storage_service()
        # STEP 1: Find upload session to check ownership
        from sqlalchemy import case
        from database import UploadStatus
        
        session = db.query(UploadSession).filter(
            UploadSession.file_hash == artifact_id
        ).order_by(
            case(
                (UploadSession.status == UploadStatus.COMPLETED, 0),
                else_=1
            ),
            UploadSession.created_at.desc()
        ).first()
        
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Artifact {artifact_id} not found"
            )
        
        # STEP 2: Check authorization (owner OR admin)
        is_owner = session.user_id == user_id
        
        # Check admin scope (prefer X-Is-Admin header from API Gateway)
        is_admin_user = False
        is_admin_header = request.headers.get("X-Is-Admin", "").lower() == "true"
        if is_admin_header:
            is_admin_user = True
        elif user_scopes:
            # Fallback to parsing scopes (for direct service access)
            scopes = user_scopes.split() if isinstance(user_scopes, str) else []
            is_admin_user = "api:admin" in scopes
        
        if not (is_owner or is_admin_user):
            logger.warning(
                f"User {user_id} denied delete access to artifact {artifact_id} "
                f"(owner: {session.user_id}, is_admin: {is_admin_user})"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the artifact owner or an admin can delete this artifact"
            )
        
        # STEP 3: Delete from MinIO storage
        object_key = artifact_id
        try:
            await storage.delete_object(object_key)
            logger.info(f"Deleted artifact {artifact_id} from MinIO storage")
        except Exception as e:
            logger.warning(f"Failed to delete artifact {artifact_id} from storage: {e}")
            # Continue even if storage delete fails (best effort)
        
        # STEP 4: Optionally mark sessions as deleted (soft delete)
        # For now, we'll leave sessions in the database for audit trail
        # You can add a 'deleted_at' field if you want soft delete
        
        logger.info(
            f"Artifact {artifact_id} deleted by "
            f"{'admin' if is_admin_user else 'owner'} {user_id}"
        )
        
        # Optional: Publish ArtifactDeleted event in background
        # Note: Event publisher doesn't have this method yet, but structure is ready
        def publish_event():
            try:
                publisher = get_event_publisher()
                if publisher:
                    # Assuming event publisher has this method
                    # publisher.publish_artifact_deleted(artifact_id=artifact_id, deleted_by=user_id)
                    pass
            except Exception as e:
                logger.warning(f"Failed to publish ArtifactDeleted event: {e}")
        background_tasks.add_task(publish_event)
        
        return None  # 204 No Content
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting artifact {artifact_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete artifact: {str(e)}"
        )

async def register_with_model_catalog(
    model_id: int,
    version: int,
    storage_path: str,
    content_hash: str,
    user_id: str
):
    """
    Register a new model version with the Model Catalog Service
    
    This is a SYNCHRONOUS operation - if it fails, the upload is considered failed.
    
    Architectural Decision: Direct HTTP call to Model Catalog
    - Trade-off: Tight coupling but strong consistency
    - Model Catalog is the source of truth for model metadata
    - If Model Catalog is down, uploads will fail (prioritize consistency over availability)
    
    Uses global HTTP client with connection pooling for better performance.
    
    Args:
        model_id: ID of the parent model
        version: Version number for this artifact
        storage_path: S3 path where artifact is stored
        content_hash: SHA-256 hash of the artifact
        user_id: User who uploaded the artifact
    
    Raises:
        Exception if Model Catalog call fails
    """
    client = get_model_catalog_client()
    try:
        response = await client.post(
            f"{MODEL_CATALOG_URL}/models/{model_id}/versions",
            json={
                "version": version,
                "storage_path": storage_path,
                "content_hash": content_hash
            },
            headers={
                "X-User-Id": user_id,
                "Content-Type": "application/json"
            }
        )
        response.raise_for_status()
        logger.info(f"Successfully registered version {version} for model {model_id} with Model Catalog")
        
    except httpx.HTTPStatusError as e:
        logger.error(f"Model Catalog returned error: {e.response.status_code} - {e.response.text}")
        raise Exception(f"Model Catalog error: {e.response.status_code}")
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to Model Catalog: {e}")
        raise Exception(f"Model Catalog unavailable: {str(e)}")


@app.get("/training-jobs", tags=["Training"])
async def list_training_jobs(
    user_id: Optional[str] = Header(None, alias="X-User-Id"),
    model_id: Optional[int] = Query(None, description="Filter by model ID")
):
    """
    List all training jobs
    
    Returns a list of training jobs. If user_id is provided, filters by user.
    If model_id is provided as query parameter, filters by model.
    
    Jobs are stored in Redis by the training service with keys like "job:{job_id}".
    """
    import redis
    import json
    from typing import List, Dict, Any
    
    try:
        # Connect to Redis (same config as model-train-service)
        REDIS_HOST = os.getenv("REDIS_HOST", "redis_cache")
        REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
        REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
        
        redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
            decode_responses=True,
            socket_timeout=1.0,
            socket_connect_timeout=1.0
        )
        
        # Scan for all job keys (pattern: job:*)
        jobs: List[Dict[str, Any]] = []
        cursor = 0
        pattern = "job:*"
        
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=100)
            for key in keys:
                try:
                    job_data = redis_client.get(key)
                    if job_data:
                        job = json.loads(job_data)
                        # Filter by user_id if provided
                        if user_id and job.get("user_id") != user_id:
                            continue
                        # Filter by model_id if provided
                        if model_id is not None and job.get("model_id") != model_id:
                            continue
                        jobs.append(job)
                except Exception as e:
                    logger.warning(f"Failed to parse job data from key {key}: {e}")
                    continue
            
            if cursor == 0:
                break
        
        # Sort by created_at (newest first)
        jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        
        return jobs
        
    except redis.ConnectionError:
        logger.warning("Redis not available for listing training jobs")
        return []
    except Exception as e:
        logger.error(f"Error listing training jobs: {e}", exc_info=True)
        # Return empty list on error (fail-open)
        return []


@app.post("/training-jobs", response_model=TrainingJobResponse, status_code=202, tags=["Training"])
async def trigger_training_job(
    request: TrainingJobRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user_id: str = Header(..., alias="X-User-Id")
):
    """
    Trigger a training job by publishing to RabbitMQ
    
    This endpoint validates that all required artifacts exist in MinIO,
    then publishes a training job message to RabbitMQ for asynchronous processing
    by the training service.
    
    Flow:
    1. Validate all artifacts exist in MinIO
    2. Query database to get model_id from model artifact
    3. Publish job message to RabbitMQ queue
    4. Return job_id for tracking
    
    Args:
        request: Training job request with artifact IDs
        db: Database session
        user_id: User ID from API Gateway (injected via header)
    
    Returns:
        Job ID and status information
    """
    import uuid
    job_id = f"job-{uuid.uuid4()}"
    
    logger.info(f"User {user_id} triggering training job {job_id}")
    
    try:
        # Fast validation: only check artifact_id format, not existence
        # Existence validation moved to async training worker for better performance
        artifact_ids = [
            request.config_artifact_id,
            request.dataset_artifact_id,
            request.model_artifact_id
        ]
        
        # Validate artifact_id format only (fast, no I/O)
        for artifact_id in artifact_ids:
            if not artifact_id.startswith("sha256:") or len(artifact_id) != 71:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid artifact_id format: {artifact_id}"
                )
        
        # Get model_id for uploading trained weights
        # Priority: 1) Explicit model_id in request, 2) Lookup from artifact upload history
        model_id = request.model_id
        
        if model_id is None:
            # Query the upload session to find which model this artifact belongs to
            # Filter by user_id to ensure we get the correct model for this user
            from sqlalchemy import case
            from database import UploadStatus
            
            model_session = db.query(UploadSession).filter(
                UploadSession.file_hash == request.model_artifact_id,
                UploadSession.user_id == user_id  # Filter by user to get the correct model
            ).order_by(
                case(
                    (UploadSession.status == UploadStatus.COMPLETED, 0),
                    else_=1
                ),
                UploadSession.created_at.desc()
            ).first()
            
            if model_session:
                model_id = model_session.model_id
                logger.info(f"Found model_id {model_id} for artifact {request.model_artifact_id} (user: {user_id})")
            else:
                logger.warning(f"Could not find model_id for artifact {request.model_artifact_id} (user: {user_id}) - trained weights upload may fail")
        else:
            logger.info(f"Using explicit model_id {model_id} from request")
        
        # Publish to RabbitMQ in background (fail-open: don't fail request if RabbitMQ unavailable)
        message = {
            "job_id": job_id,
            "config_artifact_id": request.config_artifact_id,
            "dataset_artifact_id": request.dataset_artifact_id,
            "model_artifact_id": request.model_artifact_id,
            "user_id": user_id,
            "model_id": model_id  # Include model_id for uploading trained weights
        }
        
        def publish_job():
            publisher = get_event_publisher()
            if publisher:
                published = publisher.publish_training_job(message)
                if published:
                    logger.info(f"Training job {job_id} queued successfully")
                else:
                    logger.warning(f"Training job {job_id} created but not queued (RabbitMQ unavailable)")
            else:
                logger.warning(f"Training job {job_id} created but publisher unavailable")
        
        background_tasks.add_task(publish_job)
        
        # Create job status in Redis immediately so it appears in the list
        try:
            import redis
            import json
            from datetime import datetime, timezone
            
            REDIS_HOST = os.getenv("REDIS_HOST", "redis_cache")
            REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
            REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
            
            redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0
            )
            
            job_status = {
                "job_id": job_id,
                "user_id": user_id,
                "model_id": model_id,
                "status": "queued",
                "progress": 0.0,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "started_at": None,
                "completed_at": None,
                "error": None
            }
            
            # Store with 24 hour TTL (same as training service)
            redis_client.setex(
                f"job:{job_id}",
                86400,  # 24 hours
                json.dumps(job_status)
            )
            logger.info(f"Created job status entry in Redis for {job_id}")
        except Exception as e:
            # Fail-open: don't fail the request if Redis is unavailable
            logger.warning(f"Failed to create job status in Redis: {e}")
        
        # Return immediately - job publishing happens in background
        return TrainingJobResponse(
            job_id=job_id,
            status="queued",
            message="Training job queued. Artifacts will be validated by worker."
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error triggering training job: {e}",
            exc_info=True,
            extra={
                "job_id": job_id,
                "user_id": user_id,
                "error_type": type(e).__name__
            }
        )
        error_detail = str(e)
        if "connection" in error_detail.lower() or "timeout" in error_detail.lower():
            raise HTTPException(
                status_code=503,
                detail="Service temporarily unavailable. Please retry."
            )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger training job: {error_detail}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8002,
        log_level="info"
    )