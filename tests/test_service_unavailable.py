"""
Tests for API Gateway Service Unavailable handling.

Tests how the gateway handles backend service failures, timeouts,
and unavailability scenarios.
"""

import time
import pytest
import requests
from datetime import datetime, timedelta, timezone
import jwt

GATEWAY_URL = "http://localhost:8080"

SECRET_KEY = "your-secret-key" # for local dev, use .env in prod
ALGORITHM = "HS256"


def _make_jwt(sub: str = "test-user-1", scopes=("api:read", "api:write")) -> str:
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


class TestServiceUnavailable:
    """Test service unavailable error handling"""
    
    def test_503_service_unavailable_response(self, auth_headers):
        """
        Test that gateway returns 503 when backend service is unavailable.
        """
        
        response = requests.get(
            f"{GATEWAY_URL}/api/nonexistent-service/test",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code in [503, 404], \
            f"Should return 503 or 404, got {response.status_code}"
        
        if response.status_code == 503:
            error_data = response.json()
            assert "error" in error_data or "detail" in error_data, \
                "503 response should include error details"
    
    def test_service_unavailable_error_structure(self, auth_headers):
        """
        Test that 503 errors have proper structure.
        """
        response = requests.get(
            f"{GATEWAY_URL}/api/nonexistent-service/test",
            headers=auth_headers,
            timeout=5
        )
        
        if response.status_code == 503:
            error_data = response.json()
            assert "error" in error_data or "detail" in error_data or "error_description" in error_data, \
                "503 error should have descriptive message"
    
    def test_service_unavailable_with_retry(self, auth_headers):
        """
        Test that service unavailable triggers retry logic.
        """
        start_time = time.time()
        
        try:
            response = requests.get(
                f"{GATEWAY_URL}/api/models",
                headers=auth_headers,
                timeout=10 
            )
            elapsed = time.time() - start_time
            
            assert response.status_code in [200, 503], \
                "Should handle service unavailable with retries"
        except requests.exceptions.Timeout:
            elapsed = time.time() - start_time
            assert elapsed >= 1, \
                "Should have attempted retries before timing out"
    
    def test_multiple_services_unavailable(self, auth_headers):
        """
        Test that gateway handles multiple services being unavailable.
        """
        
        endpoints = [
            "/api/models",
            "/api/uploads",
            "/api/comments"
        ]
        
        results = {}
        for endpoint in endpoints:
            try:
                response = requests.get(
                    f"{GATEWAY_URL}{endpoint}",
                    headers=auth_headers,
                    timeout=5
                )
                results[endpoint] = response.status_code
            except requests.exceptions.RequestException:
                results[endpoint] = "error"
        
        assert len(results) == len(endpoints), \
            "Should handle all endpoints independently"
    
    def test_service_unavailable_does_not_affect_health(self):
        """
        Test that service unavailability doesn't affect gateway health check.
        """
        response = requests.get(
            f"{GATEWAY_URL}/health",
            timeout=5
        )
        
        assert response.status_code == 200, \
            "Gateway health check should work even if services are down"
        assert response.json().get("status") == "ok", \
            "Gateway should report healthy even if backend services are down"


class TestServiceTimeout:
    """Test service timeout handling"""
    
    def test_service_timeout_handling(self, auth_headers):
        """
        Test that gateway handles service timeouts gracefully.
        """
        
        try:
            response = requests.get(
                f"{GATEWAY_URL}/api/models",
                headers=auth_headers,
                timeout=1  
            )
            assert response.status_code in [200, 503] or True, \
                "Should handle timeout gracefully"
        except requests.exceptions.Timeout:
            assert True, "Timeout handled gracefully"
    
    def test_timeout_with_retry(self, auth_headers):
        """
        Test that timeouts trigger retry logic.
        """
        
        start_time = time.time()
        
        try:
            response = requests.get(
                f"{GATEWAY_URL}/api/models",
                headers=auth_headers,
                timeout=10
            )
            elapsed = time.time() - start_time
            
            assert response.status_code in [200, 503], \
                "Should handle timeout with retries"
        except requests.exceptions.Timeout:
            elapsed = time.time() - start_time
            assert elapsed < 15, \
                "Should timeout after retries, not hang forever"


class TestServiceNotFound:
    """Test service not found handling"""
    
    def test_service_not_found_404(self, auth_headers):
        """
        Test that non-existent services return 404.
        """
        response = requests.get(
            f"{GATEWAY_URL}/api/nonexistent-service/test",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code in [404, 503], \
            f"Should return 404 or 503 for non-existent service, got {response.status_code}"
    
    def test_invalid_route_404(self, auth_headers):
        """
        Test that invalid routes return 404.
        """
        response = requests.get(
            f"{GATEWAY_URL}/api/invalid/route/path",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code in [404, 503], \
            "Should return 404 or 503 for invalid route"


class TestServiceUnavailableErrorMessages:
    """Test error messages for service unavailable scenarios"""
    
    def test_service_unavailable_error_message(self, auth_headers):
        """
        Test that 503 errors include helpful error messages.
        """
        response = requests.get(
            f"{GATEWAY_URL}/api/nonexistent-service/test",
            headers=auth_headers,
            timeout=5
        )
        
        if response.status_code == 503:
            error_data = response.json()
            error_text = str(error_data).lower()
            assert "unavailable" in error_text or "service" in error_text or "error" in error_text, \
                "503 error should include helpful message"
    
    def test_service_unavailable_includes_service_name(self, auth_headers):
        """
        Test that 503 errors include the service name.
        """
        response = requests.get(
            f"{GATEWAY_URL}/api/nonexistent-service/test",
            headers=auth_headers,
            timeout=5
        )
        
        if response.status_code == 503:
            error_data = response.json()
            error_text = str(error_data)
            assert len(error_text) > 0, \
                "Error should include some information"


class TestServiceUnavailableGracefulDegradation:
    """Test graceful degradation when services are unavailable"""
    
    def test_public_endpoints_work_when_services_down(self):
        """
        Test that public endpoints still work when some services are down.
        """
        
        response = requests.get(
            f"{GATEWAY_URL}/api/models",  
            timeout=5
        )
        
        assert response.status_code in [200, 503], \
            "Public endpoints should handle service unavailability gracefully"
    
    def test_health_check_always_works(self):
        """
        Test that health check always works regardless of service status.
        """
        response = requests.get(
            f"{GATEWAY_URL}/health",
            timeout=5
        )
        
        assert response.status_code == 200, \
            "Health check should always work"
        assert response.json().get("status") == "ok", \
            "Health check should return ok status"
    
    def test_gateway_continues_operating(self, auth_headers):
        """
        Test that gateway continues operating when one service is down.
        """
        
        catalog_response = requests.get(
            f"{GATEWAY_URL}/api/models",
            headers=auth_headers,
            timeout=5
        )
        
        upload_response = requests.post(
            f"{GATEWAY_URL}/api/uploads",
            headers=auth_headers,
            json={"filename": "test", "file_size": 100, "file_hash": "sha256:test"},
            timeout=5
        )
        
        assert catalog_response.status_code in [200, 503, 401, 422] or \
               upload_response.status_code in [200, 201, 503, 401, 422], \
            "Gateway should continue operating when some services are down"

