# app/storage/s3_client.py
# -----------------------------------------------------------------------------
# Thin wrapper around boto3 configuration for S3/MinIO access.
#
# Responsibilities
# - Provide a consistently configured boto3 S3 client (timeouts, retries, path
#   addressing) using environment-driven settings.
# - Offer a lightweight health check that verifies connectivity/credentials and,
#   optionally, bucket access.
#
# Notes for maintainers
# - We use "path" addressing for broader compatibility with MinIO and local S3
#   emulators. If switching to AWS with strict virtual-hosted–style requirements,
#   revisit the Config.
# - Timeouts are intentionally short to keep API responsiveness; adjust if your
#   environment has higher latency.
# - Region defaults to "us-east-1" which is broadly accepted by S3-compatible
#   endpoints; MinIO ignores region by default.
# - `s3_health` returns None when healthy and a short error string otherwise;
#   callers can surface this in readiness probes or admin dashboards.
# -----------------------------------------------------------------------------

from typing import Optional
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from ..settings import settings

def get_s3_client():
    """Create a boto3 S3 client configured for MinIO/S3.
    Uses environment from settings. Keeps timeouts sane.

    Implementation details
    ----------------------
    - addressing_style='path' avoids virtual-hosted style bucket URLs and plays
      nicely with MinIO and localhost setups.
    - Retries: modest standard-mode retries for transient network hiccups.
    - Timeouts: conservative connect/read to prevent hanging the service.
    - Credentials/endpoint/SSL are pulled from settings for flexibility across
      dev/staging/prod.
    """
    cfg = Config(
        s3={"addressing_style": "path"},
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=3,
        read_timeout=5,
    )

    client = boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        use_ssl=settings.S3_USE_SSL,
        config=cfg,
        region_name="us-east-1",
    )
    return client


def s3_health(bucket: Optional[str] = None) -> Optional[str]:
    """Return None if healthy; otherwise a short error string.
    Checks connectivity and (if provided) bucket access.

    Behavior
    --------
    - If `bucket` is provided: HEAD the bucket to validate existence/permissions.
    - Else: list_buckets() to validate connectivity and credentials.
    - On success returns None. On failure returns a compact "<ErrorType>: <msg>".
    """
    try:
        client = get_s3_client()
        # Simple low-cost call
        if bucket:
            client.head_bucket(Bucket=bucket)
        else:
            # Fallback: list buckets to validate credentials
            client.list_buckets()
        return None
    except (ClientError, BotoCoreError) as exc:
        # Return a concise diagnostic string rather than raising; useful for
        # health endpoints that want to show a single-line status.
        return f"{type(exc).__name__}: {exc}"
