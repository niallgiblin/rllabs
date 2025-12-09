"""
Event Publisher for Upload/Download Service
============================================

Publishes lifecycle events to RabbitMQ for other services to consume.

Events Published:
1. artifact.uploaded - When an artifact upload completes
2. artifact.downloaded - When an artifact is downloaded (audit trail)

Pattern: Best-Effort Delivery
- If RabbitMQ is down, operations still succeed
- Events are lost (not critical for core functionality)
"""

import json
import logging
from typing import Dict, Any, Optional
import pika
import os

logger = logging.getLogger(__name__)

class EventPublisher:
    """
    Publishes events to RabbitMQ message broker
    
    Uses topic exchange for flexible routing.
    Other services can subscribe to specific event types.
    """
    
    def __init__(
        self,
        rabbitmq_host: str = "rabbitmq",
        rabbitmq_port: int = 5672,
        username: str = "admin",
        password: str = "admin_password"
    ):
        """
        Initialise event publisher
        
        Args:
            rabbitmq_host: RabbitMQ hostname
            rabbitmq_port: RabbitMQ AMQP port
            username: RabbitMQ username
            password: RabbitMQ password
        """
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.username = username
        self.password = password
        self._connection = None
        self._channel = None
    
    def _ensure_connection(self):
        """
        Ensure RabbitMQ connection is established
        
        Lazy connection - only connects when first event is published.
        Connection is reused for subsequent events.
        """
        connection_closed = False
        if self._connection is not None:
            try:
                connection_closed = self._connection.is_closed
            except (IndexError, AttributeError) as e:
                logger.debug(f"Pika connection state check error (likely deque issue): {e}")
                connection_closed = True
            except Exception as e:
                logger.debug(f"Error checking connection state: {e}")
                connection_closed = True
        
        if self._connection is None or connection_closed:
            if self._connection is not None:
                try:
                    if not self._connection.is_closed:
                        self._connection.close()
                except (IndexError, AttributeError):
                    pass
                except Exception:
                    pass
            
            try:
                credentials = pika.PlainCredentials(self.username, self.password)
                parameters = pika.ConnectionParameters(
                    host=self.rabbitmq_host,
                    port=self.rabbitmq_port,
                    credentials=credentials,
                    heartbeat=600,
                    blocked_connection_timeout=300,
                    connection_attempts=3,  
                    retry_delay=1,  
                    socket_timeout=5  
                )
                self._connection = pika.BlockingConnection(parameters)
                self._channel = self._connection.channel()
                
                self._channel.exchange_declare(
                    exchange='artifact_events',
                    exchange_type='topic',
                    durable=True
                )
                
                self._channel.queue_declare(
                    queue='training_jobs',
                    durable=True
                )
                
                logger.info("✓ Connected to RabbitMQ")
                
            except Exception as e:
                logger.debug(f"✗ Failed to connect to RabbitMQ: {e}")  
                self._connection = None
                self._channel = None
    
    def publish_artifact_uploaded(
        self, 
        artifact_id: str, # Content hash (sha256:...)
        model_id: int, # Parent model ID
        version: int, # Version number
        storage_path: str, # S3 storage path
        uploaded_by: str, # User ID who uploaded
        file_size: int, # File size in bytes
        filename: str # Original filename
    ):
        """
        Publish ArtifactUploaded event
        
        Other services can subscribe to this event to:
        - Trigger training jobs (Training Service)
        - Update search indices (Catalog Service)
        - Send notifications (Collaboration Service)
        - Track usage metrics (Observability)
        """
        from datetime import datetime, timezone
        
        event = {
            "event_type": "ArtifactUploaded", # Event type identifier
            "artifact_id": artifact_id, # Content hash (sha256:...)
            "model_id": model_id, # Parent model ID
            "version": version, # Version number
            "storage_path": storage_path, # S3 storage path
            "uploaded_by": uploaded_by, # User ID who uploaded
            "file_size": file_size, # File size in bytes
            "filename": filename, # Original filename
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z" # timestamp
        }
        
        self._publish_event("artifact.uploaded", event)
    
    def publish_artifact_downloaded(
        self,
        artifact_id: str,# Content hash (sha256:...)
        downloaded_by: str # User ID 
    ):
        """
        Publish ArtifactDownloaded event
        Used for audit trails and usage tracking.

        """
        from datetime import datetime, timezone
        
        event = {
            "event_type": "ArtifactDownloaded", # Event type identifier
            "artifact_id": artifact_id, # Content hash (sha256:...)
            "downloaded_by": downloaded_by, # User ID who downloaded
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z" # timestamp
        }
        
        self._publish_event("artifact.downloaded", event) 
    
    def _publish_event(
            self, 
            routing_key: str,
            event: Dict[str, Any] 
            ):
        """
        Publish an event to RabbitMQ
        
        Fail-Open Pattern: If publishing fails, log but don't raise exception.
        This ensures that upload/download operations succeed even if messaging fails.
        """
        try:
            self._ensure_connection()
            
            if self._channel is None or self._connection is None:
                logger.warning("RabbitMQ not available, event not published (fail-open)")
                return
            
            try:
                if self._channel.is_closed:
                    logger.debug("Channel is closed, reconnecting...")
                    self._ensure_connection()
                    if self._channel is None:
                        logger.warning("RabbitMQ not available after reconnect, event not published (fail-open)")
                        return
            except (AttributeError, IndexError):
                logger.debug("Channel state check failed, reconnecting...")
                self._ensure_connection()
                if self._channel is None:
                    logger.warning("RabbitMQ not available after reconnect, event not published (fail-open)")
                    return
            
            self._channel.basic_publish(
                exchange='artifact_events',
                routing_key=routing_key, 
                body=json.dumps(event),
                properties=pika.BasicProperties(
                    delivery_mode=2, 
                    content_type='application/json'
                )
            )
            logger.info(f"✓ Published event: {routing_key} - {event['event_type']}")
            
        except (pika.exceptions.ChannelClosed, pika.exceptions.ConnectionClosed) as e:
            logger.debug(f"RabbitMQ channel/connection closed, event not published (fail-open): {e}")
            self._connection = None
            self._channel = None
        except Exception as e:
            logger.error(f"✗ Failed to publish event: {e}")
    
    def close(self):
        """
        Close RabbitMQ connection
        
        Called on application shutdown.
        """
        if self._connection:
            try:
                if not self._connection.is_closed:
                    self._connection.close()
                    logger.info("Closed RabbitMQ connection")
            except (IndexError, AttributeError) as e:
                logger.debug(f"Ignoring pika cleanup error (likely deque issue): {e}")
            except Exception as e:
                logger.debug(f"Error closing connection: {e}")

    def publish_training_job(self, message: dict):
        """
        Publish a training job message to RabbitMQ
        
        Returns:
            bool: True if published successfully, False if RabbitMQ unavailable
        """
        try:
            self._ensure_connection() 
            
            if self._channel is None or self._connection is None:  
                logger.warning("RabbitMQ not available, training job not published (fail-open)")
                return False  
            
            try:
                if self._channel.is_closed:
                    logger.debug("Channel is closed, reconnecting...")
                    self._ensure_connection()
                    if self._channel is None:
                        logger.warning("RabbitMQ not available after reconnect, training job not published (fail-open)")
                        return False
            except (AttributeError, IndexError):
                logger.debug("Channel state check failed, reconnecting...")
                self._ensure_connection()
                if self._channel is None:
                    logger.warning("RabbitMQ not available after reconnect, training job not published (fail-open)")
                    return False
            
            self._channel.basic_publish(  
                exchange='',
                routing_key='training_jobs',  
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  
                    content_type='application/json'
                )
            )
            logger.info(f"Published training job: {message['job_id']}")
            return True
        except (pika.exceptions.ChannelClosed, pika.exceptions.ConnectionClosed) as e:
            logger.debug(f"RabbitMQ channel/connection closed, training job not published (fail-open): {e}")
            self._connection = None
            self._channel = None
            return False
        except Exception as e:
            logger.error(f"Failed to publish training job: {e}")
            return False


_event_publisher = None


def get_event_publisher() -> Optional[EventPublisher]:
    """
    Get or create global event publisher instance
    
    Singleton pattern ensures we reuse the same RabbitMQ connection.
    
    Returns:
        EventPublisher instance or None if RabbitMQ config missing
    """
    global _event_publisher
    
    if _event_publisher is None:
        try:
            _event_publisher = EventPublisher(
                rabbitmq_host=os.getenv("RABBITMQ_HOST", "rabbitmq"),
                rabbitmq_port=int(os.getenv("RABBITMQ_PORT", "5672")),
                username=os.getenv("RABBITMQ_USER", "admin"),
                password=os.getenv("RABBITMQ_PASS", "admin_password")
            )
        except Exception as e:
            logger.warning(f"Failed to initialise event publisher: {e}")
            return None
    
    return _event_publisher
