#!/usr/bin/env python3
"""
Seed Database with Test Models
===============================

Creates a batch of models in the database for load testing.
This is better than running pytest because:
- Models persist (pytest might clean up)
- More control over quantity and variety
- Faster than running full test suite
- Can be run multiple times safely

Usage:
    # Seed with 100 models
    python scripts/seed_database.py --count 100

    # Seed with models and versions
    python scripts/seed_database.py --count 50 --with-versions

    # Seed in Kind cluster
    kubectl run seed-db --image=python:3.11 --rm -it --restart=Never -- \
        python -c "$(cat scripts/seed_database.py)" --count 100
"""

import argparse
import time
import uuid
import sys
import os
from typing import List, Optional

try:
    import requests
except ImportError:
    print("Error: 'requests' library not found. Install it with: pip install requests")
    sys.exit(1)

# Default gateway URL (can be overridden)
GATEWAY_URL = "http://localhost:8080"  # Default to port 8080
if "KUBERNETES_SERVICE_HOST" in os.environ:
    # Running in Kubernetes
    GATEWAY_URL = "http://api-gateway:8080"

def generate_token(gateway_url: str) -> Optional[str]:
    """Generate a JWT token for authentication"""
    try:
        # Use a consistent username for seeding (so we can reuse if it exists)
        username = "seed-user"
        email = "seed-user@seed.example.com"
        password = "seed_password_123"
        
        # First, try to login (in case user already exists)
        try:
            response = requests.post(
                f"{gateway_url}/api/auth/login",
                json={
                    "username": username,
                    "password": password
                },
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                token = data.get("token")
                if token:
                    return token
        except Exception as e:
            pass  # Login failed, try register
        
        # Register new user
        response = requests.post(
            f"{gateway_url}/api/auth/register",
            json={
                "username": username,
                "email": email,
                "password": password
            },
            timeout=5
        )
        
        if response.status_code == 200 or response.status_code == 201:
            data = response.json()
            token = data.get("token")
            if token:
                return token
        else:
            # If register failed, try login one more time (user might have been created)
            try:
                response = requests.post(
                    f"{gateway_url}/api/auth/login",
                    json={
                        "username": username,
                        "password": password
                    },
                    timeout=5
                )
                
                if response.status_code == 200:
                    data = response.json()
                    token = data.get("token")
                    if token:
                        return token
            except Exception:
                pass
            
    except requests.exceptions.ConnectionError as e:
        print(f"Error: Could not connect to {gateway_url}")
        print(f"   Make sure API Gateway is running and port-forward is active:")
        print(f"   kubectl port-forward svc/api-gateway 8080:8080")
        return None
    except Exception as e:
        print(f"Warning: Could not generate token: {e}")
        print("Continuing without authentication (may fail if auth required)")
    
    return None

def create_model(gateway_url: str, token: Optional[str], model_num: int, with_versions: bool = False) -> Optional[dict]:
    """Create a single model"""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    model_data = {
        "name": f"seed-model-{model_num}-{uuid.uuid4().hex[:8]}",
        "description": f"Seeded model #{model_num} for load testing. Created at {time.strftime('%Y-%m-%d %H:%M:%S')}"
    }
    
    try:
        response = requests.post(
            f"{gateway_url}/api/models",
            json=model_data,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 201:
            model = response.json()
            model_id = model.get("id")
            
            if with_versions and model_id:
                # Create a few versions
                for version_num in range(1, 4):  # Create 3 versions
                    try:
                        # Version must be an integer, not a string
                        version_data = {
                            "version": version_num,  # Integer, not string
                            "content_hash": f"sha256:{uuid.uuid4().hex}",  # SHA-256 hash format
                            "storage_path": f"models/{model_id}/v{version_num}.0.0/model.pkl"
                        }
                        
                        version_response = requests.post(
                            f"{gateway_url}/api/models/{model_id}/versions",
                            json=version_data,
                            headers=headers,
                            timeout=10
                        )
                        
                        if version_response.status_code == 201:
                            print(f"  ✓ Created version {version_num} for model {model_id}")
                        else:
                            error_detail = version_response.text[:100] if version_response.text else "No error details"
                            print(f"  ⚠ Failed to create version {version_num}: {version_response.status_code} - {error_detail}")
                    except Exception as e:
                        print(f"  ⚠ Error creating version {version_num}: {e}")
            
            return model
        else:
            print(f"  ⚠ Failed to create model {model_num}: {response.status_code} - {response.text[:100]}")
            return None
    except Exception as e:
        print(f"  ⚠ Error creating model {model_num}: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Seed database with test models")
    parser.add_argument(
        "--count",
        type=int,
        default=50,
        help="Number of models to create (default: 50)"
    )
    parser.add_argument(
        "--with-versions",
        action="store_true",
        help="Also create versions for each model"
    )
    parser.add_argument(
        "--gateway-url",
        type=str,
        default=GATEWAY_URL,
        help=f"API Gateway URL (default: {GATEWAY_URL})"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of models to create in parallel (default: 10)"
    )
    
    args = parser.parse_args()
    
    # Use the provided gateway URL or default
    gateway_url = args.gateway_url
    
    print(f"🌱 Seeding database with {args.count} models...")
    print(f"   Gateway URL: {gateway_url}")
    print(f"   With versions: {args.with_versions}")
    print()
    
    # Generate token
    print("🔑 Generating authentication token...")
    token = generate_token(gateway_url)
    if token:
        print("   ✓ Token generated")
    else:
        print("   ⚠ No token - requests may fail if auth required")
    print()
    
    # Create models
    created = 0
    failed = 0
    start_time = time.time()
    
    print(f"📦 Creating {args.count} models...")
    for i in range(1, args.count + 1):
        model = create_model(gateway_url, token, i, args.with_versions)
        if model:
            created += 1
            if i % 10 == 0:
                print(f"   Progress: {i}/{args.count} ({created} created, {failed} failed)")
        else:
            failed += 1
        
        # Small delay to avoid overwhelming the system
        if i % args.batch_size == 0:
            time.sleep(0.1)
    
    elapsed = time.time() - start_time
    
    print()
    print("=" * 60)
    print("✅ Seeding Complete!")
    print("=" * 60)
    print(f"   Created: {created} models")
    print(f"   Failed: {failed} models")
    print(f"   Time: {elapsed:.2f}s")
    print(f"   Rate: {created/elapsed:.2f} models/sec")
    print()
    
    if created > 0:
        print("💡 You can now run load tests with existing models:")
        print(f"   python tests/comprehensive_load_test.py --users 10 --duration 60")
        print()
        print("💡 Check models in database:")
        print(f"   curl {gateway_url}/api/models | jq '. | length'")
    
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()

