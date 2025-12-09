"""
Event Publisher for Model Catalog Service
Publishes lifecycle events to RabbitMQ for other services to consume
"""
import json
import logging
from typing import Dict, Any
import pika

logger = logging.getLogger(__name__)

class EventPublisher:
    """Publishes events to RabbitMQ message broker"""
    
    def __init__(self, rabbitmq_host: str = "rabbitmq", rabbitmq_port: int = 5672,
                 username: str = "admin", password: str = "admin_password"):
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.username = username
        self.password = password
        self._connection = None
        self._channel = None
    
    def _ensure_connection(self):
        """Ensure RabbitMQ connection is established"""
        # Check if connection is closed (handle pika internal errors)
        connection_closed = False
        if self._connection is not None:
            try:
                connection_closed = self._connection.is_closed
            except (IndexError, AttributeError) as e:
                # Handle pika internal deque errors
                logger.debug(f"Pika connection state check error (likely deque issue): {e}")
                connection_closed = True
            except Exception as e:
                logger.debug(f"Error checking connection state: {e}")
                connection_closed = True
        
        if self._connection is None or connection_closed:
            # Clean up old connection state safely
            if self._connection is not None:
                try:
                    if not self._connection.is_closed:
                        self._connection.close()
                except (IndexError, AttributeError):
                    # Ignore pika internal deque errors during cleanup
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
                    connection_attempts=3,  # Retry up to 3 times
                    retry_delay=1,  # 1 second between retries
                    socket_timeout=5  # 5 second timeout per attempt
                )
                self._connection = pika.BlockingConnection(parameters)
                self._channel = self._connection.channel()
                
                # Declare exchange for model events
                self._channel.exchange_declare(
                    exchange='model_events',
                    exchange_type='topic',
                    durable=True
                )
                logger.info("Connected to RabbitMQ")
            except Exception as e:
                logger.debug(f"Failed to connect to RabbitMQ: {e}")  # Changed to debug to reduce log noise
                self._connection = None
                self._channel = None
    
    def publish_model_deleted(self, model_id: int, model_name: str):
        """
        Publish ModelDeleted event when a model is deleted
        
        Args:
            model_id: ID of the deleted model
            model_name: Name of the deleted model
        """
        event = {
            "event_type": "ModelDeleted",
            "model_id": model_id,
            "model_name": model_name,
            "timestamp": None  # Will be set by consumer if needed
        }
        self._publish_event("model.deleted", event)
    
    def publish_model_created(self, model_id: int, model_name: str, created_by: str):
        """
        Publish ModelCreated event when a new model is created
        
        Args:
            model_id: ID of the created model
            model_name: Name of the created model
            created_by: User ID who created the model
        """
        event = {
            "event_type": "ModelCreated",
            "model_id": model_id,
            "model_name": model_name,
            "created_by": created_by
        }
        self._publish_event("model.created", event)
    
    def _publish_event(self, routing_key: str, event: Dict[str, Any]):
        """
        Publish an event to RabbitMQ
        
        Args:
            routing_key: Routing key for the event (e.g., "model.deleted")
            event: Event payload as dictionary
        """
        try:
            self._ensure_connection()
            
            if self._channel is None or self._connection is None:
                logger.warning("RabbitMQ not available, event not published")
                return
            
            try:
                if self._channel.is_closed:
                    logger.debug("Channel is closed, reconnecting...")
                    self._ensure_connection()
                    if self._channel is None:
                        logger.warning("RabbitMQ not available after reconnect, event not published")
                        return
            except (AttributeError, IndexError):
                logger.debug("Channel state check failed, reconnecting...")
                self._ensure_connection()
                if self._channel is None:
                    logger.warning("RabbitMQ not available after reconnect, event not published")
                    return
            
            self._channel.basic_publish(
                exchange='model_events',
                routing_key=routing_key,
                body=json.dumps(event),
                properties=pika.BasicProperties(
                    delivery_mode=2,  
                    content_type='application/json'
                )
            )
            logger.info(f"Published event: {routing_key} - {event['event_type']}")
            
        except (pika.exceptions.ChannelClosed, pika.exceptions.ConnectionClosed) as e:
            logger.debug(f"RabbitMQ channel/connection closed, event not published: {e}")
            self._connection = None
            self._channel = None
        except Exception as e:
            logger.error(f"Failed to publish event: {e}")
    
    def close(self):
        """Close RabbitMQ connection"""
        if self._connection:
            try:
                if not self._connection.is_closed:
                    self._connection.close()
                    logger.info("Closed RabbitMQ connection")
            except (IndexError, AttributeError) as e:
                # Handle pika internal deque errors during cleanup
                logger.debug(f"Ignoring pika cleanup error (likely deque issue): {e}")
            except Exception as e:
                logger.debug(f"Error closing connection: {e}")

# Global event publisher instance
_event_publisher = None

def get_event_publisher() -> EventPublisher:
    """Get or create global event publisher instance"""
    global _event_publisher
    if _event_publisher is None:
        import os
        _event_publisher = EventPublisher(
            rabbitmq_host=os.getenv("RABBITMQ_HOST", "rabbitmq"),
            rabbitmq_port=int(os.getenv("RABBITMQ_PORT", "5672")),
            username=os.getenv("RABBITMQ_USER", "admin"),
            password=os.getenv("RABBITMQ_PASS", "admin_password")
        )
    return _event_publisher

