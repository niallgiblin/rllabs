#!/usr/bin/env python3
"""
Test Event Consumer
===================

Listens to RabbitMQ for artifact events and prints them.
Use this to verify that the Upload/Download Service is publishing events correctly.

Usage:
    python test_event_consumer.py
    
    # In another terminal:
    python simple_test.py upload test.pkl --model-id 1
    
    # You should see the event appear in the consumer terminal
"""

import pika
import json
import sys

RABBITMQ_HOST = "localhost"
RABBITMQ_PORT = 5672
RABBITMQ_USER = "admin"
RABBITMQ_PASS = "admin_password"

def callback(ch, method, properties, body):
    """Handle received messages"""
    print("\n" + "="*80)
    print("--- EVENT RECEIVED")
    print("="*80)
    
    try:
        event = json.loads(body)
        print(f"\n--- Routing Key: {method.routing_key}")
        print(f"--- Event Type: {event.get('event_type', 'Unknown')}")
        print(f"\n--- Event Data:")
        print(json.dumps(event, indent=2))
        
        # Highlight key information
        if event.get('event_type') == 'ArtifactUploaded':
            print(f"\n--- Highlights:")
            print(f"   - Artifact ID: {event.get('artifact_id', 'N/A')}")
            print(f"   - Model ID: {event.get('model_id', 'N/A')}")
            print(f"   - Version: {event.get('version', 'N/A')}")
            print(f"   - Uploaded by: {event.get('uploaded_by', 'N/A')}")
            print(f"   - File size: {event.get('file_size', 'N/A')} bytes")
            
        elif event.get('event_type') == 'ArtifactDownloaded':
            print(f"\n--- Highlights:")
            print(f"   - Artifact ID: {event.get('artifact_id', 'N/A')}")
            print(f"   - Downloaded by: {event.get('downloaded_by', 'N/A')}")
        
        print("\n" + "="*80 + "\n")
        
    except json.JSONDecodeError:
        print(f"--- Failed to parse event: {body}")
    
    # Acknowledge the message
    ch.basic_ack(delivery_tag=method.delivery_tag)

def main():
    print("--- Connecting to RabbitMQ...")
    
    try:
        # Connect to RabbitMQ
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        parameters = pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            port=RABBITMQ_PORT,
            credentials=credentials,
            heartbeat=600
        )
        
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        
        print("✓ Connected to RabbitMQ")
        
        # Declare exchange (idempotent - won't error if exists)
        channel.exchange_declare(
            exchange='artifact_events',
            exchange_type='topic',
            durable=True
        )
        print("✓ Exchange 'artifact_events' ready")
        
        # Create a temporary queue for this test consumer
        result = channel.queue_declare(queue='', exclusive=True)
        queue_name = result.method.queue
        print(f"✓ Created test queue: {queue_name}")
        
        # Bind queue to exchange with wildcard routing key
        # This will receive ALL artifact events
        channel.queue_bind(
            exchange='artifact_events',
            queue=queue_name,
            routing_key='artifact.*'
        )
        print("✓ Bound to routing key: artifact.*")
        
        print("\n" + "="*80)
        print("🎧 LISTENING FOR EVENTS")
        print("="*80)
        print("\nWaiting for artifact events...")
        print("Press CTRL+C to exit\n")
        print("Try uploading a file in another terminal:")
        print("  python simple_test.py upload test.pkl --model-id 1\n")
        
        # Start consuming
        channel.basic_consume(
            queue=queue_name,
            on_message_callback=callback,
            auto_ack=False
        )
        
        channel.start_consuming()
        
    except KeyboardInterrupt:
        print("\n\n--- Shutting down...")
        sys.exit(0)
        
    except pika.exceptions.AMQPConnectionError as e:
        print(f"\n--- Failed to connect to RabbitMQ: {e}")
        print("\nMake sure RabbitMQ is running:")
        print("  docker-compose ps rabbitmq")
        sys.exit(1)
        
    except Exception as e:
        print(f"\n--- Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
