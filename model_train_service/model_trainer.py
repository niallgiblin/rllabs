from agent import Agent
import pika
import json
import httpx
import torch
import io
import logging
import hashlib
from typing import Dict, Any, List, Optional
from pathlib import Path
import tempfile

"""
Train Model Service 

Handles downloading training files (JSON configs + model weights) from MinIO
through the Upload/Download Service, triggered by RabbitMQ messages.
"""

import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment variables
UPLOAD_DOWNLOAD_SERVICE_URL = os.getenv("UPLOAD_DOWNLOAD_SERVICE_URL", "http://upload-download-service:8002")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_QUEUE = os.getenv("RABBITMQ_QUEUE", "training_jobs")
NUM_EPISODES = int(os.getenv("NUM_EPISODES", "50"))


class ArtifactDownloader:
    """
    Downloads artifacts from MinIO via the Upload/Download Service
    """
    
    def __init__(self, service_url: str, user_id: str = None):
        self.service_url = service_url
        self.user_id = user_id
    
    async def download_json(self, artifact_id: str) -> Dict[str, Any]:
        """
        Download and parse a JSON file
        
        Args:
            artifact_id: Content hash (sha256:...) of the JSON file
        
        Returns:
            Parsed JSON data as dictionary
        """
        logger.info(f"Downloading JSON artifact: {artifact_id}")
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Get presigned URL from upload/download service
            headers = {}
            if self.user_id:
                headers["X-User-Id"] = self.user_id
            
            response = await client.get(
                f"{self.service_url}/downloads/{artifact_id}",
                headers=headers,
                params={"expires_in": 3600}
            )
            response.raise_for_status()
            
            download_info = response.json()
            presigned_url = download_info["download_url"]
            
            logger.info(f"Got presigned URL, expires at: {download_info['expires_at']}")
            
            # Download actual file from MinIO
            file_response = await client.get(presigned_url)
            file_response.raise_for_status()
            
            # Parse JSON
            json_data = file_response.json()
            logger.info(f"Successfully downloaded and parsed JSON: {artifact_id}")
            
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
        logger.info(f"Downloading PyTorch model: {artifact_id}")
        
        async with httpx.AsyncClient(timeout=300.0) as client:  # Longer timeout for large files
            # Get presigned URL
            headers = {}
            if self.user_id:
                headers["X-User-Id"] = self.user_id
            
            response = await client.get(
                f"{self.service_url}/downloads/{artifact_id}",
                headers=headers,
                params={"expires_in": 3600}
            )
            response.raise_for_status()
            
            download_info = response.json()
            presigned_url = download_info["download_url"]
            file_size = download_info.get("file_size", 0)
            
            logger.info(f"Downloading {file_size / (1024**2):.2f} MB model file...")
            
            # Download file with streaming (for large files)
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
                
                # Stream download to file
                with open(save_path, "wb") as f:
                    async for chunk in file_response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
            
            logger.info(f"Successfully downloaded model to: {save_path}")
            return save_path
    
    async def download_binary(self, artifact_id: str) -> bytes:
        """
        Download any binary file as bytes
            artifact_id: Content hash (sha256:...) of the file
        
        Returns:
            Raw bytes of the file
        """
        logger.info(f"Downloading binary artifact: {artifact_id}")
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            headers = {}
            if self.user_id:
                headers["X-User-Id"] = self.user_id
            
            response = await client.get(
                f"{self.service_url}/downloads/{artifact_id}",
                headers=headers,
                params={"expires_in": 3600}
            )
            response.raise_for_status()
            
            presigned_url = response.json()["download_url"]
            
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
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {"X-User-Id": self.user_id}
            
            init_response = await client.post(
                f"{self.service_url}/uploads",
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
            
            logger.info(f"Upload session created: {upload_id} ({len(presigned_urls)} chunks)")
            
            # Step 3: Upload chunks directly to MinIO
            parts = []
            with open(filepath, "rb") as f:
                for url_data in presigned_urls:
                    part_number = url_data["part_number"]
                    url = url_data["url"]
                    
                    # Read chunk
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    
                    # Upload chunk directly to MinIO
                    logger.info(f"Uploading chunk {part_number}/{len(presigned_urls)}...")
                    chunk_response = await client.put(url, content=chunk)
                    chunk_response.raise_for_status()
                    
                    # Get ETag
                    etag = chunk_response.headers.get("ETag", "").strip('"')
                    parts.append({
                        "part_number": part_number,
                        "etag": etag
                    })
            
            # Step 4: Complete upload
            logger.info("Completing upload...")
            complete_response = await client.post(
                f"{self.service_url}/uploads/{upload_id}/complete",
                json={"parts": parts},
                headers=headers
            )
            complete_response.raise_for_status()
            
            result = complete_response.json()
            logger.info(f"Upload complete! Artifact ID: {result['artifact_id']}")
            
            return result


class TrainingJobHandler:
    
    def __init__(self):
        # Don't create downloader here create per job with user_id
        pass
    
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
        logger.info(f"Processing training job: {job_id} for user: {user_id}")
        
        # Create downloader with user_id for proper authorization
        downloader = ArtifactDownloader(
            service_url=UPLOAD_DOWNLOAD_SERVICE_URL,
            user_id=user_id
        )
        
        try:
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
            
            logger.info(f"All artifacts downloaded for job {job_id}")
            logger.info(f"Training config: {training_config}")
            logger.info(f"Dataset config: {dataset_config}")
            logger.info(f"Model path: {model_path}")
            
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
                        logger.info(f"Uploading trained model weights for job {job_id} (model_id: {model_id})")
                        try:
                            upload_result = await uploader.upload_file(
                                filepath=trained_weights_path,
                                filename=f"trained_model_{job_id}.pth",
                                artifact_type="model",
                                model_id=model_id
                            )
                            logger.info(
                                f"Trained weights uploaded successfully! "
                                f"Artifact ID: {upload_result['artifact_id']}, "
                                f"Version: {upload_result['version']}"
                            )
                        except Exception as e:
                            logger.error(f"Failed to upload trained weights: {e}", exc_info=True)
                            # Don't fail the job if upload fails - training succeeded
                    else:
                        logger.warning(
                            f"model_id not provided for job {job_id} - "
                            f"skipping trained weights upload. "
                            f"Trained weights saved locally at: {trained_weights_path}"
                        )
                finally:
                    # Clean up temporary file
                    if Path(trained_weights_path).exists():
                        try:
                            Path(trained_weights_path).unlink()
                            logger.info(f"Cleaned up temporary weights file: {trained_weights_path}")
                        except Exception as e:
                            logger.warning(f"Failed to clean up temporary file {trained_weights_path}: {e}")
            
            logger.info(f"Training job {job_id} completed successfully")
            
        except Exception as e:
            logger.error(f"Error processing training job {job_id}: {e}", exc_info=True)
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
        logger.info(f"Starting training for job {job_id}")

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
            logger.info(f"Trained weights saved to {trained_weights_path}")
            return trained_weights_path
        except Exception as e:
            logger.error(f"Failed to save trained weights: {e}")
            # Clean up temp file if it was created
            if Path(trained_weights_path).exists():
                Path(trained_weights_path).unlink()
            return None


def on_message_callback(ch, method, properties, body, handler: TrainingJobHandler):
    """
    RabbitMQ message callback
    """
    try:
        # Parse message
        message = json.loads(body)
        logger.info(f"Received message: {message}")
        
        # Process training job (async)
        import asyncio
        asyncio.run(handler.process_training_job(message))
        
        # Acknowledge message
        ch.basic_ack(delivery_tag=method.delivery_tag)
        logger.info(f"Message acknowledged: {method.delivery_tag}")
        
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        # Don't requeue on errors - acknowledge and log
        # This prevents infinite retry loops
        # In production, you might want to send to a dead letter queue instead
        ch.basic_ack(delivery_tag=method.delivery_tag)
        logger.warning(f"Message acknowledged but job failed: {method.delivery_tag}")


def start_consumer():
    """
    Start RabbitMQ consumer
    """
    logger.info("Starting RabbitMQ consumer...")
    
    # Initialize handler (downloader created per job with user_id)
    handler = TrainingJobHandler()
    
    # Connect to RabbitMQ
    rabbitmq_user = os.getenv("RABBITMQ_USER", "admin")
    rabbitmq_pass = os.getenv("RABBITMQ_PASS", "admin_password")
    rabbitmq_port = int(os.getenv("RABBITMQ_PORT", "5672"))
    
    credentials = pika.PlainCredentials(rabbitmq_user, rabbitmq_pass)
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            port=rabbitmq_port,
            credentials=credentials
        )
    )
    channel = connection.channel()
    
    # Declare queue 
    channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
    
    channel.basic_qos(prefetch_count=1)
    
    # consumer with handler
    channel.basic_consume(
        queue=RABBITMQ_QUEUE,
        on_message_callback=lambda ch, method, properties, body: 
            on_message_callback(ch, method, properties, body, handler)
    )
    
    logger.info(f"Waiting for messages on queue '{RABBITMQ_QUEUE}'...")
    logger.info("Press CTRL+C to exit")
    
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        channel.stop_consuming()
    finally:
        connection.close()
        logger.info("Connection closed")


if __name__ == "__main__":
    start_consumer()