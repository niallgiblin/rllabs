from agent import Agent
import pika
import pika.exceptions
import json
import httpx
import torch
import io
import hashlib
import time
import threading
from typing import Dict, Any, List, Optional
from pathlib import Path
import tempfile
import os
import sys
from datetime import datetime, timezone
import redis

# Try to import circuit breaker, but provide fallback if not available
try:
    from circuitbreaker import circuit, CircuitBreakerError
    CIRCUIT_BREAKER_AVAILABLE = True
except ImportError:
    CIRCUIT_BREAKER_AVAILABLE = False
    
    class CircuitBreakerError(Exception):
        """Fallback exception if circuitbreaker package not available"""
        pass
    
    def circuit(*args, **kwargs):
        """Fallback no-op circuit breaker decorator"""
        def decorator(func):
            return func
        return decorator

"""
Train Model Service 

Handles downloading training files (JSON configs + model weights) from MinIO
through the Upload/Download Service, triggered by RabbitMQ messages.
"""

# Try to use observability logger, fallback to basic logging
try:
    from observability import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Configuration from environment variables
UPLOAD_DOWNLOAD_SERVICE_URL = os.getenv("UPLOAD_DOWNLOAD_SERVICE_URL", "http://upload-download-service:8002")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_QUEUE = os.getenv("RABBITMQ_QUEUE", "training_jobs")
RABBITMQ_DLQ = f"{RABBITMQ_QUEUE}_dlq"
NUM_EPISODES = int(os.getenv("NUM_EPISODES", "50"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

# Detect if running in Kubernetes (KUBERNETES_SERVICE_HOST is automatically set in K8s pods)
IS_KUBERNETES = os.getenv("KUBERNETES_SERVICE_HOST") is not None
# MinIO endpoint replacement: in Kubernetes, replace localhost with internal service name
MINIO_INTERNAL_ENDPOINT = os.getenv("MINIO_INTERNAL_ENDPOINT", "minio:9000")

# Redis configuration for job status tracking
# Auto-detect environment: Kubernetes uses redis-master, Docker Compose uses redis
# Detect if running in Kubernetes (KUBERNETES_SERVICE_HOST is automatically set in K8s pods)
IS_KUBERNETES = os.getenv("KUBERNETES_SERVICE_HOST") is not None
# Use appropriate default based on environment
_DEFAULT_REDIS_HOST = "redis-master" if IS_KUBERNETES else "redis"
REDIS_HOST = os.getenv("REDIS_HOST", _DEFAULT_REDIS_HOST)
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_SENTINEL_HOSTS = os.getenv("REDIS_SENTINEL_HOSTS", "")
REDIS_SENTINEL_MASTER_NAME = os.getenv("REDIS_SENTINEL_MASTER_NAME", "mymaster")

# Global HTTP client with connection pooling
_http_client: Optional[httpx.AsyncClient] = None
_http_client_lock = threading.Lock()

def get_http_client() -> httpx.AsyncClient:
    """Get or create global HTTP client with connection pooling"""
    global _http_client
    # Check if client exists and is not closed
    if _http_client is None or _http_client.is_closed:
        with _http_client_lock:
            # Double-check after acquiring lock
            if _http_client is None or _http_client.is_closed:
                # Close old client if it exists but is closed
                if _http_client is not None:
                    try:
                        # Don't await here - just mark for cleanup
                        pass
                    except Exception:
                        pass
                _http_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(60.0, connect=10.0),
                    limits=httpx.Limits(
                        max_connections=100,  # REVERTED: Back to original (was 200 in Phase 2)
                        max_keepalive_connections=50,  # REVERTED: Back to original (was 100 in Phase 2)
                        keepalive_expiry=30.0
                    )
                )
                logger.info("HTTP client with connection pooling initialized")
    return _http_client

def get_http_client_status() -> str:
    """Check HTTP client status for health checks"""
    try:
        client = get_http_client()
        if client and not client.is_closed:
            return "online"
        return "offline"
    except Exception:
        return "offline"

# Global RabbitMQ connection for health checks
_rabbitmq_connection: Optional[pika.BlockingConnection] = None
_rabbitmq_connection_lock = threading.Lock()

def get_rabbitmq_connection_status() -> str:
    """Check RabbitMQ connection status for health checks"""
    global _rabbitmq_connection
    try:
        if _rabbitmq_connection is not None and not _rabbitmq_connection.is_closed:
            # Try to check if channel is still open
            try:
                channel = _rabbitmq_connection.channel()
                channel.close()
                return "online"
            except Exception:
                return "offline"
        return "offline"
    except Exception:
        return "offline"


# Global Redis client for job status tracking
_redis_client: Optional[redis.Redis] = None
_redis_client_lock = threading.Lock()

def get_redis_client() -> Optional[redis.Redis]:
    """Get or create Redis client for job status tracking"""
    global _redis_client
    if _redis_client is None:
        with _redis_client_lock:
            if _redis_client is None:
                try:
                    # Try Sentinel first if configured
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
                            password=REDIS_PASSWORD if REDIS_PASSWORD else None
                        )
                        
                        _redis_client = sentinel.master_for(
                            REDIS_SENTINEL_MASTER_NAME,
                            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                            db=0,
                            decode_responses=True,
                            socket_timeout=1.0,
                            socket_connect_timeout=1.0
                        )
                        
                        _redis_client.ping()
                        logger.info("Redis connected via Sentinel for job status tracking")
                    else:
                        # Fallback to direct connection
                        _redis_client = redis.Redis(
                            host=REDIS_HOST,
                            port=REDIS_PORT,
                            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                            decode_responses=True,
                            socket_timeout=1.0,
                            socket_connect_timeout=1.0
                        )
                        _redis_client.ping()
                        logger.info("Redis connected directly for job status tracking")
                except Exception as e:
                    logger.warning(f"Failed to connect to Redis: {e}. Job status tracking disabled.")
                    _redis_client = None
    return _redis_client


class JobStatusTracker:
    """Track training job status in Redis"""
    
    def __init__(self):
        self.redis = get_redis_client()
        self.ttl = 86400  # 24 hours
    
    def create_job(self, job_id: str, user_id: str, model_id: Optional[int] = None):
        """Create job status entry"""
        if not self.redis:
            return
        
        try:
            status = {
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
            self.redis.setex(
                f"job:{job_id}",
                self.ttl,
                json.dumps(status)
            )
            logger.info(
                "Created job status",
                extra={"event": "job_status_created", "job_id": job_id}
            )
        except Exception as e:
            logger.warning(f"Failed to create job status: {e}")
    
    def update_status(self, job_id: str, status: str, progress: float = None, error: str = None):
        """Update job status"""
        if not self.redis:
            return
        
        try:
            key = f"job:{job_id}"
            data = self.redis.get(key)
            if not data:
                logger.warning(f"Job {job_id} not found in status tracker")
                return
            
            job = json.loads(data)
            job["status"] = status
            
            if progress is not None:
                job["progress"] = progress
            
            if error:
                job["error"] = error
            
            if status == "processing" and not job.get("started_at"):
                job["started_at"] = datetime.now(timezone.utc).isoformat()
            
            if status in ["completed", "failed"]:
                job["completed_at"] = datetime.now(timezone.utc).isoformat()
            
            self.redis.setex(key, self.ttl, json.dumps(job))
            logger.info(
                f"Updated job status",
                extra={
                    "event": "job_status_updated",
                    "job_id": job_id,
                    "status": status,
                    "progress": progress
                }
            )
        except Exception as e:
            logger.warning(f"Failed to update job status: {e}")
    
    def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job status"""
        if not self.redis:
            return None
        
        try:
            data = self.redis.get(f"job:{job_id}")
            return json.loads(data) if data else None
        except Exception as e:
            logger.warning(f"Failed to get job status: {e}")
            return None


class ResilientRabbitMQConnection:
    """RabbitMQ connection with automatic retry and reconnection"""
    
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        queue: str,
        max_retries: int = 10,
        retry_delay: int = 5
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.queue = queue
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._connection: Optional[pika.BlockingConnection] = None
        self._channel: Optional[pika.channel.Channel] = None
        self._should_stop = False
    
    def connect(self):
        """Connect with retry logic and exponential backoff"""
        global _rabbitmq_connection
        
        for attempt in range(self._max_retries):
            if self._should_stop:
                logger.info("Connection attempt cancelled (shutdown requested)")
                return
                
            try:
                credentials = pika.PlainCredentials(self.username, self.password)
                parameters = pika.ConnectionParameters(
                    host=self.host,
                    port=self.port,
                    credentials=credentials,
                    heartbeat=600,
                    blocked_connection_timeout=300,
                    connection_attempts=1,
                    retry_delay=0  # We handle retries ourselves
                )
                self._connection = pika.BlockingConnection(parameters)
                self._channel = self._connection.channel()
                
                # Store globally for health checks
                with _rabbitmq_connection_lock:
                    _rabbitmq_connection = self._connection
                
                logger.info(
                    f"Connected to RabbitMQ",
                    extra={
                        "event": "rabbitmq_connected",
                        "host": self.host,
                        "port": self.port,
                        "queue": self.queue,
                        "attempt": attempt + 1
                    }
                )
                return
            except Exception as e:
                if attempt < self._max_retries - 1:
                    delay = min(self._retry_delay * (2 ** attempt), 60)  # Exponential backoff, max 60s
                    logger.warning(
                        f"RabbitMQ connection failed, retrying in {delay}s",
                        extra={
                            "event": "rabbitmq_connection_retry",
                            "attempt": attempt + 1,
                            "max_retries": self._max_retries,
                            "error": str(e)
                        }
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Failed to connect to RabbitMQ after {self._max_retries} attempts",
                        extra={
                            "event": "rabbitmq_connection_failed",
                            "error": str(e)
                        }
                    )
                    raise
    
    def ensure_connected(self):
        """Ensure connection is alive, reconnect if needed"""
        if self._connection is None or self._connection.is_closed:
            logger.warning("RabbitMQ connection lost, reconnecting...")
            self.connect()
    
    def _cleanup_connection(self):
        """Safely cleanup connection state, handling pika internal errors"""
        if self._channel:
            try:
                if not self._channel.is_closed:
                    self._channel.stop_consuming()
            except (IndexError, AttributeError) as e:
                # Handle pika internal deque errors during cleanup
                logger.debug(f"Ignoring pika channel cleanup error (likely deque issue): {e}")
            except Exception as e:
                logger.debug(f"Error stopping channel consumption: {e}")
        
        if self._connection:
            try:
                if not self._connection.is_closed:
                    self._connection.close()
            except (IndexError, AttributeError) as e:
                # Handle pika internal deque errors during cleanup
                logger.debug(f"Ignoring pika connection cleanup error (likely deque issue): {e}")
            except Exception as e:
                logger.debug(f"Error closing connection: {e}")
        
        # Reset state
        self._connection = None
        self._channel = None
    
    def setup_queue_with_dlq(self):
        """Setup queue with dead letter queue"""
        # Declare DLQ first
        try:
            self._channel.queue_declare(
                queue=RABBITMQ_DLQ,
                durable=True,
                arguments={
                    'x-message-ttl': 86400000,  # 24 hours
                    'x-max-length': 10000  # Max 10k messages
                }
            )
        except Exception as e:
            logger.debug(f"DLQ may already exist: {e}")
        
        # Check if main queue exists by trying passive declaration
        queue_exists = False
        try:
            method = self._channel.queue_declare(queue=self.queue, passive=True, durable=True)
            queue_exists = True
            logger.info(
                f"Queue already exists",
                extra={"event": "queue_exists", "queue": self.queue, "message_count": method.method.message_count}
            )
        except pika.exceptions.ChannelClosedByBroker as e:
            # Queue doesn't exist or different args - need to recreate
            queue_exists = False
            logger.info(f"Queue {self.queue} doesn't exist or has different configuration, will create with DLQ")
        except Exception as e:
            queue_exists = False
            logger.debug(f"Queue check failed: {e}")
        
        # If queue doesn't exist, create it with DLQ
        if not queue_exists:
            try:
                self._channel.queue_declare(
                    queue=self.queue,
                    durable=True,
                    arguments={
                        'x-dead-letter-exchange': '',
                        'x-dead-letter-routing-key': RABBITMQ_DLQ,
                        'x-max-priority': 10  # Support priority
                    }
                )
                logger.info(
                    f"Queue created with DLQ",
                    extra={
                        "event": "queue_dlq_configured",
                        "queue": self.queue,
                        "dlq": RABBITMQ_DLQ
                    }
                )
            except Exception as e:
                logger.error(f"Failed to create queue with DLQ: {e}")
                # Fallback: just declare without DLQ args
                try:
                    self._channel.queue_declare(queue=self.queue, durable=True)
                except Exception as e2:
                    logger.error(f"Failed to create queue: {e2}")
        else:
            # Queue exists - just declare it without args (won't change existing args)
            # This ensures the queue is available but doesn't modify it
            try:
                self._channel.queue_declare(queue=self.queue, durable=True)
                logger.info(f"Queue {self.queue} is ready (existing queue, DLQ not configured)")
            except Exception as e:
                logger.warning(f"Queue declaration failed: {e}")
    
    def start_consuming_with_retry(self, callback):
        """Start consuming with automatic reconnection on failures"""
        while not self._should_stop:
            try:
                self.ensure_connected()
                
                # Setup queue with DLQ
                self.setup_queue_with_dlq()
                
                # Set QoS - allow multiple unacked messages for better throughput
                # With async processing, we can handle multiple jobs concurrently
                # Increased from 1 to 3 to allow concurrent processing while preventing overload
                self._channel.basic_qos(prefetch_count=3)
                
                # Set up consumer with callback
                self._channel.basic_consume(
                    queue=self.queue,
                    on_message_callback=callback
                )
                
                logger.info(
                    f"Started consuming from queue: {self.queue}",
                    extra={"event": "rabbitmq_consumer_started", "queue": self.queue}
                )
                
                # Start consuming (blocks until connection lost or stopped)
                self._channel.start_consuming()
                
            except pika.exceptions.AMQPConnectionError as e:
                if self._should_stop:
                    logger.info("Consumer stopped (shutdown requested)")
                    break
                logger.error(
                    f"RabbitMQ connection error, reconnecting...",
                    extra={"event": "rabbitmq_reconnecting", "error": str(e)}
                )
                # Clean up connection state safely
                self._cleanup_connection()
                time.sleep(5)  # Wait before reconnecting
            except KeyboardInterrupt:
                logger.info("Consumer interrupted by user")
                break
            except Exception as e:
                if self._should_stop:
                    logger.info("Consumer stopped (shutdown requested)")
                    break
                # Handle pika deque errors gracefully
                if isinstance(e, IndexError) and 'deque' in str(e).lower():
                    logger.debug(f"Pika internal deque error (likely during connection cleanup): {e}")
                else:
                    logger.error(
                        f"Unexpected error in consumer, reconnecting...",
                        extra={"event": "rabbitmq_consumer_error", "error": str(e)},
                        exc_info=True
                    )
                # Clean up connection state safely
                self._cleanup_connection()
                time.sleep(5)
        
        # Cleanup - handle pika internal deque errors gracefully
        if self._channel:
            try:
                if not self._channel.is_closed:
                    self._channel.stop_consuming()
            except (IndexError, AttributeError) as e:
                # Handle pika internal deque errors during cleanup
                logger.debug(f"Ignoring pika cleanup error (likely deque issue): {e}")
            except Exception as e:
                logger.debug(f"Error stopping channel consumption: {e}")
        
        if self._connection:
            try:
                if not self._connection.is_closed:
                    self._connection.close()
            except (IndexError, AttributeError) as e:
                # Handle pika internal deque errors during cleanup
                logger.debug(f"Ignoring pika cleanup error (likely deque issue): {e}")
            except Exception as e:
                logger.debug(f"Error closing connection: {e}")
        
        logger.info("RabbitMQ consumer stopped")
    
    def stop(self):
        """Stop the consumer gracefully"""
        self._should_stop = True
        if self._channel:
            try:
                if not self._channel.is_closed:
                    self._channel.stop_consuming()
            except (IndexError, AttributeError) as e:
                # Handle pika internal deque errors during cleanup
                logger.debug(f"Ignoring pika cleanup error (likely deque issue): {e}")
            except Exception as e:
                logger.debug(f"Error stopping channel: {e}")


class ArtifactDownloader:
    """
    Downloads artifacts from MinIO via the Upload/Download Service
    """
    
    def __init__(self, service_url: str, user_id: str = None):
        self.service_url = service_url
        self.user_id = user_id
    
    @circuit(failure_threshold=5, recovery_timeout=30, expected_exception=httpx.RequestError)
    async def _download_with_circuit_breaker(self, url: str, headers: dict, params: dict = None):
        """Download with circuit breaker protection"""
        client = get_http_client()
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response
    
    async def download_json(self, artifact_id: str) -> Dict[str, Any]:
        """
        Download and parse a JSON file
        
        Args:
            artifact_id: Content hash (sha256:...) of the JSON file
        
        Returns:
            Parsed JSON data as dictionary
        """
        logger.info(
            f"Downloading JSON artifact",
            extra={"artifact_id": artifact_id, "artifact_type": "json"}
        )
        
        # Get presigned URL from upload/download service (with circuit breaker)
        headers = {}
        if self.user_id:
            headers["X-User-Id"] = self.user_id
        
        try:
            # Always request internal endpoint URL for service-to-service access
            # In both Docker Compose and Kubernetes, services need minio:9000 (not localhost:9000)
            # localhost:9000 only works for browsers running on the host machine
            params = {"expires_in": 3600, "internal": "true"}
            response = await self._download_with_circuit_breaker(
                f"{self.service_url}/downloads/{artifact_id}",
                headers=headers,
                params=params
            )
        except CircuitBreakerError:
            logger.error(
                f"Circuit breaker open for upload/download service",
                extra={"artifact_id": artifact_id, "event": "circuit_breaker_open"}
            )
            raise
        except httpx.RequestError as e:
            logger.error(
                f"HTTP request failed",
                extra={"artifact_id": artifact_id, "error": str(e), "event": "download_failed"}
            )
            raise
        
        download_info = response.json()
        presigned_url = download_info["download_url"]
        
        # No need to replace hostname - URL is already generated with correct endpoint
        # (internal endpoint since internal=true was requested)
        
        logger.info(
            f"Got presigned URL",
            extra={
                "artifact_id": artifact_id,
                "expires_at": download_info.get("expires_at"),
                "url_preview": presigned_url[:100]  # Log URL preview for debugging
            }
        )
        
        # Download actual file from MinIO (with circuit breaker)
        try:
            file_response = await self._download_with_circuit_breaker(presigned_url, {})
        except CircuitBreakerError:
            logger.error(
                f"Circuit breaker open for MinIO",
                extra={"artifact_id": artifact_id, "event": "circuit_breaker_open"}
            )
            raise
        except httpx.RequestError as e:
            logger.error(
                f"MinIO download failed",
                extra={"artifact_id": artifact_id, "error": str(e), "event": "download_failed"}
            )
            raise
        
        # Parse JSON
        json_data = file_response.json()
        logger.info(
            f"Successfully downloaded and parsed JSON",
            extra={"artifact_id": artifact_id}
        )
        
        return json_data
    
    async def download_pytorch_model(self, artifact_id: str, save_path: str = None) -> str:
        """
        Download a PyTorch .pth file
        
        Args:
            artifact_id: Content hash (sha256:...) of the .pth file
            save_path: Optional path to save the file. If None, saves to temp file.
        
        Returns:
            Path to the downloaded .pth file
        """
        logger.info(
            f"Downloading PyTorch model",
            extra={"artifact_id": artifact_id, "artifact_type": "pytorch_model"}
        )
        
        # Get presigned URL (with circuit breaker)
        headers = {}
        if self.user_id:
            headers["X-User-Id"] = self.user_id
        
        try:
            # Always request internal endpoint URL for service-to-service access
            # In both Docker Compose and Kubernetes, services need minio:9000 (not localhost:9000)
            # localhost:9000 only works for browsers running on the host machine
            params = {"expires_in": 3600, "internal": "true"}
            response = await self._download_with_circuit_breaker(
                f"{self.service_url}/downloads/{artifact_id}",
                headers=headers,
                params=params
            )
        except CircuitBreakerError:
            logger.error(
                f"Circuit breaker open for upload/download service",
                extra={"artifact_id": artifact_id, "event": "circuit_breaker_open"}
            )
            raise
        except httpx.RequestError as e:
            logger.error(
                f"HTTP request failed",
                extra={"artifact_id": artifact_id, "error": str(e), "event": "download_failed"}
            )
            raise
        
        download_info = response.json()
        presigned_url = download_info["download_url"]
        file_size = download_info.get("file_size", 0)
        
        # No need to replace hostname - URL is already generated with correct endpoint
        # (internal endpoint since internal=true was requested)
        
        logger.info(
            f"Downloading model file",
            extra={
                "artifact_id": artifact_id,
                "file_size_mb": round(file_size / (1024**2), 2)
            }
        )
        
        # Download file with streaming (for large files)
        client = get_http_client()
        try:
            async with client.stream("GET", presigned_url) as file_response:
                file_response.raise_for_status()
                
                # Determine save path
                if save_path is None:
                    # Create temp file
                    temp_file = tempfile.NamedTemporaryFile(
                        delete=False, 
                        suffix=".pth"
                    )
                    save_path = temp_file.name
                    temp_file.close()
                
                # Stream download to file (async I/O)
                try:
                    import aiofiles
                    async with aiofiles.open(save_path, "wb") as f:
                        async for chunk in file_response.aiter_bytes(chunk_size=8192):
                            await f.write(chunk)
                except ImportError:
                    # Fallback: use sync file I/O with asyncio.to_thread (slower)
                    def write_file():
                        with open(save_path, "wb") as f:
                            for chunk in file_response.iter_bytes(chunk_size=8192):
                                f.write(chunk)
                    await asyncio.to_thread(write_file)
        except httpx.RequestError as e:
            logger.error(
                f"MinIO download failed",
                extra={"artifact_id": artifact_id, "error": str(e), "event": "download_failed"}
            )
            raise
        
        logger.info(
            f"Successfully downloaded model",
            extra={"artifact_id": artifact_id, "save_path": save_path}
        )
        return save_path
    
    async def download_binary(self, artifact_id: str) -> bytes:
        """
        Download any binary file as bytes
            artifact_id: Content hash (sha256:...) of the file
        
        Returns:
            Raw bytes of the file
        """
        logger.info(
            f"Downloading binary artifact",
            extra={"artifact_id": artifact_id, "artifact_type": "binary"}
        )
        
        client = get_http_client()
        
        headers = {}
        if self.user_id:
            headers["X-User-Id"] = self.user_id
        
        # Always request internal endpoint URL for service-to-service access
        # In both Docker Compose and Kubernetes, services need minio:9000 (not localhost:9000)
        # localhost:9000 only works for browsers running on the host machine
        params = {"expires_in": 3600, "internal": "true"}
        response = await client.get(
            f"{self.service_url}/downloads/{artifact_id}",
            headers=headers,
            params=params
        )
        response.raise_for_status()
        
        presigned_url = response.json()["download_url"]
        
        # No need to replace hostname - URL is already generated with internal endpoint
        
        file_response = await client.get(presigned_url)
        file_response.raise_for_status()
        
        return file_response.content


class ArtifactUploader:
    """
    Uploads artifacts to MinIO via the Upload/Download Service
    """
    
    def __init__(self, service_url: str, user_id: str):
        self.service_url = service_url
        self.user_id = user_id
    
    def calculate_sha256(self, filepath: str) -> str:
        """
        Calculate SHA-256 hash of a file
        
        Args:
            filepath: Path to the file
            
        Returns:
            SHA-256 hash with 'sha256:' prefix
        """
        logger.info(f"Calculating SHA-256 hash of {filepath}")
        sha256_hash = hashlib.sha256()
        
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(8192), b""):
                sha256_hash.update(byte_block)
        
        hash_value = sha256_hash.hexdigest()
        return f"sha256:{hash_value}"
    
    async def upload_file(
        self,
        filepath: str,
        filename: str,
        artifact_type: str = "model",
        model_id: Optional[int] = None,
        chunk_size: int = 5242880  # 5MB
    ) -> Dict[str, Any]:
        """
        Upload a file using multipart upload
        
        Args:
            filepath: Path to the file to upload
            filename: Original filename
            artifact_type: Type of artifact (e.g., "model", "config")
            model_id: ID of the parent model (required for model artifacts)
            chunk_size: Size of each chunk in bytes (default: 5MB)
            
        Returns:
            Upload result with artifact_id, version, etc.
        """
        if model_id is None and artifact_type == "model":
            raise ValueError("model_id is required for model artifacts")
        
        logger.info(f"Uploading {filename} ({Path(filepath).stat().st_size} bytes)")
        
        # Step 1: Calculate file hash
        file_size = Path(filepath).stat().st_size
        file_hash = self.calculate_sha256(filepath)
        logger.info(f"File hash: {file_hash}")
        
        # Step 2: Initiate upload session
        client = get_http_client()
        headers = {"X-User-Id": self.user_id}
        
        # Request internal endpoint URLs for service-to-service access
        # In both Docker Compose and Kubernetes, services need minio:9000 (not localhost:9000)
        init_response = await client.post(
            f"{self.service_url}/uploads?internal=true",
            json={
                "filename": filename,
                "file_size": file_size,
                "file_hash": file_hash,
                "chunk_size": chunk_size,
                "artifact_type": artifact_type,
                "model_id": model_id
            },
            headers=headers
        )
        init_response.raise_for_status()
        
        init_data = init_response.json()
        upload_id = init_data["upload_id"]
        presigned_urls = init_data["presigned_urls"]
        
        logger.info(
            f"Upload session created",
            extra={
                "upload_id": upload_id,
                "chunk_count": len(presigned_urls),
                "file_name": filename  # Changed from 'filename' to avoid LogRecord conflict
            }
        )
        
        # Step 3: Upload chunks directly to MinIO (async file I/O)
        parts = []
        try:
            import aiofiles
            async with aiofiles.open(filepath, "rb") as f:
                for url_data in presigned_urls:
                    part_number = url_data["part_number"]
                    url = url_data["url"]
                    
                    # Read chunk (async)
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    
                    # Upload chunk directly to MinIO
                    logger.debug(
                        f"Uploading chunk",
                        extra={
                            "part_number": part_number,
                            "total_chunks": len(presigned_urls),
                            "upload_id": upload_id
                        }
                    )
                    chunk_response = await client.put(url, content=chunk)
                    chunk_response.raise_for_status()
                    
                    # Get ETag
                    etag = chunk_response.headers.get("ETag", "").strip('"')
                    parts.append({
                        "part_number": part_number,
                        "etag": etag
                    })
        except ImportError:
            # Fallback: use sync file I/O with asyncio.to_thread (slower)
            def read_and_upload_chunks():
                chunks_data = []
                with open(filepath, "rb") as f:
                    for url_data in presigned_urls:
                        part_number = url_data["part_number"]
                        url = url_data["url"]
                        
                        # Read chunk
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        chunks_data.append((part_number, url, chunk))
                return chunks_data
            
            chunks_data = await asyncio.to_thread(read_and_upload_chunks)
            
            # Upload chunks (async HTTP, but chunks already read)
            for part_number, url, chunk in chunks_data:
                logger.debug(
                    f"Uploading chunk",
                    extra={
                        "part_number": part_number,
                        "total_chunks": len(presigned_urls),
                        "upload_id": upload_id
                    }
                )
                chunk_response = await client.put(url, content=chunk)
                chunk_response.raise_for_status()
                
                # Get ETag
                etag = chunk_response.headers.get("ETag", "").strip('"')
                parts.append({
                    "part_number": part_number,
                    "etag": etag
                })
        
        # Step 4: Complete upload
        logger.info("Completing upload", extra={"upload_id": upload_id})
        complete_response = await client.post(
            f"{self.service_url}/uploads/{upload_id}/complete",
            json={"parts": parts},
            headers=headers
        )
        complete_response.raise_for_status()
        
        result = complete_response.json()
        logger.info(
            f"Upload complete",
            extra={
                "upload_id": upload_id,
                "artifact_id": result.get("artifact_id"),
                "version": result.get("version")
            }
        )
        
        return result


class TrainingJobHandler:
    
    def __init__(self):
        # Don't create downloader here create per job with user_id
        self.job_tracker = JobStatusTracker()
    
    async def process_training_job(self, message: Dict[str, Any]):
        """     
        Expected message format:
        {
            "job_id": "job-123",
            "config_artifact_id": "sha256:abc...",      # Training config JSON
            "dataset_artifact_id": "sha256:def...",     # Dataset metadata JSON
            "model_artifact_id": "sha256:ghi...",       # Pre-trained model .pth
            "user_id": "user-456",
            "model_id": 123                              # Optional: Parent model ID for trained weights
        }
        """
        job_id = message.get("job_id")
        user_id = message.get("user_id")
        model_id = message.get("model_id")
        
        # Create job status entry
        self.job_tracker.create_job(job_id, user_id, model_id)
        
        logger.info(
            f"Processing training job",
            extra={
                "event": "training_job_started",
                "job_id": job_id,
                "user_id": user_id,
                "model_id": model_id
            }
        )
        
        # Update status to processing
        self.job_tracker.update_status(job_id, "processing", progress=0.0)
        
        # Create downloader with user_id for proper authorization
        downloader = ArtifactDownloader(
            service_url=UPLOAD_DOWNLOAD_SERVICE_URL,
            user_id=user_id
        )
        
        try:
            # Validate artifacts exist (now in worker, not API endpoint)
            # This prevents wasting time downloading non-existent artifacts
            artifact_ids = [
                ("config", message["config_artifact_id"]),
                ("dataset", message["dataset_artifact_id"]),
                ("model", message["model_artifact_id"])
            ]
            
            client = get_http_client()
            for name, artifact_id in artifact_ids:
                try:
                    # Check existence without downloading (HEAD request)
                    # For training jobs, we allow using artifacts if user has access to the training job's model
                    # Pass model_id as query parameter to allow cross-model artifact usage for training
                    params = {}
                    if model_id:
                        params["model_id"] = model_id  # Allow access if user has access to training job's model
                    response = await client.head(
                        f"{UPLOAD_DOWNLOAD_SERVICE_URL}/downloads/{artifact_id}",
                        headers={"X-User-Id": user_id},
                        params=params
                    )
                    if response.status_code != 200:
                        error_msg = f"{name} artifact not found: {artifact_id}"
                        self.job_tracker.update_status(
                            job_id, "failed", 
                            error=error_msg
                        )
                        logger.error(f"Job {job_id} failed validation: {error_msg}")
                        return  # Early exit
                except Exception as e:
                    error_msg = f"Artifact validation failed: {name} - {str(e)}"
                    self.job_tracker.update_status(
                        job_id, "failed", 
                        error=error_msg
                    )
                    logger.error(f"Job {job_id} failed validation: {error_msg}")
                    return  # Early exit
            
            logger.info(
                f"All artifacts validated",
                extra={
                    "job_id": job_id,
                    "event": "artifacts_validated"
                }
            )
            
            # Download all artifacts in parallel (for efficiency)
            import asyncio
            
            config_task = downloader.download_json(
                message["config_artifact_id"]
            )
            dataset_task = downloader.download_json(
                message["dataset_artifact_id"]
            )
            model_task = downloader.download_pytorch_model(
                message["model_artifact_id"]
            )
            
            # Wait for all downloads to complete
            training_config, dataset_config, model_path = await asyncio.gather(
                config_task,
                dataset_task,
                model_task
            )
            
            logger.info(
                f"All artifacts downloaded",
                extra={
                    "job_id": job_id,
                    "config_artifact_id": message.get("config_artifact_id"),
                    "dataset_artifact_id": message.get("dataset_artifact_id"),
                    "model_artifact_id": message.get("model_artifact_id")
                }
            )
            
            # Create uploader for uploading trained weights
            uploader = ArtifactUploader(
                service_url=UPLOAD_DOWNLOAD_SERVICE_URL,
                user_id=user_id
            )
            
            # Now use the files in your training function
            trained_weights_path = await self.run_training(
                training_config=training_config,
                dataset_config=dataset_config,
                model_path=model_path,
                job_id=job_id,
                uploader=uploader,
                model_id=model_id
            )
            
            # Upload trained weights if training succeeded
            if trained_weights_path:
                try:
                    if model_id:
                        logger.info(
                            f"Uploading trained model weights",
                            extra={
                                "job_id": job_id,
                                "model_id": model_id,
                                "event": "uploading_trained_weights"
                            }
                        )
                        try:
                            upload_result = await uploader.upload_file(
                                filepath=trained_weights_path,
                                filename=f"trained_model_{job_id}.pth",
                                artifact_type="model",
                                model_id=model_id
                            )
                            logger.info(
                                f"Trained weights uploaded successfully",
                                extra={
                                    "job_id": job_id,
                                    "artifact_id": upload_result.get("artifact_id"),
                                    "version": upload_result.get("version"),
                                    "event": "trained_weights_uploaded"
                                }
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to upload trained weights",
                                extra={
                                    "job_id": job_id,
                                    "model_id": model_id,
                                    "error": str(e),
                                    "event": "upload_failed"
                                },
                                exc_info=True
                            )
                            # Don't fail the job if upload fails - training succeeded
                    else:
                        logger.warning(
                            f"model_id not provided, skipping trained weights upload",
                            extra={
                                "job_id": job_id,
                                "save_path": trained_weights_path
                            }
                        )
                finally:
                    # Clean up temporary file
                    if Path(trained_weights_path).exists():
                        try:
                            Path(trained_weights_path).unlink()
                            logger.debug(
                                f"Cleaned up temporary weights file",
                                extra={"job_id": job_id, "file_path": trained_weights_path}
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to clean up temporary file",
                                extra={"job_id": job_id, "file_path": trained_weights_path, "error": str(e)}
                            )
            
            # Update status to completed
            self.job_tracker.update_status(job_id, "completed", progress=1.0)
            
            logger.info(
                f"Training job completed successfully",
                extra={
                    "job_id": job_id,
                    "event": "training_job_completed"
                }
            )
            
        except Exception as e:
            # Update status to failed
            error_msg = str(e)
            self.job_tracker.update_status(job_id, "failed", error=error_msg)
            
            logger.error(
                f"Error processing training job",
                extra={
                    "job_id": job_id,
                    "error": error_msg,
                    "event": "training_job_failed"
                },
                exc_info=True
            )
            raise
    
    async def run_training(
        self,
        training_config: Dict[str, Any],
        dataset_config: Dict[str, Any],
        model_path: str,
        job_id: str,
        uploader: ArtifactUploader,
        model_id: Optional[int] = None
    ) -> Optional[str]:
        """
        Run training and save trained weights
        
        Args:
            training_config: Parsed DQN model architecture configuration JSON (dqn_config)
            dataset_config: Parsed maze/grid configuration JSON (grid_params)
            model_path: Path to the downloaded .pth file
            job_id: Job identifier
            uploader: ArtifactUploader instance for uploading trained weights
            model_id: Optional model ID for the trained weights
            
        Returns:
            Path to saved trained weights file, or None if training failed
        """
        logger.info(
            f"Starting training",
            extra={
                "job_id": job_id,
                "num_episodes": NUM_EPISODES,
                "event": "training_started"
            }
        )

        # Agent expects: (grid_params, dqn_config, model_weights_path)
        # grid_params = dataset_config (maze/grid configuration)
        # dqn_config = training_config (model architecture configuration)
        agent = Agent(dataset_config, training_config, model_path)
        agent.train_step(num_episodes=NUM_EPISODES)
        
        # Save trained weights to temporary file
        trained_weights_path = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=f"_{job_id}.pth",
            prefix="trained_weights_"
        ).name
        
        try:
            agent.save_weights(trained_weights_path)
            logger.info(
                f"Trained weights saved",
                extra={
                    "job_id": job_id,
                    "save_path": trained_weights_path,
                    "event": "weights_saved"
                }
            )
            return trained_weights_path
        except Exception as e:
            logger.error(
                f"Failed to save trained weights",
                extra={
                    "job_id": job_id,
                    "error": str(e),
                    "event": "save_weights_failed"
                },
                exc_info=True
            )
            # Clean up temp file if it was created
            if Path(trained_weights_path).exists():
                Path(trained_weights_path).unlink()
            return None


def on_message_callback(ch, method, properties, body, handler: TrainingJobHandler):
    """
    RabbitMQ message callback with retry logic and DLQ support
    
    Uses thread pool executor to run async code without blocking the consumer thread.
    This allows RabbitMQ to continue processing messages and handle connection issues.
    """
    job_id = None
    try:
        # Parse message
        message = json.loads(body)
        job_id = message.get("job_id", "unknown")
        
        # Get retry count from headers
        retry_count = 0
        if properties.headers:
            retry_count = properties.headers.get('x-retry-count', 0)
        
        logger.info(
            f"Received training job message",
            extra={
                "event": "message_received",
                "job_id": job_id,
                "delivery_tag": method.delivery_tag,
                "retry_count": retry_count
            }
        )
        
        # Process training job (async)
        # Note: Callback blocks until job completes, but with prefetch_count=3,
        # RabbitMQ can deliver multiple messages for concurrent processing.
        # The async I/O operations inside process_training_job() allow concurrent downloads/uploads.
        # Using asyncio.run() creates a new event loop for this thread.
        # IMPORTANT: httpx.AsyncClient must be created in the same event loop where it's used.
        # We reset the global client before each asyncio.run() to ensure it's created in the
        # correct event loop context.
        import asyncio
        # Reset HTTP client before creating new event loop
        # This ensures the client is created fresh in the new loop's context
        global _http_client
        with _http_client_lock:
            _http_client = None  # Force recreation in new event loop
        try:
            asyncio.run(handler.process_training_job(message))
        finally:
            # Clean up: reset client after loop closes to ensure fresh client for next job
            with _http_client_lock:
                _http_client = None
        
        # Acknowledge message on success
        ch.basic_ack(delivery_tag=method.delivery_tag)
        logger.info(
            f"Message acknowledged",
            extra={
                "event": "message_acknowledged",
                "job_id": job_id,
                "delivery_tag": method.delivery_tag
            }
        )
        
    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"Error processing message",
            extra={
                "event": "message_processing_failed",
                "job_id": job_id,
                "delivery_tag": method.delivery_tag,
                "retry_count": retry_count,
                "error": error_msg
            },
            exc_info=True
        )
        
        # Get retry count
        retry_count = 0
        if properties.headers:
            retry_count = properties.headers.get('x-retry-count', 0)
        
        if retry_count < MAX_RETRIES:
            # Retry with incremented count
            new_headers = properties.headers.copy() if properties.headers else {}
            new_headers['x-retry-count'] = retry_count + 1
            
            # Reject and requeue
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            logger.info(
                f"Retrying job",
                extra={
                    "event": "job_retry",
                    "job_id": job_id,
                    "retry_count": retry_count + 1,
                    "max_retries": MAX_RETRIES
                }
            )
        else:
            # Send to DLQ after max retries
            try:
                ch.basic_publish(
                    exchange='',
                    routing_key=RABBITMQ_DLQ,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,  # Persistent
                        headers={
                            'original_queue': RABBITMQ_QUEUE,
                            'failed_at': datetime.now(timezone.utc).isoformat(),
                            'retry_count': retry_count,
                            'error': error_msg
                        }
                    )
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)
                logger.warning(
                    f"Job sent to DLQ after max retries",
                    extra={
                        "event": "job_sent_to_dlq",
                        "job_id": job_id,
                        "retry_count": retry_count,
                        "dlq": RABBITMQ_DLQ
                    }
                )
            except Exception as dlq_error:
                # If DLQ publish fails, still ack to prevent infinite loop
                logger.error(
                    f"Failed to send job to DLQ, acknowledging anyway",
                    extra={
                        "event": "dlq_publish_failed",
                        "job_id": job_id,
                        "error": str(dlq_error)
                    },
                    exc_info=True
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)


def start_consumer():
    """
    Start RabbitMQ consumer with resilient connection and automatic reconnection
    """
    logger.info(
        "Starting RabbitMQ consumer",
        extra={
            "event": "consumer_starting",
            "host": RABBITMQ_HOST,
            "queue": RABBITMQ_QUEUE
        }
    )
    
    # Initialize handler (downloader created per job with user_id)
    handler = TrainingJobHandler()
    
    # Get RabbitMQ connection parameters
    rabbitmq_user = os.getenv("RABBITMQ_USER", "admin")
    rabbitmq_pass = os.getenv("RABBITMQ_PASS", "admin_password")
    rabbitmq_port = int(os.getenv("RABBITMQ_PORT", "5672"))
    
    # Create resilient connection
    resilient_connection = ResilientRabbitMQConnection(
        host=RABBITMQ_HOST,
        port=rabbitmq_port,
        username=rabbitmq_user,
        password=rabbitmq_pass,
        queue=RABBITMQ_QUEUE,
        max_retries=10,
        retry_delay=5
    )
    
    # Create callback wrapper
    def callback_wrapper(ch, method, properties, body):
        on_message_callback(ch, method, properties, body, handler)
    
    # Start consuming with automatic reconnection
    try:
        resilient_connection.start_consuming_with_retry(callback_wrapper)
    except KeyboardInterrupt:
        logger.info("Consumer interrupted by user")
        resilient_connection.stop()
    except Exception as e:
        logger.error(
            f"Consumer stopped with error",
            extra={"event": "consumer_stopped", "error": str(e)},
            exc_info=True
        )
        resilient_connection.stop()


if __name__ == "__main__":
    start_consumer()