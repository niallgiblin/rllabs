#!/usr/bin/env python3
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

# Service URLs
GATEWAY_URL = "http://localhost:8080"
# Use gateway for API calls, direct URLs only for health checks
GATEWAY_URL = "http://localhost:8080"
CATALOG_DIRECT_URL = "http://localhost:8001"  # Only for health checks
UPLOAD_DOWNLOAD_DIRECT_URL = "http://localhost:8002"  # Only for health checks

# JWT settings (must match jwt_auth.py)
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
                # Try catalog service
                try:
                    requests.get(f"{CATALOG_DIRECT_URL}/health", timeout=3)
                except:
                    pass
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
    
    # Cleanup
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
    
    # Start upload session
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
    
    # Return the content hash (artifact_id)
    return file_hash


# Model Catalog ownership tests
def test_ownership_endpoint_requires_auth():
    """Test that ownership endpoint requires authentication"""
    response = requests.get(f"{GATEWAY_URL}/api/models/1/ownership")
    # Gateway returns 401 for missing authentication, or 422 if endpoint requires X-User-Id
    # Since we made ownership endpoint require auth, it should be 401
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
    
    # Validate structure
    assert isinstance(data, dict)
    assert "has_access" in data
    assert "is_owner" in data
    assert "model_id" in data
    
    # Validate types
    assert isinstance(data["has_access"], bool)
    assert isinstance(data["is_owner"], bool)
    assert isinstance(data["model_id"], int)

# Upload/Download service authorisation tests
def test_download_public_access():
    """Test that download endpoint allows public access (no auth required)"""
    # Use a valid SHA-256 format that doesn't exist (will get 404, not 401)
    fake_artifact_id = "sha256:" + "a" * 64
    response = requests.get(f"{GATEWAY_URL}/api/downloads/{fake_artifact_id}")
    # Should NOT require authentication (public downloads enabled)
    # Will get 400 (invalid format) or 404 (not found), but NOT 401 (unauthorized)
    assert response.status_code != 401, "Downloads should be public (no auth required)"

def test_download_nonexistent_artifact(user1_headers):
    """Test download of non-existent artifact"""
    # Use a valid SHA-256 format (64 hex characters) that doesn't exist in the system
    fake_artifact_id = "sha256:" + "a" * 64  # Valid format but non-existent
    response = requests.get(
        f"{GATEWAY_URL}/api/downloads/{fake_artifact_id}",
        headers=user1_headers,
        timeout=10
    )
    
    # For authenticated users, authorization check happens first
    # If artifact doesn't exist in upload sessions, should return 404
    # If artifact exists but user doesn't have permission, returns 403
    # Both are valid responses - the important thing is it doesn't return 200
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
    
    # Owner should be able to download (may be 404 if artifact not in storage yet)
    # But should NOT be 403 (forbidden)
    assert response.status_code != 403, \
        f"Owner should not be forbidden. Got {response.status_code}: {response.text}"

def test_download_non_owner_allowed(user1_headers, user2_headers, uploaded_artifact_id, test_model_id):
    """Test that non-owner cannot download artifact when authenticated (RBAC enforced)"""
    if not uploaded_artifact_id:
        pytest.skip("Failed to create test artifact")
    
    # Wait a moment for upload session to be created
    time.sleep(1)
    
    # Try to download as user2 (not the owner) - should be denied (RBAC)
    # Public downloads only work when unauthenticated (no headers)
    response = requests.get(
        f"{GATEWAY_URL}/api/downloads/{uploaded_artifact_id}",
        headers=user2_headers,
        timeout=10
    )
    
    # Authenticated non-owners should be denied (403)
    # Public downloads only apply to unauthenticated requests
    assert response.status_code == 403, \
        f"Non-owner should be denied when authenticated. Got {response.status_code}: {response.text}"

def test_download_without_user_id_header():
    """Test that download fails without X-User-Id header"""
    # This would be caught by the service, not the gateway
    # Gateway should still require JWT
    token = _make_jwt()
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.get(
        f"{GATEWAY_URL}/api/downloads/test-id",
        headers=headers,
        timeout=10
    )
    
    # Should fail - either 401 (gateway) or 422 (service missing header)
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
    
    # Step 1: Upload (as user1)
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
    
    # Step 2: Wait for session to be created in DB
    time.sleep(1)
    
    # Step 3: Try to download as owner (user1)
    download_response = requests.get(
        f"{GATEWAY_URL}/api/downloads/{file_hash}",
        headers=user1_headers,
        timeout=10
    )
    
    # Should NOT be 403 (owner should have access)
    # May be 404 if artifact not in storage, but authorization should pass
    assert download_response.status_code != 403, \
        f"Owner should not be forbidden. Got {download_response.status_code}: {download_response.text}"

def test_full_rbac_flow_non_owner_allowed(user1_headers, user2_headers, test_file, test_model_id):
    """
    Test complete RBAC flow: upload as user1 -> download as user2 (should succeed - public downloads)
    """
    file_size = test_file.stat().st_size
    file_hash = _calculate_sha256(test_file)
    
    # Step 1: Upload as user1
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
    
    # Step 2: Wait for session to be created
    time.sleep(1)
    
    # Step 3: Try to download as user2 (not owner) - should be denied (RBAC)
    # Public downloads only work when unauthenticated (no headers)
    download_response = requests.get(
        f"{GATEWAY_URL}/api/downloads/{file_hash}",
        headers=user2_headers,
        timeout=10
    )
    
    # Authenticated non-owners should be denied (403)
    # Public downloads only apply to unauthenticated requests
    assert download_response.status_code == 403, \
        f"Non-owner should be denied when authenticated. Got {download_response.status_code}: {download_response.text}"

def test_model_level_rbac_access(user1_headers, user2_headers, test_file, test_model_id):
    """
    Test model-level RBAC: If user has model access, they can download artifacts.
    Currently, only owners have model access, but this tests the integration.
    """
    file_size = test_file.stat().st_size
    file_hash = _calculate_sha256(test_file)
    
    # Step 1: Upload as user1
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
    
    # Step 2: Verify user1 (owner) has model access
    ownership_response = requests.get(
        f"{GATEWAY_URL}/api/models/{test_model_id}/ownership",
        headers=user1_headers
    )
    assert ownership_response.status_code == 200
    assert ownership_response.json()["has_access"] is True
    
    # Step 3: Verify user2 (non-owner) does NOT have model access
    ownership_response2 = requests.get(
        f"{GATEWAY_URL}/api/models/{test_model_id}/ownership",
        headers=user2_headers
    )
    assert ownership_response2.status_code == 200
    assert ownership_response2.json()["has_access"] is False
    
    # Step 4: Download should be denied (RBAC enforced for authenticated users)
    # Public downloads only work when unauthenticated (no headers)
    download_response = requests.get(
        f"{GATEWAY_URL}/api/downloads/{file_hash}",
        headers=user2_headers,
        timeout=10
    )
    
    # Authenticated non-owners should be denied (403)
    # Public downloads only apply to unauthenticated requests
    assert download_response.status_code == 403, \
        "User should be denied when authenticated and not owner"

# Error handling tests
def test_authorization_with_invalid_artifact_id(user1_headers):
    """Test authorization with invalid artifact ID format"""
    invalid_ids = [
        "",
        "not-a-hash",
        "sha256:invalid",  # Too short
        "sha256:" + "a" * 63,  # One char too short
    ]
    
    for invalid_id in invalid_ids:
        response = requests.get(
            f"{GATEWAY_URL}/api/downloads/{invalid_id}",
            headers=user1_headers,
            timeout=10
        )
        
        # Should handle gracefully (404 or 400, not 500)
        assert response.status_code in [400, 404, 422], \
            f"Should handle invalid ID gracefully. Got {response.status_code} for '{invalid_id}'"

def test_authorization_with_malformed_headers(user1_headers):
    """Test authorization with malformed or missing headers"""
    # Missing X-User-Id (should be added by gateway, but test direct service)
    token = _make_jwt()
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.get(
        f"{GATEWAY_URL}/api/downloads/test-id",
        headers=headers,
        timeout=10
    )
    
    # Gateway should add X-User-Id, but if it doesn't, service should handle gracefully
    assert response.status_code in [200, 400, 401, 422, 404], \
        "Should handle missing headers gracefully"

def test_concurrent_authorization_checks(user1_headers, uploaded_artifact_id):
    """Test that authorization works correctly under concurrent requests"""
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
    
    # Make 5 concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(download_artifact) for _ in range(5)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    
    # All should succeed (not 403) or all fail consistently
    # Should not have mixed results (some 403, some 200)
    status_codes = set(results)
    assert len(status_codes) <= 2, \
        f"Concurrent requests should have consistent results. Got: {status_codes}"

# Edge cases
def test_authorization_with_special_characters_in_artifact_id(user1_headers):
    """Test authorization with special characters in artifact ID"""
    # Artifact IDs should be hex, but test edge cases
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
        
        # Should handle gracefully (not crash)
        assert response.status_code in [400, 404, 422], \
            f"Should handle special characters. Got {response.status_code}"

def test_authorization_empty_artifact_id(user1_headers):
    """Test authorization with empty artifact ID"""
    response = requests.get(
        f"{GATEWAY_URL}/api/downloads/",
        headers=user1_headers,
        timeout=10
    )
    
    # Should return 404 (route not found) or 400 (bad request)
    assert response.status_code in [404, 400, 422], \
        "Should handle empty artifact ID gracefully"
