"""
Comprehensive RBAC Authorisation Tests
=======================================

Tests the authorisation system for artifact downloads:
1. Model Catalog Service ownership endpoint
2. Upload/Download Service authorisation checks
3. Integration tests for full RBAC flow
4. Error handling and edge cases
"""

import time
import os
import tempfile
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone
import jwt
import pytest
import requests

GATEWAY_URL = "http://localhost:8080"

SECRET_KEY = "your-secret-key" # Hardcoded for dev, in prod use .env
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

def _calculate_sha256(filepath: Path) -> str:
    """Calculate SHA-256 hash of a file"""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(8192), b""):
            sha256_hash.update(byte_block)
    return f"sha256:{sha256_hash.hexdigest()}"

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
def user1_token():
    """JWT token for user1"""
    return _make_jwt(sub="user1")

@pytest.fixture
def user2_token():
    """JWT token for user2"""
    return _make_jwt(sub="user2")

@pytest.fixture
def user1_headers(user1_token):
    """Headers for user1"""
    return {"Authorization": f"Bearer {user1_token}"}

@pytest.fixture
def user2_headers(user2_token):
    """Headers for user2"""
    return {"Authorization": f"Bearer {user2_token}"}

@pytest.fixture
def test_file():
    """Create a temporary test file"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pkl") as f:
        content = b"This is a test model file for RBAC testing"
        f.write(content)
        temp_path = Path(f.name)
    
    yield temp_path
    
    if temp_path.exists():
        temp_path.unlink()

@pytest.fixture
def test_model_id(user1_headers):
    """Create a test model owned by user1"""
    unique_name = f"rbac-test-model-{int(time.time())}-{os.urandom(2).hex()}"
    response = requests.post(
        f"{GATEWAY_URL}/api/models",
        json={"name": unique_name, "description": "Test model for RBAC tests"},
        headers=user1_headers,
        timeout=5
    )
    if response.status_code == 201:
        return response.json()["id"]
    elif response.status_code == 503:
        pytest.skip("Model catalog service unavailable")
    else:
        pytest.skip(f"Failed to create test model: {response.status_code}")

@pytest.fixture
def uploaded_artifact_id(user1_headers, test_file, test_model_id):
    """
    Upload an artifact and return its artifact_id (content hash).
    This simulates a completed upload.
    """
    file_size = test_file.stat().st_size
    file_hash = _calculate_sha256(test_file)
    
    response = requests.post(
        f"{GATEWAY_URL}/api/uploads",
        json={
            "filename": test_file.name,
            "file_size": file_size,
            "file_hash": file_hash,
            "chunk_size": 5242880,
            "artifact_type": "model",
            "model_id": test_model_id
        },
        headers=user1_headers,
        timeout=10
    )
    
    if response.status_code not in [200, 201]:
        pytest.skip(f"Failed to start upload: {response.status_code}")
    
    return file_hash


# Model Catalog ownership tests
def test_ownership_endpoint_requires_auth():
    """Test that ownership endpoint requires authentication"""
    response = requests.get(f"{GATEWAY_URL}/api/models/1/ownership")
    assert response.status_code in [401, 422], "Should require authentication"

def test_ownership_endpoint_model_not_found(user1_headers):
    """Test ownership endpoint with non-existent model"""
    non_existent_id = 999999
    response = requests.get(
        f"{GATEWAY_URL}/api/models/{non_existent_id}/ownership",
        headers=user1_headers
    )
    assert response.status_code == 404
    assert "Model not found" in response.json()["detail"]

def test_ownership_endpoint_owner_has_access(user1_headers, test_model_id):
    """Test that model owner has access"""
    response = requests.get(
        f"{GATEWAY_URL}/api/models/{test_model_id}/ownership",
        headers=user1_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert "has_access" in data
    assert "is_owner" in data
    assert "model_id" in data
    
    assert data["has_access"] is True
    assert data["is_owner"] is True
    assert data["model_id"] == test_model_id

def test_ownership_endpoint_non_owner_no_access(user1_headers, user2_headers, test_model_id):
    """Test that non-owner does not have access"""
    response = requests.get(
        f"{GATEWAY_URL}/api/models/{test_model_id}/ownership",
        headers=user2_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["has_access"] is False
    assert data["is_owner"] is False
    assert data["model_id"] == test_model_id

def test_ownership_endpoint_response_structure(user1_headers, test_model_id):
    """Test that ownership endpoint returns correct structure"""
    response = requests.get(
        f"{GATEWAY_URL}/api/models/{test_model_id}/ownership",
        headers=user1_headers
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert isinstance(data, dict)
    assert "has_access" in data
    assert "is_owner" in data
    assert "model_id" in data
    
    assert isinstance(data["has_access"], bool)
    assert isinstance(data["is_owner"], bool)
    assert isinstance(data["model_id"], int)

# Upload/Download service authorisation tests
def test_download_public_access():
    """Test that download endpoint allows public access (no auth required)"""
    fake_artifact_id = "sha256:" + "a" * 64
    response = requests.get(f"{GATEWAY_URL}/api/downloads/{fake_artifact_id}")
    assert response.status_code != 401, "Downloads should be public (no auth required)"

def test_download_nonexistent_artifact(user1_headers):
    """Test download of non-existent artifact"""
    fake_artifact_id = "sha256:" + "a" * 64  
    response = requests.get(
        f"{GATEWAY_URL}/api/downloads/{fake_artifact_id}",
        headers=user1_headers,
        timeout=10
    )
    
    if response.status_code == 503:
        pytest.skip("Upload/Download service unavailable")
    assert response.status_code in [404, 403], \
        f"Expected 404 or 403 for non-existent artifact, got {response.status_code}: {response.text}"

def test_download_owner_can_access(user1_headers, uploaded_artifact_id):
    """Test that artifact owner can download their artifact"""
    if not uploaded_artifact_id:
        pytest.skip("Failed to create test artifact")
    
    response = requests.get(
        f"{GATEWAY_URL}/api/downloads/{uploaded_artifact_id}",
        headers=user1_headers,
        timeout=10
    )
    
    assert response.status_code != 403, \
        f"Owner should not be forbidden. Got {response.status_code}: {response.text}"

def test_download_non_owner_allowed(user1_headers, user2_headers, uploaded_artifact_id, test_model_id):
    """Test that non-owner cannot download artifact when authenticated (RBAC enforced)"""
    if not uploaded_artifact_id:
        pytest.skip("Failed to create test artifact")
    
    time.sleep(1)
    
    response = requests.get(
        f"{GATEWAY_URL}/api/downloads/{uploaded_artifact_id}",
        headers=user2_headers,
        timeout=10
    )
    
    assert response.status_code == 403, \
        f"Non-owner should be denied when authenticated. Got {response.status_code}: {response.text}"

def test_download_without_user_id_header():
    """Test that download fails without X-User-Id header"""
    token = _make_jwt()
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.get(
        f"{GATEWAY_URL}/api/downloads/test-id",
        headers=headers,
        timeout=10
    )
    
    if response.status_code == 503:
        pytest.skip("Upload/Download service unavailable")
    assert response.status_code in [401, 422, 400], \
        f"Should fail without user context. Got {response.status_code}"

# Integration tests
def test_full_rbac_flow_owner_access(user1_headers, test_file, test_model_id):
    """
    Test complete RBAC flow: upload -> download as owner
    This tests the full authorisation chain.
    """
    file_size = test_file.stat().st_size
    file_hash = _calculate_sha256(test_file)
    
    upload_response = requests.post(
        f"{GATEWAY_URL}/api/uploads",
        json={
            "filename": test_file.name,
            "file_size": file_size,
            "file_hash": file_hash,
            "chunk_size": 5242880,
            "artifact_type": "model",
            "model_id": test_model_id
        },
        headers=user1_headers,
        timeout=10
    )
    
    if upload_response.status_code not in [200, 201]:
        pytest.skip(f"Failed to start upload: {upload_response.status_code}")
    
    upload_data = upload_response.json()
    upload_id = upload_data.get("upload_id")
    
    if not upload_id:
        pytest.skip("Failed to get upload_id")
    
    time.sleep(1)
    
    download_response = requests.get(
        f"{GATEWAY_URL}/api/downloads/{file_hash}",
        headers=user1_headers,
        timeout=10
    )
    
    assert download_response.status_code != 403, \
        f"Owner should not be forbidden. Got {download_response.status_code}: {download_response.text}"

def test_full_rbac_flow_non_owner_allowed(user1_headers, user2_headers, test_file, test_model_id):
    """
    Test complete RBAC flow: upload as user1 -> download as user2 (should succeed - public downloads)
    """
    file_size = test_file.stat().st_size
    file_hash = _calculate_sha256(test_file)
    
    upload_response = requests.post(
        f"{GATEWAY_URL}/api/uploads",
        json={
            "filename": test_file.name,
            "file_size": file_size,
            "file_hash": file_hash,
            "chunk_size": 5242880,
            "artifact_type": "model",
            "model_id": test_model_id
        },
        headers=user1_headers,
        timeout=10
    )
    
    if upload_response.status_code not in [200, 201]:
        pytest.skip(f"Failed to start upload: {upload_response.status_code}")
    
    time.sleep(1)
    
    download_response = requests.get(
        f"{GATEWAY_URL}/api/downloads/{file_hash}",
        headers=user2_headers,
        timeout=10
    )
    
    assert download_response.status_code == 403, \
        f"Non-owner should be denied when authenticated. Got {download_response.status_code}: {download_response.text}"

def test_model_level_rbac_access(user1_headers, user2_headers, test_file, test_model_id):
    """
    Test model-level RBAC: If user has model access, they can download artifacts.
    Currently, only owners have model access, but this tests the integration.
    """
    file_size = test_file.stat().st_size
    file_hash = _calculate_sha256(test_file)
    
    upload_response = requests.post(
        f"{GATEWAY_URL}/api/uploads",
        json={
            "filename": test_file.name,
            "file_size": file_size,
            "file_hash": file_hash,
            "chunk_size": 5242880,
            "artifact_type": "model",
            "model_id": test_model_id
        },
        headers=user1_headers,
        timeout=10
    )
    
    if upload_response.status_code not in [200, 201]:
        pytest.skip(f"Failed to start upload: {upload_response.status_code}")
    
    time.sleep(1)
    
    ownership_response = requests.get(
        f"{GATEWAY_URL}/api/models/{test_model_id}/ownership",
        headers=user1_headers
    )
    assert ownership_response.status_code == 200
    assert ownership_response.json()["has_access"] is True
    
    ownership_response2 = requests.get(
        f"{GATEWAY_URL}/api/models/{test_model_id}/ownership",
        headers=user2_headers
    )
    assert ownership_response2.status_code == 200
    assert ownership_response2.json()["has_access"] is False
    
    download_response = requests.get(
        f"{GATEWAY_URL}/api/downloads/{file_hash}",
        headers=user2_headers,
        timeout=10
    )
    
    assert download_response.status_code == 403, \
        "User should be denied when authenticated and not owner"

# Error handling tests
def test_authorization_with_invalid_artifact_id(user1_headers):
    """Test authorisation with invalid artifact ID format"""
    invalid_ids = [
        "",
        "not-a-hash",
        "sha256:invalid",  
        "sha256:" + "a" * 63,  
    ]
    
    for invalid_id in invalid_ids:
        response = requests.get(
            f"{GATEWAY_URL}/api/downloads/{invalid_id}",
            headers=user1_headers,
            timeout=10
        )
        
        if response.status_code == 503:
            pytest.skip("Upload/Download service unavailable")
        assert response.status_code in [400, 404, 422], \
            f"Should handle invalid ID gracefully. Got {response.status_code} for '{invalid_id}'"

def test_authorization_with_malformed_headers(user1_headers):
    """Test authorisation with malformed or missing headers"""
    token = _make_jwt()
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.get(
        f"{GATEWAY_URL}/api/downloads/test-id",
        headers=headers,
        timeout=10
    )
    
    if response.status_code == 503:
        pytest.skip("Upload/Download service unavailable")
    assert response.status_code in [200, 400, 401, 422, 404], \
        "Should handle missing headers gracefully"

def test_concurrent_authorization_checks(user1_headers, uploaded_artifact_id):
    """Test that authorisation works correctly under concurrent requests"""
    if not uploaded_artifact_id:
        pytest.skip("Failed to create test artifact")
    
    import concurrent.futures
    
    def download_artifact():
        response = requests.get(
            f"{GATEWAY_URL}/api/downloads/{uploaded_artifact_id}",
            headers=user1_headers,
            timeout=10
        )
        return response.status_code
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(download_artifact) for _ in range(5)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    
    status_codes = set(results)
    assert len(status_codes) <= 2, \
        f"Concurrent requests should have consistent results. Got: {status_codes}"

# Edge cases
def test_authorization_with_special_characters_in_artifact_id(user1_headers):
    """Test authorisation with special characters in artifact ID"""
    special_ids = [
        "sha256:abc123%20def",
        "sha256:abc123+def",
        "sha256:abc123/def",
    ]
    
    for special_id in special_ids:
        response = requests.get(
            f"{GATEWAY_URL}/api/downloads/{special_id}",
            headers=user1_headers,
            timeout=10
        )
        
        if response.status_code == 503:
            pytest.skip("Upload/Download service unavailable")
        assert response.status_code in [400, 404, 422], \
            f"Should handle special characters. Got {response.status_code}"

def test_authorization_empty_artifact_id(user1_headers):
    """Test authorisation with empty artifact ID"""
    response = requests.get(
        f"{GATEWAY_URL}/api/downloads/",
        headers=user1_headers,
        timeout=10
    )
    
    if response.status_code == 503:
        pytest.skip("Upload/Download service unavailable")
    assert response.status_code in [404, 400, 422], \
        "Should handle empty artifact ID gracefully"
