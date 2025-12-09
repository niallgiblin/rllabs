"""
Pydantic Models for Request/Response Validation
================================================

These models define the API contract for the Upload/Download Service.
Pydantic provides automatic validation, serialisation, and documentation.
"""

from pydantic import BaseModel, Field
from typing import List, Optional

class PresignedURL(BaseModel):
    """
    Presigned URL for uploading a single chunk
    
    Each URL is time-limited and allows the client to upload one part
    directly to MinIO without going through our servers.
    """
    part_number: int = Field(..., description="Sequential part number (1-based)")
    url: str = Field(..., description="Presigned URL for uploading this part")
    expires_at: str = Field(..., description="ISO timestamp when URL expires")


class UploadInitRequest(BaseModel):
    """
    Request to initiate a new upload session
    
    Client calculates SHA-256 hash locally before uploading.
    This enables:
    1. Integrity verification after upload
    2. Content-addressed storage (deduplication)
    3. Idempotency (detect duplicate uploads)
    """
    filename: str = Field(..., description="Original filename", example="model_v2.3.pkl")
    file_size: int = Field(..., gt=0, description="Total file size in bytes", example=524288000)
    file_hash: str = Field(..., description="SHA-256 hash with prefix", example="sha256:abc123...")
    chunk_size: int = Field(default=5242880, description="Size of each chunk in bytes (default: 5MB)")
    artifact_type: str = Field(..., description="Type of artifact", example="model")
    model_id: int = Field(..., description="ID of the parent model", example=1)


class UploadInitResponse(BaseModel):
    """
    Response after initiating upload session
    
    Returns presigned URLs for each chunk.
    Client uploads chunks in parallel using these URLs.
    """
    upload_id: str = Field(..., description="Unique session identifier (UUID)")
    presigned_urls: List[PresignedURL] = Field(..., description="URLs for uploading each chunk")
    session_expires_at: str = Field(..., description="ISO timestamp when session expires")


class UploadPart(BaseModel):
    """
    Information about an uploaded part
    
    ETag is returned by MinIO after each chunk upload.
    We need this to complete the multipart upload.
    """
    part_number: int = Field(..., description="Part number (must match presigned URL)")
    etag: str = Field(..., description="ETag returned by MinIO after upload")


class UploadCompleteRequest(BaseModel):
    """
    Request to complete a multipart upload
    
    Client sends list of all uploaded parts with their ETags.
    """
    parts: List[UploadPart] = Field(..., description="List of uploaded parts with ETags")


class UploadCompleteResponse(BaseModel):
    """
    Response after completing upload
    
    artifact_id is the content hash (sha256:...) which serves as
    the permanent identifier for this artifact.
    
    Note: version is only set for model artifacts that are registered with Model Catalog.
    Config and dataset artifacts don't have version numbers.
    """
    artifact_id: str = Field(..., description="Content hash (sha256:...)")
    status: str = Field(default="completed", description="Upload status")
    storage_path: str = Field(..., description="S3 path where artifact is stored")
    registered_with_catalog: bool = Field(default=True, description="Whether registered with Model Catalog")
    model_id: int = Field(..., description="Parent model ID")
    version: Optional[int] = Field(None, description="Version number assigned (only for model artifacts registered with Model Catalog)")
    filename: str = Field(..., description="Original filename")
    file_size: int = Field(..., description="File size in bytes")


class DownloadResponse(BaseModel):
    """
    Response for download request
    
    Provides presigned URL for direct download from MinIO.
    URL is time-limited for security.
    """
    download_url: str = Field(..., description="Presigned URL for downloading")
    expires_at: str = Field(..., description="ISO timestamp when URL expires")
    file_size: int = Field(..., description="File size in bytes")
    filename: str = Field(..., description="Filename for download")


class TrainingJobRequest(BaseModel):
    """
    Request to trigger a training job
    
    All artifacts must exist in MinIO before the job can be queued.
    The service will verify artifact existence before publishing to RabbitMQ.
    
    If model_id is provided, trained weights will be associated with that model.
    If not provided, model_id will be inferred from the model artifact's upload history.
    """
    config_artifact_id: str = Field(
        ..., 
        description="SHA256 hash of training config JSON (dqn_config)",
        example="sha256:abc123..."
    )
    dataset_artifact_id: str = Field(
        ..., 
        description="SHA256 hash of dataset config JSON (grid_params/maze config)",
        example="sha256:def456..."
    )
    model_artifact_id: str = Field(
        ..., 
        description="SHA256 hash of pre-trained model weights .pth file",
        example="sha256:ghi789..."
    )
    model_id: Optional[int] = Field(
        None,
        description="Model ID to associate trained weights with. If not provided, will be inferred from the model artifact's upload history."
    )


class TrainingJobResponse(BaseModel):
    """
    Response after triggering a training job
    
    The job has been queued for processing by the training service.
    The training service will consume the job from RabbitMQ and process it asynchronously.
    """
    job_id: str = Field(..., description="Unique job identifier (UUID)")
    status: str = Field(default="queued", description="Job status")
    message: str = Field(..., description="Human-readable status message")
