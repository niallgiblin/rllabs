import pika
import json
from datetime import datetime
from database import db, cache, models_collection


def get_rabbitmq_connection():
    
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host='host.docker.internal', port=5672)
    )
    
    return connection




def publish_comment_created(comment_data: dict):
    """
    Publish CommentCreated event
    """
    
    connection = get_rabbitmq_connection()
    channel = connection.channel()
    
    # Declare exchange for comment events
    channel.exchange_declare(exchange='comments', exchange_type='topic', durable=True)
    
    event = {
        "eventType": "CommentCreated",
        "timestamp": datetime.utcnow().isoformat(),
        "data": comment_data
    }
    
    channel.basic_publish(
        exchange='comments',
        routing_key='comment.created',
        body=json.dumps(event)
    )
    
    connection.close()
    print(f"✅ Published CommentCreated event for comment {comment_data.get('id')}")




def handle_model_deleted(ch, method, properties, body):
    """
    Handle ModelDeleted event --> archive affected comments
    """
    
    event = json.loads(body)
    model_id = event.get("data", {}).get("modelId")
    
    if not model_id:
        print("⚠️  ModelDeleted event missing modelId")
        return
    
    # Archive comments for deleted model 
    result = db.comments.update_many(
        {"modelId": model_id},
        {"$set": {"archived": True, "archivedAt": datetime.utcnow()}}
    )
    
    # Invalidate cache
    cache.delete(f"comments:{model_id}")
    
    print(f"✅ Archived {result.modified_count} comments for deleted model {model_id}")
    ch.basic_ack(delivery_tag=method.delivery_tag)




def handle_model_created(ch, method, properties, body):
    """
    Handle ModelCreated event --> cache creator info
    """
    
    event = json.loads(body)
    model_data = event.get("data", {})
    
    
    model_id = model_data.get("modelId")
    creator_id = model_data.get("creatorId")
    
    if not model_id or not creator_id:
        print("⚠️  ModelCreated event missing modelId or creatorId")
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
    
    print(f"✅ Cached creator info for model {model_id}")
    ch.basic_ack(delivery_tag=method.delivery_tag)






def start_event_subscriber():
    """
    Start listening to model events
    """
    
    connection = get_rabbitmq_connection()
    channel = connection.channel()
    
    # Declare exchange for model events
    channel.exchange_declare(exchange='models', exchange_type='topic', durable=True)
    
    # Create queue for this service
    result = channel.queue_declare(queue='chat_service_queue', durable=True)
    queue_name = result.method.queue
    
    # Bind to model events
    channel.queue_bind(exchange='models', queue=queue_name, routing_key='model.deleted')
    channel.queue_bind(exchange='models', queue=queue_name, routing_key='model.created')
    
    
    # Set up consumers
    def callback(ch, method, properties, body):
        
        event = json.loads(body)
        event_type = event.get("eventType")
        
        if event_type == "ModelDeleted":
            handle_model_deleted(ch, method, properties, body)
        elif event_type == "ModelCreated":
            handle_model_created(ch, method, properties, body)
        else:
            print(f"⚠️  Unknown event type: {event_type}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
    
    
    channel.basic_consume(queue=queue_name, on_message_callback=callback)
    
    print("✅ Started listening for model events...")
    channel.start_consuming()