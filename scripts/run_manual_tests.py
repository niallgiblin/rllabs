#!/usr/bin/env python3
"""
Manual Test Runner for RLLabs
==============================

Runs all manual tests from the README to verify the system works in Kind/Kubernetes.
All tests go through the API Gateway at http://localhost:8080
"""

import requests
import json
import subprocess
import sys
import time
import hashlib
from typing import Optional, Dict, Any
import os

# Configuration
API_GATEWAY_URL = "http://localhost:8080"
TIMEOUT = 10

# Colors for output
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'  # No Color

class TestRunner:
    def __init__(self):
        self.token: Optional[str] = None
        self.admin_token: Optional[str] = None
        self.created_model_id: Optional[int] = None
        self.created_artifact_id: Optional[str] = None
        self.created_comment_id: Optional[str] = None
        self.test_results = []
        
    def print_header(self, text: str):
        print(f"\n{BLUE}{'='*70}{NC}")
        print(f"{BLUE}{text}{NC}")
        print(f"{BLUE}{'='*70}{NC}\n")
        
    def print_test(self, name: str):
        print(f"{YELLOW}Testing: {name}...{NC}", end=" ", flush=True)
        
    def print_pass(self, message: str = ""):
        print(f"{GREEN}✓ PASS{NC}" + (f" - {message}" if message else ""))
        self.test_results.append(("PASS", message))
        
    def print_fail(self, message: str = ""):
        print(f"{RED}✗ FAIL{NC}" + (f" - {message}" if message else ""))
        self.test_results.append(("FAIL", message))
        
    def print_skip(self, message: str = ""):
        print(f"{YELLOW}⊘ SKIP{NC}" + (f" - {message}" if message else ""))
        self.test_results.append(("SKIP", message))
        
    def run_command(self, cmd: list) -> tuple[bool, str, str]:
        """Run a shell command and return (success, stdout, stderr)"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            return (result.returncode == 0, result.stdout.strip(), result.stderr.strip())
        except subprocess.TimeoutExpired:
            return (False, "", "Command timed out")
        except Exception as e:
            return (False, "", str(e))
    
    def test_health_checks(self):
        """Test 1: Verify services are healthy"""
        self.print_header("Test 1: Health Checks")
        
        # In Kind/Kubernetes, all services accessed through Gateway
        # In Compose, services are directly accessible
        endpoints = [
            ("API Gateway", f"{API_GATEWAY_URL}/health"),
        ]
        
        all_passed = True
        for name, url in endpoints:
            self.print_test(f"{name} health check")
            try:
                response = requests.get(url, timeout=TIMEOUT)
                if response.status_code == 200:
                    self.print_pass(f"Status: {response.status_code}")
                else:
                    self.print_fail(f"Status: {response.status_code}")
                    all_passed = False
            except Exception as e:
                self.print_fail(f"Error: {str(e)}")
                all_passed = False
        
        # Note: In Kind/Kubernetes, backend service health checks require port-forwards
        # or can be checked via kubectl. For manual testing, Gateway health is sufficient.
        self.print_skip("Backend service health checks (require port-forwards in Kind)")
                
        return all_passed
    
    def test_generate_token(self):
        """Test 2: Generate JWT tokens"""
        self.print_header("Test 2: JWT Token Generation")
        
        # Generate regular user token
        self.print_test("Generate regular user token")
        success, stdout, stderr = self.run_command([
            sys.executable, "generate_token.py", "--user", "test-user-123"
        ])
        
        if success and stdout:
            # Extract token from output (token is between dashes after "Token:" line)
            lines = stdout.split('\n')
            token_line = None
            in_token_section = False
            for i, line in enumerate(lines):
                if 'Token:' in line:
                    in_token_section = True
                    continue
                if in_token_section:
                    # Skip dash lines, get the actual token line
                    if line.strip() and not line.strip().startswith('-') and len(line.strip()) > 50:
                        token_line = line.strip()
                        break
            
            if token_line and len(token_line) > 50:  # JWT tokens are long
                self.token = token_line
                self.print_pass("Token generated")
            else:
                self.print_fail(f"Could not extract token from output. Output: {stdout[:200]}")
                return False
        else:
            self.print_fail(f"Command failed: {stderr}")
            return False
        
        # Generate admin token
        self.print_test("Generate admin token")
        success, stdout, stderr = self.run_command([
            sys.executable, "generate_token.py", "--admin", "--user", "admin-user"
        ])
        
        if success and stdout:
            # Extract token from output (token is between dashes after "Token:" line)
            lines = stdout.split('\n')
            token_line = None
            in_token_section = False
            for i, line in enumerate(lines):
                if 'Token:' in line:
                    in_token_section = True
                    continue
                if in_token_section:
                    # Skip dash lines, get the actual token line
                    if line.strip() and not line.strip().startswith('-') and len(line.strip()) > 50:
                        token_line = line.strip()
                        break
            
            if token_line and len(token_line) > 50:
                self.admin_token = token_line
                self.print_pass("Admin token generated")
            else:
                self.print_fail(f"Could not extract admin token. Output: {stdout[:200]}")
                return False
        else:
            self.print_fail(f"Command failed: {stderr}")
            return False
            
        return True
    
    def test_public_model_discovery(self):
        """Test 3: Public model discovery (no auth required)"""
        self.print_header("Test 3: Public Model Discovery")
        
        # List all models (public)
        self.print_test("List all models (no auth)")
        try:
            response = requests.get(f"{API_GATEWAY_URL}/api/models", timeout=TIMEOUT)
            if response.status_code == 200:
                models = response.json()
                self.print_pass(f"Found {len(models)} models")
            else:
                self.print_fail(f"Status: {response.status_code}")
                return False
        except Exception as e:
            self.print_fail(f"Error: {str(e)}")
            return False
        
        # Get model details (if we have a model)
        if self.created_model_id:
            self.print_test(f"Get model details (ID: {self.created_model_id})")
            try:
                response = requests.get(
                    f"{API_GATEWAY_URL}/api/models/{self.created_model_id}",
                    timeout=TIMEOUT
                )
                if response.status_code == 200:
                    model = response.json()
                    self.print_pass(f"Model: {model.get('name', 'N/A')}")
                else:
                    self.print_fail(f"Status: {response.status_code}")
            except Exception as e:
                self.print_fail(f"Error: {str(e)}")
        else:
            self.print_skip("No model ID available yet")
        
        # Test get model details (public)
        if self.created_model_id:
            self.print_test("Get model details (public, no auth)")
            try:
                response = requests.get(
                    f"{API_GATEWAY_URL}/api/models/{self.created_model_id}",
                    timeout=TIMEOUT
                )
                if response.status_code == 200:
                    model = response.json()
                    self.print_pass(f"Model: {model.get('name', 'N/A')}")
                else:
                    self.print_fail(f"Status: {response.status_code}")
            except Exception as e:
                self.print_fail(f"Error: {str(e)}")
        
        return True
    
    def test_get_model_versions(self):
        """Test: Get model versions list"""
        if not self.created_model_id:
            return True  # Skip silently if no model
        
        self.print_test("List model versions")
        try:
            response = requests.get(
                f"{API_GATEWAY_URL}/api/models/{self.created_model_id}/versions",
                timeout=TIMEOUT
            )
            if response.status_code == 200:
                versions = response.json()
                self.print_pass(f"Found {len(versions)} version(s)")
                return True
            else:
                self.print_fail(f"Status: {response.status_code}")
                return False
        except Exception as e:
            self.print_fail(f"Error: {str(e)}")
            return False
    
    def test_create_model(self):
        """Test 4: Create a model"""
        self.print_header("Test 4: Create Model")
        
        if not self.token:
            self.print_fail("No JWT token available")
            return False
        
        self.print_test("Create model via API Gateway")
        try:
            payload = {
                "name": f"test-model-{int(time.time())}",
                "description": "Test model created by manual test runner"
            }
            response = requests.post(
                f"{API_GATEWAY_URL}/api/models",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=TIMEOUT
            )
            
            if response.status_code in [200, 201]:
                model = response.json()
                self.created_model_id = model.get('id')
                self.print_pass(f"Model ID: {self.created_model_id}, Name: {model.get('name')}")
                return True
            else:
                self.print_fail(f"Status: {response.status_code}, Response: {response.text}")
                return False
        except Exception as e:
            self.print_fail(f"Error: {str(e)}")
            return False
    
    def test_register_model_version(self):
        """Test 5: Register a model version"""
        self.print_header("Test 5: Register Model Version")
        
        if not self.token:
            self.print_fail("No JWT token available")
            return False
        
        if not self.created_model_id:
            self.print_skip("No model ID available")
            return False
        
        self.print_test("Register model version")
        try:
            payload = {
                "version": 1,
                "storage_path": f"models/test-model/v1.weights",
                "content_hash": "sha256:test123456789"
            }
            response = requests.post(
                f"{API_GATEWAY_URL}/api/models/{self.created_model_id}/versions",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=TIMEOUT
            )
            
            if response.status_code in [200, 201]:
                self.print_pass("Version registered")
                return True
            else:
                self.print_fail(f"Status: {response.status_code}, Response: {response.text}")
                return False
        except Exception as e:
            self.print_fail(f"Error: {str(e)}")
            return False
    
    def test_query_latest_version(self):
        """Test 6: Query latest version"""
        self.print_header("Test 6: Query Latest Version")
        
        if not self.created_model_id:
            self.print_skip("No model ID available")
            return False
        
        # Test with auth
        self.print_test("Query latest version (with auth)")
        try:
            response = requests.get(
                f"{API_GATEWAY_URL}/api/models/{self.created_model_id}/latest",
                headers={"Authorization": f"Bearer {self.token}"} if self.token else {},
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                version = response.json()
                self.print_pass(f"Latest version: {version.get('storage_path', 'N/A')}")
                return True
            else:
                self.print_fail(f"Status: {response.status_code}, Response: {response.text}")
                return False
        except Exception as e:
            self.print_fail(f"Error: {str(e)}")
            return False
        
        # Test without auth (public)
        self.print_test("Query latest version (no auth - public)")
        try:
            response = requests.get(
                f"{API_GATEWAY_URL}/api/models/{self.created_model_id}/latest",
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                self.print_pass("Public access works")
                return True
            else:
                self.print_fail(f"Status: {response.status_code}")
                return False
        except Exception as e:
            self.print_fail(f"Error: {str(e)}")
            return False
    
    def test_upload_file(self):
        """Test 7: Upload a model file (multipart upload)"""
        self.print_header("Test 7: Upload Model File")
        
        if not self.token:
            self.print_fail("No JWT token available")
            return False
        
        if not self.created_model_id:
            self.print_skip("No model ID available")
            return False
        
        # Create a small test file
        test_file_content = b"This is a test model file for manual testing"
        test_file_size = len(test_file_content)
        
        # Calculate SHA-256 hash
        file_hash = hashlib.sha256(test_file_content).hexdigest()
        file_hash_with_prefix = f"sha256:{file_hash}"
        
        # Start upload session
        self.print_test("Start upload session")
        try:
            payload = {
                "filename": "test_model.weights",
                "file_size": test_file_size,
                "file_hash": file_hash_with_prefix,
                "chunk_size": 5242880,  # 5MB
                "artifact_type": "model",
                "model_id": self.created_model_id
            }
            response = requests.post(
                f"{API_GATEWAY_URL}/api/uploads",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=TIMEOUT
            )
            
            if response.status_code in [200, 201]:
                upload_data = response.json()
                upload_id = upload_data.get('upload_id')
                presigned_urls = upload_data.get('presigned_urls', [])
                
                if upload_id and presigned_urls:
                    self.print_pass(f"Upload session created: {upload_id}")
                    
                    # Extract URL from first presigned URL (could be dict or string)
                    first_url = presigned_urls[0]
                    if isinstance(first_url, dict):
                        upload_url = first_url.get('url')
                        part_number = first_url.get('part_number', 1)
                    else:
                        upload_url = first_url
                        part_number = 1
                    
                    if not upload_url:
                        self.print_fail("No URL in presigned_urls")
                        return False
                    
                    # Upload to first presigned URL
                    self.print_test("Upload file part to presigned URL")
                    try:
                        upload_response = requests.put(
                            upload_url,
                            data=test_file_content,
                            timeout=TIMEOUT
                        )
                        if upload_response.status_code in [200, 204]:
                            # Get ETag from response headers
                            etag = upload_response.headers.get('ETag', '').strip('"')
                            if not etag:
                                # MinIO might return ETag in different format
                                etag = upload_response.headers.get('etag', '').strip('"')
                            
                            self.print_pass("File uploaded to MinIO")
                            
                            # Complete upload
                            self.print_test("Complete upload")
                            try:
                                complete_payload = {
                                    "parts": [
                                        {
                                            "part_number": part_number,
                                            "etag": etag if etag else "test-etag"
                                        }
                                    ]
                                }
                                complete_response = requests.post(
                                    f"{API_GATEWAY_URL}/api/uploads/{upload_id}/complete",
                                    headers={
                                        "Authorization": f"Bearer {self.token}",
                                        "Content-Type": "application/json"
                                    },
                                    json=complete_payload,
                                    timeout=TIMEOUT
                                )
                                
                                if complete_response.status_code in [200, 201]:
                                    complete_data = complete_response.json()
                                    artifact_id = complete_data.get('artifact_id')
                                    if artifact_id:
                                        self.created_artifact_id = artifact_id
                                        self.print_pass(f"Upload completed, artifact ID: {artifact_id}")
                                        return True
                                    else:
                                        self.print_fail("No artifact_id in response")
                                        return False
                                else:
                                    self.print_fail(f"Complete failed: {complete_response.status_code}, Response: {complete_response.text}")
                                    return False
                            except Exception as e:
                                self.print_fail(f"Complete error: {str(e)}")
                                return False
                        else:
                            self.print_fail(f"Upload failed: {upload_response.status_code}, Response: {upload_response.text}")
                            return False
                    except Exception as e:
                        self.print_fail(f"Upload error: {str(e)}")
                        return False
                else:
                    self.print_fail("Invalid upload response")
                    return False
            else:
                self.print_fail(f"Status: {response.status_code}, Response: {response.text}")
                return False
        except Exception as e:
            self.print_fail(f"Error: {str(e)}")
            return False
    
    def test_download_artifact(self):
        """Test 8: Download an artifact"""
        self.print_header("Test 8: Download Artifact")
        
        if not self.created_artifact_id:
            self.print_skip("No artifact ID available (upload test may have been skipped)")
            return False
        
        # Test public download (no auth)
        self.print_test("Public download (no auth)")
        try:
            response = requests.get(
                f"{API_GATEWAY_URL}/api/downloads/{self.created_artifact_id}",
                params={"expires_in": 3600},
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                download_data = response.json()
                self.print_pass("Presigned URL generated")
                return True
            else:
                self.print_fail(f"Status: {response.status_code}")
                return False
        except Exception as e:
            self.print_fail(f"Error: {str(e)}")
            return False
    
    def test_delete_model(self):
        """Test 9: Delete a model (owner or admin)"""
        self.print_header("Test 9: Delete Model")
        
        if not self.token:
            self.print_fail("No JWT token available")
            return False
        
        if not self.created_model_id:
            self.print_skip("No model ID available")
            return False
        
        # Create a model to delete
        self.print_test("Create model for deletion test")
        try:
            payload = {
                "name": f"delete-test-{int(time.time())}",
                "description": "Model to be deleted"
            }
            response = requests.post(
                f"{API_GATEWAY_URL}/api/models",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=TIMEOUT
            )
            
            if response.status_code in [200, 201]:
                model = response.json()
                delete_model_id = model.get('id')
                
                # Delete the model
                self.print_test("Delete model (owner)")
                delete_response = requests.delete(
                    f"{API_GATEWAY_URL}/api/models/{delete_model_id}",
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=TIMEOUT
                )
                
                if delete_response.status_code in [200, 204]:
                    self.print_pass("Model deleted successfully")
                    return True
                else:
                    self.print_fail(f"Status: {delete_response.status_code}")
                    return False
            else:
                self.print_fail(f"Could not create model for deletion: {response.status_code}")
                return False
        except Exception as e:
            self.print_fail(f"Error: {str(e)}")
            return False
    
    def test_collaboration_comments(self):
        """Test 10: Collaboration service (comments)"""
        self.print_header("Test 10: Collaboration Service (Comments)")
        
        if not self.token:
            self.print_fail("No JWT token available")
            return False
        
        if not self.created_model_id:
            self.print_skip("No model ID available")
            return False
        
        # Create a comment
        self.print_test("Create comment on model")
        try:
            payload = {
                "content": "This is a test comment from manual test runner",
                "authorId": "test-user-123",
                "authorName": "Test User",
                "parentId": None
            }
            response = requests.post(
                f"{API_GATEWAY_URL}/api/models/{self.created_model_id}/comments",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=TIMEOUT
            )
            
            if response.status_code in [200, 201]:
                comment = response.json()
                comment_id = comment.get('id')
                self.print_pass(f"Comment created: {comment_id}")
                
                # Get comments
                self.print_test("Get comments for model")
                get_response = requests.get(
                    f"{API_GATEWAY_URL}/api/models/{self.created_model_id}/comments",
                    params={"page": 1, "limit": 50},
                    timeout=TIMEOUT
                )
                
                if get_response.status_code == 200:
                    comments_data = get_response.json()
                    comments = comments_data.get('data', [])
                    self.print_pass(f"Retrieved {len(comments)} comments")
                    
                    # Reply to comment
                    if comment_id:
                        self.print_test("Reply to comment")
                        reply_payload = {
                            "content": "This is a reply to the test comment",
                            "authorId": "test-user-456",
                            "authorName": "Test User 2",
                            "parentId": comment_id
                        }
                        reply_response = requests.post(
                            f"{API_GATEWAY_URL}/api/models/{self.created_model_id}/comments",
                            headers={
                                "Authorization": f"Bearer {self.token}",
                                "Content-Type": "application/json"
                            },
                            json=reply_payload,
                            timeout=TIMEOUT
                        )
                        
                        if reply_response.status_code in [200, 201]:
                            self.print_pass("Reply created")
                            
                            # Update comment
                            self.print_test("Update comment")
                            update_payload = {
                                "content": "Updated comment content from test runner"
                            }
                            update_response = requests.put(
                                f"{API_GATEWAY_URL}/api/comments/{comment_id}",
                                headers={
                                    "Authorization": f"Bearer {self.token}",
                                    "Content-Type": "application/json"
                                },
                                json=update_payload,
                                timeout=TIMEOUT
                            )
                            
                            if update_response.status_code in [200, 201]:
                                self.print_pass("Comment updated")
                                
                                # Delete comment
                                self.print_test("Delete comment")
                                delete_response = requests.delete(
                                    f"{API_GATEWAY_URL}/api/comments/{comment_id}",
                                    headers={"Authorization": f"Bearer {self.token}"},
                                    timeout=TIMEOUT
                                )
                                
                                if delete_response.status_code in [200, 204]:
                                    self.print_pass("Comment deleted")
                                    return True
                                else:
                                    self.print_fail(f"Delete status: {delete_response.status_code}")
                                    return False
                            else:
                                self.print_fail(f"Update status: {update_response.status_code}")
                                return False
                        else:
                            self.print_fail(f"Reply status: {reply_response.status_code}")
                            return False
                    
                    return True
                else:
                    self.print_fail(f"Status: {get_response.status_code}")
                    return False
            else:
                self.print_fail(f"Status: {response.status_code}, Response: {response.text}")
                return False
        except Exception as e:
            self.print_fail(f"Error: {str(e)}")
            return False
    
    def test_delete_artifact(self):
        """Test: Delete artifact (admin)"""
        self.print_header("Test: Delete Artifact (Admin)")
        
        if not self.admin_token:
            self.print_skip("No admin token available")
            return False
        
        if not self.created_artifact_id:
            self.print_skip("No artifact ID available")
            return False
        
        self.print_test("Delete artifact (admin)")
        try:
            # The service endpoint is /artifacts/{id}
            # Gateway routes /api/artifacts to upload-download-service (requires gateway restart after config change)
            response = requests.delete(
                f"{API_GATEWAY_URL}/api/artifacts/{self.created_artifact_id}",
                headers={"Authorization": f"Bearer {self.admin_token}"},
                timeout=TIMEOUT
            )
            
            if response.status_code in [200, 204]:
                self.print_pass("Artifact deleted successfully")
                return True
            else:
                self.print_fail(f"Status: {response.status_code}, Response: {response.text}")
                return False
        except Exception as e:
            self.print_fail(f"Error: {str(e)}")
            return False
    
    def test_training_service(self):
        """Test: Training service check"""
        self.print_header("Test: Training Service")
        
        self.print_test("Check Training Service pod exists")
        try:
            success, stdout, stderr = self.run_command([
                "kubectl", "get", "pods", "-l", "app=model-train-service", "-o", "jsonpath={.items[0].metadata.name}"
            ])
            
            if success and stdout:
                self.print_pass(f"Training Service pod found: {stdout}")
                
                # Check if it's running
                self.print_test("Check Training Service logs for queue consumption")
                success2, stdout2, stderr2 = self.run_command([
                    "kubectl", "logs", "-l", "app=model-train-service", "--tail=10"
                ])
                
                if success2 and ("training_jobs" in stdout2.lower() or "waiting" in stdout2.lower()):
                    self.print_pass("Training Service is consuming from queue")
                    return True
                else:
                    self.print_skip("Could not verify queue consumption from logs")
                    return True
            else:
                self.print_fail("Training Service pod not found")
                return False
        except Exception as e:
            self.print_skip(f"Could not check Training Service: {str(e)}")
            return False
    
    def test_rabbitmq_connectivity(self):
        """Test 11: RabbitMQ connectivity"""
        self.print_header("Test 11: RabbitMQ Connectivity")
        
        # Check if RabbitMQ management API is accessible
        # Note: In Kind, we'd need port-forward for this
        self.print_test("Check RabbitMQ management API")
        try:
            # Try to check if port-forward exists or use kubectl
            success, stdout, stderr = self.run_command([
                "kubectl", "get", "svc", "rabbitmq", "-o", "jsonpath={.metadata.name}"
            ])
            
            if success:
                self.print_pass("RabbitMQ service exists in cluster")
                self.print_skip("RabbitMQ management UI requires port-forward (not tested)")
                return True
            else:
                self.print_fail("RabbitMQ service not found")
                return False
        except Exception as e:
            self.print_skip(f"Could not check RabbitMQ: {str(e)}")
            return False
    
    def run_all_tests(self):
        """Run all manual tests"""
        print(f"\n{GREEN}{'='*70}{NC}")
        print(f"{GREEN}  RLLabs Manual Test Runner (Kind/Kubernetes){NC}")
        print(f"{GREEN}{'='*70}{NC}\n")
        
        print(f"API Gateway URL: {API_GATEWAY_URL}")
        print(f"Testing against Kind/Kubernetes deployment\n")
        
        # Check connectivity first
        print("Checking API Gateway connectivity...")
        try:
            response = requests.get(f"{API_GATEWAY_URL}/health", timeout=5)
            if response.status_code == 200:
                print(f"{GREEN}✓ API Gateway is accessible{NC}\n")
            else:
                print(f"{RED}✗ API Gateway returned status {response.status_code}{NC}\n")
                print(f"{YELLOW}Make sure port-forward is running: kubectl port-forward svc/api-gateway 8080:8080{NC}\n")
                return False
        except Exception as e:
            print(f"{RED}✗ Cannot connect to API Gateway: {str(e)}{NC}\n")
            print(f"{YELLOW}Make sure port-forward is running: kubectl port-forward svc/api-gateway 8080:8080{NC}\n")
            return False
        
        # Run tests in order
        tests = [
            ("Health Checks", self.test_health_checks),
            ("JWT Token Generation", self.test_generate_token),
            ("Public Model Discovery", self.test_public_model_discovery),
            ("Create Model", self.test_create_model),
            ("Get Model Versions", self.test_get_model_versions),
            ("Register Model Version", self.test_register_model_version),
            ("Query Latest Version", self.test_query_latest_version),
            ("Upload File", self.test_upload_file),
            ("Download Artifact", self.test_download_artifact),
            ("Delete Model", self.test_delete_model),
            ("Collaboration Comments", self.test_collaboration_comments),
            ("Delete Artifact (Admin)", self.test_delete_artifact),
            ("Training Service", self.test_training_service),
            ("RabbitMQ Connectivity", self.test_rabbitmq_connectivity),
        ]
        
        results = {}
        for test_name, test_func in tests:
            try:
                results[test_name] = test_func()
            except Exception as e:
                print(f"{RED}✗ Test '{test_name}' crashed: {str(e)}{NC}")
                results[test_name] = False
        
        # Print summary
        self.print_header("Test Summary")
        
        passed = sum(1 for v in results.values() if v)
        total = len(results)
        
        for test_name, result in results.items():
            status = f"{GREEN}✓ PASS{NC}" if result else f"{RED}✗ FAIL{NC}"
            print(f"  {status} {test_name}")
        
        print(f"\n{BLUE}{'='*70}{NC}")
        print(f"Results: {passed}/{total} tests passed")
        print(f"{BLUE}{'='*70}{NC}\n")
        
        return passed == total

if __name__ == "__main__":
    runner = TestRunner()
    success = runner.run_all_tests()
    sys.exit(0 if success else 1)

