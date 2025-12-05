#!/usr/bin/env python3
"""
Load Testing Script
=========================================

This script performs load testing on the RLLabs API Gateway and services
to demonstrate scaling behaviour and identify bottlenecks.

Usage:
    python load_test.py --url http://localhost:8080 --users 10 --duration 60
    python load_test.py --url http://api-gateway:8080 --users 50 --duration 300 --token <JWT_TOKEN>

Requirements:
    pip install httpx asyncio aiohttp
"""

import asyncio
import aiohttp
import argparse
import time
import json
from typing import Dict, List
from datetime import datetime
import statistics

class LoadTester:
    def __init__(self, base_url: str, token: str = None, users: int = 10, duration: int = 60):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.users = users
        self.duration = duration
        self.results: List[Dict] = []
        self.errors: List[Dict] = []
        
    async def make_request(self, session: aiohttp.ClientSession, endpoint: str, method: str = "GET", data: dict = None):
        """Make a single HTTP request and record metrics"""
        url = f"{self.base_url}{endpoint}"
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        
        start_time = time.time()
        try:
            if method == "GET":
                async with session.get(url, headers=headers) as response:
                    response_text = await response.text()
                    status = response.status
            elif method == "POST":
                async with session.post(url, headers=headers, json=data) as response:
                    response_text = await response.text()
                    status = response.status
            else:
                return None
            
            elapsed = time.time() - start_time
            
            result = {
                "endpoint": endpoint,
                "method": method,
                "status": status,
                "response_time": elapsed,
                "timestamp": datetime.now().isoformat(),
                "success": 200 <= status < 300
            }
            
            if not result["success"]:
                self.errors.append(result)
            else:
                self.results.append(result)
            
            return result
            
        except Exception as e:
            elapsed = time.time() - start_time
            error_result = {
                "endpoint": endpoint,
                "method": method,
                "status": "error",
                "response_time": elapsed,
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
                "success": False
            }
            self.errors.append(error_result)
            return error_result
    
    async def user_simulation(self, session: aiohttp.ClientSession, user_id: int):
        """Simulate a single user's behavior"""
        endpoints = [
            ("/health", "GET"),
            ("/api/models", "GET"),  # Public endpoint
            ("/api/models", "GET"),  # Repeat read
        ]
        
        # If token provided, add authenticated endpoints
        if self.token:
            endpoints.extend([
                ("/api/models", "GET"),  # Authenticated read
            ])
        
        start_time = time.time()
        request_count = 0
        
        while time.time() - start_time < self.duration:
            for endpoint, method in endpoints:
                await self.make_request(session, endpoint, method)
                request_count += 1
                # Small delay between requests
                await asyncio.sleep(0.1)
        
        return request_count
    
    async def run_load_test(self):
        """Run the load test with multiple concurrent users"""
        print(f"Starting load test:")
        print(f"  URL: {self.base_url}")
        print(f"  Users: {self.users}")
        print(f"  Duration: {self.duration}s")
        print(f"  Token: {'Provided' if self.token else 'None (public endpoints only)'}")
        print()
        
        connector = aiohttp.TCPConnector(limit=100)
        timeout = aiohttp.ClientTimeout(total=30)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            tasks = []
            for user_id in range(self.users):
                task = asyncio.create_task(self.user_simulation(session, user_id))
                tasks.append(task)
            
            # Wait for all users to complete
            await asyncio.gather(*tasks)
        
        self.print_results()
    
    def print_results(self):
        """Print test results and statistics"""
        total_requests = len(self.results) + len(self.errors)
        successful_requests = len(self.results)
        failed_requests = len(self.errors)
        
        if not self.results:
            print("No successful requests recorded!")
            return
        
        response_times = [r["response_time"] for r in self.results]
        
        print("\n" + "="*60)
        print("LOAD TEST RESULTS")
        print("="*60)
        print(f"Total Requests: {total_requests}")
        print(f"Successful: {successful_requests} ({successful_requests/total_requests*100:.1f}%)")
        print(f"Failed: {failed_requests} ({failed_requests/total_requests*100:.1f}%)")
        print()
        print("Response Time Statistics (successful requests):")
        print(f"  Min: {min(response_times)*1000:.2f}ms")
        print(f"  Max: {max(response_times)*1000:.2f}ms")
        print(f"  Mean: {statistics.mean(response_times)*1000:.2f}ms")
        print(f"  Median: {statistics.median(response_times)*1000:.2f}ms")
        if len(response_times) > 1:
            print(f"  Std Dev: {statistics.stdev(response_times)*1000:.2f}ms")
        print(f"  95th Percentile: {self.percentile(response_times, 0.95)*1000:.2f}ms")
        print(f"  99th Percentile: {self.percentile(response_times, 0.99)*1000:.2f}ms")
        print()
        print(f"Requests per second: {total_requests/self.duration:.2f}")
        print()
        
        # Group by endpoint
        endpoint_stats = {}
        for result in self.results:
            endpoint = result["endpoint"]
            if endpoint not in endpoint_stats:
                endpoint_stats[endpoint] = []
            endpoint_stats[endpoint].append(result["response_time"])
        
        print("Response Times by Endpoint:")
        for endpoint, times in endpoint_stats.items():
            print(f"  {endpoint}:")
            print(f"    Mean: {statistics.mean(times)*1000:.2f}ms")
            print(f"    P95: {self.percentile(times, 0.95)*1000:.2f}ms")
            print(f"    Count: {len(times)}")
        
        if self.errors:
            print()
            print("Error Summary:")
            error_types = {}
            for error in self.errors:
                error_type = error.get("status", "unknown")
                error_types[error_type] = error_types.get(error_type, 0) + 1
            for error_type, count in error_types.items():
                print(f"  {error_type}: {count}")
    
    def percentile(self, data: List[float], percentile: float) -> float:
        """Calculate percentile"""
        sorted_data = sorted(data)
        index = int(len(sorted_data) * percentile)
        return sorted_data[min(index, len(sorted_data) - 1)]


async def main():
    parser = argparse.ArgumentParser(description="Load test RLLabs services")
    parser.add_argument("--url", default="http://localhost:8080", help="Base URL of API Gateway")
    parser.add_argument("--users", type=int, default=10, help="Number of concurrent users")
    parser.add_argument("--duration", type=int, default=60, help="Test duration in seconds")
    parser.add_argument("--token", help="JWT token for authenticated requests")
    
    args = parser.parse_args()
    
    tester = LoadTester(
        base_url=args.url,
        token=args.token,
        users=args.users,
        duration=args.duration
    )
    
    await tester.run_load_test()


if __name__ == "__main__":
    asyncio.run(main())

