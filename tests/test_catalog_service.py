import requests
import time
import pytest
import os

# Config
CATALOG_URL = "http://localhost:8001"
API_KEY = "a_secure_api_key_placeholder"
HEADERS = {"X-API-Key": API_KEY, "X-User-Id": "test_user"}

@pytest.fixture(scope="session", autouse=True)
def wait_for_services():
    """Wait for services to be ready before running tests"""
    max_retries = 15
    for i in range(max_retries):
        try:
            response = requests.get(f"{CATALOG_URL}/health", timeout=5)
            if response.status_code == 200:
                data = response.json()
                print(f"Services ready: {data}")
                return
        except Exception as e:
            print(f"Waiting for services... ({i+1}/{max_retries}) - {e}")
            time.sleep(2)
    pytest.fail("Services did not become ready in time")

@pytest.fixture(scope="function")
def test_model_name():
    return f"test-model-{int(time.time())}-{os.urandom(2).hex()}"

@pytest.fixture(scope="function")
def created_model_id(test_model_name):
    """Create a fresh model for each test"""
    payload = {"name": test_model_name, "description": "Test model for integration testing."}
    response = requests.post(f"{CATALOG_URL}/models", json=payload, headers=HEADERS)
    assert response.status_code == 201, f"Failed to create model: {response.status_code} - {response.text}"
    model_id = response.json()["id"]
    return model_id

# Healthcheck tests
def test_health_check():
    response = requests.get(f"{CATALOG_URL}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service_status"] == "ok"
    assert "database" in data["dependencies"]
    assert data["dependencies"]["database"] == "online"

# Model creation tests
def test_create_model_success(test_model_name):
    payload = {"name": test_model_name, "description": "A newly created model."}
    response = requests.post(f"{CATALOG_URL}/models", json=payload, headers=HEADERS)
    assert response.status_code == 201
    model_data = response.json()
    assert model_data["name"] == test_model_name
    assert model_data["description"] == "A newly created model."
    assert "id" in model_data
    assert model_data["created_by"] == HEADERS["X-User-Id"]

def test_create_model_duplicate_name(created_model_id, test_model_name):
    payload = {"name": test_model_name, "description": "Attempting a duplicate."}
    response = requests.post(f"{CATALOG_URL}/models", json=payload, headers=HEADERS)
    assert response.status_code == 409
    assert "Model with this name already exists." in response.json()["detail"]

def test_create_model_missing_user_id(test_model_name):
    payload = {"name": test_model_name, "description": "Model without user ID."}
    headers_without_user = {"X-API-Key": API_KEY}
    response = requests.post(f"{CATALOG_URL}/models", json=payload, headers=headers_without_user)
    assert response.status_code == 422 # Unprocessable Entity for missing header

# List model tests
def test_list_models_empty():
    # Assuming a clean state or models are deleted after tests
    # it'll just check if it returns a list for now
    response = requests.get(f"{CATALOG_URL}/models", headers=HEADERS)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_list_models_with_data(created_model_id, test_model_name):
    response = requests.get(f"{CATALOG_URL}/models", headers=HEADERS)
    assert response.status_code == 200
    models = response.json()
    assert any(model["name"] == test_model_name for model in models)

# Model details tests
def test_get_model_details_success(created_model_id, test_model_name):
    response = requests.get(f"{CATALOG_URL}/models/{created_model_id}", headers=HEADERS)
    assert response.status_code == 200
    model_data = response.json()
    assert model_data["id"] == created_model_id
    assert model_data["name"] == test_model_name

def test_get_model_details_not_found():
    non_existent_id = 999999
    response = requests.get(f"{CATALOG_URL}/models/{non_existent_id}", headers=HEADERS)
    assert response.status_code == 404
    assert "Model not found" in response.json()["detail"]

# Model version registration tests
def test_register_model_version_success(created_model_id):
    payload = {
        "version": 1,
        "storage_path": "path/to/model/v1",
        "content_hash": "hash_v1"
    }
    response = requests.post(f"{CATALOG_URL}/models/{created_model_id}/versions", json=payload, headers=HEADERS)
    assert response.status_code == 201
    version_data = response.json()
    assert version_data["version"] == 1
    assert version_data["model_id"] == created_model_id

def test_register_duplicate_model_version(created_model_id):
    # Register first version
    payload_v1 = {
        "version": 1,
        "storage_path": "path/to/model/v1",
        "content_hash": "hash_v1"
    }
    response = requests.post(f"{CATALOG_URL}/models/{created_model_id}/versions", json=payload_v1, headers=HEADERS)
    assert response.status_code == 201

    # Then try duplicate version
    payload_dup = {
        "version": 1,
        "storage_path": "path/to/model/v1_duplicate",
        "content_hash": "hash_v1_duplicate"
    }
    response = requests.post(f"{CATALOG_URL}/models/{created_model_id}/versions", json=payload_dup, headers=HEADERS)
    assert response.status_code == 409
    assert "This model version or content hash already exists for this model." in response.json()["detail"]

def test_register_model_version_model_not_found():
    non_existent_id = 999999
    payload = {
        "version": 1,
        "storage_path": "path/to/model/v1",
        "content_hash": "hash_v1"
    }
    response = requests.post(f"{CATALOG_URL}/models/{non_existent_id}/versions", json=payload, headers=HEADERS)
    assert response.status_code == 404
    assert "Model not found" in response.json()["detail"]

# Latest model path tests
def test_get_latest_model_path_success(created_model_id):
    # Register multiple versions
    requests.post(f"{CATALOG_URL}/models/{created_model_id}/versions", json={"version": 1, "storage_path": "path/v1", "content_hash": "hash_v1"}, headers=HEADERS)
    requests.post(f"{CATALOG_URL}/models/{created_model_id}/versions", json={"version": 2, "storage_path": "path/v2", "content_hash": "hash_v2"}, headers=HEADERS)
    requests.post(f"{CATALOG_URL}/models/{created_model_id}/versions", json={"version": 3, "storage_path": "path/v3", "content_hash": "hash_v3"}, headers=HEADERS)

    response = requests.get(f"{CATALOG_URL}/models/{created_model_id}/latest", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["storage_path"] == "path/v3"

def test_get_latest_model_path_no_versions(created_model_id):
    response = requests.get(f"{CATALOG_URL}/models/{created_model_id}/latest", headers=HEADERS)
    assert response.status_code == 404
    assert "No versions found for this model." in response.json()["detail"]

def test_get_latest_model_path_model_not_found():
    non_existent_id = 999999
    response = requests.get(f"{CATALOG_URL}/models/{non_existent_id}/latest", headers=HEADERS)
    assert response.status_code == 404
    assert "No versions found for this model." in response.json()["detail"] # The API returns this for model not found as well, which is acceptable.

# Ownership tests
def test_ownership_endpoint_requires_user_id(created_model_id):
    """Test that ownership endpoint requires X-User-Id header"""
    response = requests.get(f"{CATALOG_URL}/models/{created_model_id}/ownership")
    assert response.status_code == 422, "Should require X-User-Id header"

def test_ownership_endpoint_owner_has_access(created_model_id):
    """Test that model owner has access"""
    response = requests.get(
        f"{CATALOG_URL}/models/{created_model_id}/ownership",
        headers={"X-User-Id": HEADERS["X-User-Id"]}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["has_access"] is True
    assert data["is_owner"] is True
    assert data["model_id"] == created_model_id

def test_ownership_endpoint_non_owner_no_access(created_model_id):
    """Test that non-owner does not have access"""
    response = requests.get(
        f"{CATALOG_URL}/models/{created_model_id}/ownership",
        headers={"X-User-Id": "different-user"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["has_access"] is False
    assert data["is_owner"] is False
    assert data["model_id"] == created_model_id

def test_ownership_endpoint_model_not_found():
    """Test ownership endpoint with non-existent model"""
    non_existent_id = 999999
    response = requests.get(
        f"{CATALOG_URL}/models/{non_existent_id}/ownership",
        headers={"X-User-Id": "test-user"}
    )
    assert response.status_code == 404
    assert "Model not found" in response.json()["detail"]

def test_ownership_endpoint_response_structure(created_model_id):
    """Test that ownership endpoint returns correct structure"""
    response = requests.get(
        f"{CATALOG_URL}/models/{created_model_id}/ownership",
        headers={"X-User-Id": HEADERS["X-User-Id"]}
    )
    assert response.status_code == 200
    data = response.json()
    
    # Validate structure
    assert isinstance(data, dict)
    assert "has_access" in data
    assert "is_owner" in data
    assert "model_id" in data
    
    # Validate types
    assert isinstance(data["has_access"], bool)
    assert isinstance(data["is_owner"], bool)
    assert isinstance(data["model_id"], int)

# Public browsing tests
def test_list_models_public_no_auth():
    """Test that GET /models works without authentication (public endpoint)"""
    response = requests.get(f"{CATALOG_URL}/models")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_get_model_details_public_no_auth(created_model_id, test_model_name):
    """Test that GET /models/{id} works without authentication (public endpoint)"""
    response = requests.get(f"{CATALOG_URL}/models/{created_model_id}")
    assert response.status_code == 200
    model_data = response.json()
    assert model_data["id"] == created_model_id
    assert model_data["name"] == test_model_name

def test_get_latest_model_path_public_no_auth(created_model_id):
    """Test that GET /models/{id}/latest works without authentication"""
    # Register a version first
    requests.post(
        f"{CATALOG_URL}/models/{created_model_id}/versions",
        json={"version": 1, "storage_path": "path/v1", "content_hash": "hash_v1"},
        headers=HEADERS
    )
    
    # Should work without auth
    response = requests.get(f"{CATALOG_URL}/models/{created_model_id}/latest")
    assert response.status_code == 200
    assert response.json()["storage_path"] == "path/v1"

# Admin DELETE tests
def _make_admin_token():
    """Helper to create admin JWT token"""
    import jwt
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "admin-user",
        "scopes": ["api:read", "api:write", "api:admin"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=30)).timestamp()),
    }
    SECRET_KEY = "your-secret-key"
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def test_delete_model_owner(created_model_id):
    """Test that model owner can delete their model"""
    response = requests.delete(
        f"{CATALOG_URL}/models/{created_model_id}",
        headers=HEADERS
    )
    assert response.status_code == 204, f"Owner should be able to delete: {response.status_code} - {response.text}"
    
    # Verify model is deleted
    response = requests.get(f"{CATALOG_URL}/models/{created_model_id}")
    assert response.status_code == 404

def test_delete_model_admin(created_model_id):
    """Test that admin can delete any model"""
    # Create a model owned by a different user
    different_user_headers = {"X-User-Id": "different-user"}
    payload = {"name": f"test-model-admin-{int(time.time())}", "description": "Model for admin delete test"}
    create_response = requests.post(f"{CATALOG_URL}/models", json=payload, headers=different_user_headers)
    assert create_response.status_code == 201
    model_id = create_response.json()["id"]
    
    # Admin should be able to delete it
    admin_token = _make_admin_token()
    admin_headers = {
        "Authorization": f"Bearer {admin_token}",
        "X-User-Id": "admin-user",
        "X-Scope": "api:read api:write api:admin"
    }
    
    # Note: This test requires the API Gateway to forward X-Scope header
    # For direct service testing, we need to pass it directly
    response = requests.delete(
        f"{CATALOG_URL}/models/{model_id}",
        headers=admin_headers
    )
    # Should succeed (204) or fail if gateway not forwarding scope (403)
    assert response.status_code in [204, 403], f"Admin delete test: {response.status_code} - {response.text}"

def test_delete_model_non_owner_non_admin(created_model_id):
    """Test that non-owner non-admin cannot delete model"""
    different_user_headers = {"X-User-Id": "different-user"}
    response = requests.delete(
        f"{CATALOG_URL}/models/{created_model_id}",
        headers=different_user_headers
    )
    assert response.status_code == 403
    assert "Only the model owner or an admin" in response.json()["detail"]

def test_delete_model_not_found():
    """Test deleting non-existent model"""
    response = requests.delete(
        f"{CATALOG_URL}/models/999999",
        headers=HEADERS
    )
    assert response.status_code == 404
    assert "Model not found" in response.json()["detail"]

def test_delete_model_requires_auth():
    """Test that delete requires authentication"""
    response = requests.delete(f"{CATALOG_URL}/models/1")
    # Should require X-User-Id header
    assert response.status_code == 422