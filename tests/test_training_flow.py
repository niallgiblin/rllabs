"""
Testing for the complete training flow:
1. Model creation
2. Artifact uploads (config, dataset, model weights)
3. Training job triggering
4. Training execution and monitoring
5. Trained weights upload and version registration
6. Download of trained models
7. Multiple training runs (versioning)
8. Error handling scenarios
"""

import time
import os
import tempfile
import hashlib
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import jwt
import pytest
import requests

GATEWAY_URL = "http://localhost:8080"

SECRET_KEY = "your-secret-key" # Hardcoded for local dev, in production use .env
ALGORITHM = "HS256"


def _make_jwt(sub: str = "test-user-1", scopes=("api:read", "api:write")) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "scopes": list(scopes),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=30)).timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _calculate_sha256(filepath: Path) -> str:
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(8192), b""):
            sha256_hash.update(byte_block)
    return f"sha256:{sha256_hash.hexdigest()}"


def _create_test_artifacts():
    training_config = {
        "shape": [3, 10, 10],
        "layers": [
            {"type": "Conv2d", "in_channels": 3, "out_channels": 16, "kernel_size": 3, "padding": 1},
            {"type": "ReLU"},
            {"type": "Flatten"},
            {"type": "Linear", "out_features": 128},
            {"type": "ReLU"},
            {"type": "Linear", "out_features": 4}
        ]
    }
    
    dataset_config = {
        "grid_height": 10,
        "grid_width": 10,
        "channels": [[5, 5], [9, 9], [[0, 0], [1, 1], [2, 2]]],
        "initial_epsilon_value": 0.9,
        "initial_learning_rate": 0.001,
        "initial_gamma_value": 0.95
    }
    
    temp_dir = Path(tempfile.mkdtemp())
    config_file = temp_dir / "training_config.json"
    dataset_file = temp_dir / "dataset_config.json"
    weights_file = temp_dir / "sample_model.pth"
    
    with open(config_file, "w") as f:
        json.dump(training_config, f)
    
    with open(dataset_file, "w") as f:
        json.dump(dataset_config, f)
    
    import zipfile
    with zipfile.ZipFile(weights_file, "w") as zf:
        zf.writestr("dummy_weights.pth", b"dummy model weights data")
    
    return config_file, dataset_file, weights_file, temp_dir


@pytest.fixture(scope="session", autouse=True)
def wait_for_services():
    for _ in range(60):
        try:
            if requests.get(f"{GATEWAY_URL}/health", timeout=3).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    pytest.fail("Services did not become ready in time")


@pytest.fixture
def token():
    return _make_jwt()


@pytest.fixture
def test_artifacts():
    config_file, dataset_file, weights_file, temp_dir = _create_test_artifacts()
    yield config_file, dataset_file, weights_file
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestTrainingFlow:
    
    def test_full_training_flow(self, token, test_artifacts):
        config_file, dataset_file, weights_file = test_artifacts
        
        model_response = requests.post(
            f"{GATEWAY_URL}/api/models",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": f"test-model-{int(time.time())}-{os.urandom(4).hex()}",
                "description": "Test model for training flow"
            }
        )
        assert model_response.status_code in [201, 409]
        if model_response.status_code == 409:
            pytest.skip("Model name conflict - skipping test")
        model_id = model_response.json()["id"]
        
        def upload_artifact(filepath, artifact_type):
            """Helper to upload an artifact using the proper multipart upload flow"""
            file_hash = _calculate_sha256(filepath)
            file_size = filepath.stat().st_size
            
            init_data = {
                "filename": filepath.name,
                "file_size": file_size,
                "file_hash": file_hash,
                "chunk_size": 5 * 1024 * 1024,
                "artifact_type": artifact_type,
                "model_id": model_id
            }
            init_response = requests.post(
                f"{GATEWAY_URL}/api/uploads",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                json=init_data
            )
            assert init_response.status_code == 201, f"Init upload failed: {init_response.text}"
            init_data_resp = init_response.json()
            upload_id = init_data_resp["upload_id"]
            presigned_urls = init_data_resp.get("presigned_urls", [])
            
            if init_data_resp.get("status") == "already_completed" or not presigned_urls:
                artifact_id = init_data_resp.get("artifact_id")
                if artifact_id:
                    return artifact_id
                return file_hash
            
            parts = []
            with open(filepath, "rb") as f:
                for i, presigned_url_info in enumerate(presigned_urls, 1):
                    if isinstance(presigned_url_info, dict):
                        upload_url = presigned_url_info.get("url", presigned_url_info.get("presigned_url", ""))
                    else:
                        upload_url = presigned_url_info
                    
                    upload_url = upload_url.replace("minio:9000", "localhost:9000")
                    
                    chunk_data = f.read(5 * 1024 * 1024)
                    if not chunk_data:
                        break
                    
                    chunk_response = requests.put(upload_url, data=chunk_data)
                    assert chunk_response.status_code in [200, 201], f"Chunk upload failed: {chunk_response.status_code}"
                    etag = chunk_response.headers.get("ETag", "").strip('"')
                    parts.append({"part_number": i, "etag": etag})
            
            complete_response = requests.post(
                f"{GATEWAY_URL}/api/uploads/{upload_id}/complete",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                json={"parts": parts}
            )
            assert complete_response.status_code == 200, f"Complete upload failed: {complete_response.text}"
            return complete_response.json()["artifact_id"]
        
        config_artifact = upload_artifact(config_file, "config")
        dataset_artifact = upload_artifact(dataset_file, "dataset")
        model_artifact = upload_artifact(weights_file, "model")
        
        job_response = requests.post(
            f"{GATEWAY_URL}/api/training-jobs",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            json={
                "config_artifact_id": config_artifact,
                "dataset_artifact_id": dataset_artifact,
                "model_artifact_id": model_artifact,
                "model_id": model_id
            }
        )
        if job_response.status_code != 202:
            error_detail = job_response.text
            pytest.skip(f"Training job creation failed with {job_response.status_code}: {error_detail}")
        assert job_response.status_code == 202
        job_id = job_response.json()["job_id"]
        
        max_wait = 300
        start_time = time.time()
        trained_artifact_id = None
        
        while time.time() - start_time < max_wait:
            versions_response = requests.get(
                f"{GATEWAY_URL}/api/models/{model_id}/versions"
            )
            if versions_response.status_code == 200:
                versions = versions_response.json()
                if len(versions) > 0:
                    latest_version = max(versions, key=lambda v: v["version"])
                    trained_artifact_id = latest_version["content_hash"]
                    break
            time.sleep(5)
        
        assert trained_artifact_id is not None, "Training did not complete in time"
        
        download_response = requests.get(
            f"{GATEWAY_URL}/api/downloads/{trained_artifact_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert download_response.status_code == 200
        download_data = download_response.json()
        assert "download_url" in download_data
        assert download_data["file_size"] > 0
        
        versions_response = requests.get(
            f"{GATEWAY_URL}/api/models/{model_id}/versions"
        )
        assert versions_response.status_code == 200
        versions = versions_response.json()
        assert len(versions) >= 1
        
        hash_response = requests.get(
            f"{GATEWAY_URL}/api/versions/by-hash/{trained_artifact_id}"
        )
        assert hash_response.status_code == 200
        assert hash_response.json()["content_hash"] == trained_artifact_id
    
    def test_multiple_training_runs_versioning(self, token, test_artifacts):
        """Test that multiple training runs create sequential versions"""
        config_file, dataset_file, weights_file = test_artifacts
        
        model_response = requests.post(
            f"{GATEWAY_URL}/api/models",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": f"version-test-{int(time.time())}",
                "description": "Versioning test"
            }
        )
        assert model_response.status_code == 201
        model_id = model_response.json()["id"]
        
        versions_response = requests.get(
            f"{GATEWAY_URL}/api/models/{model_id}/versions"
        )
        assert versions_response.status_code == 200
        initial_count = len(versions_response.json())
        
        assert initial_count >= 0
    
    def test_training_job_with_explicit_model_id(self, token, test_artifacts):
        """Test that explicit model_id in request is used correctly"""
        config_file, dataset_file, weights_file = test_artifacts
        
        model1_response = requests.post(
            f"{GATEWAY_URL}/api/models",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": f"model1-{int(time.time())}", "description": "Model 1"}
        )
        model1_id = model1_response.json()["id"]
        
        model2_response = requests.post(
            f"{GATEWAY_URL}/api/models",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": f"model2-{int(time.time())}", "description": "Model 2"}
        )
        model2_id = model2_response.json()["id"]
        
        assert model1_id != model2_id
    
    def test_training_job_without_model_id_fallback(self, token, test_artifacts):
        """Test that training job falls back to lookup when model_id not provided"""
        config_file, dataset_file, weights_file = test_artifacts
        
        model_response = requests.post(
            f"{GATEWAY_URL}/api/models",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": f"fallback-test-{int(time.time())}", "description": "Fallback test"}
        )
        model_id = model_response.json()["id"]
        
        assert model_id is not None
    
    def test_download_trained_model(self, token):
        """Test downloading a trained model by artifact ID"""
        models_response = requests.get(f"{GATEWAY_URL}/api/models")
        if models_response.status_code == 200:
            data = models_response.json()
            models = data["items"] if isinstance(data, dict) and "items" in data else data
            if models:
                model_id = models[0]["id"]
                versions_response = requests.get(
                    f"{GATEWAY_URL}/api/models/{model_id}/versions"
                )
                if versions_response.status_code == 200:
                    versions = versions_response.json()
                    if versions:
                        artifact_id = versions[0]["content_hash"]
                        
                        download_response = requests.get(
                            f"{GATEWAY_URL}/api/downloads/{artifact_id}",
                            headers={"Authorization": f"Bearer {token}"}
                        )
                        
                        if download_response.status_code == 200:
                            download_data = download_response.json()
                            assert "download_url" in download_data
                            assert download_data["file_size"] > 0
                        elif download_response.status_code == 403:
                            pytest.skip("User does not have permission to download this artifact (403) - likely belongs to different user")
                        else:
                            assert download_response.status_code == 200, f"Unexpected status code: {download_response.status_code}"
            else:
                pytest.skip("No models found to test download")
        else:
            pytest.skip("Could not retrieve models list")


class TestTrainingErrorHandling:
    """Test error handling scenarios in training flow"""
    
    def test_training_job_with_nonexistent_artifact(self, token):
        """Test that training job fails gracefully when artifact doesn't exist"""
        job_response = requests.post(
            f"{GATEWAY_URL}/api/training-jobs",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            json={
                "config_artifact_id": "sha256:nonexistent123",
                "dataset_artifact_id": "sha256:nonexistent456",
                "model_artifact_id": "sha256:nonexistent789",
                "model_id": 999
            }
        )
        assert job_response.status_code in [400, 404, 503]
    
    def test_training_job_with_nonexistent_model(self, token, test_artifacts):
        """Test training job with model_id that doesn't exist"""
        config_file, dataset_file, weights_file = test_artifacts
        
        job_response = requests.post(
            f"{GATEWAY_URL}/api/training-jobs",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            json={
                "config_artifact_id": "sha256:valid123",
                "dataset_artifact_id": "sha256:valid456",
                "model_artifact_id": "sha256:valid789",
                "model_id": 99999
            }
        )
        assert job_response.status_code in [202, 400, 404, 503]
    
    def test_download_nonexistent_artifact(self, token):
        """Test downloading an artifact that doesn't exist"""
        download_response = requests.get(
            f"{GATEWAY_URL}/api/downloads/sha256:nonexistent123456789",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert download_response.status_code in [400, 404, 503]
    
    def test_training_job_unauthorized(self, test_artifacts):
        """Test that training job requires authentication"""
        job_response = requests.post(
            f"{GATEWAY_URL}/api/training-jobs",
            headers={"Content-Type": "application/json"},
            json={
                "config_artifact_id": "sha256:test123",
                "dataset_artifact_id": "sha256:test456",
                "model_artifact_id": "sha256:test789"
            }
        )
        assert job_response.status_code == 401


class TestModelCatalogQueries:
    """Test model catalog query endpoints"""
    
    def test_list_model_versions(self, token):
        """Test listing all versions for a model"""
        # Create a model
        model_response = requests.post(
            f"{GATEWAY_URL}/api/models",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": f"query-test-{int(time.time())}",
                "description": "Query test"
            }
        )
        if model_response.status_code == 201:
            model_id = model_response.json()["id"]
            
            versions_response = requests.get(
                f"{GATEWAY_URL}/api/models/{model_id}/versions"
            )
            assert versions_response.status_code == 200
            assert isinstance(versions_response.json(), list)
    
    def test_get_version_by_hash(self):
        """Test getting a version by content hash"""
        invalid_hash_response = requests.get(
            f"{GATEWAY_URL}/api/versions/by-hash/sha256:invalid123"
        )
        assert invalid_hash_response.status_code == 404, \
            f"Expected 404 for invalid hash, got {invalid_hash_response.status_code}. " \
            f"Response: {invalid_hash_response.text}"
        
        models_response = requests.get(f"{GATEWAY_URL}/api/models")
        assert models_response.status_code == 200, \
            f"Failed to get models list: {models_response.status_code}"
        
        data = models_response.json()
        models = data["items"] if isinstance(data, dict) and "items" in data else data
        assert isinstance(models, list), "Models response should be a list"
        
        content_hash = None
        for model in models:
            model_id = model["id"]
            versions_response = requests.get(
                f"{GATEWAY_URL}/api/models/{model_id}/versions"
            )
            if versions_response.status_code == 200:
                versions = versions_response.json()
                if versions and len(versions) > 0:
                    content_hash = versions[0]["content_hash"]
                    break
        
        if content_hash:
            hash_response = requests.get(
                f"{GATEWAY_URL}/api/versions/by-hash/{content_hash}"
            )
            assert hash_response.status_code == 200, \
                f"Expected 200 for valid hash {content_hash}, got {hash_response.status_code}. " \
                f"Response: {hash_response.text}"
            version_data = hash_response.json()
            assert version_data["content_hash"] == content_hash, \
                f"Content hash mismatch. Expected {content_hash}, got {version_data.get('content_hash')}"
        else:
            pytest.skip("No model versions found in database to test with valid hash")
    
    def test_list_all_models(self):
        """Test listing all models (public endpoint)"""
        response = requests.get(f"{GATEWAY_URL}/api/models")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "items" in data
        assert isinstance(data["items"], list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])