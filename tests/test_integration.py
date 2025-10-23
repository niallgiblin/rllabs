import requests
import time
import pytest

# Config
CATALOG_URL = "http://localhost:8001"
API_KEY = "a_secure_api_key_placeholder"
HEADERS = {"X-API-Key": API_KEY}
INVALID_HEADERS = {"X-API-Key": "invalid-key"}

# Fixtures
@pytest.fixture(scope="module")
def test_model_name():
    """Generate a unique model name for the test session.
    
    Why: Prevent tests from clashing with each other,
    especially if they run in parallel or if old data is left in the database.
    """
    return f"test-model-{int(time.time())}"

@pytest.fixture(scope="module")
def created_model_id(test_model_name):
    """Get the ID of the test model, creating it if it doesn't exist.
    
    Why: Idempotency. It prevents conflicts between tests that might also
    create the same model, making the test suite resilient to execution order.
    """
    # Check if model already exists from another test run in this session
    response = requests.get(f"{CATALOG_URL}/models")
    assert response.status_code == 200
    models = response.json()
    existing_model = next((m for m in models if m['name'] == test_model_name), None)

    if existing_model:
        model_id = existing_model['id']
        print(f"Setup: Found existing model '{test_model_name}' with ID: {model_id}")
        return model_id
    else:
        # If not found, create one
        print(f"Setup: Model '{test_model_name}' not found, creating it.")
        payload = {"name": test_model_name, "description": "A model for integration testing."}
        response = requests.post(f"{CATALOG_URL}/models", json=payload, headers=HEADERS)
        assert response.status_code == 201, f"Failed to create model. Status: {response.status_code}, Body: {response.text}"
        model_id = response.json()["id"]
        print(f"Setup: Created model '{test_model_name}' with ID: {model_id}")
        return model_id

# Connectivity
def test_health_check():
    """Check the /health endpoint to see if the service is up and running.
    """
    response = requests.get(f"{CATALOG_URL}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service_status"] == "ok"
    assert data["dependencies"]["database"] == "online"

# Authentication
def test_create_model_no_api_key():
    """Try to create a model without an API key.
    
    Why: Ensures endpoints are actually protected.
    """
    payload = {"name": "no-api-key-test", "description": "This should fail."}
    response = requests.post(f"{CATALOG_URL}/models", json=payload)
    assert response.status_code == 403

def test_create_model_invalid_api_key():
    """Try to create a model with the wrong API key.
    
    Why: API key Validation.
    """
    payload = {"name": "invalid-api-key-test", "description": "This should also fail."}
    response = requests.post(f"{CATALOG_URL}/models", json=payload, headers=INVALID_HEADERS)
    assert response.status_code == 403

# Endpoints
def test_create_model_and_list(test_model_name):
    """Create a model and then make sure it shows up in the list of all models.
    
    Why: Tests the end-to-end flow for creating and listing.
    """
    payload = {"name": test_model_name, "description": "A model for integration testing."}
    response = requests.post(f"{CATALOG_URL}/models", json=payload, headers=HEADERS)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == test_model_name
    assert "id" in data

    response = requests.get(f"{CATALOG_URL}/models")
    assert response.status_code == 200
    models = response.json()
    assert isinstance(models, list)
    assert any(m["name"] == test_model_name for m in models)

def test_create_model_duplicate_name(test_model_name):
    """Try to create a model with a name that's already been used.
    
    Why: postgreSQL requires model names to be unique.
    """
    payload = {"name": test_model_name, "description": "Attempting a duplicate."}
    response = requests.post(f"{CATALOG_URL}/models", json=payload, headers=HEADERS)
    assert response.status_code in [409]

def test_create_model_invalid_payload():
    """Try to create a model missing the 'name').
    
    Why: The API should know to reject this
    """
    payload = {"description": "This payload is missing the 'name' field."}
    response = requests.post(f"{CATALOG_URL}/models", json=payload, headers=HEADERS)
    assert response.status_code == 422  # 422 means Unprocessable Entity

def test_get_model_details(created_model_id, test_model_name):
    """Check if we can fetch the details of a specific model we already created.
    
    Why: This confirms that we can retrieve individual items by their ID, which is
    a fundamental part of any REST API.
    """
    response = requests.get(f"{CATALOG_URL}/models/{created_model_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == created_model_id
    assert data["name"] == test_model_name

def test_get_model_details_not_found():
    """Try to fetch a model that doesn't exist.
    
    Why: API must return 404 if it doesn't exist.
    """
    non_existent_id = 999999
    response = requests.get(f"{CATALOG_URL}/models/{non_existent_id}")
    assert response.status_code == 404

# Version Endpoint
def test_register_model_version(created_model_id):
    """Register a new version for our test model.
    
    Why: Core functionality for tracking model iterations.
    """
    payload = {
        "version": 1,
        "storage_path": "path/to/model/v1",
        "content_hash": "hash_v1"
    }
    response = requests.post(f"{CATALOG_URL}/models/{created_model_id}/versions", json=payload, headers=HEADERS)
    assert response.status_code == 201
    data = response.json()
    assert data["version"] == 1
    assert data["storage_path"] == "path/to/model/v1"

def test_register_model_version_for_nonexistent_model():
    """Try to add a version to a model that doesn't exist.
    
    Why: Should 404 this.
    """
    non_existent_id = 999999
    payload = {"version": 1, "storage_path": "path/to/model/v1", "content_hash": "hash_fail"}
    response = requests.post(f"{CATALOG_URL}/models/{non_existent_id}/versions", json=payload, headers=HEADERS)
    assert response.status_code == 404

def test_register_duplicate_model_version(created_model_id):
    """Try to register the same version number twice for the same model.
    
    Why: A model can't have two 'version 1's.
    """
    # Version 1 was already created in a previous test so this should be rejected
    payload = {
        "version": 1,
        "storage_path": "path/to/model/v1_duplicate",
        "content_hash": "hash_v1_duplicate"
    }
    response = requests.post(f"{CATALOG_URL}/models/{created_model_id}/versions", json=payload, headers=HEADERS)
    assert response.status_code == 409

def test_register_model_version_invalid_payload(created_model_id):
    """Try to register a version with missing information.
    
    Why: Must have required fields.
    """
    payload = {"storage_path": "path/missing/version"} # Missing 'version' and 'content_hash'
    response = requests.post(f"{CATALOG_URL}/models/{created_model_id}/versions", json=payload, headers=HEADERS)
    assert response.status_code == 422

# Latest Version Endpoint

def test_get_latest_model_path(created_model_id):
    """Check the /latest endpoint to make sure it gives us the newest version.
    
    Why: Latest version must be correct.
    """
    # Register a newer version which should become latest
    payload_v2 = {
        "version": 2,
        "storage_path": "path/to/model/v2",
        "content_hash": "hash_v2"
    }
    response = requests.post(f"{CATALOG_URL}/models/{created_model_id}/versions", json=payload_v2, headers=HEADERS)
    assert response.status_code == 201

    # Check the /latest endpoint for v2
    response = requests.get(f"{CATALOG_URL}/models/{created_model_id}/latest")
    assert response.status_code == 200
    data = response.json()
    assert data["storage_path"] == "path/to/model/v2"

def test_get_latest_model_path_no_versions():
    """Try to get the latest version of a model that has no versions.
    
    Why: If a model exists but has no versions registered, asking for the 'latest'
    should result in a 404.
    """
    # Create new model with no versions
    payload = {"name": f"no-versions-model-{int(time.time())}", "description": "A model with no versions."}
    response = requests.post(f"{CATALOG_URL}/models", json=payload, headers=HEADERS)
    assert response.status_code == 201
    model_id = response.json()["id"]

    # 'latest' path and expect a 404
    response = requests.get(f"{CATALOG_URL}/models/{model_id}/latest")
    assert response.status_code == 404
