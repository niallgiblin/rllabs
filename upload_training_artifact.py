#!/usr/bin/env python3
"""
Quick script to upload training artifacts (config, dataset) and get artifact IDs.

Usage:
    python upload_training_artifact.py training_config.json
    python upload_training_artifact.py dataset.json --type dataset
"""

import argparse
import hashlib
import requests
from pathlib import Path
import sys


def calculate_sha256(filepath: Path) -> str:
    """Calculate SHA-256 hash of a file"""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(8192), b""):
            sha256_hash.update(byte_block)
    return f"sha256:{sha256_hash.hexdigest()}"


def upload_artifact(filepath: Path, model_id: int, artifact_type: str = "config", user_id: str = "test_user"):
    """Upload a training artifact (config or dataset) and return artifact ID"""
    
    if not filepath.exists():
        print(f"❌ File not found: {filepath}")
        return None
    
    base_url = "http://localhost:8002"
    file_size = filepath.stat().st_size
    print(f"📤 Uploading {filepath.name} ({file_size:,} bytes) as {artifact_type}...")
    
    # Step 1: Calculate hash
    file_hash = calculate_sha256(filepath)
    print(f"   Hash: {file_hash}")
    
    # Step 2: Initiate upload (model_id is required even for config/dataset)
    print("   Initiating upload session...")
    init_response = requests.post(
        f"{base_url}/uploads",
        json={
            "filename": filepath.name,
            "file_size": file_size,
            "file_hash": file_hash,
            "chunk_size": 5242880,  # 5MB
            "artifact_type": artifact_type,
            "model_id": model_id,  # Required even for config/dataset
        },
        headers={"X-User-Id": user_id}
    )
    
    if init_response.status_code != 201:
        print(f"❌ Failed to initiate upload: {init_response.status_code}")
        print(f"   Response: {init_response.text}")
        return None
    
    upload_data = init_response.json()
    upload_id = upload_data["upload_id"]
    presigned_urls = upload_data["presigned_urls"]
    
    print(f"   Upload session: {upload_id}")
    print(f"   Uploading {len(presigned_urls)} chunk(s)...")
    
    # Step 3: Upload chunks
    parts = []
    chunk_size = 5242880  # 5MB
    
    with open(filepath, "rb") as f:
        for url_data in presigned_urls:
            part_number = url_data["part_number"]
            url = url_data["url"]
            
            # Replace Docker hostname with localhost
            url = url.replace("minio:9000", "localhost:9000")
            
            chunk = f.read(chunk_size)
            if not chunk:
                break
            
            chunk_response = requests.put(url, data=chunk)
            if chunk_response.status_code not in [200, 201]:
                print(f"❌ Failed to upload chunk {part_number}")
                return None
            
            etag = chunk_response.headers.get("ETag", "").strip('"')
            parts.append({"part_number": part_number, "etag": etag})
            print(f"   ✓ Chunk {part_number} uploaded")
    
    # Step 4: Complete upload
    print("   Completing upload...")
    complete_response = requests.post(
        f"{base_url}/uploads/{upload_id}/complete",
        json={"parts": parts},
        headers={"X-User-Id": user_id}
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
        description="Upload training artifacts (config, dataset) and get artifact IDs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Upload training config (requires model_id - create a model first!)
  python upload_training_artifact.py training_config.json --model-id 1
  
  # Upload dataset
  python upload_training_artifact.py dataset.json --model-id 1 --type dataset
  
  # Upload model weights
  python upload_training_artifact.py model.pth --model-id 1 --type model
  
Note: You need to create a model first! You can do this via:
  - The frontend UI (http://localhost:5173)
  - Or via API: curl -X POST http://localhost:8080/api/models -H "Content-Type: application/json" -d '{"name": "My Model", "description": "Test"}'
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
        "--user-id",
        default="test_user",
        help="User ID (default: test_user)"
    )
    
    args = parser.parse_args()
    
    try:
        artifact_id = upload_artifact(args.filepath, args.model_id, args.type, args.user_id)
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
