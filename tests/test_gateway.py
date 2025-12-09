import pytest
import requests
import jwt
from datetime import datetime, timedelta, timezone

GATEWAY_URL = "http://localhost:8080"

SECRET_KEY = "your-secret-key" # Hardcoded for local dev, in prod use .env
ALGORITHM = "HS256"

@pytest.fixture(scope="session")
def access_token():
    """Create a signed JWT accepted by the gateway."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "test-user",
        "scopes": ["api:read", "api:write"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=30)).timestamp()),
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token

@pytest.fixture(scope="session")
def auth_headers(access_token):
    """Get headers with authentication token"""
    return {"Authorization": f"Bearer {access_token}"}

def test_health_check():
    response = requests.get(f"{GATEWAY_URL}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    

# Public catalog browsing tests
def test_public_catalog_browsing_without_auth():
    """Test that GET /api/models is public (no auth required)"""
    response = requests.get(f"{GATEWAY_URL}/api/models")
    assert response.status_code in [200, 503, 404], f"Expected public access, got {response.status_code}"

def test_public_catalog_browsing_with_auth(auth_headers):
    """Test that GET /api/models works with authentication too"""
    response = requests.get(f"{GATEWAY_URL}/api/models", headers=auth_headers)
    assert response.status_code in [200, 503, 404]

def test_public_model_details_without_auth():
    """Test that GET /api/models/{id} is public (no auth required)"""
    response = requests.get(f"{GATEWAY_URL}/api/models/999999")
    assert response.status_code in [404, 503], f"Expected public access, got {response.status_code}"

# Protected route tests (write operations)
def test_protected_write_route_without_auth():
    """Test that POST/PUT/DELETE require authentication"""
    response = requests.post(f"{GATEWAY_URL}/api/models", json={"name": "test"})
    assert response.status_code == 401, "POST should require authentication"
    
    response = requests.delete(f"{GATEWAY_URL}/api/models/1")
    assert response.status_code == 401, "DELETE should require authentication"

def test_protected_route_with_auth(auth_headers):
    response = requests.get(f"{GATEWAY_URL}/api/models", headers=auth_headers)
    assert response.status_code in [200, 503, 404]

def test_protected_route_with_invalid_token():
    """Test accessing protected route (POST/DELETE) with an invalid token"""
    headers = {"Authorization": "Bearer invalid-token"}
    response = requests.post(f"{GATEWAY_URL}/api/models", json={"name": "test"}, headers=headers)
    assert response.status_code == 401, "Protected write operations should require valid auth"

# Public route tests
def test_public_route():
    """Test accessing public routes without authentication"""
    response = requests.get(f"{GATEWAY_URL}/public/models")
    assert response.status_code in [200, 503, 404]

# Rate limiting tests
def test_rate_limiting_authenticated(auth_headers):
    """Test rate limiting for authenticated users (if Redis is running)"""
    for i in range(10):
        response = requests.get(f"{GATEWAY_URL}/api/models", headers=auth_headers)
        if response.status_code == 429:
            assert "Rate limit exceeded" in response.json()["detail"]
            break

def test_rate_limiting_unauthenticated():
    """Test IP-based rate limiting for unauthenticated users"""
    for i in range(10):
        response = requests.get(f"{GATEWAY_URL}/api/models")
        if response.status_code == 429:
            assert "Rate limit exceeded" in response.json()["detail"]
            break
