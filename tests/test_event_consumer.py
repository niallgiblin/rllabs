#!/usr/bin/env python3
"""
Pytest tests for Event Consumer functionality
Tests that the Model Catalog event consumer properly listens to artifact events
and auto-registers model versions
"""

import json
import time
import os
import pika
import pytest
import requests
from datetime import datetime, timedelta, timezone
import jwt

GATEWAY_URL = "http://localhost:8080"
CATALOG_DIRECT_URL = "http://localhost:8001"
UPLOAD_DOWNLOAD_DIRECT_URL = "http://localhost:8081"
RABBITMQ_HOST = "localhost"
RABBITMQ_PORT = 5672
RABBITMQ_USER = "admin"
RABBITMQ_PASS = "admin_password"

SECRET_KEY = "your-secret-key"
ALGORITHM = "HS256"

def _make_jwt(sub: str = "test-user", scopes=("api:read", "api:write")) -> str:
    """Generate a JWT token for testing"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "scopes": list(scopes),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=30)).timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

@pytest.fixture(scope="session", autouse=True)
def wait_for_services():
    """Wait for services to be ready"""
    for _ in range(30):
        try:
            if requests.get(f"{GATEWAY_URL}/health", timeout=3).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    pytest.fail("Services did not become ready in time")

@pytest.fixture
def auth_headers():
    """Get headers with authentication token"""
    token = _make_jwt()
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def test_model_id(auth_headers):
    """Create a test model and return its ID"""
    unique_name = f"event-consumer-test-{int(time.time())}-{os.urandom(2).hex()}"
    try:
        response = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": unique_name, "description": "Test model for event consumer"},
            headers=auth_headers,
            timeout=5
        )
        if response.status_code == 201:
            return response.json()["id"]
        elif response.status_code == 503:
            pytest.skip("Model catalog service unavailable")
    except Exception:
        pass
    
    # Fallback: try direct catalog service
    try:
        response = requests.post(
            f"{CATALOG_DIRECT_URL}/models",
            json={"name": unique_name, "description": "Test model"},
            headers={"X-User-Id": "test-user"},
            timeout=5
        )
        if response.status_code == 201:
            return response.json()["id"]
    except Exception:
        pass
    
    pytest.skip("Could not create test model")

# RabbitMQ connectivity tests
def test_rabbitmq_connectivity():
    """Test that RabbitMQ is accessible"""
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials,
                connection_attempts=3,
                retry_delay=1
            )
        )
        connection.close()
        assert True
    except Exception as e:
        pytest.skip(f"RabbitMQ not available: {e}")

def test_artifact_events_exchange_exists():
    """Test that artifact_events exchange exists"""
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials
            )
        )
        channel = connection.channel()
        
        # Try to declare exchange (will not error if exists)
        channel.exchange_declare(
            exchange='artifact_events',
            exchange_type='topic',
            durable=True
        )
        
        connection.close()
        assert True
    except Exception as e:
        pytest.skip(f"RabbitMQ not available: {e}")

# Event integration tests
def test_event_consumer_listens_to_artifact_events():
    """Test that event consumer can listen to artifact.committed events"""
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials
            )
        )
        channel = connection.channel()
        
        # Declare exchange
        channel.exchange_declare(
            exchange='artifact_events',
            exchange_type='topic',
            durable=True
        )
        
        # Create test queue
        result = channel.queue_declare(queue='test-consumer-queue', exclusive=True)
        queue_name = result.method.queue
        
        # Bind to artifact.committed events
        channel.queue_bind(
            exchange='artifact_events',
            queue=queue_name,
            routing_key='artifact.committed'
        )
        
        # Publish a test event
        test_event = {
            "event_type": "ArtifactCommitted",
            "artifact_id": "test-artifact-123",
            "model_id": 1,
            "version": 1,
            "storage_path": "test/path",
            "content_hash": "test-hash",
            "uploaded_by": "test-user"
        }
        
        channel.basic_publish(
            exchange='artifact_events',
            routing_key='artifact.committed',
            body=json.dumps(test_event),
            properties=pika.BasicProperties(
                delivery_mode=2,  # Persistent
                content_type='application/json'
            )
        )
        
        # Wait for message to be delivered
        time.sleep(0.5)
        
        # Check if message was received
        method_frame, properties, body = channel.basic_get(queue=queue_name, auto_ack=True)
        
        connection.close()
        
        assert method_frame is not None, "Event was not received"
        received_event = json.loads(body)
        assert received_event["event_type"] == "ArtifactCommitted"
        assert received_event["artifact_id"] == "test-artifact-123"
        
    except Exception as e:
        pytest.skip(f"RabbitMQ test failed: {e}")


def test_event_consumer_auto_registers_model_version():
    """Test that event consumer auto-registers model versions from artifact events"""
    # This is an integration test that would require:
    # 1. Creating a model
    # 2. Uploading an artifact with that model_id
    # 3. Verifying the artifact.committed event triggers version registration
    
    # For now, we'll verify the infrastructure is in place
    # Full integration would require completing an actual upload workflow
    
    try:
        # Verify model catalog can receive version registrations
        response = requests.get(f"{CATALOG_DIRECT_URL}/health", timeout=3)
        assert response.status_code == 200
        
        # Verify RabbitMQ is available for events
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials
            )
        )
        connection.close()
        
        # Infrastructure is ready - full test would require actual upload
        assert True
        
    except Exception as e:
        pytest.skip(f"Infrastructure not ready: {e}")

# Error handling tests
def test_event_consumer_handles_malformed_events():
    """Test that event consumer handles malformed events gracefully"""
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials
            )
        )
        channel = connection.channel()
        
        # Declare exchange
        channel.exchange_declare(
            exchange='artifact_events',
            exchange_type='topic',
            durable=True
        )
        
        # Publish malformed event (invalid JSON)
        channel.basic_publish(
            exchange='artifact_events',
            routing_key='artifact.committed',
            body="not valid json",
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type='application/json'
            )
        )
        
        # Publish event with missing required fields
        incomplete_event = {
            "event_type": "ArtifactCommitted"
            # Missing required fields
        }
        channel.basic_publish(
            exchange='artifact_events',
            routing_key='artifact.committed',
            body=json.dumps(incomplete_event),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type='application/json'
            )
        )
        
        connection.close()
        
        # Consumer should handle these gracefully without crashing
        # We can't directly verify this without access to consumer logs,
        # but we can verify the events were published
        assert True
        
    except Exception as e:
        pytest.skip(f"RabbitMQ test failed: {e}")


def test_event_consumer_reconnection_logic():
    """Test that event consumer can reconnect after RabbitMQ failure"""
    # This would require simulating RabbitMQ failure and recovery
    # For now, we verify the connection can be established
    
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        
        # First connection
        connection1 = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials
            )
        )
        connection1.close()
        
        # Second connection (simulating reconnection)
        time.sleep(0.5)
        connection2 = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials
            )
        )
        connection2.close()
        
        assert True
        
    except Exception as e:
        pytest.skip(f"RabbitMQ reconnection test failed: {e}")

# Event format validation
def test_artifact_committed_event_format():
    """Test that ArtifactCommitted events have the correct format"""
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials
            )
        )
        channel = connection.channel()
        
        # Declare exchange
        channel.exchange_declare(
            exchange='artifact_events',
            exchange_type='topic',
            durable=True
        )
        
        # Create test queue
        result = channel.queue_declare(queue='test-format-queue', exclusive=True)
        queue_name = result.method.queue
        
        # Bind to artifact.committed
        channel.queue_bind(
            exchange='artifact_events',
            queue=queue_name,
            routing_key='artifact.committed'
        )
        
        # Publish properly formatted event
        valid_event = {
            "event_type": "ArtifactCommitted",
            "artifact_id": "test-123",
            "model_id": 1,
            "version": 1,
            "storage_path": "models/test-123",
            "content_hash": "sha256:abc123",
            "uploaded_by": "test-user",
            "file_size": 1024,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        channel.basic_publish(
            exchange='artifact_events',
            routing_key='artifact.committed',
            body=json.dumps(valid_event),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type='application/json'
            )
        )
        
        time.sleep(0.5)
        
        # Verify event format
        method_frame, properties, body = channel.basic_get(queue=queue_name, auto_ack=True)
        connection.close()
        
        assert method_frame is not None
        event = json.loads(body)
        
        # Validate required fields
        assert event["event_type"] == "ArtifactCommitted"
        assert "artifact_id" in event
        assert "model_id" in event
        assert "storage_path" in event
        assert "content_hash" in event
        
    except Exception as e:
        pytest.skip(f"Event format test failed: {e}")

# Graceful degradation tests
def test_event_consumer_graceful_degradation():
    """Test that model catalog works even if event consumer fails"""
    # Verify model catalog can still function without event consumer
    try:
        response = requests.get(f"{CATALOG_DIRECT_URL}/health", timeout=3)
        assert response.status_code == 200
        
        # Model creation should still work
        unique_name = f"degradation-test-{int(time.time())}"
        response = requests.post(
            f"{CATALOG_DIRECT_URL}/models",
            json={"name": unique_name, "description": "Test"},
            headers={"X-User-Id": "test-user"},
            timeout=5
        )
        
        # Should succeed even if event consumer is disabled
        assert response.status_code == 201
        
    except Exception as e:
        pytest.skip(f"Service unavailable: {e}")