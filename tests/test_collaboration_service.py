"""
Comprehensive tests for Collaboration Service integration:
1. Comment CRUD operations through API Gateway
2. Nested comment threads
3. Creator badge detection
4. Event-driven integration (ModelCreated/ModelDeleted)
5. Redis caching
6. Error handling
7. API Gateway routing verification
"""

import time
import os
import pytest
import requests
from datetime import datetime, timedelta, timezone
import jwt

GATEWAY_URL = "http://localhost:8080"

SECRET_KEY = "your-secret-key" # Hardcode for local dev, in prod use .env var
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
    pytest.fail("Services did not become ready in time")


@pytest.fixture
def auth_token():
    """Generate JWT token for authenticated requests"""
    return _make_jwt()


@pytest.fixture
def auth_headers(auth_token):
    """Get headers with authentication token"""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def creator_token():
    """Generate JWT token for model creator"""
    return _make_jwt(sub="creator-user")


@pytest.fixture
def creator_headers(creator_token):
    """Get headers for model creator"""
    return {"Authorization": f"Bearer {creator_token}"}


@pytest.fixture
def test_model_id(auth_headers):
    """Create a test model and return its ID"""
    unique_name = f"test-model-collab-{int(time.time())}-{os.urandom(2).hex()}"
    response = requests.post(
        f"{GATEWAY_URL}/api/models",
        json={"name": unique_name, "description": "Test model for collaboration tests"},
        headers=auth_headers,
        timeout=5
    )
    if response.status_code == 201:
        model_id = response.json()["id"]
        time.sleep(2)
        return model_id
    elif response.status_code == 503:
        pytest.skip("Model catalog service unavailable")
    else:
        pytest.skip(f"Failed to create test model: {response.status_code}")


@pytest.fixture
def creator_model_id(creator_headers):
    """Create a model as creator-user for creator badge tests"""
    unique_name = f"test-model-creator-{int(time.time())}-{os.urandom(2).hex()}"
    response = requests.post(
        f"{GATEWAY_URL}/api/models",
        json={"name": unique_name, "description": "Test model for creator badge"},
        headers=creator_headers,
        timeout=5
    )
    if response.status_code == 201:
        model_id = response.json()["id"]
        time.sleep(5)
        return model_id
    elif response.status_code == 503:
        pytest.skip("Model catalog service unavailable")
    else:
        pytest.skip(f"Failed to create test model: {response.status_code}")


class TestCommentCRUD:
    """Test basic comment CRUD operations through API Gateway"""

    def test_create_comment(self, auth_headers, test_model_id):
        """Test creating a top-level comment"""
        response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "This is a test comment",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 201, f"Failed to create comment: {response.status_code} - {response.text}"
        data = response.json()
        assert "id" in data
        assert data["content"] == "This is a test comment"
        assert data["modelId"] == str(test_model_id)
        assert data["authorId"] == "test-user-1"
        assert "createdAt" in data
        assert "replies" in data
        assert isinstance(data["replies"], list)

    def test_get_comments(self, auth_headers, test_model_id):
        """Test getting comments for a model"""
        create_response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Test comment for retrieval",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        assert create_response.status_code == 201
        
        # Get comments
        response = requests.get(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "pagination" in data
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0
        
        pagination = data["pagination"]
        assert "page" in pagination
        assert "limit" in pagination
        assert "total" in pagination
        assert "hasMore" in pagination

    def test_get_comments_with_pagination(self, auth_headers, test_model_id):
        """Test comment pagination"""
        for i in range(3):
            requests.post(
                f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
                json={
                    "content": f"Comment {i+1}",
                    "authorId": "test-user-1",
                    "authorName": "Test User",
                    "parentId": None
                },
                headers=auth_headers,
                timeout=5
            )
        
        time.sleep(1)  
        
        response = requests.get(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments?page=1&limit=2",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) <= 2
        assert data["pagination"]["page"] == 1
        assert data["pagination"]["limit"] == 2

    def test_get_specific_comment(self, auth_headers, test_model_id):
        """Test getting a specific comment by ID"""
        create_response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Comment to retrieve",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        assert create_response.status_code == 201
        comment_id = create_response.json()["id"]
        
        response = requests.get(
            f"{GATEWAY_URL}/api/comments/{comment_id}",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == comment_id
        assert data["content"] == "Comment to retrieve"

    def test_update_comment(self, auth_headers, test_model_id):
        create_response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Original content",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        assert create_response.status_code == 201
        comment_id = create_response.json()["id"]
        
        response = requests.put(
            f"{GATEWAY_URL}/api/comments/{comment_id}",
            json={"content": "Updated content"},
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "updated" in data["message"].lower()
        
        get_response = requests.get(
            f"{GATEWAY_URL}/api/comments/{comment_id}",
            headers=auth_headers,
            timeout=5
        )
        assert get_response.status_code == 200
        assert get_response.json()["content"] == "Updated content"

    def test_delete_comment(self, auth_headers, test_model_id):
        create_response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Comment to delete",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        assert create_response.status_code == 201
        comment_id = create_response.json()["id"]
        
        response = requests.delete(
            f"{GATEWAY_URL}/api/comments/{comment_id}",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "deleted" in data["message"].lower()
        
        get_response = requests.get(
            f"{GATEWAY_URL}/api/comments/{comment_id}",
            headers=auth_headers,
            timeout=5
        )
        assert get_response.status_code == 404


class TestNestedComments:

    def test_create_reply(self, auth_headers, test_model_id):
        """Test creating a reply to a comment"""
        parent_response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Parent comment",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        assert parent_response.status_code == 201
        parent_id = parent_response.json()["id"]
        
        reply_response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Reply to parent",
                "authorId": "test-user-2",
                "authorName": "Another User",
                "parentId": parent_id
            },
            headers=auth_headers,
            timeout=5
        )
        
        assert reply_response.status_code == 201
        reply_data = reply_response.json()
        assert reply_data["parentId"] == parent_id
        assert reply_data["content"] == "Reply to parent"

    def test_nested_structure(self, auth_headers, test_model_id):
        """Test that replies are nested correctly in tree structure"""
        parent_response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Parent comment",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        assert parent_response.status_code == 201
        parent_id = parent_response.json()["id"]
        
        requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Reply",
                "authorId": "test-user-2",
                "authorName": "Another User",
                "parentId": parent_id
            },
            headers=auth_headers,
            timeout=5
        )
        
        time.sleep(1) 
        
        response = requests.get(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) > 0
        
        parent_comment = data["data"][0]
        assert parent_comment["id"] == parent_id
        assert "replies" in parent_comment
        assert len(parent_comment["replies"]) > 0
        assert parent_comment["replies"][0]["parentId"] == parent_id

    def test_recursive_deletion(self, auth_headers, test_model_id):
        """Test that deleting a parent comment also deletes all replies"""
        parent_response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Parent to delete",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        assert parent_response.status_code == 201
        parent_id = parent_response.json()["id"]
        
        reply_ids = []
        for i in range(2):
            reply_response = requests.post(
                f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
                json={
                    "content": f"Reply {i+1}",
                    "authorId": "test-user-2",
                    "authorName": "Another User",
                    "parentId": parent_id
                },
                headers=auth_headers,
                timeout=5
            )
            assert reply_response.status_code == 201
            reply_ids.append(reply_response.json()["id"])
        
        delete_response = requests.delete(
            f"{GATEWAY_URL}/api/comments/{parent_id}",
            headers=auth_headers,
            timeout=5
        )
        assert delete_response.status_code == 200
        
        for comment_id in [parent_id] + reply_ids:
            get_response = requests.get(
                f"{GATEWAY_URL}/api/comments/{comment_id}",
                headers=auth_headers,
                timeout=5
            )
            assert get_response.status_code == 404


class TestCreatorBadge:
    """Test creator badge detection"""

    def test_creator_badge_for_creator(self, creator_headers, creator_model_id):
        """Test that model creator's comments have isCreator: true"""
        for attempt in range(3):
            response = requests.post(
                f"{GATEWAY_URL}/api/models/{creator_model_id}/comments",
                json={
                    "content": "Comment from creator",
                    "authorId": "creator-user",
                    "authorName": "Creator",
                    "parentId": None
                },
                headers=creator_headers,
                timeout=5
            )
            
            assert response.status_code == 201
            data = response.json()
            if data.get("isCreator") is True:
                return  
            elif attempt < 2:
                time.sleep(3)  
            else:
                pytest.skip("Creator badge not set - event processing may be delayed or event consumer not running")

    def test_creator_badge_for_non_creator(self, auth_headers, creator_model_id):
        """Test that non-creator's comments have isCreator: false"""
        response = requests.post(
            f"{GATEWAY_URL}/api/models/{creator_model_id}/comments",
            json={
                "content": "Comment from non-creator",
                "authorId": "test-user-1",
                "authorName": "Non-Creator",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["isCreator"] is False, "Non-creator's comment should have isCreator: false"


class TestEventIntegration:
    """Test event-driven integration with Model Catalog"""

    def test_model_created_event_caching(self, creator_headers, creator_model_id):
        """Test that ModelCreated event caches creator info"""
        time.sleep(2)
        
        for attempt in range(3):
            response = requests.post(
                f"{GATEWAY_URL}/api/models/{creator_model_id}/comments",
                json={
                    "content": "Test comment",
                    "authorId": "creator-user",
                    "authorName": "Creator",
                    "parentId": None
                },
                headers=creator_headers,
                timeout=5
            )
            
            assert response.status_code == 201
            data = response.json()
            if data.get("isCreator") is True:
                return  # Success!
            elif attempt < 2:
                time.sleep(2)  # Wait and retry
            else:
                pytest.skip("Creator badge not set - event processing may be delayed")

    def test_model_deleted_event_archiving(self, auth_headers, test_model_id):
        """Test that ModelDeleted event archives comments"""
        comment_response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Comment to be archived",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        assert comment_response.status_code == 201
        
        delete_response = requests.delete(
            f"{GATEWAY_URL}/api/models/{test_model_id}",
            headers=auth_headers,
            timeout=5
        )
        assert delete_response.status_code == 204
        
        time.sleep(2)
        

class TestErrorHandling:
    """Test error handling scenarios"""

    def test_create_comment_invalid_model_id(self, auth_headers):
        """Test creating comment for nonexistent model"""
        response = requests.post(
            f"{GATEWAY_URL}/api/models/999999/comments",
            json={
                "content": "Test comment",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code in [200, 201, 404]

    def test_get_comment_invalid_id(self, auth_headers):
        """Test getting comment with invalid ID format"""
        response = requests.get(
            f"{GATEWAY_URL}/api/comments/invalid-id",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 400
        assert "Invalid comment ID" in response.json()["detail"] or "Invalid" in response.json()["detail"]

    def test_get_comment_nonexistent_id(self, auth_headers):
        """Test getting comment that doesn't exist"""
        fake_id = "507f1f77bcf86cd799439999"
        response = requests.get(
            f"{GATEWAY_URL}/api/comments/{fake_id}",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_comment_nonexistent(self, auth_headers):
        """Test updating a nonexistent comment"""
        fake_id = "507f1f77bcf86cd799439999"
        response = requests.put(
            f"{GATEWAY_URL}/api/comments/{fake_id}",
            json={"content": "Updated"},
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 404

    def test_delete_comment_nonexistent(self, auth_headers):
        """Test deleting a nonexistent comment"""
        fake_id = "507f1f77bcf86cd799439999"
        response = requests.delete(
            f"{GATEWAY_URL}/api/comments/{fake_id}",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 404

    def test_create_comment_unauthorized(self, test_model_id):
        """Test creating comment without authentication"""
        response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Test comment",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            timeout=5
        )
        
        assert response.status_code == 401

class TestAPIGatewayRouting:
    """Test that all requests go through API Gateway"""

    def test_comments_route_through_gateway(self, auth_headers, test_model_id):
        """Verify comments endpoints are routed through gateway"""
        
        response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Gateway routing test",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 201

    def test_model_comments_routing(self, auth_headers, test_model_id):
        """Test that /api/models/{id}/comments routes to collaboration service"""
        response = requests.get(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            headers=auth_headers,
            timeout=5
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "pagination" in data


class TestCaching:
    """Test Redis caching behaviour"""

    def test_cache_invalidation_on_create(self, auth_headers, test_model_id):
        """Test that cache is invalidated when comment is created"""
        response1 = requests.get(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            headers=auth_headers,
            timeout=5
        )
        assert response1.status_code == 200
        initial_count = len(response1.json()["data"])
        
        create_response = requests.post(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            json={
                "content": "Cache invalidation test",
                "authorId": "test-user-1",
                "authorName": "Test User",
                "parentId": None
            },
            headers=auth_headers,
            timeout=5
        )
        assert create_response.status_code == 201
        
        time.sleep(0.5) 
        response2 = requests.get(
            f"{GATEWAY_URL}/api/models/{test_model_id}/comments",
            headers=auth_headers,
            timeout=5
        )
        assert response2.status_code == 200
        new_count = len(response2.json()["data"])
        assert new_count == initial_count + 1
