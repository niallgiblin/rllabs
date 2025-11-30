"""
Simple Upload/Download Test Script
===================================

Tests the upload/download service directly without API Gateway authentication.
Use this for local testing and development.

Usage:
    python simple_test.py upload test_model.pkl
    python simple_test.py download sha256:abc123...
"""

import argparse
import hashlib
import requests
from pathlib import Path
import sys


def calculate_sha256(filepath: Path) -> str:
    """Calculate SHA-256 hash of a file"""
    print(f"--- Calculating SHA-256 hash of {filepath}...")
    sha256_hash = hashlib.sha256()
    
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(8192), b""):
            sha256_hash.update(byte_block)
    
    hash_value = sha256_hash.hexdigest()
    print(f"--- Hash: sha256: {hash_value}")
    return f"sha256:{hash_value}"


def upload_file(filepath: Path, model_id: int, user_id: str = "test_user"):
    """Upload a file directly to the service"""
    
    if not filepath.exists():
        print(f"--- File not found: {filepath}")
        return
    
    # Connect directly to service (not through API Gateway)
    base_url = "http://localhost:8002"
    
    file_size = filepath.stat().st_size
    print(f"--- Uploading {filepath} ({file_size:,} bytes)")
    
    # Step 1: Calculate hash
    file_hash = calculate_sha256(filepath)
    
    # Step 2: Initiate upload
    print("--- Initiating upload session...")
    
    init_response = requests.post(
        f"{base_url}/uploads",
        json={
            "filename": filepath.name,
            "file_size": file_size,
            "file_hash": file_hash,
            "chunk_size": 5242880,  # 5MB
            "artifact_type": "model",
            "model_id": model_id
        },
        headers={"X-User-Id": user_id}  # Direct header, no JWT needed
    )
    
    if init_response.status_code != 201:
        print(f"--- Failed to initiate upload: {init_response.status_code} ---")
        print(f"---   Response: {init_response.text}")
        return
    
    upload_data = init_response.json()
    upload_id = upload_data["upload_id"]
    presigned_urls = upload_data["presigned_urls"]
    
    print(f"--- Upload session created: {upload_id}")
    print(f"--- Uploading {len(presigned_urls)} chunks...")
    
    # Step 3: Upload chunks
    parts = []
    chunk_size = 5242880  # 5MB
    
    with open(filepath, "rb") as f:
        for i, url_data in enumerate(presigned_urls, 1):
            part_number = url_data["part_number"]
            url = url_data["url"]
            
            # Replace Docker hostname with localhost for host machine access
            # Presigned URLs use 'minio:9000' internally, but host machine needs 'localhost:9000'
            url = url.replace("minio:9000", "localhost:9000")
            
            # Read chunk
            chunk = f.read(chunk_size)
            if not chunk:
                break
            
            # Upload chunk directly to MinIO
            print(f"--- Uploading chunk {part_number}/{len(presigned_urls)}...", end=" ")
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
    
    # Step 4: Complete upload
    print("--- Completing upload...")
    complete_response = requests.post(
        f"{base_url}/uploads/{upload_id}/complete",
        json={"parts": parts},
        headers={"X-User-Id": user_id}
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


def download_file(artifact_id: str, output_path: Path = None, user_id: str = "test_user"):
    """Download a file directly from the service"""
    
    base_url = "http://localhost:8002"
    
    print(f"--- Downloading {artifact_id}...")
    
    # Step 1: Get download URL
    download_response = requests.get(
        f"{base_url}/downloads/{artifact_id}",
        headers={"X-User-Id": user_id}
    )
    
    if download_response.status_code != 200:
        print(f"--- Failed to get download URL: {download_response.status_code} ---")
        print(f"--- Response: {download_response.text}")
        return
    
    download_data = download_response.json()
    download_url = download_data["download_url"]
    file_size = download_data["file_size"]
    filename = download_data.get("filename", artifact_id)
    
    # Replace Docker hostname with localhost for host machine access
    download_url = download_url.replace("minio:9000", "localhost:9000")
    
    print(f"--- Download URL obtained")
    
    # Step 2: Download file
    if output_path is None:
        output_path = Path(filename)
    
    print(f"--- Downloading to {output_path} ({file_size:,} bytes)...")
    
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
                print(f"\r --- Progress: {progress:.1f}%", end="")
    
    print()
    print(f"--- Download complete: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Simple Upload/Download Test")
    subparsers = parser.add_subparsers(dest="command", help="Command")
    
    # Upload
    upload_parser = subparsers.add_parser("upload", help="Upload a file")
    upload_parser.add_argument("filepath", type=Path, help="File to upload")
    upload_parser.add_argument("--model-id", type=int, default=1, help="Model ID (default: 1)")
    upload_parser.add_argument("--user-id", default="test_user", help="User ID (default: test_user)")
    
    # Download
    download_parser = subparsers.add_parser("download", help="Download a file")
    download_parser.add_argument("artifact_id", help="Artifact ID (sha256:...)")
    download_parser.add_argument("--output", type=Path, help="Output file path")
    download_parser.add_argument("--user-id", default="test_user", help="User ID (default: test_user)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    try:
        if args.command == "upload":
            upload_file(args.filepath, args.model_id, args.user_id)
        elif args.command == "download":
            download_file(args.artifact_id, args.output, args.user_id)
    except Exception as e:
        print(f"\n Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
