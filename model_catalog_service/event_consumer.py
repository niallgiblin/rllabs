"""
Event Consumer for Model Catalog Service
Consumes artifact events from RabbitMQ to auto-register model versions

This module listens to ArtifactCommitted events from the Upload/Download Service
and automatically registers model versions when artifacts are committed.
"""
import json
import logging
import threading
from typing import Optional
import pika

logger = logging.getLogger(__name__)

try:
    import database
    from sqlalchemy import func
    from sqlalchemy.exc import IntegrityError
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    logger.warning("Database modules not available, event consumer will log events only")

class EventConsumer:
    """Consumes events from RabbitMQ message broker"""
    
    def __init__(self, rabbitmq_host: str = "rabbitmq", rabbitmq_port: int = 5672,
                 username: str = "admin", password: str = "admin_password"):
        self.rabbitmq_host = rabbitmq_host
        self.rabbitmq_port = rabbitmq_port
        self.username = username
        self.password = password
        self._connection = None
        self._channel = None
        self._consumer_thread = None
        self._consuming = False
    
    def _ensure_connection(self):
        """Ensure RabbitMQ connection is established"""
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
                
                result = self._channel.queue_declare(
                    queue='model_catalog_artifact_events',
                    durable=True
                )
                queue_name = result.method.queue
                
                self._channel.queue_bind(
                    exchange='artifact_events',
                    queue=queue_name,
                    routing_key='artifact.committed'
                )
                
                logger.info("Connected to RabbitMQ for event consumption")
            except Exception as e:
                logger.debug(f"Failed to connect to RabbitMQ: {e}")
                self._connection = None
                self._channel = None
    
    def _register_model_version(self, model_id: int, storage_path: str, content_hash: str):
        """
        Register a model version in the database
        
        Args:
            model_id: ID of the parent model
            storage_path: Storage path of the artifact
            content_hash: Content hash (SHA-256) of the artifact
        
        Returns:
            Version number that was assigned, or None if registration failed
        """
        if not DATABASE_AVAILABLE:
            logger.warning("Database not available, cannot register model version")
            return None
        
        try:
            db_gen = database.get_db()
            db = next(db_gen)
            
            try:
                db_model = db.query(database.Model).filter(database.Model.id == model_id).first()
                if not db_model:
                    logger.warning(f"Model {model_id} not found, skipping version registration")
                    return None
                
                max_version = db.query(func.max(database.ModelVersion.version)).filter(
                    database.ModelVersion.model_id == model_id
                ).scalar()
                version = (max_version or 0) + 1
                
                existing = db.query(database.ModelVersion).filter(
                    database.ModelVersion.model_id == model_id,
                    database.ModelVersion.content_hash == content_hash
                ).first()
                
                if existing:
                    logger.info(
                        f"Version with content_hash {content_hash[:16]}... already exists "
                        f"for model {model_id} (version {existing.version}), skipping"
                    )
                    return existing.version
                
                db_version = database.ModelVersion(
                    model_id=model_id,
                    version=version,
                    storage_path=storage_path,
                    content_hash=content_hash
                )
                db.add(db_version)
                db.commit()
                db.refresh(db_version)
                
                logger.info(
                    f"Auto-registered version {version} for model {model_id} "
                    f"(content_hash: {content_hash[:16]}..., storage_path: {storage_path[:50]}...)"
                )
                
                try:
                    from cache import (
                        invalidate_model_versions, invalidate_model, invalidate_models_list,
                        get_cache, PREFIX
                    )
                    invalidate_model_versions(model_id)
                    invalidate_model(model_id)
                    invalidate_models_list()
                    try:
                        get_cache().delete(f"{PREFIX}:models:count")
                    except Exception:
                        pass
                except ImportError:
                    pass
                
                return version
                
            except IntegrityError as e:
                db.rollback()
                logger.warning(f"IntegrityError registering version for model {model_id}: {e}")
                return None
            except Exception as e:
                db.rollback()
                logger.error(
                    f"Error registering version for model {model_id}: {e}",
                    exc_info=True
                )
                return None
            finally:
                db.close()
                try:
                    next(db_gen, None)
                except StopIteration:
                    pass
                    
        except Exception as e:
            logger.error(f"Unexpected error in _register_model_version: {e}", exc_info=True)
            return None
    
    def _handle_artifact_committed(self, event: dict):
        """
        Handle ArtifactCommitted event by auto-registering model version
        
        Args:
            event: Event payload containing artifact information
        """
        try:
            artifact_id = event.get("artifact_id")
            model_id = event.get("model_id")
            storage_path = event.get("storage_path")
            content_hash = event.get("content_hash")
            uploaded_by = event.get("uploaded_by")
            
            if not model_id:
                logger.debug(f"ArtifactCommitted event for artifact {artifact_id} has no model_id, skipping")
                return
            
            if not storage_path or not content_hash:
                logger.warning(
                    f"ArtifactCommitted event missing required fields: "
                    f"storage_path={storage_path}, content_hash={content_hash}"
                )
                return
            
            logger.info(
                f"ArtifactCommitted event received: artifact_id={artifact_id[:16]}..., "
                f"model_id={model_id}, storage_path={storage_path[:50]}..., "
                f"uploaded_by={uploaded_by}"
            )
            
            version = self._register_model_version(
                model_id=model_id,
                storage_path=storage_path,
                content_hash=content_hash
            )
            
            if version:
                logger.info(
                    f"Successfully auto-registered version {version} for model {model_id} "
                    f"from ArtifactCommitted event"
                )
            else:
                logger.debug(
                    f"Version registration skipped for model {model_id} "
                    f"(may already exist or registration failed)"
                )
            
        except Exception as e:
            logger.error(f"Error handling ArtifactCommitted event: {e}", exc_info=True)
    
    def _on_message(self, ch, method, properties, body):
        """Callback for processing RabbitMQ messages"""
        try:
            event = json.loads(body)
            event_type = event.get("event_type")
            
            if event_type == "ArtifactCommitted":
                self._handle_artifact_committed(event)
            else:
                logger.debug(f"Received unknown event type: {event_type}")
            
            ch.basic_ack(delivery_tag=method.delivery_tag)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse event message: {e}")
            ch.basic_ack(delivery_tag=method.delivery_tag) 
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            ch.basic_ack(delivery_tag=method.delivery_tag)  
    
    def start_consuming(self):
        """Start consuming events from RabbitMQ"""
        if self._consuming:
            logger.warning("Event consumer is already consuming")
            return
        
        try:
            self._ensure_connection()
            
            if self._channel is None or self._connection is None:
                logger.warning("RabbitMQ not available, event consumer not started")
                return
            
            self._consuming = True
            
            def consume():
                try:
                    queue_name = 'model_catalog_artifact_events'
                    self._channel.basic_consume(
                        queue=queue_name,
                        on_message_callback=self._on_message,
                        auto_ack=False
                    )
                    logger.info(f"Started consuming events from queue: {queue_name}")
                    self._channel.start_consuming()
                except Exception as e:
                    logger.error(f"Error in consumer thread: {e}", exc_info=True)
                    self._consuming = False
            
            self._consumer_thread = threading.Thread(target=consume, daemon=True, name="EventConsumer")
            self._consumer_thread.start()
            logger.info("Event consumer thread started")
            
        except Exception as e:
            logger.error(f"Failed to start event consumer: {e}", exc_info=True)
            self._consuming = False
    
    def stop_consuming(self):
        """Stop consuming events from RabbitMQ"""
        if not self._consuming:
            return
        
        self._consuming = False
        
        try:
            if self._channel and not self._channel.is_closed:
                self._channel.stop_consuming()
                logger.info("Stopped consuming events")
        except (IndexError, AttributeError) as e:
            logger.debug(f"Ignoring pika cleanup error (likely deque issue): {e}")
        except Exception as e:
            logger.debug(f"Error stopping consumer: {e}")
        
        if self._consumer_thread and self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=5)
    
    def close(self):
        """Close RabbitMQ connection"""
        self.stop_consuming()
        
        if self._connection:
            try:
                if not self._connection.is_closed:
                    self._connection.close()
                    logger.info("Closed RabbitMQ connection")
            except (IndexError, AttributeError) as e:
                logger.debug(f"Ignoring pika cleanup error (likely deque issue): {e}")
            except Exception as e:
                logger.debug(f"Error closing connection: {e}")

_event_consumer = None

def get_event_consumer() -> Optional[EventConsumer]:
    """Get or create global event consumer instance"""
    global _event_consumer
    if _event_consumer is None:
        import os
        _event_consumer = EventConsumer(
            rabbitmq_host=os.getenv("RABBITMQ_HOST", "rabbitmq"),
            rabbitmq_port=int(os.getenv("RABBITMQ_PORT", "5672")),
            username=os.getenv("RABBITMQ_USER", "admin"),
            password=os.getenv("RABBITMQ_PASS", "admin_password")
        )
    return _event_consumer
