# app/storage/integrity.py
# -----------------------------------------------------------------------------
# Integrity utilities for content verification.
#
# This module exposes helpers for computing cryptographic digests over objects
# stored in an S3-compatible blob store. The main consumer is the upload
# finalize flow, which recomputes a SHA-256 server-side and compares it to a
# client-provided hash to ensure end-to-end integrity.
#
# Notes for maintainers:
# - Uses aioboto3 (async) to stream the object body without loading it fully
#   into memory — crucial for large files.
# - Chunk size is 1 MiB per read; adjust if you need a different latency/CPU
#   profile, but keep it a multiple of 1 MiB for typical S3 performance.
# - Returns the hex-encoded digest string (lowercase) to match common tooling.
# - The imports `aiofiles` and `asyncio` are currently unused; they are often
#   kept around for parity with local-file hashing variants or future helpers.
# -----------------------------------------------------------------------------

import hashlib
import aiofiles
import asyncio

from aioboto3 import Session
from ..settings import settings

async def compute_s3_sha256(bucket: str, key: str) -> str:
    """Stream an object from S3 and compute SHA-256.

    Parameters
    ----------
    bucket : str
        Name of the bucket where the object resides.
    key : str
        Object key/path within the bucket.

    Returns
    -------
    str
        Hex-encoded SHA-256 digest of the object.

    Implementation details
    ----------------------
    - Opens an async S3 client using configured endpoint/credentials/SSL.
    - Requests the object and iterates its streaming body in 1 MiB chunks.
    - Incrementally updates the hashlib.sha256() state for each chunk.
    - Closes the stream/client via async context managers.
    """
    sha = hashlib.sha256()
    async with Session().client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        use_ssl=settings.S3_USE_SSL,
    ) as s3:
        # Get an object and read its payload as an async stream to avoid buffering.
        obj = await s3.get_object(Bucket=bucket, Key=key)
        async with obj["Body"] as stream:
            while True:
                # Read up to 1 MiB at a time; returns b'' when exhausted.
                chunk = await stream.read(1024 * 1024)
                if not chunk:
                    break
                sha.update(chunk)
    # Produce the canonical hex digest (e.g., 'a3b1...').
    return sha.hexdigest()
