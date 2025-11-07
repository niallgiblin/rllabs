#!/usr/bin/env python3
"""
API Gateway Upload Client
==========================

Uploads files through the API Gateway (port 8080) with JWT authentication.
This simulates a real frontend client uploading through the gateway.

Usage:
    # First, generate a JWT token
    export JWT_TOKEN=$(python generate_token.py)
    
    # Then upload
    python gateway_upload_client.py upload test_model.pkl --model-id 1
    python gateway_upload_client.py download sha256:abc123...
"""

import argparse
import hashlib
import requests
from pathlib import Path
import sys
import os


def calculate_sha256(filepath: Path) -> str:
    """Calculate SHA-256 hash of a file"""
    print(f"--- Calculating SHA-256 hash of {filepath}...")
    sha256_hash = hashlib.sha256()
    
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(8192), b""):
            sha256_hash.update(byte_block)
    
    hash_value = sha256_hash.hexdigest()
    print(f"--- Hash: sha256:{hash_value}")
    return f"sha256:{hash_value}"


def get_jwt_token() -> str:
    """Get JWT token from environment variable"""
    token = os.getenv("JWT_TOKEN")
    if not token:
        print("--- Error: JWT_TOKEN environment variable not set")
        print("\nPlease generate a token first:")
        print("  export JWT_TOKEN=$(python generate_token.py)")
        sys.exit(1)
    return token


def upload_file(filepath: Path, model_id: int):
    """Upload a file through API Gateway"""
    
    if not filepath.exists():
        print(f"--- File not found: {filepath}")
        return
    
    # Get JWT token
    jwt_token = get_jwt_token()
    
    # Use API Gateway URL
    base_url = "http://localhost:8080"
    
    file_size = filepath.stat().st_size
    print(f"--- Uploading {filepath} ({file_size:,} bytes)")
    print(f"--- Using API Gateway with JWT authentication")
    
    # Step 1: Calculate hash
    file_hash = calculate_sha256(filepath)
    
    # Step 2: Initiate upload through API Gateway
    print("--- Initiating upload session via API Gateway...")
    
    init_response = requests.post(
        f"{base_url}/api/uploads",
        json={
            "filename": filepath.name,
            "file_size": file_size,
            "file_hash": file_hash,
            "chunk_size": 5242880,  # 5MB
            "artifact_type": "model",
            "model_id": model_id
        },
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json"
        }
    )
    
    if init_response.status_code != 201:
        print(f"--- Failed to initiate upload: {init_response.status_code}")
        print(f"--- Response: {init_response.text}")
        return
    
    upload_data = init_response.json()
    upload_id = upload_data["upload_id"]
    presigned_urls = upload_data["presigned_urls"]
    
    print(f"--- Upload session created: {upload_id}")
    print(f"--- Uploading {len(presigned_urls)} chunks...")
    
    # Step 3: Upload chunks DIRECTLY to MinIO (bypass gateway)
    parts = []
    chunk_size = 5242880  # 5MB
    
    with open(filepath, "rb") as f:
        for i, url_data in enumerate(presigned_urls, 1):
            part_number = url_data["part_number"]
            url = url_data["url"]
            
            # Read chunk
            chunk = f.read(chunk_size)
            if not chunk:
                break
            
            # Upload chunk directly to MinIO (not through API Gateway!)
            print(f"--- Uploading chunk {part_number}/{len(presigned_urls)} directly to MinIO...", end=" ")
            chunk_response = requests.put(url, data=chunk)
            
            if chunk_response.status_code not in [200, 201]:
                print(f"--- Failed")
                print(f"--- Response: {chunk_response.status_code}")
                return
            
            # Get ETag
            etag = chunk_response.headers.get("ETag", "").strip('"')
            parts.append({
                "part_number": part_number,
                "etag": etag
            })
            print("✓")
    
    # Step 4: Complete upload through API Gateway
    print("--- Completing upload via API Gateway...")
    complete_response = requests.post(
        f"{base_url}/api/uploads/{upload_id}/complete",
        json={"parts": parts},
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json"
        }
    )
    
    if complete_response.status_code != 200:
        print(f"--- Failed to complete upload: {complete_response.status_code}")
        print(f"--- Response: {complete_response.text}")
        return
    
    result = complete_response.json()
    print(f"--- Upload complete!")
    print(f"--- Artifact ID: {result['artifact_id']}")
    print(f"--- Version: {result['version']}")
    print(f"--- Storage Path: {result['storage_path']}")
    print(f"--- Registered with catalog: {result.get('registered_with_catalog', False)}")
    
    return result


def download_file(artifact_id: str, output_path: Path = None):
    """Download a file through API Gateway"""
    
    # Get JWT token
    jwt_token = get_jwt_token()
    
    base_url = "http://localhost:8080"
    
    print(f"--- Downloading {artifact_id} via API Gateway...")
    
    # Step 1: Get download URL through API Gateway
    download_response = requests.get(
        f"{base_url}/api/downloads/{artifact_id}",
        headers={
            "Authorization": f"Bearer {jwt_token}"
        }
    )
    
    if download_response.status_code != 200:
        print(f"--- Failed to get download URL: {download_response.status_code}")
        print(f"--- Response: {download_response.text}")
        return
    
    download_data = download_response.json()
    download_url = download_data["download_url"]
    file_size = download_data["file_size"]
    filename = download_data.get("filename", artifact_id)
    
    print(f"--- Download URL obtained from API Gateway")
    
    # Step 2: Download file DIRECTLY from MinIO (bypass gateway)
    if output_path is None:
        output_path = Path(filename)
    
    print(f"--- Downloading directly from MinIO to {output_path} ({file_size:,} bytes)...")
    
    file_response = requests.get(download_url, stream=True)
    if file_response.status_code != 200:
        print(f"--- Failed to download file: {file_response.status_code}")
        return
    
    # Stream download
    downloaded_size = 0
    with open(output_path, "wb") as f:
        for chunk in file_response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                downloaded_size += len(chunk)
                progress = (downloaded_size / file_size) * 100
                print(f"\r--- Progress: {progress:.1f}%", end="")
    
    print()
    print(f"--- Download complete: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Upload/Download via API Gateway with JWT authentication"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command")
    
    # Upload
    upload_parser = subparsers.add_parser("upload", help="Upload a file")
    upload_parser.add_argument("filepath", type=Path, help="File to upload")
    upload_parser.add_argument("--model-id", type=int, required=True, help="Model ID")
    
    # Download
    download_parser = subparsers.add_parser("download", help="Download a file")
    download_parser.add_argument("artifact_id", help="Artifact ID (sha256:...)")
    download_parser.add_argument("--output", type=Path, help="Output file path")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        print("\n" + "="*80)
        print("SETUP INSTRUCTIONS:")
        print("="*80)
        print("\n1. Generate a JWT token:")
        print("   export JWT_TOKEN=$(python generate_token.py)")
        print("\n2. Create a model (if you haven't already):")
        print('   curl -X POST http://localhost:8080/api/models \\')
        print('     -H "Content-Type: application/json" \\')
        print('     -H "Authorization: Bearer $JWT_TOKEN" \\')
        print('     -d \'{"name":"test-model","description":"Test"}\'')
        print("\n3. Upload a file:")
        print("   python gateway_upload_client.py upload test.pkl --model-id 1")
        print("\n" + "="*80 + "\n")
        sys.exit(1)
    
    try:
        if args.command == "upload":
            upload_file(args.filepath, args.model_id)
        elif args.command == "download":
            download_file(args.artifact_id, args.output)
    except Exception as e:
        print(f"\n--- Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
