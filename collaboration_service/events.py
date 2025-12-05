import pika
import json
from datetime import datetime
from database import db, cache, models_collection
import os
import threading

# Global RabbitMQ connection and channel (reused across requests)
_rabbitmq_connection = None
_rabbitmq_channel = None
_rabbitmq_lock = threading.Lock()

def get_rabbitmq_connection():
    """
    Get or create RabbitMQ connection (singleton pattern - reuse connection)
    Thread-safe connection reuse to avoid creating new connections for every event
    """
    global _rabbitmq_connection, _rabbitmq_channel
    
    with _rabbitmq_lock:
        # Check if connection is closed (handle pika internal errors)
        connection_closed = False
        if _rabbitmq_connection is not None:
            try:
                connection_closed = _rabbitmq_connection.is_closed
            except (IndexError, AttributeError) as e:
                # Handle pika internal deque errors
                print(f"⚠️  Pika connection state check error (likely deque issue): {e}")
                connection_closed = True
            except Exception as e:
                print(f"⚠️  Error checking connection state: {e}")
                connection_closed = True
        
        if _rabbitmq_connection is None or connection_closed:
            # Clean up old connection state safely
            if _rabbitmq_connection is not None:
                try:
                    if not _rabbitmq_connection.is_closed:
                        _rabbitmq_connection.close()
                except (IndexError, AttributeError):
                    # Ignore pika internal deque errors during cleanup
                    pass
                except Exception:
                    pass
            
            try:
                rabbitmq_host = os.getenv("RABBITMQ_HOST", "rabbitmq")
                rabbitmq_port = int(os.getenv("RABBITMQ_PORT", "5672"))
                rabbitmq_user = os.getenv("RABBITMQ_USER", "admin")
                rabbitmq_pass = os.getenv("RABBITMQ_PASS", "admin_password")
                
                credentials = pika.PlainCredentials(rabbitmq_user, rabbitmq_pass)
                _rabbitmq_connection = pika.BlockingConnection(
                    pika.ConnectionParameters(
                        host=rabbitmq_host,
                        port=rabbitmq_port,
                        credentials=credentials,
                        heartbeat=600,
                        blocked_connection_timeout=300,
                        connection_attempts=3,  # Retry up to 3 times
                        retry_delay=2,  # 2 seconds between retries
                        socket_timeout=5  # 5 second timeout per attempt
                    )
                )
                _rabbitmq_channel = _rabbitmq_connection.channel()
                
                # Declare exchange once (idempotent)
                _rabbitmq_channel.exchange_declare(exchange='comments', exchange_type='topic', durable=True)
                
                print("✅ RabbitMQ connection established (reused)")
            except Exception as e:
                print(f"⚠️  Failed to connect to RabbitMQ: {e}")
                _rabbitmq_connection = None
                _rabbitmq_channel = None
        
        return _rabbitmq_connection, _rabbitmq_channel




def publish_comment_created(comment_data: dict):
    """
    Publish CommentCreated event (non-blocking, reuses connection)
    Uses singleton connection pattern to avoid creating new connections for every event
    """
    try:
        connection, channel = get_rabbitmq_connection()
        
        if connection is None or channel is None:
            print(f"⚠️  RabbitMQ not available, skipping CommentCreated event for comment {comment_data.get('id')}")
            return
        
        event = {
            "eventType": "CommentCreated",
            "timestamp": datetime.utcnow().isoformat(),
            "data": comment_data
        }
        
        channel.basic_publish(
            exchange='comments',
            routing_key='comment.created',
            body=json.dumps(event),
            properties=pika.BasicProperties(
                delivery_mode=2,  # Make message persistent
                content_type='application/json'
            )
        )
        
        print(f"✅ Published CommentCreated event for comment {comment_data.get('id')}")
    except Exception as e:
        print(f"⚠️  Failed to publish CommentCreated event: {e}")
        # Fail-open: don't block request if event publishing fails




def handle_model_deleted(ch, method, properties, body):
    """
    Handle ModelDeleted event --> archive affected comments
    Model Catalog publishes: {"event_type": "ModelDeleted", "model_id": 1, "model_name": "..."}
    """
    
    event = json.loads(body)
    # Model Catalog format: {"event_type": "ModelDeleted", "model_id": 1, ...}
    model_id = str(event.get("model_id"))  # Convert to string for MongoDB
    
    if not model_id:
        print("⚠️  ModelDeleted event missing model_id")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return
    
    # Archive comments for deleted model 
    result = db.comments.update_many(
        {"modelId": model_id},
        {"$set": {"archived": True, "archivedAt": datetime.utcnow()}}
    )
    
    # Invalidate cache
    try:
        if cache:
            cache.delete(f"comments:{model_id}")
    except Exception:
        pass  # Fail-open: cache failures don't break the service
    
    print(f"✅ Archived {result.modified_count} comments for deleted model {model_id}")
    ch.basic_ack(delivery_tag=method.delivery_tag)




def handle_model_created(ch, method, properties, body):
    """
    Handle ModelCreated event --> cache creator info
    Model Catalog publishes: {"event_type": "ModelCreated", "model_id": 1, "model_name": "...", "created_by": "user-123"}
    """
    
    event = json.loads(body)
    # Model Catalog format: {"event_type": "ModelCreated", "model_id": 1, "created_by": "user-123", ...}
    model_id = str(event.get("model_id"))  # Convert to string for MongoDB
    creator_id = event.get("created_by")  # Model Catalog uses "created_by"
    
    if not model_id or not creator_id:
        print(f"⚠️  ModelCreated event missing model_id or created_by. Event: {event}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return
    
    # Cache model metadata in local collection for badge logic
    models_collection.update_one(
        {"modelId": model_id},
        {"$set": {
            "modelId": model_id,
            "creatorId": creator_id,
            "createdAt": datetime.utcnow()
        }},
        upsert=True
    )
    
    print(f"✅ Cached creator info for model {model_id} (creator: {creator_id})")
    ch.basic_ack(delivery_tag=method.delivery_tag)






def start_event_subscriber():
    """
    Start listening to model events from Model Catalog Service
    Model Catalog publishes to 'model_events' exchange with routing keys 'model.created' and 'model.deleted'
    """
    
    connection = get_rabbitmq_connection()
    channel = connection.channel()
    
    # Declare exchange for model events (Model Catalog uses 'model_events')
    channel.exchange_declare(exchange='model_events', exchange_type='topic', durable=True)
    
    # Create queue for this service
    result = channel.queue_declare(queue='collaboration_service_queue', durable=True)
    queue_name = result.method.queue
    
    # Bind to model events (Model Catalog routing keys)
    channel.queue_bind(exchange='model_events', queue=queue_name, routing_key='model.deleted')
    channel.queue_bind(exchange='model_events', queue=queue_name, routing_key='model.created')
    
    
    # Set up consumers
    def callback(ch, method, properties, body):
        
        try:
            event = json.loads(body)
            # Model Catalog format: {"event_type": "ModelCreated", ...} or {"event_type": "ModelDeleted", ...}
            event_type = event.get("event_type")  # Model Catalog uses "event_type" not "eventType"
            
            if event_type == "ModelDeleted":
                handle_model_deleted(ch, method, properties, body)
            elif event_type == "ModelCreated":
                handle_model_created(ch, method, properties, body)
            else:
                print(f"⚠️  Unknown event type: {event_type}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            print(f"⚠️  Error processing event: {e}")
            ch.basic_ack(delivery_tag=method.delivery_tag)  # Ack to prevent infinite retry
    
    
    channel.basic_consume(queue=queue_name, on_message_callback=callback)
    
    print("✅ Started listening for model events from 'model_events' exchange...")
    channel.start_consuming()