import pytest
import requests
import jwt
from datetime import datetime, timedelta, timezone

# Config
GATEWAY_URL = "http://localhost:8080"

# JWT settings must match jwt_auth.py
SECRET_KEY = "your-secret-key"
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

# Healthcheck
def test_health_check():
    """Test gateway health endpoint"""
    response = requests.get(f"{GATEWAY_URL}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    

# Protected route tests
def test_protected_route_without_auth():
    """Test accessing protected route without authentication"""
    response = requests.get(f"{GATEWAY_URL}/api/models")
    assert response.status_code == 401

def test_protected_route_with_auth(auth_headers):
    """Test accessing protected route with authentication"""
    response = requests.get(f"{GATEWAY_URL}/api/models", headers=auth_headers)
    # Should either succeed or show service unavailable if model-catalog isn't running
    assert response.status_code in [200, 503, 404]

def test_protected_route_with_invalid_token():
    """Test accessing protected route with an invalid token"""
    headers = {"Authorization": "Bearer invalid-token"}
    response = requests.get(f"{GATEWAY_URL}/api/models", headers=headers)
    assert response.status_code == 401

# Public route tests
def test_public_route():
    """Test accessing public routes without authentication"""
    response = requests.get(f"{GATEWAY_URL}/public/models")
    # Should either succeed or show service unavailable
    assert response.status_code in [200, 503, 404]

# Rate limiting tests
def test_rate_limiting(auth_headers):
    """Test rate limiting (if Redis is running)"""
    # Make multiple requests quickly
    for i in range(10):
        response = requests.get(f"{GATEWAY_URL}/api/models", headers=auth_headers)
        # If rate limited, we'll get 429, otherwise 200, 503 or 404
        if response.status_code == 429:
            assert "Rate limit exceeded" in response.json()["detail"]
            break
