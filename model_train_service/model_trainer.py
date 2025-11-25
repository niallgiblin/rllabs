from agent import Agent
import pika
import json
import httpx
import torch
import io
import logging
from typing import Dict, Any, List
from pathlib import Path
import tempfile

"""
Train Model Service 

Handles downloading training files (JSON configs + model weights) from MinIO
through the Upload/Download Service, triggered by RabbitMQ messages.
"""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
UPLOAD_DOWNLOAD_SERVICE_URL = "http://upload-download-service:8002"
RABBITMQ_HOST = "rabbitmq"
RABBITMQ_QUEUE = "training_jobs"
NUM_EPISODES = 50


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


class TrainingJobHandler:
    
    def __init__(self, downloader: ArtifactDownloader):
        self.downloader = downloader
    
    async def process_training_job(self, message: Dict[str, Any]):
        """     
        Expected message format:
        {
            "job_id": "job-123",
            "config_artifact_id": "sha256:abc...",      # Training config JSON
            "dataset_artifact_id": "sha256:def...",     # Dataset metadata JSON
            "model_artifact_id": "sha256:ghi...",       # Pre-trained model .pth
            "user_id": "user-456"
        }
        """
        job_id = message.get("job_id")
        logger.info(f"Processing training job: {job_id}")
        
        try:
            # Download all artifacts in parallel (for efficiency)
            import asyncio
            
            config_task = self.downloader.download_json(
                message["config_artifact_id"]
            )
            dataset_task = self.downloader.download_json(
                message["dataset_artifact_id"]
            )
            model_task = self.downloader.download_pytorch_model(
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
            
            # Now use the files in your training function
            await self.run_training(
                training_config=training_config,
                dataset_config=dataset_config,
                model_path=model_path,
                job_id=job_id
            )
            
            logger.info(f"Training job {job_id} completed successfully")
            
        except Exception as e:
            logger.error(f"Error processing training job {job_id}: {e}", exc_info=True)
            raise
    
    async def run_training(
        self,
        training_config: Dict[str, Any],
        dataset_config: Dict[str, Any],
        model_path: str,
        job_id: str
    ):
        """
        Your actual training logic goes here
        
        Args:
            training_config: Parsed training configuration JSON
            dataset_config: Parsed dataset configuration JSON
            model_path: Path to the downloaded .pth file
            job_id: Job identifier
        """
        logger.info(f"Starting training for job {job_id}")

        agent = Agent(training_config, dataset_config, model_path)
        agent.train_step(num_episodes=NUM_EPISODES)


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
        # Reject and requeue message for retry
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def start_consumer():
    """
    Start RabbitMQ consumer
    """
    logger.info("Starting RabbitMQ consumer...")
    
    # Initialize downloader and handler
    downloader = ArtifactDownloader(
        service_url=UPLOAD_DOWNLOAD_SERVICE_URL,
        user_id=None  
    )
    handler = TrainingJobHandler(downloader)
    
    # Connect to RabbitMQ
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=RABBITMQ_HOST)
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