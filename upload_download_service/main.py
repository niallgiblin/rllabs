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

from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
from typing import Optional, List
import logging
import httpx

from database import get_db, create_db_and_tables
from models import (
    UploadInitRequest, UploadInitResponse, PresignedURL,
    UploadCompleteRequest, UploadCompleteResponse,
    DownloadResponse, UploadPart
)
from storage import StorageService
from session_manager import SessionManager
from event_publisher import get_event_publisher

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment
import os
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin_password")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "models")
MINIO_USE_SSL = os.getenv("MINIO_USE_SSL", "false").lower() == "true"
MODEL_CATALOG_URL = os.getenv("MODEL_CATALOG_URL", "http://model_catalog_service:8000")

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
    
    # Initialize storage service (creates bucket if needed)
    storage = StorageService(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        bucket=MINIO_BUCKET,
        use_ssl=MINIO_USE_SSL
    )
    await storage.initialize()
    
    logger.info("Upload/Download Service ready")
    
    yield
    
    logger.info("Upload/Download Service shutting down...")

# FastAPI application
app = FastAPI(
    title="Upload/Download Service",
    description="Manages secure upload and download of model artifacts",
    version="1.0.0",
    lifespan=lifespan
)

# Health check endpoint for Kubernetes readiness/liveness probes
@app.get("/health", tags=["Monitoring"])
async def health_check(db: Session = Depends(get_db)):
    """
    Health check endpoint
    Verifies database and storage connectivity
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
        storage = StorageService(
            endpoint=MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            bucket=MINIO_BUCKET,
            use_ssl=MINIO_USE_SSL
        )
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
    
    # Initialize services
    storage = StorageService(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        bucket=MINIO_BUCKET,
        use_ssl=MINIO_USE_SSL
    )
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
        
    except Exception as e:
        logger.error(f"Error initiating upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate upload: {str(e)}"
        )


@app.post("/uploads/{upload_id}/complete", response_model=UploadCompleteResponse, tags=["Uploads"])
async def complete_upload(
    upload_id: str,
    request: UploadCompleteRequest,
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
    
    # Initialize services
    storage = StorageService(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        bucket=MINIO_BUCKET,
        use_ssl=MINIO_USE_SSL
    )
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
            logger.error(f"Failed to register with Model Catalog: {e}")
            # Mark upload as failed since catalog registration is required
            await session_manager.fail_upload(upload_id, f"Model Catalog registration failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Upload completed but failed to register with Model Catalog: {str(e)}"
            )
        
        # ASYNCHRONOUS: Publish ArtifactUploaded event (best-effort, fail-open)
        # This is optional - if it fails, we log but don't fail the upload
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
            # Don't fail the upload if event publishing fails
        
        return UploadCompleteResponse(**result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to complete upload: {str(e)}"
        )


@app.post("/uploads/{upload_id}/abort", tags=["Uploads"])
async def abort_upload(
    upload_id: str,
    db: Session = Depends(get_db),
    user_id: str = Header(..., alias="X-User-Id")
):
    """
    Abort an in-progress upload
    
    Flow:
    1. Mark session as aborted in database
    2. Clean up partial data in MinIO
    3. Remove idempotency keys
    
    Use cases:
    - User cancels upload
    - Upload fails and needs cleanup
    - Client-side error handling
    
    Args:
        upload_id: Session ID to abort
        user_id: User ID from API Gateway
    
    Returns:
        Status confirmation
    """
    logger.info(f"User {user_id} aborting upload {upload_id}")
    
    storage = StorageService(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        bucket=MINIO_BUCKET,
        use_ssl=MINIO_USE_SSL
    )
    session_manager = SessionManager(db, storage)
    
    try:
        await session_manager.abort_upload(upload_id, user_id)
        logger.info(f"Upload {upload_id} aborted successfully")
        
        return {
            "status": "aborted",
            "upload_id": upload_id,
            "cleanup_completed": True
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
    db: Session = Depends(get_db),
    user_id: str = Header(..., alias="X-User-Id")
):
    """
    Generate a presigned download URL
    
    Flow:
    1. Verify artifact exists in storage
    2. Check user permissions (placeholder - extend with RBAC if needed)
    3. Generate time-limited presigned GET URL
    4. Log download request for audit trail
    5. Optionally publish ArtifactDownloaded event
    
    Architectural Decision: Presigned URLs for direct downloads
    - Trade-off: High performance (direct MinIO access) but time-limited URLs
    - Security: URLs expire (default 1 hour), preventing URL sharing
    - No bandwidth through our service = better scalability
    
    Args:
        artifact_id: Content hash (sha256:...) of the artifact
        expires_in: URL expiration time in seconds (default: 3600 = 1 hour)
        user_id: User ID from API Gateway
    
    Returns:
        Presigned download URL and file metadata
    """
    logger.info(f"User {user_id} requesting download for {artifact_id}")
    
    storage = StorageService(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        bucket=MINIO_BUCKET,
        use_ssl=MINIO_USE_SSL
    )
    
    try:
        # Verify artifact exists and get metadata
        object_key = artifact_id  # artifact_id is the content-addressed key
        file_info = await storage.get_object_info(object_key)
        
        if not file_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Artifact {artifact_id} not found"
            )
        
        # TODO: Add RBAC permission check here
        # For now, any authenticated user can download
        # Future: Check if user has access to the model this artifact belongs to
        
        # Generate presigned download URL
        download_url = await storage.generate_presigned_get_url(
            object_key=object_key,
            expires_in=expires_in
        )
        
        from datetime import datetime, timedelta
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        
        logger.info(f"Generated download URL for {artifact_id}, expires at {expires_at}")
        
        # Optional: Publish ArtifactDownloaded event for audit trail
        try:
            publisher = get_event_publisher()
            if publisher:
                publisher.publish_artifact_downloaded(
                    artifact_id=artifact_id,
                    downloaded_by=user_id
                )
        except Exception as e:
            logger.warning(f"Failed to publish ArtifactDownloaded event: {e}")
            # Don't fail the download if event publishing fails
        
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
    
    Args:
        model_id: ID of the parent model
        version: Version number for this artifact
        storage_path: S3 path where artifact is stored
        content_hash: SHA-256 hash of the artifact
        user_id: User who uploaded the artifact
    
    Raises:
        Exception if Model Catalog call fails
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8002,
        log_level="info"
    )
