"""
Storage Service - MinIO/S3 Operations
======================================

Handles all interactions with MinIO (S3-compatible object storage).

Key Features:
1. Multipart uploads for large files
2. Presigned URLs for direct client-to-storage access
3. Content-addressed storage (files stored by SHA-256 hash)
4. Async operations using aioboto3

Architectural Decision: Why MinIO over filesystem?
- Pros: Scalable, distributed, S3-compatible, built-in redundancy
- Cons: Additional infrastructure, network latency
- Trade-off: Better for distributed systems, worth the complexity
"""

import aioboto3
from botocore.exceptions import ClientError
from botocore.config import Config
from typing import Dict, List, Optional
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class StorageService:
    """
    Wrapper around MinIO/S3 operations
    
    Uses aioboto3 for async operations (non-blocking I/O).
    This is crucial for handling many concurrent uploads without blocking.
    """
    
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        use_ssl: bool = False,
        public_endpoint: Optional[str] = None
    ):
        """
        Initialize storage service
        
        Args:
            endpoint: MinIO endpoint for internal access (e.g., "minio:9000")
            access_key: MinIO access key
            secret_key: MinIO secret key
            bucket: Bucket name for storing artifacts
            use_ssl: Whether to use HTTPS (False for local development)
            public_endpoint: Public endpoint for presigned URLs (e.g., "localhost:9000")
                           If None, uses endpoint. Used for browser-accessible URLs.
        """
        self.endpoint = endpoint
        self.public_endpoint = public_endpoint or endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.use_ssl = use_ssl
        
        # aioboto3 session for async operations
        # Configure connection pooling and retries for better performance
        config = Config(
            max_pool_connections=50,  # Max connections in connection pool
            retries={
                'max_attempts': 3,  # Retry failed requests
                'mode': 'adaptive'  # Adaptive retry mode
            },
            connect_timeout=10,  # Connection timeout
            read_timeout=30  # Read timeout for operations
        )
        self.session = aioboto3.Session()
        self.config = config
    
    async def initialize(self):
        """
        Initialize storage (create bucket if needed)
        
        Called on application startup.
        Idempotent - safe to call multiple times.
        """
        async with self.session.client(
            's3',
            endpoint_url=f"{'https' if self.use_ssl else 'http'}://{self.endpoint}",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=self.config,
        ) as s3_client:
            try:
                # Check if bucket exists
                await s3_client.head_bucket(Bucket=self.bucket)
                logger.info(f"✓ Storage bucket '{self.bucket}' exists")
            except ClientError as e:
                # Bucket doesn't exist, create it
                if e.response['Error']['Code'] == '404':
                    await s3_client.create_bucket(Bucket=self.bucket)
                    logger.info(f"✓ Created storage bucket '{self.bucket}'")
                else:
                    logger.error(f"✗ Error checking bucket: {e}")
                    raise
    
    async def initiate_multipart_upload(self, object_key: str) -> str:
        """
        Start a multipart upload session with MinIO
        
        Multipart uploads allow:
        1. Uploading large files in chunks (parallel uploads)
        2. Resuming failed uploads
        3. Better error recovery (only retry failed chunks)
        
        Args:
            object_key: S3 key for the object (e.g., "temp/uuid")
        
        Returns:
            MinIO's upload ID for this multipart session
        
        Note: Uses internal endpoint since this is server-to-server communication
        """
        # Use internal endpoint for server operations (not presigned URLs)
        # Merge connection pooling config with signature config
        config = Config(
            max_pool_connections=self.config.max_pool_connections,
            retries=self.config.retries,
            connect_timeout=self.config.connect_timeout,
            read_timeout=self.config.read_timeout,
            signature_version='s3v4',
            s3={
                'addressing_style': 'path'
            }
        )
        
        async with self.session.client(
            's3',
            endpoint_url=f"{'https' if self.use_ssl else 'http'}://{self.endpoint}",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=config
        ) as s3_client:
            try:
                response = await s3_client.create_multipart_upload(
                    Bucket=self.bucket,
                    Key=object_key
                )
                upload_id = response['UploadId']
                logger.info(f"Initiated multipart upload: {object_key} (upload_id: {upload_id})")
                return upload_id
                
            except ClientError as e:
                logger.error(f"Failed to initiate multipart upload: {e}")
                raise Exception(f"Storage error: {str(e)}")
    
    async def generate_presigned_upload_url(
        self,
        object_key: str,
        upload_id: str,
        part_number: int,
        expires_in: int = 3600
    ) -> str:
        """
        Generate a presigned URL for uploading one part
        
        Presigned URLs allow clients to upload directly to MinIO without
        passing data through our service. This is crucial for performance.
        
        Security: URLs are time-limited and part-specific
        
        Args:
            object_key: S3 key for the object
            upload_id: MinIO's multipart upload ID
            part_number: Sequential part number (1-based)
            expires_in: URL expiration time in seconds (default: 1 hour)
        
        Returns:
            Presigned URL that client can use to PUT the chunk
            Uses public_endpoint if configured (for browser access)
        """
        # Use public endpoint for presigned URLs (browser-accessible)
        # but use internal endpoint for actual operations
        presigned_endpoint = self.public_endpoint
        
        # Configure boto3 to use path-style addressing and ensure signature compatibility
        # Merge connection pooling config with signature config
        config = Config(
            max_pool_connections=self.config.max_pool_connections,
            retries=self.config.retries,
            connect_timeout=self.config.connect_timeout,
            read_timeout=self.config.read_timeout,
            signature_version='s3v4',
            s3={
                'addressing_style': 'path'
            }
        )
        
        async with self.session.client(
            's3',
            endpoint_url=f"{'https' if self.use_ssl else 'http'}://{presigned_endpoint}",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=config
        ) as s3_client:
            try:
                url = await s3_client.generate_presigned_url(
                    'upload_part',
                    Params={
                        'Bucket': self.bucket,
                        'Key': object_key,
                        'PartNumber': part_number,
                        'UploadId': upload_id
                    },
                    ExpiresIn=expires_in
                )
                return url
                
            except ClientError as e:
                logger.error(f"Failed to generate presigned URL: {e}")
                raise Exception(f"Storage error: {str(e)}")
    
    async def complete_multipart_upload(
        self,
        object_key: str,
        upload_id: str,
        parts: List[Dict[str, any]]
    ) -> Dict:
        """
        Complete a multipart upload (stitch chunks together)
        
        After all chunks are uploaded, this tells MinIO to combine them
        into a single object.
        
        Args:
            object_key: S3 key for the object
            upload_id: MinIO's multipart upload ID
            parts: List of dicts with 'PartNumber' and 'ETag'
        
        Returns:
            Response from MinIO with ETag of final object
        """
        async with self.session.client(
            's3',
            endpoint_url=f"{'https' if self.use_ssl else 'http'}://{self.endpoint}",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=self.config,
        ) as s3_client:
            try:
                response = await s3_client.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=object_key,
                    UploadId=upload_id,
                    MultipartUpload={'Parts': parts}
                )
                logger.info(f"Completed multipart upload: {object_key}")
                return response
                
            except ClientError as e:
                logger.error(f"Failed to complete multipart upload: {e}")
                raise Exception(f"Storage error: {str(e)}")
    
    async def abort_multipart_upload(self, object_key: str, upload_id: str):
        """
        Cancel a multipart upload and clean up partial data
        
        Important for cleanup when uploads fail or are cancelled by user.
        
        Args:
            object_key: S3 key for the object
            upload_id: MinIO's multipart upload ID
        """
        async with self.session.client(
            's3',
            endpoint_url=f"{'https' if self.use_ssl else 'http'}://{self.endpoint}",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=self.config,
        ) as s3_client:
            try:
                await s3_client.abort_multipart_upload(
                    Bucket=self.bucket,
                    Key=object_key,
                    UploadId=upload_id
                )
                logger.info(f"Aborted multipart upload: {object_key}")
                
            except ClientError as e:
                logger.warning(f"Failed to abort multipart upload: {e}")
                # Don't raise - best effort cleanup
    
    async def copy_object(self, source_key: str, dest_key: str):
        """
        Copy an object to a new location
        
        Used to move uploaded file from temp location to content-addressed location.
        Example: temp/uuid -> sha256:abc123...
        
        Args:
            source_key: Source S3 key
            dest_key: Destination S3 key
        """
        async with self.session.client(
            's3',
            endpoint_url=f"{'https' if self.use_ssl else 'http'}://{self.endpoint}",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=self.config,
        ) as s3_client:
            try:
                await s3_client.copy_object(
                    Bucket=self.bucket,
                    CopySource={'Bucket': self.bucket, 'Key': source_key},
                    Key=dest_key
                )
                logger.info(f"Copied object: {source_key} -> {dest_key}")
                
            except ClientError as e:
                logger.error(f"Failed to copy object: {e}")
                raise Exception(f"Storage error: {str(e)}")
    
    async def delete_object(self, object_key: str):
        """
        Delete an object from storage
        
        Used to clean up temp files after copying to final location.
        
        Args:
            object_key: S3 key to delete
        """
        async with self.session.client(
            's3',
            endpoint_url=f"{'https' if self.use_ssl else 'http'}://{self.endpoint}",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=self.config,
        ) as s3_client:
            try:
                await s3_client.delete_object(
                    Bucket=self.bucket,
                    Key=object_key
                )
                logger.info(f"Deleted object: {object_key}")
                
            except ClientError as e:
                logger.warning(f"Failed to delete object: {e}")
                # Don't raise - best effort cleanup
    
    async def get_object_info(self, object_key: str) -> Optional[Dict]:
        """
        Get metadata about an object
        
        Used to verify file exists and get size/etag.
        
        Args:
            object_key: S3 key to check
        
        Returns:
            Dict with 'size', 'etag', 'filename' or None if not found
        """
        async with self.session.client(
            's3',
            endpoint_url=f"{'https' if self.use_ssl else 'http'}://{self.endpoint}",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=self.config,
        ) as s3_client:
            try:
                response = await s3_client.head_object(
                    Bucket=self.bucket,
                    Key=object_key
                )
                return {
                    'size': response.get('ContentLength', 0),
                    'etag': response.get('ETag', '').strip('"'),
                    'filename': object_key.split('/')[-1]
                }
                
            except ClientError as e:
                if e.response['Error']['Code'] == '404':
                    return None
                logger.error(f"Failed to get object info: {e}")
                raise Exception(f"Storage error: {str(e)}")
    
    async def generate_presigned_get_url(
        self,
        object_key: str,
        expires_in: int = 3600
    ) -> str:
        """
        Generate a presigned URL for downloading an object
        
        Allows clients to download directly from MinIO.
        
        Args:
            object_key: S3 key to download
            expires_in: URL expiration time in seconds (default: 1 hour)
        
        Returns:
            Presigned URL for GET operation
            Uses public_endpoint if configured (for browser access)
        """
        # Use public endpoint for presigned URLs (browser-accessible)
        presigned_endpoint = self.public_endpoint
        
        # Configure boto3 to use path-style addressing and ensure signature compatibility
        # Merge connection pooling config with signature config
        config = Config(
            max_pool_connections=self.config.max_pool_connections,
            retries=self.config.retries,
            connect_timeout=self.config.connect_timeout,
            read_timeout=self.config.read_timeout,
            signature_version='s3v4',
            s3={
                'addressing_style': 'path'
            }
        )
        
        async with self.session.client(
            's3',
            endpoint_url=f"{'https' if self.use_ssl else 'http'}://{presigned_endpoint}",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=config
        ) as s3_client:
            try:
                url = await s3_client.generate_presigned_url(
                    'get_object',
                    Params={
                        'Bucket': self.bucket,
                        'Key': object_key
                    },
                    ExpiresIn=expires_in
                )
                return url
                
            except ClientError as e:
                logger.error(f"Failed to generate download URL: {e}")
                raise Exception(f"Storage error: {str(e)}")
