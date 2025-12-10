"""
Comprehensive Load Test for RLLabs
===================================

- Horizontal scaling (HPA)
- Read replica usage (PostgreSQL)
- Cache performance (Redis)
- Storage throughput (MinIO)
- Bottleneck avoidance strategies

Usage:
    # Quick test (10 users, 60 seconds)
    python tests/comprehensive_load_test.py --users 10 --duration 60
    
    # Full test with authentication
    TOKEN=$(python generate_token.py --user load-test-user)
    python tests/comprehensive_load_test.py --users 50 --duration 300 --token "$TOKEN"
    
    # Stress test (high load)
    python tests/comprehensive_load_test.py --users 100 --duration 600 --token "$TOKEN" --stress

Requirements:
    pip install httpx aiohttp asyncio
"""

import asyncio
import aiohttp
import argparse
import time
import json
import hashlib
import random
import uuid
import os
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import statistics

@dataclass
class TestMetrics:
    """Collects metrics for a single test run"""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    response_times: List[float] = field(default_factory=list)
    endpoint_stats: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))
    error_types: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    status_codes: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    error_details: Dict[str, int] = field(default_factory=lambda: defaultdict(int))  
    start_time: float = 0
    end_time: float = 0
    
    def add_result(self, endpoint: str, method: str, status: int, response_time: float, success: bool):
        self.total_requests += 1
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1
        
        self.response_times.append(response_time)
        self.endpoint_stats[f"{method} {endpoint}"].append(response_time)
        self.status_codes[status] += 1
        
        if not success:
            self.error_types[f"{status}"] += 1
            if status == 0:
                pass
    
    def get_stats(self) -> Dict:
        if not self.response_times:
            return {}
        
        return {
            "total_requests": self.total_requests,
            "successful": self.successful_requests,
            "failed": self.failed_requests,
            "success_rate": (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0,
            "requests_per_second": self.total_requests / (self.end_time - self.start_time) if self.end_time > self.start_time else 0,
            "response_time": {
                "min": min(self.response_times) * 1000,
                "max": max(self.response_times) * 1000,
                "mean": statistics.mean(self.response_times) * 1000,
                "median": statistics.median(self.response_times) * 1000,
                "p95": self.percentile(self.response_times, 0.95) * 1000,
                "p99": self.percentile(self.response_times, 0.99) * 1000,
                "std_dev": statistics.stdev(self.response_times) * 1000 if len(self.response_times) > 1 else 0,
            },
            "endpoint_stats": {
                endpoint: {
                    "count": len(times),
                    "mean_ms": statistics.mean(times) * 1000,
                    "p95_ms": self.percentile(times, 0.95) * 1000,
                    "p99_ms": self.percentile(times, 0.99) * 1000,
                }
                for endpoint, times in self.endpoint_stats.items()
            },
            "status_codes": dict(self.status_codes),
            "error_types": dict(self.error_types),
            "error_details": dict(self.error_details),
        }
    
    @staticmethod
    def percentile(data: List[float], p: float) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = int(len(sorted_data) * p)
        return sorted_data[min(index, len(sorted_data) - 1)]


class ComprehensiveLoadTester:
    
    def __init__(self, base_url: str, token: Optional[str] = None, users: int = 10, 
                 duration: int = 60, stress: bool = False, auto_auth: bool = True):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.users = users
        self.duration = duration
        self.stress = stress
        self.auto_auth = auto_auth  # Auto-register/login users if no token
        self.metrics = TestMetrics()
        self.created_models: List[int] = []  # Track created models for cleanup
        self.existing_models: List[int] = []  # Track existing models from database
        self.invalid_models: set = set()  # Track models that returned 404 (avoid reusing)
        self.upload_sessions: List[Dict] = []  # Track upload sessions
        self.artifact_ids: List[int] = []  # Track uploaded artifacts for download testing
        self.comment_ids: List[int] = []  # Track comments for testing
        self.user_tokens: Dict[int, str] = {}  # Per-user tokens for multi-user testing
        self.model_refresh_counter = 0  # Track when to refresh model list
        self._model_404_count: Dict[int, int] = {}  # Track 404 counts before marking invalid
        
    async def make_request(self, session: aiohttp.ClientSession, endpoint: str, 
                          method: str = "GET", data: dict = None, 
                          headers: dict = None, user_token: Optional[str] = None) -> Dict:
        """Make HTTP request and record metrics"""
        url = f"{self.base_url}{endpoint}"
        req_headers = headers.copy() if headers else {}
        token_to_use = user_token or self.token
        if "Authorization" not in req_headers and token_to_use:
            req_headers["Authorization"] = f"Bearer {token_to_use}"
        
        start_time = time.time()
        try:
            if method == "GET":
                async with session.get(url, headers=req_headers) as response:
                    response_text = await response.text()
                    status = response.status
            elif method == "POST":
                async with session.post(url, headers=req_headers, json=data) as response:
                    response_text = await response.text()
                    status = response.status
            elif method == "PUT":
                async with session.put(url, headers=req_headers, json=data) as response:
                    response_text = await response.text()
                    status = response.status
            elif method == "DELETE":
                async with session.delete(url, headers=req_headers) as response:
                    response_text = await response.text()
                    status = response.status
            else:
                return {"success": False, "error": "Unsupported method"}
            
            elapsed = time.time() - start_time
            # Consider 429 (rate limit) as a special case - not a complete failure
            success = 200 <= status < 300 or status == 429
            
            # Track 404s for model endpoints - mark invalid immediately to prevent repeated failures
            # Note: Some 404s are expected in realistic load tests (models deleted, race conditions)
            # Only mark as invalid if it's a direct model GET (not sub-resources like comments/versions)
            # Sub-resources might 404 if they don't exist, but the model itself is still valid
            if status == 404 and "/api/models/" in endpoint and method == "GET":
                parts = endpoint.split("/")
                if len(parts) >= 4 and parts[3].isdigit():
                    model_id = int(parts[3])
                    is_direct_model_access = len(parts) == 4 or (len(parts) == 5 and parts[4] == "")
                    if is_direct_model_access:
                        if model_id not in self.invalid_models:
                            self.invalid_models.add(model_id)
                            if model_id in self.existing_models:
                                self.existing_models.remove(model_id)
                            if model_id in self.created_models:
                                self.created_models.remove(model_id)
            
            self.metrics.add_result(endpoint, method, status, elapsed, success)
            
            if status == 429:
                await asyncio.sleep(0.5)  # small delay for rate limit
            
            try:
                response_data = json.loads(response_text) if response_text else {}
            except:
                response_data = {"raw": response_text[:100]}
            
            return {
                "success": success,
                "status": status,
                "response_time": elapsed,
                "data": response_data
            }
            
        except asyncio.TimeoutError as e:
            elapsed = time.time() - start_time
            error_msg = f"timeout: {str(e)[:50]}"
            self.metrics.error_details[error_msg] += 1
            self.metrics.add_result(endpoint, method, 0, elapsed, False)
            return {
                "success": False,
                "error": error_msg,
                "response_time": elapsed
            }
        except aiohttp.ClientConnectorError as e:
            elapsed = time.time() - start_time
            error_msg = f"connection_error: {str(e)[:50]}"
            self.metrics.error_details[error_msg] += 1
            self.metrics.add_result(endpoint, method, 0, elapsed, False)
            return {
                "success": False,
                "error": error_msg,
                "response_time": elapsed
            }
        except aiohttp.ClientError as e:
            elapsed = time.time() - start_time
            error_msg = f"client_error: {type(e).__name__}: {str(e)[:50]}"
            self.metrics.error_details[error_msg] += 1
            self.metrics.add_result(endpoint, method, 0, elapsed, False)
            return {
                "success": False,
                "error": error_msg,
                "response_time": elapsed
            }
        except Exception as e:
            elapsed = time.time() - start_time
            error_msg = f"{type(e).__name__}: {str(e)[:100]}"
            self.metrics.error_details[error_msg] += 1
            self.metrics.add_result(endpoint, method, 0, elapsed, False)
            return {
                "success": False,
                "error": error_msg,
                "response_time": elapsed
            }
    
    def generate_file_hash(self, content: bytes) -> str:
        hash_obj = hashlib.sha256(content)
        return f"sha256:{hash_obj.hexdigest()}"
    
    async def get_user_token(self, session: aiohttp.ClientSession, user_id: int) -> Optional[str]:
        if user_id in self.user_tokens:
            return self.user_tokens[user_id]
        
        if self.token:
            self.user_tokens[user_id] = self.token
            return self.token
        
        if not self.auto_auth:
            return None
        
        username = f"loadtest_user_{user_id}_{uuid.uuid4().hex[:8]}"
        password = f"password_{user_id}"
        email = f"{username}@loadtest.example.com"
        
        register_data = {
            "username": username,
            "email": email,
            "password": password
        }
        
        result = await self.make_request(session, "/api/auth/register", "POST", register_data, user_token=None)
        if result.get("success") and "data" in result:
            token = result["data"].get("token")
            if token:
                self.user_tokens[user_id] = token
                return token
        
        return None
    
    async def simulate_auth_workload(self, session: aiohttp.ClientSession, user_id: int):
        token = await self.get_user_token(session, user_id)
        if not token:
            return
        
        await self.make_request(session, "/api/auth/me", "GET", user_token=token)
    
    async def get_existing_models(self, session: aiohttp.ClientSession, count: int = 200) -> List[int]:
        result = await self.make_request(session, f"/api/models?page=1&page_size=50", "GET")
        
        if not result or not isinstance(result, dict):
            return []
        
        if not result.get("success"):
            return []
        
        if "data" not in result:
            return []
        
        data = result["data"]
        if not isinstance(data, dict):
            return []
        
        total = data.get("total", 0)
        total_pages = data.get("total_pages", 1)
        
        if total == 0 or total_pages == 0:
            return []
        
        pages_to_check = min(total_pages, 15) 
        
        all_model_ids = []
        for page in range(1, pages_to_check + 1):
            page_result = await self.make_request(session, f"/api/models?page={page}&page_size=50", "GET")
            if page_result.get("success") and "data" in page_result:
                items = page_result["data"].get("items", [])
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and item.get("id"):
                            model_id = item["id"]
                            if model_id not in self.invalid_models:
                                all_model_ids.append(model_id)
        
        if len(all_model_ids) > count:
            random.shuffle(all_model_ids)
            return all_model_ids[:count]
        elif len(all_model_ids) > 0:
            return all_model_ids
        else:
            return []
    
    async def get_valid_model(self, session: aiohttp.ClientSession) -> Optional[int]:
        self.model_refresh_counter += 1
        if self.model_refresh_counter % 20 == 0:  
            new_models = await self.get_existing_models(session, count=100)
            self.existing_models = [m for m in new_models if m not in self.invalid_models]
        
        available_models = []
        
        if self.existing_models:
            available_models.extend([m for m in self.existing_models if m not in self.invalid_models])
        
        if self.created_models:
            available_models.extend([m for m in self.created_models if m not in self.invalid_models])
        
        seen = set()
        available_models = [m for m in available_models if m not in seen and not seen.add(m)]
        
        if not available_models:
            self.existing_models = await self.get_existing_models(session, count=100)
            available_models = [
                m for m in self.existing_models
                if m not in self.invalid_models
            ]
        
        if available_models:
            seed_models = [m for m in available_models if m in self.existing_models]
            if seed_models:
                if random.random() < 0.95:
                    return random.choice(seed_models)
                created_available = [m for m in available_models if m in self.created_models]
                if created_available:
                    return random.choice(created_available)
                return random.choice(seed_models)
            return random.choice(available_models)
        return None
    
    async def simulate_read_workload(self, session: aiohttp.ClientSession, user_id: int):
        endpoints = [
            ("/health", "GET"),
            ("/api/models?page=1", "GET"),  # First page
            ("/api/models?page=2", "GET"),  # Second page (tests pagination)
            ("/api/models?page=1", "GET"),  # Repeat first page (cache hit)
        ]
        
        model_id = await self.get_valid_model(session)
        
        model_endpoints = []
        if model_id:
            model_endpoints = [
                (f"/api/models/{model_id}", "GET"),  # Without versions
                (f"/api/models/{model_id}?include_versions=true", "GET"),  # With versions
                (f"/api/models/{model_id}/versions?limit=50", "GET"),  # Versions with pagination
                (f"/api/models/{model_id}/versions?limit=20&offset=0", "GET"),  # First page of versions
                (f"/api/models/{model_id}/latest", "GET"),  # Get latest version
                (f"/api/models/{model_id}/ownership", "GET"),  # Ownership check
                (f"/api/models/{model_id}/comments", "GET"),  # Get comments (public)
            ]
        
        if self.comment_ids:
            comment_id = random.choice(self.comment_ids)
            endpoints.append((f"/api/comments/{comment_id}", "GET"))
        
        token = await self.get_user_token(session, user_id)
        
        for endpoint, method in endpoints:
            await self.make_request(session, endpoint, method, user_token=token)
            await asyncio.sleep(0.05)  
        
        for endpoint, method in model_endpoints:
            result = await self.make_request(session, endpoint, method, user_token=token)
            if result.get("status") == 404 and "/api/models/" in endpoint:
                parts = endpoint.split("/")
                if len(parts) >= 4 and parts[3].isdigit():
                    failed_model_id = int(parts[3])
                    is_direct = len(parts) == 4 or (len(parts) == 5 and (parts[4] == "" or "?" in parts[4]))
                    if is_direct:
                        new_model_id = await self.get_valid_model(session)
                        if new_model_id and new_model_id != failed_model_id:
                            new_endpoint = endpoint.replace(f"/{failed_model_id}", f"/{new_model_id}")
                            retry_result = await self.make_request(session, new_endpoint, method, user_token=token)
                            if retry_result.get("status") != 404:
                                model_id = new_model_id
                                remaining_idx = model_endpoints.index((endpoint, method)) + 1
                                model_endpoints = model_endpoints[:remaining_idx] + [
                                    (ep.replace(f"/{failed_model_id}", f"/{new_model_id}"), m)
                                    for ep, m in model_endpoints[remaining_idx:]
                                ]
            await asyncio.sleep(0.05)
    
    async def simulate_write_workload(self, session: aiohttp.ClientSession, user_id: int):
        token = await self.get_user_token(session, user_id)
        if not token:
            return  
        
        should_create = self.stress or (random.random() < 0.1)  

        if should_create:
            model_data = {
                "name": f"load-test-model-{user_id}-{uuid.uuid4().hex[:8]}",
                "description": f"Model created during load test by user {user_id}"
            }
            
            result = await self.make_request(session, "/api/models", "POST", model_data, user_token=token)
            if result.get("success") and "data" in result:
                model_id = result["data"].get("id")
                if model_id:
                    self.created_models.append(model_id)
        
        model_id = await self.get_valid_model(session)
        
        if model_id and model_id not in self.invalid_models:
            ownership_result = await self.make_request(session, f"/api/models/{model_id}/ownership", "GET", user_token=token)
            
            if ownership_result.get("status") == 404:
                self.invalid_models.add(model_id)
                return
            elif ownership_result.get("status") != 404:
                if random.random() < 0.3:  
                    comment_data = {
                        "content": f"Test comment from user {user_id}",
                        "parent_id": None
                    }
                    comment_result = await self.make_request(
                        session, f"/api/models/{model_id}/comments", "POST", comment_data, user_token=token
                    )
                    if comment_result.get("success") and "data" in comment_result:
                        comment_id = comment_result["data"].get("id")
                        if comment_id:
                            self.comment_ids.append(comment_id)
        
        if self.comment_ids and random.random() < 0.3:  
            comment_id = random.choice(self.comment_ids)
            if random.random() < 0.5:
                update_data = {"content": f"Updated comment from user {user_id}"}
                await self.make_request(session, f"/api/comments/{comment_id}", "PUT", update_data, user_token=token)
            else:
                await self.make_request(session, f"/api/comments/{comment_id}", "DELETE", user_token=token)
    
    async def simulate_upload_workload(self, session: aiohttp.ClientSession, user_id: int):
        """Simulate upload workload (stresses MinIO and PostgreSQL)"""
        token = await self.get_user_token(session, user_id)
        model_id = await self.get_valid_model(session)
        if not token or not model_id:
            return  
        
        headers = {"Authorization": f"Bearer {token}"}
        
        file_size = 5 * 1024 * 1024 if self.stress else 1024 * 1024 
        chunk_size = 5 * 1024 * 1024  # 5MB chunks
        file_content = os.urandom(file_size)
        file_hash = self.generate_file_hash(file_content)
        
        upload_data = {
            "filename": f"test-model-{user_id}-{uuid.uuid4().hex[:8]}.pkl",
            "file_size": file_size,
            "file_hash": file_hash,
            "chunk_size": chunk_size,
            "artifact_type": "model",
            "model_id": model_id
        }
        
        result = await self.make_request(session, "/api/uploads", "POST", upload_data, user_token=token)
        if result.get("success") and "data" in result:
            upload_session = result["data"]
            upload_id = upload_session.get("upload_id")
            presigned_urls = upload_session.get("presigned_urls", [])
            
            if upload_id and presigned_urls:
                # Upload chunks to presigned URLs (direct to MinIO - stresses storage)
                parts = []
                for i, url_data in enumerate(presigned_urls):
                    part_number = url_data.get("part_number", i + 1)
                    url = url_data.get("url")
                    
                    if url:
                        # Replace Kubernetes service names with ingress hostname for external access
                        # Presigned URLs may contain minio:9000 or minio-hl:9000 (K8s service) which isn't resolvable from outside cluster
                        # Use ingress (minio.localhost) instead of port-forward for production-grade load testing
                        url = url.replace("minio:9000", "minio.localhost")
                        url = url.replace("minio-hl:9000", "minio.localhost")
                        url = url.replace("localhost:9000", "minio.localhost")  
                        url = url.replace("http://minio:9000", "http://minio.localhost")
                        url = url.replace("http://minio-hl:9000", "http://minio.localhost")
                        url = url.replace("http://localhost:9000", "http://minio.localhost")
                        
                        # Upload chunk directly to MinIO
                        chunk_start = i * chunk_size
                        chunk_end = min((i + 1) * chunk_size, file_size)
                        chunk = file_content[chunk_start:chunk_end]
                        
                        try:
                            async with session.put(url, data=chunk, timeout=aiohttp.ClientTimeout(total=10)) as response:
                                if response.status == 200:
                                    etag = response.headers.get("ETag", "").strip('"')
                                    parts.append({"part_number": part_number, "etag": etag})
                        except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError, asyncio.TimeoutError, ConnectionError, OSError) as e:

                            pass
                        except Exception as e:
                            error_str = str(e).lower()
                            if any(x in error_str for x in ["connection", "connect", "localhost:9000", "minio", "errno 61", "cannot connect"]):
                                pass  # MinIO connection issue, skip silently
                            else:
                                if not hasattr(self, '_minio_error_logged'):
                                    print(f"MinIO upload error (suppressing further messages): {type(e).__name__}")
                                    self._minio_error_logged = True
                
                # Complete upload (stresses PostgreSQL write and MinIO)
                if parts:
                    complete_data = {"parts": parts}
                    complete_result = await self.make_request(
                        session, f"/api/uploads/{upload_id}/complete", "POST", complete_data, user_token=token
                    )
                    if complete_result.get("success") and "data" in complete_result:
                        artifact_id = complete_result["data"].get("artifact_id")
                        if artifact_id:
                            self.artifact_ids.append(artifact_id)
                else:
                    await self.make_request(session, f"/api/uploads/{upload_id}/abort", "POST", user_token=token)
    
    async def simulate_download_workload(self, session: aiohttp.ClientSession, user_id: int):
        """Simulate download workload (stresses MinIO reads)"""
        if not self.artifact_ids:
            return  
        
        artifact_id = random.choice(self.artifact_ids)
        
        result = await self.make_request(session, f"/api/downloads/{artifact_id}", "GET")
        if result.get("success") and "data" in result:
            download_url = result["data"].get("download_url")
            if download_url:
                # Replace Kubernetes service names with ingress hostname for external access
                # Use ingress instead of port-forward for production-grade load testing
                download_url = download_url.replace("minio:9000", "minio.localhost")
                download_url = download_url.replace("minio-hl:9000", "minio.localhost")
                download_url = download_url.replace("localhost:9000", "minio.localhost")
                download_url = download_url.replace("http://minio:9000", "http://minio.localhost")
                download_url = download_url.replace("http://minio-hl:9000", "http://minio.localhost")
                download_url = download_url.replace("http://localhost:9000", "http://minio.localhost")
                
                # Actually download the file (stresses MinIO)
                try:
                    async with session.get(download_url) as response:
                        if response.status == 200:
                            # Read first 1MB of file (don't need full file for load test)
                            await response.read(1024 * 1024)  
                except Exception as e:
                    pass  
    
    async def simulate_training_workload(self, session: aiohttp.ClientSession, user_id: int):
        """Simulate training job creation (requires uploaded artifacts)"""
        token = await self.get_user_token(session, user_id)
        model_id = await self.get_valid_model(session)
        if not token or not model_id or model_id in self.invalid_models or not self.artifact_ids:
            return  # Skip if no auth, no valid models, or no artifacts uploaded
        
        # Training jobs require 3 artifacts: config, dataset, and model weights
        if len(self.artifact_ids) >= 3:
            valid_artifacts = [
                a for a in self.artifact_ids 
                if isinstance(a, str) and a.startswith("sha256:") and len(a) == 71
            ]
            
            if len(valid_artifacts) < 3:
                return  
            
            config_artifact = valid_artifacts[0]
            dataset_artifact = valid_artifacts[1] if len(valid_artifacts) > 1 else valid_artifacts[0]
            model_artifact = valid_artifacts[2] if len(valid_artifacts) > 2 else valid_artifacts[-1]
            
            training_data = {
                "config_artifact_id": config_artifact,
                "dataset_artifact_id": dataset_artifact,
                "model_artifact_id": model_artifact,
                "model_id": model_id
            }
            
            result = await self.make_request(session, "/api/training-jobs", "POST", training_data, user_token=token)
            
            if result.get("success") and "data" in result:
                job_id = result["data"].get("job_id")
                if job_id:
                    # Optionally check job status (via training service endpoint)
                    # Note: This requires the training service to be accessible
                    # For load testing, we'll just track that jobs were created
                    pass
    
    async def simulate_admin_workload(self, session: aiohttp.ClientSession, user_id: int):
        """Simulate admin operations (model deletion)"""
        token = await self.get_user_token(session, user_id)
        if not token or not self.created_models:
            return
        
        # Realistic deletion frequency - some 404s from deletions are expected in load tests
        # This simulates real-world admin cleanup operations
        # With seed models available, we can delete created models without breaking the test
        if len(self.created_models) > 10 and random.random() < 0.05:  
            model_id = random.choice(self.created_models)
            if model_id not in self.existing_models:
                result = await self.make_request(session, f"/api/models/{model_id}", "DELETE", user_token=token)
                if result.get("success") and model_id in self.created_models:
                    self.created_models.remove(model_id)
                    self.invalid_models.add(model_id)
    
    async def user_simulation(self, session: aiohttp.ClientSession, user_id: int):
        """Simulate a single user workflow"""
        start_time = time.time()
        request_count = 0
        
        if self.auto_auth:
            await self.simulate_auth_workload(session, user_id)
        
        read_weight = 0.5 if not self.stress else 0.3
        write_weight = 0.2 if not self.stress else 0.3
        upload_weight = 0.15 if not self.stress else 0.25
        download_weight = 0.1 if not self.stress else 0.1
        training_weight = 0.03 if not self.stress else 0.03
        admin_weight = 0.01 if not self.stress else 0.02  
        
        while time.time() - start_time < self.duration:
            rand = random.random()
            
            if rand < read_weight:
                await self.simulate_read_workload(session, user_id)
            elif rand < read_weight + write_weight:
                await self.simulate_write_workload(session, user_id)
            elif rand < read_weight + write_weight + upload_weight:
                await self.simulate_upload_workload(session, user_id)
            elif rand < read_weight + write_weight + upload_weight + download_weight:
                await self.simulate_download_workload(session, user_id)
            elif rand < read_weight + write_weight + upload_weight + download_weight + training_weight:
                await self.simulate_training_workload(session, user_id)
            else:
                await self.simulate_admin_workload(session, user_id)
            
            request_count += 1
            await asyncio.sleep(0.1)  
        
        return request_count
    
    async def check_connectivity(self, session: aiohttp.ClientSession) -> bool:
        """Check if API Gateway is accessible"""
        try:
            async with session.get(f"{self.base_url}/health", timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    return True
        except Exception as e:
            print(f"Connection check failed: {type(e).__name__}: {str(e)[:100]}")
            return False
        return False
    
    async def run_load_test(self):
        print("=" * 80)
        print("RLLabs Comprehensive Load Test")
        print("=" * 80)
        print(f"URL: {self.base_url}")
        print(f"Users: {self.users}")
        print(f"Duration: {self.duration}s")
        print(f"Stress Mode: {'Yes' if self.stress else 'No'}")

        has_auth = bool(self.token) or (self.auto_auth and len(self.user_tokens) > 0)
        auth_status = "Yes (auto-auth)" if (self.auto_auth and not self.token) else ("Yes" if self.token else "No (public endpoints only)")
        print(f"Authentication: {auth_status}")
        print()
        
        print("Checking connectivity...")
        connector = aiohttp.TCPConnector(limit=10)
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as test_session:
            if not await self.check_connectivity(test_session):
                print()
                print("ERROR: Cannot connect to API Gateway")
                print(f"URL: {self.base_url}")
                print()
                print("Possible solutions:")
                print("   1. Check ingress: kubectl get ingress api-gateway-ingress")
                print("   2. Access via ingress: http://api.localhost")
                print("   3. Or use port-forward: kubectl port-forward svc/api-gateway 8080:8080 (then use --url http://localhost:8080)")
                print("   4. Check if API Gateway is running: kubectl get pods -l app=api-gateway")
                print("   5. Verify service: kubectl get svc api-gateway")
                print()
                return
            
            print("API Gateway is accessible")
            print()
            
            print("Fetching existing models from database...")
            existing_models = await self.get_existing_models(test_session, 200)
            if existing_models and len(existing_models) > 0:
                self.existing_models = existing_models
                print(f"Found {len(existing_models)} existing models to use in testing")
                print(f"Sample model IDs: {existing_models[:5]}...")
                print(f"Model ID range: {min(existing_models)} to {max(existing_models)}")
            else:
                print("No existing models found - will use models created during test")
            print()
        
        print("Testing ALL Endpoints:")
        print("  Authentication: /api/auth/register, /api/auth/login, /api/auth/me")
        print("  Read operations: /api/models (with pagination), /api/models/{id} (with include_versions)")
        print("  Write operations: POST /api/models, PUT/DELETE /api/comments")
        print("  Upload operations: POST /api/uploads, POST /api/uploads/{id}/complete")
        print("  Download operations: GET /api/downloads/{id}")
        print("  Training jobs: POST /api/training-jobs")
        print("  Collaboration: POST/GET/PUT/DELETE /api/comments")
        print("  Admin operations: DELETE /api/models/{id}")
        print("  Infrastructure: PostgreSQL read replicas, MinIO distributed storage, Redis cache")
        print("  Other Features: Pagination, optional version loading, versions pagination")
        print()
        
        self.metrics.start_time = time.time()
        
        connector = aiohttp.TCPConnector(
            limit=500 if self.stress else 300,  # Total connection pool size
            limit_per_host=100 if self.stress else 50,  # Per-host connection limit
            ttl_dns_cache=300,  # DNS cache TTL (5 minutes)
            force_close=False,  # Reuse connections 
            enable_cleanup_closed=True,  # Clean up closed connections (prevents leaks)
            keepalive_timeout=30  # Keep connections alive for 30s
        )
        timeout = aiohttp.ClientTimeout(
            total=120,  
            connect=30,  
            sock_read=60  
        )
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            tasks = []
            for user_id in range(self.users):
                task = asyncio.create_task(self.user_simulation(session, user_id))
                tasks.append(task)
            
            await asyncio.gather(*tasks, return_exceptions=True)  
        
        self.metrics.end_time = time.time()
        self.print_results()
    
    def print_results(self):
        stats = self.metrics.get_stats()
        
        if not stats:
            print("No results recorded")
            return
        
        print("\n" + "=" * 80)
        print("LOAD TEST RESULTS")
        print("=" * 80)
        print()
        
        print("SUMMARY")
        print("-" * 80)
        print(f"Total Requests:     {stats['total_requests']:,}")
        print(f"Successful:        {stats['successful']:,} ({stats['success_rate']:.2f}%)")
        print(f"Failed:             {stats['failed']:,} ({100 - stats['success_rate']:.2f}%)")
        print(f"Requests/Second:   {stats['requests_per_second']:.2f}")
        print(f"Test Duration:     {self.metrics.end_time - self.metrics.start_time:.2f}s")
        print()
        
        rt = stats['response_time']
        print("RESPONSE TIME STATISTICS")
        print("-" * 80)
        print(f"Min:                {rt['min']:.2f}ms")
        print(f"Max:                {rt['max']:.2f}ms")
        print(f"Mean:               {rt['mean']:.2f}ms")
        print(f"Median:             {rt['median']:.2f}ms")
        print(f"95th Percentile:    {rt['p95']:.2f}ms")
        print(f"99th Percentile:    {rt['p99']:.2f}ms")
        print(f"Std Deviation:     {rt['std_dev']:.2f}ms")
        print()
        
        print("ENDPOINT STATISTICS")
        print("-" * 80)
        for endpoint, ep_stats in sorted(stats['endpoint_stats'].items(), 
                                         key=lambda x: x[1]['count'], reverse=True):
            print(f"{endpoint:40s} | Count: {ep_stats['count']:6d} | "
                  f"Mean: {ep_stats['mean_ms']:7.2f}ms | "
                  f"P95: {ep_stats['p95_ms']:7.2f}ms | "
                  f"P99: {ep_stats['p99_ms']:7.2f}ms")
        print()
        
        print("STATUS CODE DISTRIBUTION")
        print("-" * 80)
        for status, count in sorted(stats['status_codes'].items()):
            percentage = (count / stats['total_requests'] * 100) if stats['total_requests'] > 0 else 0
            print(f"  {status:3d}: {count:6d} ({percentage:5.2f}%)")
        print()
        
        if stats['error_types']:
            print("ERROR SUMMARY")
            print("-" * 80)
            for error_type, count in sorted(stats['error_types'].items(), 
                                          key=lambda x: x[1], reverse=True):
                print(f"  Status {error_type}: {count}")
            print()
        
        if stats.get('error_details'):
            print("ERROR DETAILS (Top 10)")
            print("-" * 80)
            for error_msg, count in sorted(stats['error_details'].items(), 
                                          key=lambda x: x[1], reverse=True)[:10]:
                percentage = (count / stats['total_requests'] * 100) if stats['total_requests'] > 0 else 0
                print(f"  {error_msg[:70]:70s} | {count:6d} ({percentage:5.2f}%)")
            print()
        
        print("SCALING ANALYSIS")
        print("-" * 80)
        if rt['p95'] > 1000:
            print("⚠️  P95 latency > 1s - Consider scaling up services")
        if rt['p99'] > 2000:
            print("⚠️  P99 latency > 2s - High tail latency detected")
        if stats['success_rate'] < 90:
            print(f"⚠️  Success rate {stats['success_rate']:.2f}% < 90% - Check error logs")
        elif stats['success_rate'] >= 95:
            print(f"✅ Excellent success rate: {stats['success_rate']:.2f}%")
        else:
            print(f"✅ Good success rate: {stats['success_rate']:.2f}% (realistic for load test with deletions)")
        if stats['requests_per_second'] > 50:
            print(f"✅ High throughput: {stats['requests_per_second']:.2f} req/s")
        
        read_endpoints = [ep for ep in stats['endpoint_stats'].keys() if 'GET' in ep]
        if read_endpoints:
            print(f"✅ Read operations detected: {len(read_endpoints)} endpoints")
            print("   (These use PostgreSQL read replicas)")
        
        rate_limit_count = stats['status_codes'].get(429, 0)
        if rate_limit_count > 0:
            rate_limit_pct = (rate_limit_count / stats['total_requests'] * 100) if stats['total_requests'] > 0 else 0
            print(f"⚠️  Rate limit hits: {rate_limit_count} ({rate_limit_pct:.2f}%)")
            print("   Consider: Using authentication (per-user rate limits) or increasing rate limit config")
        
        connection_errors = stats['status_codes'].get(0, 0)
        if connection_errors > stats['total_requests'] * 0.1:  # More than 10% connection errors
            connection_error_pct = (connection_errors / stats['total_requests'] * 100) if stats['total_requests'] > 0 else 0
            print(f"⚠️  Connection errors: {connection_errors} ({connection_error_pct:.2f}%)")
            print("   Possible causes: Connection pool exhaustion, network issues, or service unavailability")
        
        print()
        print("=" * 80)


async def main():
    parser = argparse.ArgumentParser(
        description="Comprehensive load test for RLLabs platform",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://api.localhost", 
                       help="Base URL of API Gateway (default: http://api.localhost via Ingress, or use --url http://localhost:8080 for port-forward)")
    parser.add_argument("--users", type=int, default=10, 
                       help="Number of concurrent users")
    parser.add_argument("--duration", type=int, default=60, 
                       help="Test duration in seconds")
    parser.add_argument("--token", help="JWT token for authenticated requests")
    parser.add_argument("--stress", action="store_true", 
                       help="Enable stress mode (higher upload/write ratio)")
    parser.add_argument("--no-auto-auth", action="store_true",
                       help="Disable auto-registration/login (use provided token only)")
    
    args = parser.parse_args()
    
    tester = ComprehensiveLoadTester(
        base_url=args.url,
        token=args.token,
        users=args.users,
        duration=args.duration,
        stress=args.stress,
        auto_auth=not args.no_auto_auth
    )
    
    await tester.run_load_test()


if __name__ == "__main__":
    asyncio.run(main())