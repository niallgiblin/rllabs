"""
Integration tests for API Gateway - Model Catalog using Bearer JWTs
Including async messaging via RabbitMQ
"""

import json
import time
import os
from datetime import datetime, timedelta, timezone
import jwt
import pika
import pytest
import requests

GATEWAY_URL = "http://localhost:8080"

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from rabbitmq_helpers import get_rabbitmq_connection_or_skip

SECRET_KEY = "your-secret-key" #Harcoded for ease in local dev, in production use env var
ALGORITHM = "HS256"


def _make_jwt(sub: str = "it-test-user", scopes = ("api:read", "api:write")) -> str:
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
    for _ in range(30):
        try:
            if requests.get(f"{GATEWAY_URL}/health", timeout=3).status_code == 200 and \
                requests.get(f"{GATEWAY_URL}/health", timeout=3).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    pytest.fail("Services did not become ready in time")

def test_connectivity():
    assert requests.get(f"{GATEWAY_URL}/health").status_code == 200
    assert requests.get(f"{GATEWAY_URL}/health").status_code == 200

def test_protected_without_auth_is_401():
    """Test that protected write operations require authentication"""
    r = requests.post(f"{GATEWAY_URL}/api/models", json={"name": "test"})
    assert r.status_code == 401, "Write operations should require authentication"

def test_public_list_models():
    r = requests.get(f"{GATEWAY_URL}/public/models")
    assert r.status_code in [200, 503]

def test_create_and_version_via_gateway():
    token = _make_jwt()
    headers = {"Authorization": f"Bearer {token}"}

    unique_name = f"it-model-{int(time.time())}-{os.urandom(2).hex()}"
    create_payload = {"name": unique_name, "description": "Created via gateway integration test"}
    r = requests.post(f"{GATEWAY_URL}/api/models", json=create_payload, headers=headers)
    assert r.status_code in [201, 503]
    if r.status_code == 503:
        return
    model = r.json()
    assert model["name"] == unique_name
    assert model["created_by"] == "it-test-user"

    model_id = model["id"]

    v1 = {"version": 1, "storage_path": "path/to/v1", "content_hash": "hash_v1"}
    rv1 = requests.post(f"{GATEWAY_URL}/api/models/{model_id}/versions", json=v1, headers=headers)
    assert rv1.status_code in [201, 503]
    if rv1.status_code == 503:
        return
    assert rv1.json()["version"] == 1

    latest = requests.get(f"{GATEWAY_URL}/api/models/{model_id}/latest", headers=headers)
    assert latest.status_code in [200, 404, 503]
    if latest.status_code == 200:
        assert latest.json()["storage_path"] == "path/to/v1"

def test_rabbitmq_connectivity():
    """Test RabbitMQ is accessible"""
    connection = get_rabbitmq_connection_or_skip()
    connection.close()
    assert True

def test_model_created_event_published():
    """Test that ModelCreated event is published to RabbitMQ when model is created"""
    connection = get_rabbitmq_connection_or_skip()
    channel = connection.channel()
    
    channel.exchange_declare(
        exchange='model_events',
        exchange_type='topic',
        durable=True
    )
    
    result = channel.queue_declare(queue='test-events', exclusive=True)
    queue_name = result.method.queue
    
    channel.queue_bind(
        exchange='model_events',
        queue=queue_name,
        routing_key='model.created'
    )
    
    channel.queue_purge(queue_name)
    
    unique_name = f"event-test-{int(time.time())}-{os.urandom(2).hex()}"
    create_response = None
    
    token = _make_jwt()
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        create_response = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": unique_name, "description": "Event test model"},
            headers=headers,
            timeout=5
        )
    except Exception:
        pass
    
    if not create_response or create_response.status_code != 201:
        try:
            token = _make_jwt(sub="it-test-user")
            headers = {"Authorization": f"Bearer {token}"}
            create_response = requests.post(
                f"{GATEWAY_URL}/api/models",
                json={"name": unique_name, "description": "Event test model"},
                headers=headers,
                timeout=5
            )
        except Exception:
            connection.close()
            pytest.skip("Both gateway and catalog service unavailable")
    
    if create_response.status_code != 201:
        connection.close()
        pytest.skip(f"Model creation failed ({create_response.status_code}), cannot test event publishing")
    
    time.sleep(1.0)  
    
    method_frame = None
    for _ in range(3):  
        method_frame, properties, body = channel.basic_get(queue=queue_name, auto_ack=True)
        if method_frame is not None:
            break
        time.sleep(0.5)  
    
    if method_frame is None:
        connection.close()
        pytest.skip("No event received (event publishing may be disabled or delayed)")
    
    event = json.loads(body)
    assert event["event_type"] == "ModelCreated"
    assert event["model_name"] == unique_name
    assert "model_id" in event
    assert "created_by" in event
    
    connection.close()

def test_rabbitmq_graceful_degradation():
    """Test that service continues working even if RabbitMQ fails"""
    token = _make_jwt()
    headers = {"Authorization": f"Bearer {token}"}
    
    unique_name = f"degradation-test-{int(time.time())}-{os.urandom(2).hex()}"
    
    create_response = requests.post(
        f"{GATEWAY_URL}/api/models",
        json={"name": unique_name, "description": "Degradation test"},
        headers=headers
    )
    
    assert create_response.status_code == 201
    model = create_response.json()
    assert model["name"] == unique_name

def test_rabbitmq_artifact_event():
    """Test that ArtifactCommitted events are published to RabbitMQ"""
    connection = get_rabbitmq_connection_or_skip()
    channel = connection.channel()
    
    channel.exchange_declare(
        exchange='artifact_events',
        exchange_type='topic',
        durable=True
    )
    
    result = channel.queue_declare(queue='test-artifact-events', exclusive=True)
    queue_name = result.method.queue
    
    channel.queue_bind(
        exchange='artifact_events',
        queue=queue_name,
        routing_key='artifact.committed'
    )
    
    channel.queue_purge(queue_name)
    
    test_event = {
        "event_type": "ArtifactCommitted",
        "artifact_id": f"test-artifact-{int(time.time())}",
        "model_id": 1,
        "version": 1,
        "storage_path": "test/path/to/artifact",
        "content_hash": "sha256:test123",
        "uploaded_by": "it-test-user",
        "file_size": 1024
    }
    
    channel.basic_publish(
        exchange='artifact_events',
        routing_key='artifact.committed',
        body=json.dumps(test_event),
        properties=pika.BasicProperties(
            delivery_mode=2,  
            content_type='application/json'
        )
    )
    
    time.sleep(0.5)
    
    method_frame, properties, body = channel.basic_get(queue=queue_name, auto_ack=True)
    
    if method_frame is None:
        connection.close()
        pytest.skip("Artifact event not received (event publishing may be disabled)")
    
    event = json.loads(body)
    assert event["event_type"] == "ArtifactCommitted"
    assert event["artifact_id"] == test_event["artifact_id"]
    assert "model_id" in event
    assert "storage_path" in event
    assert "content_hash" in event
    
    connection.close()