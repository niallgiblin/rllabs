# app/services/upload_service.py
# -----------------------------------------------------------------------------
# Service layer for handling the *upload* lifecycle:
# - start_upload: create an UploadSession and pre-sign multipart upload URLs
# - complete_upload: finalize multipart upload, verify integrity, persist Artifact
# - abort_upload: cancel multipart upload and mark session aborted
#
# Key behaviors & assumptions:
# - Uses aioboto3 for async S3-compatible operations (S3, MinIO, etc.).
# - Single-part path for simplicity: we currently pre-sign only PartNumber=1.
#   (Extend the loop in start_upload for true multi-part uploads.)
# - End-to-end integrity check: complete_upload recomputes SHA-256 from object
#   in storage and compares with client-provided expected_hash.
# - Idempotence / dedupe: persisted Artifact storage_key is content-addressed
#   under 'sha256/<expected_hash>'. Copy temp object into that key when verified.
# - Resilience: _copy_object uses tenacity to retry transient failures.
# - Side-effects are logged and state transitions are flushed by repository calls.
# - External notification to Model Catalog is fired in the background (non-blocking).
# -----------------------------------------------------------------------------

import uuid
import asyncio
from aioboto3 import Session
from botocore.exceptions import ClientError
from tenacity import retry, stop_after_attempt, wait_exponential
from ..settings import settings
from ..repositories.sessions_repo import create_session, mark_completed, mark_aborted, update_parts, get_session
from ..repositories.artifacts_repo import create_artifact
from ..storage.integrity import compute_s3_sha256
from ..services.events import notify_model_catalog
import logging

logger = logging.getLogger("upload_download_service")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def _copy_object(s3, bucket: str, src: str, dest: str):
    """
    Server-side copy within the same bucket, with retries on transient errors.

    Parameters
    ----------
    s3 : aioboto3 S3 client
        Active async client used for the copy operation.
    bucket : str
        Target bucket name (also used for source).
    src : str
        Source object key.
    dest : str
        Destination object key.
    """
    # Uses S3's CopyObject API to avoid client-side re-upload.
    await s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": src}, Key=dest)


async def start_upload(db, filename: str, size_bytes: int, mime_type: str, expected_hash: str, owner_id: str):
    """
    Initialize an upload session and return pre-signed URL(s) for uploading parts.

    Flow
    ----
    1) Create UploadSession with a temporary object key (temp/<uuid>).
    2) Create a multipart upload on that temp key.
    3) Generate pre-signed URL(s) for part uploads (currently only Part 1).
    4) Store the S3 UploadId in session.parts_json for later completion.

    Returns
    -------
    dict
        {
          "upload_id": <session id>,
          "temp_key": "temp/<uuid>",
          "part_urls": [ <URL for part 1>, ... ]
        }
    """
    # Generate a unique ID to correlate DB session and temporary storage key.
    upload_id = str(uuid.uuid4())
    temp_key = f"temp/{upload_id}"

    # Persist the upload session in 'in_progress' with optional expected hash.
    session = create_session(db, temp_key=temp_key, owner_id=owner_id, expected_hash=expected_hash)

    # Create multipart upload and produce pre-signed URLs for each part.
    async with Session().client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        use_ssl=settings.S3_USE_SSL,
    ) as s3:
        multipart = await s3.create_multipart_upload(Bucket=settings.S3_BUCKET, Key=temp_key)
        upload_key = multipart["UploadId"]

        part_urls = []
        # NOTE: For true multipart, adjust range and client-side splitting.
        for part_num in range(1, 2):
            url = await s3.generate_presigned_url(
                "upload_part",
                Params={"Bucket": settings.S3_BUCKET, "Key": temp_key, "UploadId": upload_key, "PartNumber": part_num},
                ExpiresIn=settings.PRESIGN_TTL_SECONDS,
            )
            part_urls.append(url)

        # Save UploadId so we can complete/abort later.
        update_parts(db, session.id, {"s3_upload_id": upload_key})

    logger.info(f"Created upload session {session.id} for {filename}")
    return {"upload_id": session.id, "temp_key": temp_key, "part_urls": part_urls}


async def complete_upload(db, upload_id: str, expected_hash: str, etag: str):
    """
    Finalize multipart upload, verify integrity, and persist Artifact metadata.

    Steps
    -----
    1) Look up UploadSession by id.
    2) Call CompleteMultipartUpload using the saved UploadId, providing parts list.
    3) Compute SHA-256 of the assembled object in storage and compare to expected_hash.
    4) If hash matches, copy from temp key to content-addressed key: sha256/<hash>.
    5) Delete the temporary object.
    6) Create Artifact row (dedup handled at repository/DB level if unique).
    7) Mark session as completed.
    8) Notify external Model Catalog asynchronously (fire-and-forget).

    Raises
    ------
    ValueError
        If the session is missing or the integrity check fails.
    ClientError
        If S3 completion fails (propagated after logging).
    """
    # Resolve the session. If it's gone or invalid, we cannot proceed.
    session = get_session(db, upload_id)
    if not session:
        raise ValueError("Upload session not found")

    async with Session().client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        use_ssl=settings.S3_USE_SSL,
    ) as s3:
        try:
            # We currently only expect one part (PartNumber=1). Strip quotes around ETag if provided.
            parts = [{"ETag": etag.strip('\"'), "PartNumber": 1}]
            await s3.complete_multipart_upload(
                Bucket=settings.S3_BUCKET,
                Key=session.temp_key,
                MultipartUpload={"Parts": parts},
                UploadId=session.parts_json.get("s3_upload_id"),
            )
        except ClientError as e:
            logger.error(f"Failed to complete multipart upload: {e}")
            raise

        # Verify end-to-end integrity by recomputing SHA-256 from the stored object.
        actual_hash = await compute_s3_sha256(settings.S3_BUCKET, session.temp_key)
        if actual_hash != expected_hash:
            logger.warning(f"Integrity check failed for {upload_id}")
            raise ValueError("Hash mismatch")

        # Materialize a stable, content-addressed key for the final artifact.
        storage_key = f"sha256/{expected_hash}"
        # Server-side copy to the final location, then remove the temp object.
        await _copy_object(s3, settings.S3_BUCKET, session.temp_key, storage_key)
        await s3.delete_object(Bucket=settings.S3_BUCKET, Key=session.temp_key)

    # Persist artifact metadata. Size/mime_type are placeholders here; populate from headers if needed.
    artifact = create_artifact(
        db,
        storage_key=storage_key,
        content_hash=expected_hash,
        size_bytes=0,
        mime_type="application/octet-stream",
    )
    # Mark workflow state as completed.
    mark_completed(db, upload_id)

    # Notify external catalog (non-blocking); failures won't affect client response.
    asyncio.create_task(notify_model_catalog(artifact.id, expected_hash))

    logger.info(f"Upload {upload_id} finalized and verified")
    return artifact


async def abort_upload(db, upload_id: str):
    """
    Abort an in-progress multipart upload and mark the session aborted.

    Behavior
    --------
    - If session is missing, returns {"error": "session not found"}.
    - Attempts S3 abort using recorded UploadId; logs (but does not raise) on failure.
    - Updates session status to 'aborted'.
    """
    # Ensure a valid session exists to derive temp key and UploadId.
    session = get_session(db, upload_id)
    if not session:
        return {"error": "session not found"}

    async with Session().client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        use_ssl=settings.S3_USE_SSL,
    ) as s3:
        try:
            # Issue an abort to release storage-side resources for the multipart session.
            await s3.abort_multipart_upload(
                Bucket=settings.S3_BUCKET,
                Key=session.temp_key,
                UploadId=session.parts_json.get("s3_upload_id"),
            )
        except ClientError as e:
            # Best-effort cleanup: proceed to mark aborted even if S3 abort fails.
            logger.warning(f"Abort failed for {upload_id}: {e}")

    # Persist aborted state in the DB for audit/UX.
    mark_aborted(db, upload_id)
    return {"status": "aborted", "upload_id": upload_id}
