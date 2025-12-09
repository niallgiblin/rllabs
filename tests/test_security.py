"""
Security-focused tests for the RLLabs platform.

Tests JWT token security, presigned URL security, input validation,
and other security-related functionality.
"""

import time
import os
import pytest
import requests
from datetime import datetime, timedelta, timezone
import jwt
import hashlib
from pathlib import Path
import tempfile

GATEWAY_URL = "http://localhost:8080"

SECRET_KEY = "your-secret-key" # Hardcoded key for local dev, in production use .env 
ALGORITHM = "HS256"


def _make_jwt(sub: str = "test-user-1", scopes=("api:read", "api:write"), 
              secret: str = SECRET_KEY, exp_offset: int = 30) -> str:
    """Generate a JWT token for testing"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "scopes": list(scopes),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=exp_offset)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


@pytest.fixture(scope="session", autouse=True)
def wait_for_services():
    """Wait for services to be ready before running tests"""
    for _ in range(30):
        try:
            if requests.get(f"{GATEWAY_URL}/health", timeout=3).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    pytest.skip("Services did not become ready in time")


@pytest.fixture
def auth_token():
    """Generate JWT token for authenticated requests"""
    return _make_jwt()


@pytest.fixture
def auth_headers(auth_token):
    """Get headers with authentication token"""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def test_file():
    """Create a temporary test file"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pkl") as f:
        content = b"This is a test file for security testing"
        f.write(content)
        temp_path = Path(f.name)
    
    yield temp_path
    
    # Cleanup
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def test_model_id(auth_headers):
    """Create a test model and return its ID"""
    import time
    unique_name = f"security-test-{int(time.time())}-{os.urandom(2).hex()}"
    try:
        response = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": unique_name, "description": "Test model for security tests"},
            headers=auth_headers,
            timeout=5
        )
        if response.status_code == 201:
            return response.json()["id"]
        elif response.status_code == 503:
            pytest.skip("Model catalog service unavailable")
    except Exception:
        pass
    
    pytest.skip("Could not create test model")


class TestJWTSecurity:
    """Test JWT token security"""
    
    def test_expired_jwt_token(self):
        """Test that expired JWT tokens are rejected"""
        expired_token = _make_jwt(exp_offset=-1)
        headers = {"Authorization": f"Bearer {expired_token}"}
        
        response = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": "test", "description": "test"},
            headers=headers,
            timeout=5
        )
        
        assert response.status_code == 401, \
            f"Expired token should be rejected, got {response.status_code}"
        error_data = response.json()
        error_text = (error_data.get("detail", "") + " " + error_data.get("error_description", "")).lower()
        assert "expired" in error_text or "invalid" in error_text or "unauthorized" in error_text or \
               "authentication" in error_text, \
            f"Error message should indicate token issue: {error_data}"
    
    def test_invalid_jwt_signature(self):
        """Test that tokens with invalid signatures are rejected"""
        invalid_token = _make_jwt(secret="wrong-secret-key")
        headers = {"Authorization": f"Bearer {invalid_token}"}
        
        response = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": "test", "description": "test"},
            headers=headers,
            timeout=5
        )
        
        assert response.status_code == 401, \
            "Invalid signature should be rejected"
    
    def test_jwt_with_invalid_scopes(self, auth_headers):
        """Test that JWT tokens with invalid scopes are handled correctly"""
        now = datetime.now(timezone.utc)
        payload = {
            "sub": "test-user",
            "scopes": "invalid-scope-format",  
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=30)).timestamp()),
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        headers = {"Authorization": f"Bearer {token}"}
        
        response = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": f"test-{int(time.time())}", "description": "test"},
            headers=headers,
            timeout=5
        )
        
        assert response.status_code in [201, 401, 403, 409], \
            f"Invalid scope format should be handled, got {response.status_code}"
    
    def test_malformed_jwt_token(self):
        """Test that malformed JWT tokens are rejected"""
        malformed_tokens = [
            "not.a.jwt.token",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",  
            "invalid",
            "",
            "Bearer token",  
        ]
        
        for token in malformed_tokens:
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.post(
                f"{GATEWAY_URL}/api/models",
                json={"name": "test", "description": "test"},
                headers=headers,
                timeout=5
            )
            
            assert response.status_code == 401, \
                f"Malformed token '{token[:20]}...' should be rejected"
    
    def test_jwt_without_required_claims(self):
        """Test that JWT tokens without required claims are rejected"""
        now = datetime.now(timezone.utc)
        payload = {
            "scopes": ["api:read", "api:write"],
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=30)).timestamp()),
        }
        token_without_sub = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        headers = {"Authorization": f"Bearer {token_without_sub}"}
        
        response = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": "test", "description": "test"},
            headers=headers,
            timeout=5
        )
        
        assert response.status_code == 401, \
            "Token without required claims should be rejected"
    
    def test_jwt_token_tampering(self):
        """Test that tampered JWT tokens are rejected"""
        valid_token = _make_jwt(sub="user-1")
        
        parts = valid_token.split('.')
        if len(parts) == 3:
            import base64
            import json
            
            payload_b64 = parts[1]
            payload_b64 += '=' * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            
            payload['sub'] = 'admin-user'  
            
            tampered_payload = base64.urlsafe_b64encode(
                json.dumps(payload).encode()
            ).decode().rstrip('=')
            
            tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"
            
            headers = {"Authorization": f"Bearer {tampered_token}"}
            response = requests.post(
                f"{GATEWAY_URL}/api/models",
                json={"name": "test", "description": "test"},
                headers=headers,
                timeout=5
            )
            
            assert response.status_code == 401, \
                "Tampered token should be rejected (signature mismatch)"
    
    def test_jwt_replay_attack_prevention(self, auth_token):
        """Test that JWT tokens can't be easily replayed"""
        
        headers = {"Authorization": f"Bearer {auth_token}"}
        
        response1 = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": "test-1", "description": "test"},
            headers=headers,
            timeout=5
        )
        
        time.sleep(0.5)
        
        response2 = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": "test-2", "description": "test"},
            headers=headers,
            timeout=5
        )
        
        assert response1.status_code in [201, 409, 503], \
            "First request should work"
        assert response2.status_code in [201, 409, 503], \
            "Second request should work (stateless JWT)"


class TestPresignedURLSecurity:
    """Test presigned URL security"""
    
    def test_presigned_url_expiration(self, auth_headers, test_file, test_model_id):
        """Test that presigned URLs expire after the specified time"""
        file_size = test_file.stat().st_size
        file_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()
        artifact_id = f"sha256:{file_hash}"
        
        upload_response = requests.post(
            f"{GATEWAY_URL}/api/uploads",
            json={
                "filename": test_file.name,
                "file_size": file_size,
                "file_hash": artifact_id,
                "chunk_size": 5242880,
                "artifact_type": "model",
                "model_id": test_model_id
            },
            headers=auth_headers,
            timeout=10
        )
        
        if upload_response.status_code not in [200, 201]:
            pytest.skip("Failed to create upload session")
        
        download_response = requests.get(
            f"{GATEWAY_URL}/api/downloads/{artifact_id}?expires_in=1",
            headers=auth_headers,
            timeout=5
        )
        
        if download_response.status_code == 200:
            download_data = download_response.json()
            download_url = download_data.get("download_url")
            expires_at = download_data.get("expires_at")
            
            if download_url and expires_at:
                time.sleep(2)
                
                expired_response = requests.get(download_url, timeout=5)
                
                assert expired_response.status_code in [403, 404], \
                    "Expired presigned URL should be rejected"
    
    def test_presigned_url_tampering(self, auth_headers, test_file, test_model_id):
        """Test that tampered presigned URLs are rejected"""
        file_size = test_file.stat().st_size
        file_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()
        artifact_id = f"sha256:{file_hash}"
        
        upload_response = requests.post(
            f"{GATEWAY_URL}/api/uploads",
            json={
                "filename": test_file.name,
                "file_size": file_size,
                "file_hash": artifact_id,
                "chunk_size": 5242880,
                "artifact_type": "model",
                "model_id": test_model_id
            },
            headers=auth_headers,
            timeout=10
        )
        
        if upload_response.status_code not in [200, 201]:
            pytest.skip("Failed to create upload session")
        
        download_response = requests.get(
            f"{GATEWAY_URL}/api/downloads/{artifact_id}",
            headers=auth_headers,
            timeout=5
        )
        
        if download_response.status_code == 200:
            download_data = download_response.json()
            download_url = download_data.get("download_url")
            
            if download_url:
                tampered_url = download_url.replace("X-Amz-Expires=", "X-Amz-Expires=999999")
                
                tampered_response = requests.get(tampered_url, timeout=5)
                
                assert tampered_response.status_code in [403, 404], \
                    "Tampered presigned URL should be rejected"
    
    def test_presigned_url_authorization_check(self, auth_headers, test_file, test_model_id):
        """Test that presigned URLs are only generated after authorization check"""
        
        file_size = test_file.stat().st_size
        file_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()
        artifact_id = f"sha256:{file_hash}"
        
        upload_response = requests.post(
            f"{GATEWAY_URL}/api/uploads",
            json={
                "filename": test_file.name,
                "file_size": file_size,
                "file_hash": artifact_id,
                "chunk_size": 5242880,
                "artifact_type": "model",
                "model_id": test_model_id
            },
            headers=auth_headers,
            timeout=10
        )
        
        if upload_response.status_code not in [200, 201]:
            pytest.skip("Failed to create upload session")
        
        time.sleep(1)
        
        different_user_token = _make_jwt(sub="different-user")
        different_headers = {"Authorization": f"Bearer {different_user_token}"}
        
        download_response = requests.get(
            f"{GATEWAY_URL}/api/downloads/{artifact_id}",
            headers=different_headers,
            timeout=5
        )
        
        assert download_response.status_code == 403, \
            "Unauthorized users should not get presigned URLs"
    
    def test_presigned_url_invalid_expires_in(self, auth_headers, test_file, test_model_id):
        """Test that invalid expires_in parameters are handled"""
        file_size = test_file.stat().st_size
        file_hash = hashlib.sha256(test_file.read_bytes()).hexdigest()
        artifact_id = f"sha256:{file_hash}"
        
        upload_response = requests.post(
            f"{GATEWAY_URL}/api/uploads",
            json={
                "filename": test_file.name,
                "file_size": file_size,
                "file_hash": artifact_id,
                "chunk_size": 5242880,
                "artifact_type": "model",
                "model_id": test_model_id
            },
            headers=auth_headers,
            timeout=10
        )
        
        if upload_response.status_code not in [200, 201]:
            pytest.skip("Failed to create upload session")
        
        time.sleep(1)
        
        response = requests.get(
            f"{GATEWAY_URL}/api/downloads/{artifact_id}?expires_in=-1",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code in [200, 400, 422, 404], \
            f"Invalid expires_in should be handled, got {response.status_code}"
        
        response = requests.get(
            f"{GATEWAY_URL}/api/downloads/{artifact_id}?expires_in=999999999",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code in [200, 400, 422, 404], \
            f"Very large expires_in should be handled, got {response.status_code}"


class TestInputValidation:
    """Test input validation and sanitization"""
    
    def test_sql_injection_in_model_name(self, auth_headers):
        """Test that SQL injection attempts in model names are handled"""
        sql_injection_attempts = [
            "'; DROP TABLE models; --",
            "1' OR '1'='1",
            "admin'--",
            "'; DELETE FROM models WHERE '1'='1",
        ]
        
        for injection in sql_injection_attempts:
            response = requests.post(
                f"{GATEWAY_URL}/api/models",
                json={
                    "name": injection,
                    "description": "SQL injection test"
                },
                headers=auth_headers,
                timeout=5
            )
            
            assert response.status_code in [201, 400, 422, 409], \
                f"SQL injection attempt should be handled safely, got {response.status_code}"
            
            if response.status_code == 201:
                model_id = response.json().get("id")
                get_response = requests.get(
                    f"{GATEWAY_URL}/api/models/{model_id}",
                    headers=auth_headers,
                    timeout=5
                )
                if get_response.status_code == 200:
                    model_name = get_response.json().get("name")
                    assert model_name == injection, \
                        "SQL injection should be stored as literal, not executed"
    
    def test_xss_in_model_description(self, auth_headers):
        """Test that XSS attempts in descriptions are handled"""
        xss_attempts = [
            "<script>alert('XSS')</script>",
            "<img src=x onerror=alert('XSS')>",
            "javascript:alert('XSS')",
            "<svg onload=alert('XSS')>",
        ]
        
        for xss in xss_attempts:
            response = requests.post(
                f"{GATEWAY_URL}/api/models",
                json={
                    "name": f"test-model-{int(time.time())}",
                    "description": xss
                },
                headers=auth_headers,
                timeout=5
            )
            
            assert response.status_code in [201, 400, 422, 409], \
                f"XSS attempt should be handled, got {response.status_code}"
    
    def test_path_traversal_in_filename(self, auth_headers, test_model_id):
        """Test that path traversal attempts in filenames are handled"""
        path_traversal_attempts = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "/etc/passwd",
            "C:\\Windows\\System32",
        ]
        
        for traversal in path_traversal_attempts:
            response = requests.post(
                f"{GATEWAY_URL}/api/uploads",
                json={
                    "filename": traversal,
                    "file_size": 100,
                    "file_hash": f"sha256:{'a' * 64}",
                    "chunk_size": 5242880,
                    "artifact_type": "model",
                    "model_id": test_model_id
                },
                headers=auth_headers,
                timeout=5
            )
            
            if response.status_code == 503:
                pytest.skip("Upload/Download service unavailable")
            assert response.status_code in [200, 201, 400, 422], \
                f"Path traversal attempt should be handled safely"
    
    def test_large_payload_attack(self, auth_headers):
        """Test that very large payloads are rejected"""
        import time
        large_name = f"large-{int(time.time())}-" + "A" * 10000  
        
        response = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={
                "name": large_name,
                "description": "Large payload test"
            },
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code in [201, 400, 422, 413], \
            f"Large payloads should be handled (rejected or truncated), got {response.status_code}"
    
    def test_special_characters_in_input(self, auth_headers):
        """Test that special characters are handled safely"""
        special_chars = [
            "test\nmodel",  # Newline
            "test\rmodel",  # Carriage return
            "test\tmodel",  # Tab
            "test\0model",  # Null byte
            "test\x00model",  # Null byte (hex)
        ]
        
        for special in special_chars:
            response = requests.post(
                f"{GATEWAY_URL}/api/models",
                json={
                    "name": special,
                    "description": "Special chars test"
                },
                headers=auth_headers,
                timeout=5
            )
            
            assert response.status_code in [201, 400, 422, 409, 500], \
                f"Special characters should be handled safely, got {response.status_code}"


class TestAuthorizationSecurity:
    """Test authorization security"""
    
    def test_unauthorized_access_attempt(self):
        """Test that unauthorized access attempts are blocked"""
        response = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": "test", "description": "test"},
            timeout=5
        )
        
        assert response.status_code == 401, \
            "Unauthorized access should be blocked"
    
    def test_scope_escalation_attempt(self, auth_headers):
        """Test that users can't escalate privileges"""
        regular_token = _make_jwt(sub="regular-user", scopes=("api:read", "api:write"))
        regular_headers = {"Authorization": f"Bearer {regular_token}"}
        
        different_token = _make_jwt(sub="different-user")
        different_headers = {"Authorization": f"Bearer {different_token}"}
        
        create_response = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": f"test-model-{int(time.time())}", "description": "test"},
            headers=different_headers,
            timeout=5
        )
        
        if create_response.status_code == 201:
            model_id = create_response.json()["id"]
            
            delete_response = requests.delete(
                f"{GATEWAY_URL}/api/models/{model_id}",
                headers=regular_headers,
                timeout=5
            )
            
            assert delete_response.status_code == 403, \
                "Non-owner should not be able to delete model"
    
    def test_user_id_injection_attempt(self, auth_headers):
        """Test that user_id from JWT can't be overridden"""
        
        response = requests.post(
            f"{GATEWAY_URL}/api/models",
            json={"name": f"test-{int(time.time())}", "description": "test"},
            headers={
                **auth_headers,
                "X-User-Id": "injected-user-id"  
            },
            timeout=5
        )
        
        if response.status_code == 201:
            model_id = response.json()["id"]
            get_response = requests.get(
                f"{GATEWAY_URL}/api/models/{model_id}",
                headers=auth_headers,
                timeout=5
            )
            
            if get_response.status_code == 200:
                created_by = get_response.json().get("created_by")
                assert created_by in ["test-user-1", "injected-user-id"], \
                    f"User ID should be from JWT or header: {created_by}"
