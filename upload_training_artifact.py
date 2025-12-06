#!/usr/bin/env python3
"""
Upload training artifacts through the API Gateway with JWT authentication.

This script follows the production security architecture by routing all requests
through the API Gateway, which handles authentication, rate limiting, and routing.

Usage:
    python upload_training_artifact.py training_config.json --model-id 1
    python upload_training_artifact.py dataset.json --model-id 1 --type dataset
    python upload_training_artifact.py model.pth --model-id 1 --type model
"""

import argparse
import hashlib
import requests
from pathlib import Path
import sys
import os
import subprocess
import json


def calculate_sha256(filepath: Path) -> str:
    """Calculate SHA-256 hash of a file"""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(8192), b""):
            sha256_hash.update(byte_block)
    return f"sha256:{sha256_hash.hexdigest()}"


def get_jwt_token(gateway_url: str = "http://localhost:8080", username: str = "test-user", password: str = None) -> str:
    """
    Get JWT token for authentication.
    
    Tries in order:
    1. JWT_TOKEN environment variable
    2. Login via /api/auth/login (if password provided)
    3. Generate token using generate_token.py script
    """
    # Try environment variable first
    token = os.getenv("JWT_TOKEN")
    if token:
        return token
    
    # Try login if password provided
    if password:
        try:
            response = requests.post(
                f"{gateway_url}/api/auth/login",
                json={"username": username, "password": password},
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("token")
        except Exception:
            pass  # Fall through to token generation
    
    # Fallback: Generate token using generate_token.py
    try:
        script_path = Path(__file__).parent / "generate_token.py"
        if script_path.exists():
            result = subprocess.run(
                [sys.executable, str(script_path), "--user", username],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                # Extract token from output (it's printed on a line by itself)
                for line in result.stdout.split("\n"):
                    if line and not line.startswith("=") and not line.startswith("-") and "Token:" not in line:
                        # Token is usually a long base64-like string
                        if len(line.strip()) > 50 and "." in line:
                            return line.strip()
        # If script doesn't exist or failed, try importing directly
        sys.path.insert(0, str(Path(__file__).parent))
        from generate_token import generate_token
        return generate_token(user_id=username)
    except Exception as e:
        print(f"⚠️  Warning: Could not generate token automatically: {e}")
        print("   Please set JWT_TOKEN environment variable or use --password")
        print("   Example: export JWT_TOKEN=$(python generate_token.py)")
        return None


def upload_artifact(
    filepath: Path,
    model_id: int,
    artifact_type: str = "config",
    gateway_url: str = "http://localhost:8080",
    jwt_token: str = None,
    minio_endpoint: str = None
):
    """
    Upload a training artifact through the API Gateway.
    
    Args:
        filepath: File to upload
        model_id: Model ID (required)
        artifact_type: Type of artifact (config, dataset, or model)
        gateway_url: API Gateway URL (default: http://localhost:8080)
        jwt_token: JWT token for authentication (auto-generated if not provided)
        minio_endpoint: MinIO endpoint for presigned URLs (auto-detected if not provided)
    """
    if not filepath.exists():
        print(f"❌ File not found: {filepath}")
        return None
    
    # Get JWT token if not provided
    if not jwt_token:
        jwt_token = get_jwt_token(gateway_url)
        if not jwt_token:
            print("❌ Failed to get JWT token. Please set JWT_TOKEN environment variable.")
            print("   Run: export JWT_TOKEN=$(python generate_token.py)")
            return None
    
    file_size = filepath.stat().st_size
    print(f"📤 Uploading {filepath.name} ({file_size:,} bytes) as {artifact_type}...")
    print(f"   Using API Gateway: {gateway_url}")
    
    # Step 1: Calculate hash
    file_hash = calculate_sha256(filepath)
    print(f"   Hash: {file_hash}")
    
    # Step 2: Initiate upload through API Gateway
    print("   Initiating upload session via API Gateway...")
    init_response = requests.post(
        f"{gateway_url}/api/uploads",
        json={
            "filename": filepath.name,
            "file_size": file_size,
            "file_hash": file_hash,
            "chunk_size": 5242880,  # 5MB
            "artifact_type": artifact_type,
            "model_id": model_id,
        },
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json"
        },
        timeout=30
    )
    
    if init_response.status_code == 401:
        print(f"❌ Authentication failed. Please check your JWT token.")
        print(f"   Generate a new token: python generate_token.py")
        return None
    
    if init_response.status_code != 201:
        print(f"❌ Failed to initiate upload: {init_response.status_code}")
        print(f"   Response: {init_response.text}")
        return None
    
    upload_data = init_response.json()
    upload_id = upload_data["upload_id"]
    presigned_urls = upload_data["presigned_urls"]
    
    print(f"   Upload session: {upload_id}")
    print(f"   Uploading {len(presigned_urls)} chunk(s)...")
    
    # Step 3: Upload chunks directly to MinIO (bypass gateway for performance)
    parts = []
    chunk_size = 5242880  # 5MB
    
    # Detect MinIO endpoint from first presigned URL or use provided endpoint
    # In Docker Compose: presigned URLs already have localhost:9000 (MINIO_PUBLIC_ENDPOINT)
    # In Kubernetes: presigned URLs have minio:9000 (internal) and need replacement
    if presigned_urls:
        first_url = presigned_urls[0]["url"]
        # Replace internal MinIO endpoint with accessible endpoint
        if minio_endpoint:
            # User provided explicit endpoint
            minio_endpoint_clean = minio_endpoint.replace("http://", "").replace("https://", "")
            print(f"   Using provided MinIO endpoint: {minio_endpoint_clean}")
        elif "minio:9000" in first_url:
            # Kubernetes: URL contains internal endpoint, needs replacement
            # Try ingress first (minio.localhost), fallback to port-forward (localhost:9000)
            try:
                test_response = requests.get("http://minio.localhost/minio/health/live", timeout=2)
                if test_response.status_code == 200:
                    minio_endpoint_clean = "minio.localhost:9000"
                    print(f"   Detected MinIO ingress: {minio_endpoint_clean}")
                else:
                    minio_endpoint_clean = "localhost:9000"
                    print(f"   Using MinIO port-forward: {minio_endpoint_clean}")
            except:
                minio_endpoint_clean = "localhost:9000"
                print(f"   Using MinIO port-forward (ingress not available): {minio_endpoint_clean}")
        else:
            # Docker Compose or already correct: URL already has accessible endpoint (localhost:9000)
            minio_endpoint_clean = None
            print(f"   MinIO endpoint already accessible (Docker Compose or configured endpoint)")
    
    with open(filepath, "rb") as f:
        for url_data in presigned_urls:
            part_number = url_data["part_number"]
            url = url_data["url"]
            
            # Replace MinIO endpoint if needed (Kubernetes case)
            if minio_endpoint_clean:
                # Replace internal endpoint (minio:9000) with accessible one
                # Handle both http://minio:9000 and minio:9000 patterns
                if "http://minio:9000" in url:
                    url = url.replace("http://minio:9000", f"http://{minio_endpoint_clean}")
                elif "https://minio:9000" in url:
                    url = url.replace("https://minio:9000", f"https://{minio_endpoint_clean}")
                else:
                    url = url.replace("minio:9000", minio_endpoint_clean)
            # If minio_endpoint_clean is None, URL is already correct (Docker Compose case)
            
            chunk = f.read(chunk_size)
            if not chunk:
                break
            
            chunk_response = requests.put(url, data=chunk, timeout=60)
            if chunk_response.status_code not in [200, 201]:
                print(f"❌ Failed to upload chunk {part_number}: {chunk_response.status_code}")
                print(f"   URL: {url[:100]}...")
                return None
            
            etag = chunk_response.headers.get("ETag", "").strip('"')
            parts.append({"part_number": part_number, "etag": etag})
            print(f"   ✓ Chunk {part_number} uploaded")
    
    # Step 4: Complete upload through API Gateway
    print("   Completing upload via API Gateway...")
    complete_response = requests.post(
        f"{gateway_url}/api/uploads/{upload_id}/complete",
        json={"parts": parts},
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json"
        },
        timeout=30
    )
    
    if complete_response.status_code != 200:
        print(f"❌ Failed to complete upload: {complete_response.status_code}")
        print(f"   Response: {complete_response.text}")
        return None
    
    result = complete_response.json()
    artifact_id = result['artifact_id']
    
    print(f"\n✅ Upload complete!")
    print(f"   Artifact ID: {artifact_id}")
    print(f"\n📋 Copy this artifact ID into the training job form:")
    print(f"   {artifact_id}\n")
    
    return artifact_id


def main():
    parser = argparse.ArgumentParser(
        description="Upload training artifacts through API Gateway with JWT authentication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Upload training config (requires model_id - create a model first!)
  python upload_training_artifact.py training_config.json --model-id 1
  
  # Upload dataset
  python upload_training_artifact.py dataset.json --model-id 1 --type dataset
  
  # Upload model weights
  python upload_training_artifact.py model.pth --model-id 1 --type model
  
  # Docker Compose (default - works automatically)
  # API Gateway: http://localhost:8080
  # MinIO: auto-detected from presigned URLs (already has localhost:9000)
  
  # Kubernetes with port-forward (default - works automatically)
  # API Gateway: http://localhost:8080 (via port-forward)
  # MinIO: auto-detected (tries ingress, falls back to localhost:9000 port-forward)
  
  # Kubernetes with ingress
  python upload_training_artifact.py model.pth --model-id 1 --gateway-url http://api.localhost
  python upload_training_artifact.py model.pth --model-id 1 --minio-endpoint minio.localhost:9000

Authentication:
  The script automatically gets a JWT token using one of these methods:
  1. JWT_TOKEN environment variable (recommended)
  2. Login via --username and --password
  3. Auto-generate token using generate_token.py
  
  To generate a token manually:
    export JWT_TOKEN=$(python generate_token.py)
  
  Or login via API:
    curl -X POST http://localhost:8080/api/auth/login \\
      -H "Content-Type: application/json" \\
      -d '{"username":"test-user","password":"password"}'

Note: You need to create a model first! You can do this via:
  - The frontend UI (http://localhost:5173 or via ingress)
  - Or via API: curl -X POST http://localhost:8080/api/models \\
      -H "Content-Type: application/json" \\
      -H "Authorization: Bearer $JWT_TOKEN" \\
      -d '{"name": "My Model", "description": "Test"}'
        """
    )
    
    parser.add_argument("filepath", type=Path, help="File to upload")
    parser.add_argument(
        "--model-id",
        type=int,
        required=True,
        help="Model ID (required - create a model first via the UI or API)"
    )
    parser.add_argument(
        "--type",
        choices=["config", "dataset", "model"],
        default="config",
        help="Artifact type (default: config)"
    )
    parser.add_argument(
        "--gateway-url",
        default="http://localhost:8080",
        help="API Gateway URL (default: http://localhost:8080). For ingress, use http://api.localhost"
    )
    parser.add_argument(
        "--minio-endpoint",
        help="MinIO endpoint for presigned URLs (default: auto-detect). Use 'minio.localhost:9000' for ingress or 'localhost:9000' for port-forward"
    )
    parser.add_argument(
        "--username",
        default="test-user",
        help="Username for login (default: test-user)"
    )
    parser.add_argument(
        "--password",
        help="Password for login (if not provided, will auto-generate token)"
    )
    parser.add_argument(
        "--jwt-token",
        help="JWT token (overrides auto-generation). Can also set JWT_TOKEN env var"
    )
    
    args = parser.parse_args()
    
    # Use provided token or get from env
    jwt_token = args.jwt_token or os.getenv("JWT_TOKEN")
    
    try:
        artifact_id = upload_artifact(
            args.filepath,
            args.model_id,
            args.type,
            args.gateway_url,
            jwt_token,
            args.minio_endpoint
        )
        if artifact_id:
            sys.exit(0)
        else:
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Upload cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
