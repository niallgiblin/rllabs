"""
Upload/Download Service - Main Application
============================================

This service manages secure file uploads and downloads for model artifacts using MinIO (S3-compatible storage).
"""

# Observability setup
import os
import sys

shared_path = os.path.join(os.path.dirname(__file__), 'shared')
if os.path.exists(shared_path) and shared_path not in sys.path:
    sys.path.insert(0, shared_path)

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "upload-download-service")

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


from fastapi import FastAPI, Depends, HTTPException, Header, status, Request, BackgroundTasks, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
from typing import Optional
import httpx

from database import get_db, get_read_db, create_db_and_tables
from models import (
    UploadInitRequest, UploadInitResponse,
    UploadCompleteRequest, UploadCompleteResponse,
    DownloadResponse,
    TrainingJobRequest, TrainingJobResponse
)
from storage import StorageService
from session_manager import SessionManager
from event_publisher import get_event_publisher
from authorization import check_download_permission
from database import UploadSession

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_PUBLIC_ENDPOINT = os.getenv("MINIO_PUBLIC_ENDPOINT", "localhost:9000")  
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin_password")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "models")
MINIO_USE_SSL = os.getenv("MINIO_USE_SSL", "false").lower() == "true"
MODEL_CATALOG_URL = os.getenv("MODEL_CATALOG_URL", "http://model-catalog-service:8000")

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management
    - Startup: Create database tables and initialize MinIO bucket
    - Shutdown: Close connections gracefully
    """
    logger.info("Upload/Download Service starting...")
    
    create_db_and_tables()
    
    storage = get_storage_service()
    await storage.initialize()
    
    logger.info("Upload/Download Service ready")
    
    yield
    
    logger.info("Upload/Download Service shutting down...")
    
    global _model_catalog_client
    if _model_catalog_client:
        await _model_catalog_client.aclose()
        _model_catalog_client = None


app = FastAPI(
    title="Upload/Download Service",
    description="Manages secure upload and download of model artifacts",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/health", tags=["Monitoring"], include_in_schema=False)
async def health_check():
    """
    Lightweight health check endpoint for Kubernetes probes
    
    Note: This is intentionally lightweight to avoid expensive operations
    during frequent health checks. For detailed health, use /health/detailed
    Excluded from schema and Prometheus metrics to minimise overhead.
    """
    return {"status": "ok"}

try:
    from prometheus_fastapi_instrumentator import Instrumentator
    instrumentator = Instrumentator()
    instrumentator.instrument(app).expose(app)
    logger.info("Prometheus metrics enabled")
except ImportError:
    logger.warning("prometheus-fastapi-instrumentator not available - metrics disabled")

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
    user_id: str = Header(..., alias="X-User-Id"),
    internal: bool = Query(False, description="Generate URLs with internal endpoint for service-to-service access")
):
    """
    Initiate a multipart upload session
    
    Flow:
    1. Check idempotency (prevent duplicate uploads of same file)
    2. Create upload session in PostgreSQL
    3. Initiate multipart upload with MinIO
    4. Generate presigned URLs for each chunk
    5. Return upload_id and presigned URLs to client
    
    Args:
        request: Upload configuration (filename, size, hash, chunks)
        user_id: User ID from API Gateway JWT (injected via header)
    
    Returns:
        Upload session ID and presigned URLs for each chunk
    """
    logger.info(f"User {user_id} initiating upload for {request.filename} ({request.file_size} bytes)")
    
    storage = get_storage_service()
    session_manager = SessionManager(db, storage)
    
    try:
        existing_session = await session_manager.check_idempotency(
            file_hash=request.file_hash,
            user_id=user_id
        )
        
        if existing_session:
            logger.info(f"Returning existing upload session {existing_session['upload_id']} (idempotent)")
            return UploadInitResponse(**existing_session)
        
        use_internal_endpoint = internal
        upload_session = await session_manager.create_upload_session(
            filename=request.filename,
            file_size=request.file_size,
            file_hash=request.file_hash,
            chunk_size=request.chunk_size,
            artifact_type=request.artifact_type,
            model_id=request.model_id,
            user_id=user_id,
            use_internal_endpoint=use_internal_endpoint
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
    
    Args:
        upload_id: Session ID from initiate_upload
        request: List of uploaded parts with ETags
        user_id: User ID from API Gateway
    
    Returns:
        Artifact ID (content hash) and storage location
    """
    logger.info(f"User {user_id} completing upload {upload_id}")
    
    storage = get_storage_service()
    session_manager = SessionManager(db, storage)
    
    try:
        result = await session_manager.complete_upload(
            upload_id=upload_id,
            parts=request.parts,
            user_id=user_id
        )
        
        logger.info(f"Upload {upload_id} completed successfully, artifact_id: {result['artifact_id']}")
        
        session = db.query(UploadSession).filter(UploadSession.upload_id == upload_id).first()
        artifact_type = session.artifact_type if session else "model" 
        
        if artifact_type == "model" and result.get('model_id'):
            try:
                catalog_result = await register_with_model_catalog(
                    model_id=result['model_id'],
                    storage_path=result['storage_path'],
                    content_hash=result['artifact_id'],
                    user_id=user_id
                )
                result['version'] = catalog_result['version']
                result['storage_path'] = catalog_result['storage_path']
                
                session.storage_path = catalog_result['storage_path']
                db.commit()
                
                logger.info(f"Registered model artifact with Model Catalog: model_id={result['model_id']}, version={result['version']}")
                result['registered_with_catalog'] = True
            except Exception as e:
                error_str = str(e)
                if "409" in error_str or "Conflict" in error_str:
                    logger.warning(f"Model Catalog returned 409 (version already exists) - upload successful: {e}")
                    result['registered_with_catalog'] = False
                    result['catalog_message'] = "Version already exists in catalog"
                else:
                    logger.error(f"Failed to register with Model Catalog: {e}")
                    await session_manager.fail_upload(upload_id, f"Model Catalog registration failed: {str(e)}")
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=f"Upload completed but failed to register with Model Catalog: {str(e)}"
                    )
        else:
            logger.info(f"Skipping Model Catalog registration for {artifact_type} artifact (only model artifacts are registered as versions)")
            result['registered_with_catalog'] = False
            result['catalog_message'] = f"{artifact_type} artifacts are not registered as model versions"
            result['version'] = None  
        
        def publish_event():
            try:
                publisher = get_event_publisher()
                if publisher:
                    publisher.publish_artifact_uploaded(
                        artifact_id=result['artifact_id'],
                        model_id=result['model_id'],
                        version=result.get('version'),  
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
    Abort an in-progress upload (optimised for performance)
    
    Flow:
    1. Mark session as aborted in database (fast, <200ms)
    2. Return success immediately
    3. Clean up partial data in MinIO in background (non-blocking)

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
        temp_object_key, minio_upload_id = await session_manager.abort_upload(upload_id, user_id)
        
        background_tasks.add_task(
            session_manager.cleanup_minio_upload,
            temp_object_key,
            minio_upload_id
        )
        
        logger.info(f"Upload {upload_id} aborted successfully (cleanup scheduled in background)")
        
        return {
            "status": "aborted",
            "upload_id": upload_id,
            "cleanup_scheduled": True  
        }
        
    except Exception as e:
        logger.error(f"Error aborting upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to abort upload: {str(e)}"
        )

@app.head("/downloads/{artifact_id}", tags=["Downloads"])
async def check_download_url(
    artifact_id: str,
    db: Session = Depends(get_read_db),
    user_id: Optional[str] = Header(None, alias="X-User-Id"),
    model_id: Optional[int] = Query(None, description="Model ID for training job context (allows cross-model artifact usage)")
):
    """
    Check if an artifact exists (HEAD request for validation)
    
    Used by training service to validate artifacts before downloading.
    Returns 200 if artifact exists and user has permission, 404/403 otherwise.
    
    For training jobs, model_id can be provided to allow using artifacts from other models
    if the user has access to the training job's model.
    """
    storage = get_storage_service()
    
    try:
        object_key = artifact_id
        file_info = await storage.get_object_info(object_key)
        
        if not file_info:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Artifact {artifact_id} not found")
        
        has_permission, error_code = await check_download_permission(db, user_id, artifact_id, training_model_id=model_id)
        if not has_permission:
            if error_code == "404":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Artifact {artifact_id} not found")
            elif error_code == "403":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request")
        
        return Response(status_code=200)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking artifact {artifact_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


@app.get("/downloads/{artifact_id}", response_model=DownloadResponse, tags=["Downloads"])
async def get_download_url(
    artifact_id: str,
    expires_in: int = 3600,
    internal: bool = Query(False, description="Generate URL with internal endpoint for service-to-service access"),
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
    
    Args:
        artifact_id: Content hash (sha256:...) of the artifact
        expires_in: URL expiration time in seconds (default: 3600 = 1 hour)
        user_id: User ID from API Gateway (optional - downloads are public)
    
    Returns:
        Presigned download URL and file metadata
    """
    if user_id:
        logger.info(f"User {user_id} requesting download for {artifact_id}")
    else:
        logger.info(f"Public download requested for {artifact_id}")
    
    storage = get_storage_service()
    
    try:
        has_permission, error_code = await check_download_permission(db, user_id, artifact_id)
        if not has_permission:
            if error_code == "404":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Artifact {artifact_id} not found"
                )
            elif error_code == "400":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid artifact_id format: {artifact_id}"
                )
            elif error_code == "500":
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Internal error checking permissions"
                )
            else:
                user_context = f"User {user_id}" if user_id else "Anonymous user"
                logger.warning(f"{user_context} denied access to artifact {artifact_id}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to download this artifact"
                )
        
        object_key = artifact_id  
        file_info = await storage.get_object_info(object_key)
        
        if not file_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Artifact {artifact_id} not found"
            )
        
        use_internal_endpoint = internal
        download_url = await storage.generate_presigned_get_url(
            object_key=object_key,
            expires_in=expires_in,
            use_internal_endpoint=use_internal_endpoint
        )
        
        from datetime import datetime, timedelta
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        
        logger.info(f"Generated download URL for {artifact_id}, expires at {expires_at}")
        
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
    1. Check authorisation (owner or admin)
    2. Delete the artifact file from MinIO storage
    3. Optionally mark upload sessions as deleted (soft delete)
    
    Args:
        artifact_id: Content hash (sha256:...) of the artifact
        user_id: User ID from API Gateway
        user_scopes: Space-separated scopes from X-Scope header
    
    Returns:
        204 No Content on success
    """
    logger.info(f"User {user_id} requesting delete for artifact {artifact_id}")
    
    if not artifact_id.startswith("sha256:") or len(artifact_id) != 71:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid artifact_id format: {artifact_id}"
        )
    
    try:
        storage = get_storage_service()
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
        
        is_owner = session.user_id == user_id
        
        is_admin_user = False
        is_admin_header = request.headers.get("X-Is-Admin", "").lower() == "true"
        if is_admin_header:
            is_admin_user = True
        elif user_scopes:
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
        
        object_key = artifact_id
        try:
            await storage.delete_object(object_key)
            logger.info(f"Deleted artifact {artifact_id} from MinIO storage")
        except Exception as e:
            logger.warning(f"Failed to delete artifact {artifact_id} from storage: {e}")
        
        logger.info(
            f"Artifact {artifact_id} deleted by "
            f"{'admin' if is_admin_user else 'owner'} {user_id}"
        )
        
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
        
        return None
        
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
    storage_path: str,
    content_hash: str,
    user_id: str
) -> dict:
    """
    Register a new model version with the Model Catalog Service
    
    Uses global HTTP client with connection pooling for better performance.
    
    Args:
        model_id: ID of the parent model
        storage_path: S3 path where artifact is stored (will be updated with correct version)
        content_hash: SHA-256 hash of the artifact
        user_id: User who uploaded the artifact
    
    Returns:
        dict with 'version' (assigned version number) and 'storage_path' (updated path)
    
    Raises:
        Exception if Model Catalog call fails
    """
    client = get_model_catalog_client()
    try:
        response = await client.post(
            f"{MODEL_CATALOG_URL}/models/{model_id}/versions",
            json={
                "storage_path": storage_path,
                "content_hash": content_hash
            },
            headers={
                "X-User-Id": user_id,
                "Content-Type": "application/json"
            }
        )
        response.raise_for_status()
        version_data = response.json()
        assigned_version = version_data.get("version")
        updated_storage_path = version_data.get("storage_path", storage_path)
        logger.info(f"Successfully registered version {assigned_version} for model {model_id} with Model Catalog")
        
        return {
            "version": assigned_version,
            "storage_path": updated_storage_path
        }
        
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
                        if user_id and job.get("user_id") != user_id:
                            continue
                        if model_id is not None and job.get("model_id") != model_id:
                            continue
                        jobs.append(job)
                except Exception as e:
                    logger.warning(f"Failed to parse job data from key {key}: {e}")
                    continue
            
            if cursor == 0:
                break
        
        jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        
        return jobs
        
    except redis.ConnectionError:
        logger.warning("Redis not available for listing training jobs")
        return []
    except Exception as e:
        logger.error(f"Error listing training jobs: {e}", exc_info=True)
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
        artifact_ids = [
            request.config_artifact_id,
            request.dataset_artifact_id,
            request.model_artifact_id
        ]
        
        for artifact_id in artifact_ids:
            if not artifact_id.startswith("sha256:") or len(artifact_id) != 71:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid artifact_id format: {artifact_id}"
                )
        
        model_id = request.model_id
        
        if model_id is None:
            from sqlalchemy import case
            from database import UploadStatus
            
            model_session = db.query(UploadSession).filter(
                UploadSession.file_hash == request.model_artifact_id,
                UploadSession.user_id == user_id  
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
        
        message = {
            "job_id": job_id,
            "config_artifact_id": request.config_artifact_id,
            "dataset_artifact_id": request.dataset_artifact_id,
            "model_artifact_id": request.model_artifact_id,
            "user_id": user_id,
            "model_id": model_id 
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
            
            redis_client.setex(
                f"job:{job_id}",
                86400,  # 24 hours
                json.dumps(job_status)
            )
            logger.info(f"Created job status entry in Redis for {job_id}")
        except Exception as e:
            logger.warning(f"Failed to create job status in Redis: {e}")
        
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