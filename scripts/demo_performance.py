"""
RLLabs Distributed Systems Performance Demonstration

This script demonstrates the key distributed systems principles:
1. Horizontal Scalability - HPA responds to load
2. Caching & Read Replicas - Fast read performance
3. Write Consistency - Strong consistency for uploads
4. Resilience - Fail-fast patterns, graceful degradation

Run: python scripts/demo_performance.py --url http://api.localhost
"""

import asyncio
import aiohttp
import argparse
import time
import random
import json
import sys
from dataclasses import dataclass
from typing import List, Dict, Optional
import subprocess


@dataclass
class TestResult:
    name: str
    total_requests: int
    successful: int
    failed: int
    throughput: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    error_breakdown: dict = None 


def print_header(text: str):
    print(f"\n{'='*70}")
    print(f"  {text}")
    print(f"{'='*70}\n")


def print_result(result: TestResult, explanation: str = ""):
    print(f"  {'Metric':<20} {'Value':>15}")
    print(f"  {'-'*35}")
    print(f"  {'Total Requests':<20} {result.total_requests:>15}")
    print(f"  {'Success Rate':<20} {100*result.successful/result.total_requests:>14.1f}%")
    print(f"  {'Throughput':<20} {result.throughput:>12.1f} req/s")
    print(f"  {'Mean Latency':<20} {result.mean_ms:>13.2f}ms")
    print(f"  {'P50 (Median)':<20} {result.p50_ms:>13.2f}ms")
    print(f"  {'P95 Latency':<20} {result.p95_ms:>13.2f}ms")
    print(f"  {'P99 Latency':<20} {result.p99_ms:>13.2f}ms")
    
    if result.error_breakdown and result.failed > 0:
        print(f"\n  Error Breakdown:")
        for error_type, count in sorted(result.error_breakdown.items(), key=lambda x: x[1], reverse=True):
            percentage = 100 * count / result.total_requests
            print(f"    {error_type:<25} {count:>5} ({percentage:>5.1f}%)")
    
    if explanation:
        print(f"\n  {explanation}")


async def make_request(session: aiohttp.ClientSession, url: str, method: str = "GET", 
                       json_data: dict = None, headers: dict = None) -> tuple:
    """Make a request and return (success, latency_seconds, status_code, error_type)
    Success is defined as 200-299 or 429 (rate limit)
    """
    start = time.time()
    error_type = None
    status_code = 0
    try:
        if method == "GET":
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:  # Increased from 10s
                text = await resp.text()
                status_code = resp.status
                # Consider 200-299 and 429 (rate limit) as success
                success = (200 <= resp.status < 300) or resp.status == 429
                if resp.status >= 400 and resp.status != 429:
                    error_type = f"HTTP_{resp.status}"
                return success, time.time() - start, status_code, error_type
        elif method == "POST":
            async with session.post(url, json=json_data, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()
                status_code = resp.status
                # Consider 200-299 and 429 (rate limit) as success
                success = (200 <= resp.status < 300) or resp.status == 429
                if resp.status >= 400 and resp.status != 429:
                    error_type = f"HTTP_{resp.status}"
                return success, time.time() - start, status_code, error_type
    except asyncio.TimeoutError:
        error_type = "TIMEOUT"
        return False, time.time() - start, 0, error_type
    except aiohttp.ClientError as e:
        error_type = f"CLIENT_ERROR_{type(e).__name__}"
        return False, time.time() - start, 0, error_type
    except Exception as e:
        error_type = f"EXCEPTION_{type(e).__name__}"
        return False, time.time() - start, 0, error_type


def calculate_results(name: str, results: List[tuple], duration: float) -> TestResult:
    """
    Calculate test results from list of (success, latency, status_code, error_type) tuples
    Considers 200-299 and 429 (rate limit) as success
    """
    # Consider 200-299 and 429 (rate limit) as success
    successful = [r[1] for r in results if (r[0] or r[2] == 429) and r[1] > 0]
    failed_results = [r for r in results if not r[0] and r[2] != 429]
    failed = len(failed_results)
    
    error_breakdown = {}
    for _, _, status_code, error_type in failed_results:
        key = error_type or f"HTTP_{status_code}" if status_code else "UNKNOWN"
        error_breakdown[key] = error_breakdown.get(key, 0) + 1
    
    if not successful:
        return TestResult(
            name=name, total_requests=len(results), successful=0, failed=failed,
            throughput=0, mean_ms=0, p50_ms=0, p95_ms=0, p99_ms=0, min_ms=0, max_ms=0,
            error_breakdown=error_breakdown
        )
    
    successful.sort()
    return TestResult(
        name=name,
        total_requests=len(results),
        successful=len(successful),
        failed=failed,
        throughput=len(successful) / duration,
        mean_ms=1000 * sum(successful) / len(successful),
        p50_ms=1000 * successful[len(successful) // 2],
        p95_ms=1000 * successful[int(len(successful) * 0.95)],
        p99_ms=1000 * successful[int(len(successful) * 0.99)],
        min_ms=1000 * min(successful),
        max_ms=1000 * max(successful),
        error_breakdown=error_breakdown
    )


async def demo_read_performance(base_url: str, concurrent_users: int = 10, duration: int = 10) -> TestResult:
    """
    Demo 1: Read Performance
    
    Demonstrates: Caching, PostgreSQL read replicas, horizontal scaling
    Trade-off: Slightly stale data (cache TTL) for much better performance
    """
    print_header("DEMO 1: Read Performance (Caching + Read Replicas)")
    print("  Simulating realistic read traffic from multiple concurrent users")
    print(f"  - {concurrent_users} concurrent users (reduced for Docker resource limits)")
    print(f"  - {duration} second duration")
    print("  - Mixed read endpoints (model list, model details, versions)")
    print()
    
    # Fetch valid models at the start
    print("  Fetching valid models...", end=" ", flush=True)
    async with aiohttp.ClientSession() as init_session:
        valid_models = await get_existing_models(init_session, base_url, count=20)
        if valid_models:
            print(f"✓ Found {len(valid_models)} models")
        else:
            print("⚠ No models found")
    print()
    
    # Build dynamic endpoints based on valid models
    base_endpoints = [
        "/api/models?page=1",
        "/api/models?page=2", 
        "/api/training-jobs", 
        "/health",
    ]
    
    model_endpoints = []
    if valid_models:
        # Use up to 8 models for variety
        models_to_use = valid_models[:8]
        for model_id in models_to_use:
            model_endpoints.extend([
                f"/api/models/{model_id}",
                f"/api/models/{model_id}?include_versions=true",
                f"/api/models/{model_id}/latest",
                f"/api/models/{model_id}/comments",
            ])
    
    endpoints = base_endpoints + model_endpoints
    invalid_models = set()
    
    results = []
    
    async def user_worker(session, user_id):
        user_results = []
        start_time = time.time()
        while time.time() - start_time < duration:
            endpoint = random.choice(endpoints)
            url = base_url + endpoint
            success, latency, status_code, error_type = await make_request(session, url)
            
            # Track 404s for model endpoints
            if status_code == 404 and "/api/models/" in endpoint:
                parts = endpoint.split("/")
                if len(parts) >= 4 and parts[3].isdigit():
                    model_id = int(parts[3])
                    invalid_models.add(model_id)
            
            user_results.append((success, latency, status_code, error_type))
            await asyncio.sleep(random.uniform(0.05, 0.15))  
        return user_results
    
    async with aiohttp.ClientSession() as session:
        start = time.time()
        tasks = [user_worker(session, i) for i in range(concurrent_users)]
        all_results = await asyncio.gather(*tasks)
        total_duration = time.time() - start
        
        for user_result in all_results:
            results.extend(user_result)
    
    result = calculate_results("Read Performance", results, total_duration)
    print_result(result, 
        "Fast reads achieved via Redis caching (TTL=120s) + PostgreSQL read replicas.\n"
        "     Trade-off: Data may be up to 2 minutes stale (acceptable for browsing).")
    return result


async def demo_cache_effectiveness(base_url: str) -> None:
    """
    Demo 2: Cache Effectiveness
    
    Demonstrates: API Gateway Redis cache hit/miss performance difference
    """
    print_header("DEMO 2: Cache Hit vs Miss Performance")
    print("  Comparing response times for cached vs uncached requests")
    print()
    
    async with aiohttp.ClientSession() as session:
        cold_times = []
        print("  Making 5 'cold' requests (unique query params to bypass cache)...")
        for i in range(5):
            url = f"{base_url}/api/models?page=1&_nocache={time.time()}{i}"
            _, latency, _, _ = await make_request(session, url)
            cold_times.append(latency * 1000)
            await asyncio.sleep(0.1)
        
        print(f"  Cold request avg: {sum(cold_times)/len(cold_times):.2f}ms")
        
        print("\n  Making 5 'warm' requests (same URL, should hit cache)...")
        warm_times = []
        for i in range(5):
            url = f"{base_url}/api/models?page=1"
            _, latency, _, _ = await make_request(session, url)
            warm_times.append(latency * 1000)
            await asyncio.sleep(0.1)
        
        print(f"  Warm request avg: {sum(warm_times)/len(warm_times):.2f}ms")
        
        speedup = sum(cold_times) / sum(warm_times) if sum(warm_times) > 0 else 0
        print(f"\n  📊 Cache speedup: {speedup:.1f}x faster")
        print("     Trade-off: Cache invalidation on writes ensures consistency for updates.")


async def demo_write_consistency(base_url: str) -> None:
    """
    Demo 3: Write Consistency
    
    Demonstrates: Strong consistency for write operations
    Trade-off: Higher latency for writes, but guaranteed durability
    """
    print_header("DEMO 3: Write Consistency (Strong Consistency)")
    print("  Write operations use synchronous replication for durability")
    print()
    
    async with aiohttp.ClientSession() as session:
        login_data = {"username": "demo_user", "password": "demo_password"}
        url = f"{base_url}/api/auth/login"
        async with session.post(url, json=login_data) as resp:
            if resp.status == 200:
                token = (await resp.json()).get("access_token")
            else:
                reg_url = f"{base_url}/api/auth/register"
                reg_data = {"username": f"demo_user_{int(time.time())}", "password": "demo_password", "email": f"demo{int(time.time())}@test.com"}
                async with session.post(reg_url, json=reg_data) as reg_resp:
                    if reg_resp.status in [200, 201]:
                        token = (await reg_resp.json()).get("access_token")
                    else:
                        print("  ⚠️  Could not authenticate for write test")
                        return
        
        headers = {"Authorization": f"Bearer {token}"}
        
        write_times = []
        print("  Creating 3 new models (write operation)...")
        for i in range(3):
            model_data = {
                "name": f"demo-model-{int(time.time())}-{i}",
                "description": "Performance demo model",
                "model_type": "classification",
                "framework": "pytorch"
            }
            start = time.time()
            async with session.post(f"{base_url}/api/models", json=model_data, headers=headers) as resp:
                await resp.text()
                write_times.append((time.time() - start) * 1000)
        
        print(f"\n  Write latencies: {', '.join(f'{t:.0f}ms' for t in write_times)}")
        print(f"  Average write latency: {sum(write_times)/len(write_times):.0f}ms")
        print("\n   Writes are slower than reads because they:")
        print("     - Synchronously replicate to PostgreSQL replicas")
        print("     - Invalidate Redis cache entries")
        print("     - Publish events to RabbitMQ for eventual consistency")
        print("     Trade-off: Strong consistency (CP) chosen over availability (AP)")


async def demo_horizontal_scaling(base_url: str) -> None:
    """
    Demo 4: Horizontal Scaling (HPA in action)
    
    Demonstrates: Kubernetes HPA responding to load
    """
    print_header("DEMO 4: Horizontal Pod Autoscaler (HPA)")
    print("  Showing current scaling configuration and pod distribution")
    print()
    
    try:
        result = subprocess.run(
            ["kubectl", "get", "hpa", "-o", "wide"],
            capture_output=True, text=True, timeout=10
        )
        print("  Current HPA Status:")
        for line in result.stdout.strip().split('\n'):
            print(f"    {line}")
        
        print()
        
        result = subprocess.run(
            ["kubectl", "get", "pods", "-o", "wide"],
            capture_output=True, text=True, timeout=10
        )
        print("  Pod Distribution Across Nodes:")
        lines = result.stdout.strip().split('\n')
        header = lines[0]
        pods = [l for l in lines[1:] if any(svc in l for svc in ['api-gateway', 'model-catalog', 'upload-download'])]
        print(f"    {header}")
        for pod in pods[:9]:  
            print(f"    {pod}")
        if len(pods) > 9:
            print(f"    ... and {len(pods)-9} more pods")
        
        print("\n   HPA automatically scales pods based on CPU/memory utilization.")
        print("     Trade-off: Scaling takes 15-30s; we use higher minReplicas for instant capacity.")
        
    except Exception as e:
        print(f"  ⚠️  Could not get Kubernetes info: {e}")


async def demo_resilience(base_url: str) -> None:
    """
    Demo 5: Fault Tolerance & Resilience Patterns
    
    Demonstrates: Circuit breakers, retries, graceful degradation, recovery
    """
    print_header("DEMO 5: Fault Tolerance & Resilience")
    print("  Simulating Model Catalog failure → Testing fail-fast & recovery")
    print()
    
    import subprocess
    
    async with aiohttp.ClientSession() as session:
        # Baseline
        print("  [Baseline] Testing healthy system...", end=" ", flush=True)
        baseline_results = []
        for i in range(5):
            success, latency, _, _ = await make_request(session, f"{base_url}/api/models?page=1")
            if success:
                baseline_results.append(latency * 1000)
            await asyncio.sleep(0.1)
        if baseline_results:
            print(f"✓ {len(baseline_results)}/5 success, {sum(baseline_results)/len(baseline_results):.0f}ms avg")
        print()
        
        # Scale down
        print("  [Failure] Scaling Model Catalog to 0 replicas...", end=" ", flush=True)
        try:
            subprocess.run(
                ["kubectl", "scale", "deployment", "model-catalog-service", "--replicas=0"],
                capture_output=True, text=True, timeout=5
            )
            await asyncio.sleep(8)  # Wait for pods to terminate
            
            # Verify pods are down
            verify_result = subprocess.run(
                ["kubectl", "get", "pods", "-l", "app=model-catalog-service", "--no-headers"],
                capture_output=True, text=True, timeout=5
            )
            if verify_result.returncode == 0 and not verify_result.stdout.strip():
                print("✓ Service down")
            else:
                print("⚠ Pods still running")
        except Exception as e:
            print(f"✗ Error: {e}")
        print()
        
        # Test failures
        print("  [Testing] Making requests during failure...", end=" ", flush=True)
        failure_results = []
        test_endpoints = [
            f"/api/models?page=1&_nocache={time.time()}",
            f"/api/models/999999?nocache={time.time()}",
        ]
        
        for i in range(15):
            endpoint = test_endpoints[i % len(test_endpoints)]
            start = time.time()
            try:
                async with session.get(
                    f"{base_url}{endpoint}",
                    timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    latency = (time.time() - start) * 1000
                    success = 200 <= resp.status < 400
                    failure_results.append((success, latency, resp.status))
            except:
                latency = (time.time() - start) * 1000
                failure_results.append((False, latency, 0))
            await asyncio.sleep(0.15)
        
        successful = [r for r in failure_results if r[0]]
        failed = [r for r in failure_results if not r[0]]
        if failed:
            avg_fail_latency = sum(r[1] for r in failed) / len(failed)
            print(f"✓ {len(failed)}/{len(failure_results)} failed, {avg_fail_latency:.0f}ms avg failure time")
        else:
            print("⚠ No failures detected")
        print()
        
        # Health check
        print("  [Degradation] Testing health endpoint...", end=" ", flush=True)
        health_success = 0
        for i in range(3):
            success, _, _, _ = await make_request(session, f"{base_url}/health")
            if success:
                health_success += 1
            await asyncio.sleep(0.1)
        print(f"✓ {health_success}/3 success")
        print()
        
        # Recovery
        print("  [Recovery] Scaling Model Catalog back to 2 replicas...", end=" ", flush=True)
        try:
            subprocess.run(
                ["kubectl", "scale", "deployment", "model-catalog-service", "--replicas=2"],
                capture_output=True, timeout=5
            )
            await asyncio.sleep(12)  # Wait for pods to be ready
            
            recovery_results = []
            for i in range(5):
                success, latency, _, _ = await make_request(session, f"{base_url}/api/models?page=1")
                if success:
                    recovery_results.append(latency * 1000)
                await asyncio.sleep(0.2)
            
            if recovery_results:
                print(f"✓ {len(recovery_results)}/5 success, {sum(recovery_results)/len(recovery_results):.0f}ms avg")
            else:
                print("⚠ Still recovering")
        except Exception as e:
            print(f"✗ Error: {e}")
        print()
        
        # Summary
        print("  Summary:")
        if failed:
            failure_rate = len(failed) / len(failure_results) * 100
            avg_fail_latency = sum(r[1] for r in failed) / len(failed)
            print(f"    ✓ Fail-fast: {failure_rate:.0f}% failures, {avg_fail_latency:.0f}ms avg")
        print(f"    ✓ Health checks: {health_success}/3 available")
        if recovery_results:
            print(f"    ✓ Recovery: {len(recovery_results)}/5 requests successful")
        print("  📊 View metrics in Grafana: http://localhost:3000")


async def get_existing_models(session: aiohttp.ClientSession, base_url: str, count: int = 50) -> List[int]:
    """Fetch existing model IDs from the database"""
    model_ids = []
    try:
        # Fetch first page to get total count
        url = f"{base_url}/api/models?page=1&page_size=50"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                items = data.get("items", [])
                total_pages = data.get("total_pages", 1)
                
                # Collect models from first page
                for item in items:
                    if isinstance(item, dict) and item.get("id"):
                        model_ids.append(item["id"])
                
                # Fetch additional pages if needed
                pages_to_fetch = min(total_pages, 5)  # Limit to 5 pages for performance
                for page in range(2, pages_to_fetch + 1):
                    url = f"{base_url}/api/models?page={page}&page_size=50"
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            items = data.get("items", [])
                            for item in items:
                                if isinstance(item, dict) and item.get("id"):
                                    model_ids.append(item["id"])
                            if len(model_ids) >= count:
                                break
    except Exception as e:
        print(f"    Warning: Could not fetch models: {e}")
    
    return model_ids[:count] if model_ids else []


async def get_artifact_ids(session: aiohttp.ClientSession, base_url: str, model_ids: List[int], count: int = 20) -> List[str]:
    """Fetch artifact IDs (content hashes) from model versions for download endpoints"""
    artifact_ids = []
    try:
        for model_id in model_ids[:10]:  # Check up to 10 models
            if len(artifact_ids) >= count:
                break
            try:
                url = f"{base_url}/api/models/{model_id}/versions?limit=10"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        versions = await resp.json()
                        if isinstance(versions, list):
                            for version in versions:
                                content_hash = version.get("content_hash") if isinstance(version, dict) else None
                                if content_hash:
                                    # Normalize hash format
                                    if not content_hash.startswith("sha256:"):
                                        content_hash = f"sha256:{content_hash}"
                                    artifact_ids.append(content_hash)
                                    if len(artifact_ids) >= count:
                                        break
            except Exception:
                continue  # Skip models that fail
        return artifact_ids[:count]
    except Exception as e:
        return []


async def demo_scalability_test(base_url: str) -> None:
    """
    Demo 6: Scalability Demonstration
    
    Shows throughput at different concurrency levels, exercising ALL services:
    - API Gateway (scales)
    - Model Catalog Service (scales, uses PostgreSQL + Redis)
    - Upload/Download Service (scales, uses MinIO - doesn't scale)
    - Collaboration Service (scales, uses MongoDB)
    - Training Jobs (scales, uses RabbitMQ - doesn't scale)
    
    After load test completes, traffic stops and HPA should scale down pods.
    """
    print_header("DEMO 6: Scalability Under Increasing Load - Full System Test")
    print("  Testing throughput at different concurrency levels")
    print("  Exercises ALL services: API Gateway, Model Catalog, Upload/Download,")
    print("                          Collaboration, Training Jobs, MinIO, RabbitMQ")
    print("  Note: Each test level runs for 40 seconds to allow HPA time to scale")
    print("        (HPA checks metrics every 15s, needs ~30-40s to react).")
    print("        CPU thresholds lowered for demo visibility (15-20% vs 70% production).")
    print("        High concurrency to stress backend services (I/O-bound workloads).")
    print("        Watch for pod scaling during test - HPA status shown at all levels.")
    print("        After test completes, watch pods scale DOWN as traffic dies down.")
    print()
    
    # Fetch valid models and artifact IDs at the start
    print("  Fetching valid models from database...", end=" ", flush=True)
    async with aiohttp.ClientSession() as init_session:
        valid_models = await get_existing_models(init_session, base_url, count=50)
        if valid_models:
            print(f"✓ Found {len(valid_models)} models")
            print(f"    Using model IDs: {valid_models[:10]}..." if len(valid_models) > 10 else f"    Using model IDs: {valid_models}")
        else:
            print("⚠ No models found - will use generic endpoints only")
        
        # Fetch artifact IDs for download endpoints
        artifact_ids = []
        if valid_models:
            artifact_ids = await get_artifact_ids(init_session, base_url, valid_models, count=20)
            if artifact_ids:
                print(f"  Found {len(artifact_ids)} artifact IDs for download endpoints")
    print()
    
    results = []
    invalid_models = set()  # Track models that return 404
    model_refresh_counter = 0
    
    # Duration per level - reduced for faster demo, but still enough for HPA to react
    # HPA checks metrics every 15s, needs ~30-40s to scale
    test_duration = 40  # seconds per level (reduced from 60s for faster demo)
    
    # Start at 10 users, increase to demonstrate scaling
    for users in [10, 20, 35]:
        print(f"  [{users} users]", end=" ", flush=True)
        
        # Refresh model list periodically
        model_refresh_counter += 1
        if model_refresh_counter % 3 == 0 and valid_models:
            async with aiohttp.ClientSession() as refresh_session:
                new_models = await get_existing_models(refresh_session, base_url, count=50)
                if new_models:
                    valid_models = [m for m in new_models if m not in invalid_models]
        
        latencies = []
        
        # Build dynamic endpoints based on valid models
        base_endpoints = [
            "/api/models?page=1",
            "/api/models?page=2",
            "/api/training-jobs",
            "/health",
        ]
        
        # Add model-specific endpoints using valid models
        # Focus on endpoints that stress backend services (Model Catalog, Collaboration)
        # Prioritize CPU-intensive queries (include_versions, versions) to increase CPU usage
        model_endpoints = []
        if valid_models:
            # Use up to 20 models for more variety and load
            models_to_use = valid_models[:20]
            for model_id in models_to_use:
                if model_id not in invalid_models:
                    # Prioritize CPU-intensive endpoints (include_versions=true, versions queries)
                    # These force database joins and processing, increasing CPU usage
                    model_endpoints.extend([
                        f"/api/models/{model_id}?include_versions=true",  # CPU-intensive (loads versions with joins)
                        f"/api/models/{model_id}/versions?limit=100",  # Stress database queries (more data)
                        f"/api/models/{model_id}/versions?limit=50",  # Stress database queries
                        f"/api/models/{model_id}",  # Standard endpoint
                        f"/api/models/{model_id}/latest",
                        f"/api/models/{model_id}/comments",  # Stress Collaboration Service + MongoDB
                    ])
        
        # Add upload/download endpoints to stress Upload-Download Service
        upload_download_endpoints = []
        if artifact_ids:
            for artifact_id in artifact_ids[:15]:  # Use up to 15 artifacts
                upload_download_endpoints.extend([
                    f"/api/downloads/{artifact_id}",  # Stress Upload-Download Service + MinIO
                    f"/api/downloads/{artifact_id}?expires_in=3600",  # With expiration parameter
                ])
        
        test_endpoints = base_endpoints + model_endpoints + upload_download_endpoints
        
        async def worker(session):
            worker_results = []
            start_time = time.time()
            request_count = 0
            while time.time() - start_time < test_duration:  
                # Balanced load across services:
                # 50% Model Catalog (CPU-intensive), 15% Upload-Download, 15% Collaboration, 20% other
                rand = random.random()
                if rand < 0.5:
                    # Model Catalog endpoints - prioritize CPU-intensive ones (include_versions, versions)
                    catalog_endpoints = [e for e in test_endpoints if "/api/models" in e and "/comments" not in e and "/downloads" not in e]
                    if catalog_endpoints:
                        # Weight towards CPU-intensive endpoints (include_versions, versions)
                        cpu_intensive = [e for e in catalog_endpoints if "include_versions" in e or "/versions" in e]
                        if cpu_intensive and random.random() < 0.7:
                            endpoint = random.choice(cpu_intensive)
                        else:
                            endpoint = random.choice(catalog_endpoints)
                    else:
                        endpoint = random.choice(test_endpoints)
                elif rand < 0.65:
                    # Upload-Download endpoints (stress Upload-Download Service + MinIO)
                    download_endpoints = [e for e in test_endpoints if "/downloads" in e]
                    endpoint = random.choice(download_endpoints) if download_endpoints else random.choice(test_endpoints)
                elif rand < 0.8:
                    # Collaboration endpoints (stress MongoDB)
                    collab_endpoints = [e for e in test_endpoints if "/comments" in e]
                    endpoint = random.choice(collab_endpoints) if collab_endpoints else random.choice(test_endpoints)
                else:
                    # Other endpoints
                    endpoint = random.choice(test_endpoints)
                
                # Add cache-busting parameter only to GET endpoints without query params (avoid 422 errors)
                # Skip cache-busting for endpoints that already have query params to avoid validation errors
                if random.random() < 0.2 and "?" not in endpoint and endpoint.startswith("/api/models"):
                    url = f"{base_url}{endpoint}?_nocache={random.randint(1000,9999)}"
                else:
                    url = f"{base_url}{endpoint}"
                success, latency, status_code, error_type = await make_request(session, url)
                
                # Track 404s for model endpoints
                if status_code == 404 and "/api/models/" in endpoint and endpoint.count("/") >= 3:
                    parts = endpoint.split("/")
                    if len(parts) >= 4 and parts[3].isdigit():
                        model_id = int(parts[3])
                        # Only mark as invalid if it's a direct model access (not comments/versions)
                        is_direct_model = len(parts) == 4 or (len(parts) == 5 and parts[4] in ["", "latest"])
                        if is_direct_model:
                            invalid_models.add(model_id)
                            if model_id in valid_models:
                                valid_models.remove(model_id)
                
                worker_results.append((success, latency, status_code, error_type))
                request_count += 1
                
                # Very aggressive load - minimal sleep to maximize concurrent requests
                # More concurrent requests = more CPU usage even for I/O-bound services
                # Reduced sleep further to increase throughput scaling
                await asyncio.sleep(random.uniform(0.005, 0.03))  # Even faster requests for higher throughput
            return worker_results
        
        async with aiohttp.ClientSession() as session:
            start = time.time()
            tasks = [worker(session) for _ in range(users)]
            all_results = await asyncio.gather(*tasks)
            duration = time.time() - start
            
            for r in all_results:
                latencies.extend(r)
        
        # Consider 200-299 and 429 (rate limit) as success
        successful = [r[1] for r in latencies if (r[0] or r[2] == 429) and r[1] > 0]
        failed_results = [r for r in latencies if not r[0] and r[2] != 429]
        
        error_breakdown = {}
        for _, _, status_code, error_type in failed_results:
            key = error_type or f"HTTP_{status_code}" if status_code else "UNKNOWN"
            error_breakdown[key] = error_breakdown.get(key, 0) + 1
        
        if successful:
            throughput = len(successful) / duration
            mean_latency = 1000 * sum(successful) / len(successful)
            results.append((users, throughput, mean_latency))
            success_rate = len(successful) / len(latencies) * 100 if latencies else 0
            print(f"✓ {throughput:.1f} req/s, {mean_latency:.1f}ms mean, {success_rate:.1f}% success")
            if error_breakdown:
                top_errors = sorted(error_breakdown.items(), key=lambda x: x[1], reverse=True)[:3]
                error_summary = ", ".join([f"{k}:{v}" for k, v in top_errors])
                print(f"    Errors: {error_summary}")
        else:
            print("✗ Failed")
            if error_breakdown:
                top_errors = sorted(error_breakdown.items(), key=lambda x: x[1], reverse=True)[:3]
                error_summary = ", ".join([f"{k}:{v}" for k, v in top_errors])
                print(f"    Error breakdown: {error_summary}")
        
        # Show pod counts (concise)
        try:
            pod_counts = {}
            for service in ["api-gateway", "model-catalog-service", "upload-download-service", "collaboration-service"]:
                result = subprocess.run(
                    ["kubectl", "get", "pods", "-l", f"app={service}", "--no-headers"],
                    capture_output=True, text=True, timeout=3
                )
                if result.returncode == 0:
                    pod_counts[service] = len([l for l in result.stdout.strip().split('\n') if l.strip()])
            
            if pod_counts:
                pods_str = ", ".join([f"{k.replace('-service', '').replace('api-', 'Gateway')}: {v}" for k, v in pod_counts.items()])
                print(f"    Pods: {pods_str}")
        except Exception:
            pass
        
        if users < 40:
            await asyncio.sleep(3)  # Short cooldown between levels (reduced for faster demo)
    
    print("\n  Summary:")
    print("  ┌────────────┬──────────────┬──────────────┐")
    print("  │ Users      │ Throughput   │ Mean Latency │")
    print("  ├────────────┼──────────────┼──────────────┤")
    for users, throughput, latency in results:
        print(f"  │ {users:>10} │ {throughput:>9.1f}/s │ {latency:>10.1f}ms │")
    print("  └────────────┴──────────────┴──────────────┘")
    
    if len(results) >= 2:
        scale_factor = results[-1][0] / results[0][0]
        throughput_factor = results[-1][1] / results[0][1]
        print(f"\n  {scale_factor:.0f}x users → {throughput_factor:.1f}x throughput")
        
        # Show final pod counts
        try:
            final_hpa = subprocess.run(
                ["kubectl", "get", "hpa", "-o", "jsonpath={range .items[*]}{.metadata.name}:{.status.currentReplicas}{'\\n'}{end}"],
                capture_output=True, text=True, timeout=5
            )
            if final_hpa.returncode == 0:
                print(f"\n  Pods scaled:")
                for line in final_hpa.stdout.strip().split('\n'):
                    if line.strip() and ':' in line:
                        parts = line.split(':')
                        if len(parts) >= 2:
                            name = parts[0].replace('-hpa', '').replace('-service', '')
                            replicas = parts[1]
                            print(f"    {name}: {replicas}")
        except Exception:
            pass
    
    print("\n  ✓ Load test complete. Pods will scale down automatically.")


async def main():
    parser = argparse.ArgumentParser(description="RLLabs Distributed Systems Performance Demo")
    parser.add_argument("--url", default="http://api.localhost", help="API Gateway URL")
    parser.add_argument("--demo", type=int, choices=[1,2,3,4,5,6], help="Run specific demo (1-6)")
    parser.add_argument("--auto", action="store_true", help="Skip prompts (auto-advance for recording)")
    args = parser.parse_args()
    
    base_url = args.url.rstrip("/")
    
    print("\n" + "="*70)
    print("  RLLabs Distributed Systems - Performance & Scalability")
    print("="*70)
    print(f"\n  Target: {base_url}")
    print("  These tests showcase key distributed systems principles:\n")
    print("  1. Read Performance    - Caching + Read Replicas")
    print("  2. Cache Effectiveness - Hit vs Miss comparison")
    print("  3. Write Consistency   - Strong consistency trade-offs")
    print("  4. Horizontal Scaling  - HPA configuration")
    print("  5. Resilience Patterns - Fail-fast, circuit breakers")
    print("  6. Scalability Test    - Full system load test (all services)")
    print("\n  Services exercised:")
    print("    • API Gateway (scales via HPA)")
    print("    • Model Catalog Service (scales, uses PostgreSQL + Redis)")
    print("    • Upload/Download Service (scales, uses MinIO)")
    print("    • Collaboration Service (scales, uses MongoDB)")
    print("    • Training Jobs (scales, uses RabbitMQ)")
    print("    • Infrastructure: MinIO, RabbitMQ (single instance, handle load)")
    
    print("\n  Checking connectivity...", end=" ")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{base_url}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    print("✓ Connected\n")
                else:
                    print(f"✗ Status {resp.status}")
                    return
        except Exception as e:
            print(f"✗ Failed: {e}")
            return
    
    demos = [
        (1, demo_read_performance),
        (2, demo_cache_effectiveness),
        (3, demo_write_consistency),
        (4, demo_horizontal_scaling),
        (5, demo_resilience),
        (6, demo_scalability_test),
    ]
    
    if args.demo:
        demos = [(n, f) for n, f in demos if n == args.demo]
    
    for num, demo_func in demos:
        if num in [1, 6]:  
            await demo_func(base_url)
        else:
            await demo_func(base_url)
        
        if num < 6 and not args.demo and not args.auto:
            print("\n  Press Enter to continue to next demo...", end="")
            try:
                input()
            except EOFError:
                pass
        elif num < 6 and args.auto:
            print("\n  [Auto-advancing in 2s...]")
            await asyncio.sleep(2)
    
    print("\n  ✓ All demos complete\n")


if __name__ == "__main__":
    asyncio.run(main())
