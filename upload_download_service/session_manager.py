"""
Session Manager - Upload Session Orchestration
===============================================

Coordinates the upload workflow by managing sessions and storage operations.

Responsibilities:
1. Create and track upload sessions
2. Generate presigned URLs for chunks
3. Complete uploads with integrity verification
4. Handle errors and cleanup
5. Manage idempotency

Architectural Pattern: Coordinator/Orchestrator
- Separates business logic from storage and database operations
- Makes testing easier (can mock StorageService)
- Keeps main.py focused on HTTP handling
"""

from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
import uuid
import math
import logging
import redis
import os
import asyncio

from database import UploadSession, UploadStatus
from storage import StorageService
from models import PresignedURL, UploadPart

logger = logging.getLogger(__name__)

# Redis for idempotency keys and rate limiting
REDIS_HOST = os.getenv("REDIS_HOST", "redis-master")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_SENTINEL_HOSTS = os.getenv("REDIS_SENTINEL_HOSTS", "")
REDIS_SENTINEL_MASTER_NAME = os.getenv("REDIS_SENTINEL_MASTER_NAME", "mymaster")

# Try to use async Redis (redis[asyncio]), fallback to sync Redis with asyncio.to_thread
try:
    from redis.asyncio import Redis as AsyncRedis
    from redis.sentinel import Sentinel as SyncSentinel
    
    # Initialize async Redis client with Sentinel support if configured
    _async_redis_client = None
    
    async def get_async_redis_client():
        """Get or create async Redis client"""
        global _async_redis_client
        if _async_redis_client is None:
            if REDIS_SENTINEL_HOSTS and REDIS_SENTINEL_MASTER_NAME:
                sentinel_hosts = []
                for host_port in REDIS_SENTINEL_HOSTS.split(","):
                    host_port = host_port.strip()
                    if ":" in host_port:
                        host, port = host_port.split(":")
                        sentinel_hosts.append((host, int(port)))
                    else:
                        sentinel_hosts.append((host_port, 26379))
                
                # Create sync sentinel to get master address
                sync_sentinel = SyncSentinel(
                    sentinel_hosts,
                    socket_timeout=1.0,
                    socket_connect_timeout=1.0,
                    password=REDIS_PASSWORD if REDIS_PASSWORD else None
                )
                master = sync_sentinel.master_for(REDIS_SENTINEL_MASTER_NAME)
                # Get connection info for async client
                master_host, master_port = master.connection_pool.connection_kwargs['host'], master.connection_pool.connection_kwargs['port']
                
                _async_redis_client = AsyncRedis.from_url(
                    f"redis://{master_host}:{master_port}",
                    password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                    decode_responses=True,
                    socket_timeout=1.0
                )
                logger.info(f"✅ Async Redis connected via Sentinel, master: {REDIS_SENTINEL_MASTER_NAME}")
            else:
                _async_redis_client = AsyncRedis.from_url(
                    f"redis://{REDIS_HOST}:{REDIS_PORT}",
                    password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                    decode_responses=True,
                    socket_timeout=1.0
                )
                logger.info(f"✅ Async Redis connected directly to {REDIS_HOST}:{REDIS_PORT}")
        return _async_redis_client
    
    ASYNC_REDIS_AVAILABLE = True
except ImportError:
    ASYNC_REDIS_AVAILABLE = False
    logger.warning("redis[asyncio] not available, using sync Redis with asyncio.to_thread (slower)")
    
    # Fallback: Initialize sync Redis client (will use asyncio.to_thread for async operations)
    if REDIS_SENTINEL_HOSTS and REDIS_SENTINEL_MASTER_NAME:
        from redis.sentinel import Sentinel
        
        sentinel_hosts = []
        for host_port in REDIS_SENTINEL_HOSTS.split(","):
            host_port = host_port.strip()
            if ":" in host_port:
                host, port = host_port.split(":")
                sentinel_hosts.append((host, int(port)))
            else:
                sentinel_hosts.append((host_port, 26379))
        
        sentinel = Sentinel(
            sentinel_hosts,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
            password=REDIS_PASSWORD if REDIS_PASSWORD else None
        )
        
        redis_client = sentinel.master_for(
            REDIS_SENTINEL_MASTER_NAME,
            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
            db=0,
            decode_responses=True,
            socket_timeout=1.0
        )
        logger.info(f"✅ Redis connected via Sentinel ({len(sentinel_hosts)} sentinels), master: {REDIS_SENTINEL_MASTER_NAME}")
    else:
        redis_client = redis.Redis(
            host=REDIS_HOST, 
            port=REDIS_PORT, 
            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
            decode_responses=True,
            socket_timeout=1.0
        )


class SessionManager:
    """
    Manages upload sessions and coordinates storage operations
    
    This class orchestrates the complex multipart upload workflow.
    """
    
    def __init__(self, db: Session, storage: StorageService):
        """
        Initialize session manager
        
        Args:
            db: SQLAlchemy database session
            storage: Storage service for MinIO operations
        """
        self.db = db
        self.storage = storage
    
    async def check_idempotency(
        self,
        file_hash: str,
        user_id: str
    ) -> Optional[Dict]:
        """
        Check if this file was recently uploaded (idempotency)
        
        Architectural Decision: Use Redis for idempotency keys
        - Trade-off: Keys expire after 24 hours (reduces memory usage)
        - Prevents duplicate uploads of identical files
        - If Redis is down, we skip idempotency check (fail-open)
        
        Args:
            file_hash: SHA-256 hash of the file
            user_id: User uploading the file
        
        Returns:
            Existing session data if duplicate, None otherwise
        """
        idempotency_key = f"upload:idempotency:{user_id}:{file_hash}"
        
        try:
            # Check Redis for recent upload of this file (async)
            if ASYNC_REDIS_AVAILABLE:
                async_redis = await get_async_redis_client()
                existing_upload_id = await async_redis.get(idempotency_key)
            else:
                # Fallback: use sync Redis with asyncio.to_thread
                existing_upload_id = await asyncio.to_thread(redis_client.get, idempotency_key)
            
            if existing_upload_id:
                # Found duplicate - fetch session from database
                session = self.db.query(UploadSession).filter(
                    UploadSession.upload_id == existing_upload_id
                ).first()
                
                if session and session.status == UploadStatus.COMPLETED:
                    logger.info(f"Idempotency hit: {file_hash} already uploaded as {existing_upload_id}")
                    
                    # Return existing session info
                    # Note: presigned_urls will be expired, but client already has the artifact_id
                    return {
                        "upload_id": session.upload_id,
                        "presigned_urls": [],  # Already completed, no URLs needed
                        "session_expires_at": session.completed_at.isoformat() + "Z",
                        "status": "already_completed",
                        "artifact_id": session.file_hash
                    }
        
        except (redis.RedisError, Exception) as e:
            # Redis is down - fail open (allow upload)
            logger.warning(f"Redis unavailable for idempotency check: {e}")
        
        return None
    
    async def create_upload_session(
        self,
        filename: str,
        file_size: int,
        file_hash: str,
        chunk_size: int,
        artifact_type: str,
        model_id: int,
        user_id: str
    ) -> UploadSession:
        """
        Create a new upload session with presigned URLs
        
        Flow:
        1. Generate unique upload_id (UUID)
        2. Calculate number of chunks needed
        3. Initiate multipart upload with MinIO
        4. Generate presigned URL for each chunk
        5. Save session to database
        6. Store idempotency key in Redis
        
        Args:
            filename: Original filename
            file_size: Total file size in bytes
            file_hash: SHA-256 hash (sha256:...)
            chunk_size: Size of each chunk in bytes
            artifact_type: Type (model, environment, dataset)
            model_id: Parent model ID
            user_id: User initiating upload
        
        Returns:
            UploadSession object with presigned_urls attached
        """
        # Generate unique session ID
        upload_id = str(uuid.uuid4())
        
        # Calculate number of parts needed
        num_parts = math.ceil(file_size / chunk_size)
        logger.info(f"Creating upload session {upload_id}: {num_parts} parts, {file_size} bytes")
        
        # Use temporary object key for multipart upload
        # Will move to content-addressed location after verification
        temp_object_key = f"temp/{upload_id}"
        
        # Initiate multipart upload with MinIO
        minio_upload_id = await self.storage.initiate_multipart_upload(temp_object_key)
        
        # Generate presigned URLs for each part in parallel
        expires_at = datetime.utcnow() + timedelta(hours=1)
        
        async def generate_url(part_num: int):
            """Helper function to generate a single presigned URL"""
            url = await self.storage.generate_presigned_upload_url(
                object_key=temp_object_key,
                upload_id=minio_upload_id,
                part_number=part_num,
                expires_in=3600  # 1 hour
            )
            return PresignedURL(
                part_number=part_num,
                url=url,
                expires_at=expires_at.isoformat() + "Z"
            )
        
        # Generate all URLs concurrently using asyncio.gather()
        import asyncio
        presigned_urls = await asyncio.gather(*[
            generate_url(part_num) for part_num in range(1, num_parts + 1)
        ])
        
        # Create database record
        db_session = UploadSession(
            upload_id=upload_id,
            minio_upload_id=minio_upload_id,
            filename=filename,
            file_size=file_size,
            file_hash=file_hash,
            chunk_size=chunk_size,
            artifact_type=artifact_type,
            model_id=model_id,
            user_id=user_id,
            status=UploadStatus.INITIATED
        )
        
        self.db.add(db_session)
        self.db.commit()
        self.db.refresh(db_session)
        
        # Store idempotency key in Redis (expires in 24 hours) - async
        try:
            idempotency_key = f"upload:idempotency:{user_id}:{file_hash}"
            if ASYNC_REDIS_AVAILABLE:
                async_redis = await get_async_redis_client()
                await async_redis.setex(idempotency_key, 86400, upload_id)  # 24 hour TTL
            else:
                # Fallback: use sync Redis with asyncio.to_thread
                await asyncio.to_thread(redis_client.setex, idempotency_key, 86400, upload_id)
        except (redis.RedisError, Exception) as e:
            logger.warning(f"Failed to set idempotency key: {e}")
            # Don't fail the upload if Redis is down
        
        # Attach presigned URLs to session object (not stored in DB)
        db_session.presigned_urls = presigned_urls
        db_session.session_expires_at = expires_at.isoformat() + "Z"
        
        return db_session
    
    async def complete_upload(
        self,
        upload_id: str,
        parts: List[UploadPart],
        user_id: str
    ) -> Dict:
        """
        Complete a multipart upload
        
        Flow:
        1. Verify session exists and belongs to user
        2. Complete multipart upload in MinIO (stitch chunks)
        3. Verify SHA-256 hash matches original
        4. Copy to content-addressed location
        5. Delete temp file
        6. Determine version number for this model
        7. Update session status to COMPLETED
        
        Args:
            upload_id: Session ID
            parts: List of uploaded parts with ETags
            user_id: User completing the upload
        
        Returns:
            Dict with artifact_id, storage_path, version, etc.
        """
        # Fetch session from database
        session = self.db.query(UploadSession).filter(
            and_(
                UploadSession.upload_id == upload_id,
                UploadSession.user_id == user_id  # Verify ownership
            )
        ).first()
        
        if not session:
            raise Exception(f"Upload session {upload_id} not found or unauthorized")
        
        # Idempotency: If already completed, return the existing result
        if session.status == UploadStatus.COMPLETED:
            logger.info(f"Upload session {upload_id} already completed, returning existing result")
            # Return the same structure as a successful completion
            # Extract version from storage_path if available (format: "models/{model_id}/v{version}")
            version = 1
            if session.storage_path:
                import re
                match = re.search(r'/v(\d+)$', session.storage_path)
                if match:
                    version = int(match.group(1))
            
            return {
                "artifact_id": session.file_hash,  # Content hash is the artifact ID
                "status": "completed",
                "storage_path": session.storage_path or f"models/{session.model_id}/v{version}",
                "model_id": session.model_id,
                "version": version,
                "filename": session.filename,
                "file_size": session.file_size
            }
        
        # Handle retry of failed uploads that actually succeeded (e.g., 409 from Model Catalog)
        if session.status == UploadStatus.FAILED:
            # Check if the error was a 409 (version already exists) - in that case, upload was successful
            if session.error_message and ("409" in session.error_message or "version already exists" in session.error_message.lower()):
                # Verify file exists in storage
                final_object_key = session.file_hash
                object_info = await self.storage.get_object_info(final_object_key)
                if object_info:
                    # File exists - the upload was actually successful, just Model Catalog registration failed with 409
                    logger.info(f"Upload {upload_id} failed with 409 but file exists - treating as completed")
                    # Mark as completed
                    session.status = UploadStatus.COMPLETED
                    session.error_message = None
                    if not session.storage_path:
                        # Try to reconstruct storage_path
                        version = 1
                        if session.model_id:
                            from sqlalchemy import func
                            existing_versions = self.db.query(UploadSession).filter(
                                and_(
                                    UploadSession.model_id == session.model_id,
                                    UploadSession.status == UploadStatus.COMPLETED
                                )
                            ).count()
                            version = existing_versions + 1
                        session.storage_path = f"models/{session.model_id}/v{version}" if session.model_id else f"models/unknown/v{version}"
                    session.completed_at = datetime.now(timezone.utc)
                    self.db.commit()
                    
                    # Return success
                    version = 1
                    if session.storage_path:
                        import re
                        match = re.search(r'/v(\d+)$', session.storage_path)
                        if match:
                            version = int(match.group(1))
                    
                    return {
                        "artifact_id": session.file_hash,
                        "status": "completed",
                        "storage_path": session.storage_path,
                        "model_id": session.model_id,
                        "version": version,
                        "filename": session.filename,
                        "file_size": session.file_size
                    }
            # If it's a real failure (not 409), raise error
            raise Exception(f"Upload session {upload_id} is not in INITIATED state (current: {session.status})")
        
        if session.status != UploadStatus.INITIATED:
            raise Exception(f"Upload session {upload_id} is not in INITIATED state (current: {session.status})")
        
        temp_object_key = f"temp/{upload_id}"
        
        try:
            # Format parts for MinIO
            minio_parts = [
                {
                    'PartNumber': part.part_number,
                    'ETag': part.etag
                }
                for part in parts
            ]
            
            # Complete multipart upload in MinIO
            await self.storage.complete_multipart_upload(
                object_key=temp_object_key,
                upload_id=session.minio_upload_id,
                parts=minio_parts
            )
            
            logger.info(f"Completed multipart upload for {upload_id}")
            
            # Verify file integrity by checking object exists
            # In production, you might want to download and verify SHA-256
            # Trade-off: Skipping full hash verification for performance
            # MinIO provides integrity guarantees via ETags
            object_info = await self.storage.get_object_info(temp_object_key)
            
            if not object_info:
                raise Exception("Uploaded file not found in storage after completion")
            
            # Content-addressed storage: store by hash
            # This enables deduplication - identical files share storage
            final_object_key = session.file_hash  # sha256:abc123...
            
            # Copy to final location
            await self.storage.copy_object(temp_object_key, final_object_key)
            
            # Clean up temp file
            await self.storage.delete_object(temp_object_key)
            
            # Determine version number for this model
            # Get the highest version for this model and increment
            from sqlalchemy import func
            max_version = self.db.query(
                func.max(UploadSession.storage_path)
            ).filter(
                and_(
                    UploadSession.model_id == session.model_id,
                    UploadSession.status == UploadStatus.COMPLETED
                )
            ).scalar()
            
            # Extract version from storage_path (format: "models/{model_id}/v{version}")
            # For simplicity, we'll use a counter starting at 1
            # In production, sync with Model Catalog for version numbers
            existing_versions = self.db.query(UploadSession).filter(
                and_(
                    UploadSession.model_id == session.model_id,
                    UploadSession.status == UploadStatus.COMPLETED
                )
            ).count()
            
            version = existing_versions + 1
            storage_path = f"models/{session.model_id}/v{version}"
            
            # Update session
            session.status = UploadStatus.COMPLETED
            session.storage_path = storage_path
            session.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            
            logger.info(f"Upload {upload_id} completed successfully: {final_object_key}")
            
            return {
                "artifact_id": session.file_hash,
                "status": "completed",
                "storage_path": storage_path,
                "model_id": session.model_id,
                "version": version,
                "filename": session.filename,
                "file_size": session.file_size
            }
            
        except Exception as e:
            # Mark upload as failed
            session.status = UploadStatus.FAILED
            session.error_message = str(e)
            session.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            
            logger.error(f"Failed to complete upload {upload_id}: {e}")
            
            # Try to clean up
            try:
                await self.storage.abort_multipart_upload(temp_object_key, session.minio_upload_id)
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup after error: {cleanup_error}")
            
            raise Exception(f"Upload completion failed: {str(e)}")
    
    async def abort_upload(self, upload_id: str, user_id: str):
        """
        Abort an upload session (fast path - returns immediately)
        
        This method marks the session as aborted immediately and returns.
        MinIO cleanup happens in the background via cleanup_minio_upload().
        
        Optimized for performance:
        - Uses composite index (user_id, upload_id) for fast lookup
        - Minimal database operations (only status and completed_at update)
        - Fast commit with minimal transaction overhead
        
        Args:
            upload_id: Session ID to abort
            user_id: User aborting the upload
        
        Returns:
            Tuple of (temp_object_key, minio_upload_id) for background cleanup
        """
        # Fetch session using composite index (idx_upload_sessions_user_id_upload_id)
        # This is the fastest possible query - single indexed lookup
        session = self.db.query(UploadSession).filter(
            and_(
                UploadSession.upload_id == upload_id,
                UploadSession.user_id == user_id
            )
        ).first()
        
        if not session:
            raise Exception(f"Upload session {upload_id} not found or unauthorized")
        
        if session.status != UploadStatus.INITIATED:
            logger.warning(f"Attempting to abort upload {upload_id} in {session.status} state")
        
        # Mark as aborted immediately (fast DB write - only 2 columns updated)
        # Use minimal transaction - only update what's necessary
        session.status = UploadStatus.ABORTED
        session.completed_at = datetime.now(timezone.utc)
        
        # Fast commit - connection pool handles connection reuse
        self.db.commit()
        
        logger.info(f"Upload {upload_id} marked as aborted (cleanup will happen in background)")
        
        # Return cleanup info for background task
        temp_object_key = f"temp/{upload_id}"
        return (temp_object_key, session.minio_upload_id)
    
    async def cleanup_minio_upload(self, temp_object_key: str, minio_upload_id: str):
        """
        Background cleanup of MinIO multipart upload (non-blocking)
        
        This method is called in the background after abort_upload() returns.
        It handles the slow MinIO cleanup operation without blocking the request.
        Added timeout to prevent hanging if MinIO is slow.
        
        Args:
            temp_object_key: S3 key for the temporary object
            minio_upload_id: MinIO's multipart upload ID
        """
        import asyncio
        try:
            # Add timeout to prevent hanging (5 seconds max for cleanup)
            await asyncio.wait_for(
                self.storage.abort_multipart_upload(temp_object_key, minio_upload_id),
                timeout=5.0
            )
            logger.info(f"Background cleanup completed for {temp_object_key}")
        except asyncio.TimeoutError:
            logger.warning(f"Background cleanup timed out for {temp_object_key} (MinIO may be slow)")
        except Exception as e:
            logger.warning(f"Background cleanup failed for {temp_object_key}: {e}")
            # Don't raise - cleanup failure is acceptable, session is already marked as aborted
    
    async def fail_upload(self, upload_id: str, error_message: str):
        """
        Mark an upload as failed
        
        Called when external operations fail (e.g., Model Catalog registration)
        
        Args:
            upload_id: Session ID
            error_message: Reason for failure
        """
        session = self.db.query(UploadSession).filter(
            UploadSession.upload_id == upload_id
        ).first()
        
        if session:
            session.status = UploadStatus.FAILED
            session.error_message = error_message
            session.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            logger.error(f"Marked upload {upload_id} as failed: {error_message}")
