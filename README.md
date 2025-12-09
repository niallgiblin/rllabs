# RLLabs - Reinforcement Learning Collaboration Platform

A distributed microservices platform for collaborative reinforcement learning model development, training, and management. Built with FastAPI, Vue.js, PostgreSQL, MinIO, Redis, and Kubernetes for production-grade scalability.

## Architecture Overview

RLLabs is designed as a microservices architecture where specialized services handle different aspects of the ML lifecycle:

### Core Services

**Frontend (Vue.js)**

- Modern Vue 3 + Tailwind CSS v4 interface
- Interactive Model Catalog and Collaboration UI
- Runs on http://localhost:5173 (dev)

**API Gateway** (Port 8080)

- Single entry point for all client traffic
- JWT-based authentication and authorization
- **Public Catalog Browsing**: GET `/api/models*` endpoints are public (no auth required)
- **Protected Operations**: POST/PUT/DELETE operations require authentication
- Request routing, load balancing, and rate limiting
- **IP-Based Rate Limiting**: Unauthenticated users rate-limited by IP address
- User context propagation to backend services
- Health monitoring and service discovery
- **Fault Tolerance**: Circuit breakers, retry with exponential backoff
- **Graceful Degradation**: In-memory rate limiting when Redis unavailable
- **Enhanced Logging**: Tracks authenticated vs anonymous requests for monitoring

**Model Catalog Service** (Port 8001)

- Metadata management for ML models and versions
- PostgreSQL-backed model registration and versioning
- Query latest model versions and storage paths
- Created-by tracking for ownership
- **Public Browsing**: GET endpoints are accessible without authentication
- **Admin Delete**: Owners and admins can delete models (DELETE `/models/{model_id}`)
- **Event Consumer**: Listens to `artifact.committed` events to auto-register model versions
- **Event Publisher**: Publishes `ModelCreated` and `ModelDeleted` events to RabbitMQ

**Upload/Download Service** (Port 8002)

- MinIO-backed model file operations
- Multipart uploads with presigned URLs (authentication required)
- **RBAC Authorization**:
  - **Public Downloads**: Unauthenticated users can download any artifact
  - **Authenticated Downloads**: Owner-based and model-level permission checks before presigned URL generation
  - **Uploads**: Authentication required (must be signed in to upload)
- Download with presigned URLs and authorization checks
- **Admin Delete**: Owners and admins can delete artifacts (DELETE `/artifacts/{artifact_id}`)
- Content-addressed storage (SHA-256 based deduplication)
- PostgreSQL metadata for artifacts and upload sessions
- **Event Publisher**: Publishes `ArtifactCommitted` events to RabbitMQ
- **Integration**: Auto-registers artifacts as model versions when `model_id` provided
- **Security**: Authorization at application layer (before MinIO access) for secure + performant file transfers

**Training Service** (Port 8003)

- RabbitMQ consumer for training job processing
- Downloads training artifacts (configs, datasets, model weights) from MinIO
- Runs reinforcement learning training using PyTorch
- Uploads trained model weights back to MinIO as new model versions
- Integrated with Upload/Download Service for artifact management
- Automatic model versioning after training completion

**Collaboration Service** (Port 8004)

- Model comments and threaded discussions
- MongoDB replica set for CP (Consistency + Partition Tolerance) configuration
- Redis caching for comment queries
- Event-driven integration with Model Catalog (listens to ModelCreated/ModelDeleted events)
- Creator badge detection for comment authors
- Nested comment threads with pagination
- Automatic comment archiving when models are deleted

### Supporting Infrastructure

**PostgreSQL** (Port 5432)

- Primary database for Model Catalog metadata
- Stores model definitions, versions, and relationships

**MongoDB Replica Set** (Ports 27017/27018/27019)

- 3-node replica set (rs0) for Collaboration Service
- CP (Consistency + Partition Tolerance) configuration
- Write concern "majority" for consistency
- Read preference "secondary preferred" for high availability

**Redis** (Port 6379)

- Rate limiting and caching layer
- Session management and distributed locking
- Comment caching for Collaboration Service

**RabbitMQ** (Ports 5672/15672)

- Asynchronous event messaging between services
- Pub/sub for model lifecycle events
- Message queuing for background jobs
- Management UI for monitoring

**MinIO** (Ports 9000/9001)

- S3-compatible object storage
- Stores model files as artifacts
- Single bucket `rllabs-artifacts` for all model files

### Observability Stack

**Prometheus** (Port 9090)

- Metrics collection from all services
- Pre-configured scrape targets for all microservices
- Alerting rules for Four Golden Signals (Latency, Traffic, Errors, Saturation)
- 15-day retention with lifecycle management

**Grafana** (Port 3000)

- Unified dashboard for metrics, logs, and traces
- Pre-configured data sources: Prometheus, Loki, Jaeger, Alertmanager
- Custom RLLabs dashboard (`kubernetes/grafana-dashboard-rllabs.json`)

**Jaeger** (Port 16686)

- Distributed tracing backend
- Receives traces via OTLP (OpenTelemetry Protocol)
- Waterfall visualization for request flows
- Service dependency graphs

**Loki + Promtail**

- Centralized log aggregation (like Prometheus, but for logs)
- Promtail agents collect logs from all pods
- LogQL query language for structured log queries
- Trace ID correlation with Jaeger

**Alertmanager** (Port 9093)

- Alert routing and notification management
- Grouping, silencing, and inhibition rules
- Configurable receivers (Slack, PagerDuty, email)

## Current Implementation Status

### Fully Implemented

**Frontend**

- Landing page with model browsing
- Model Collaboration page (UI skeleton)
- Upload and Auth overlays
- Responsive design with Tailwind v4

**API Gateway**

- Bearer JWT authentication (HS256)
- Health endpoints for liveness/readiness
- Route proxying to Model Catalog
- Rate limiting (Redis-backed)
- User context forwarding via headers
- Security headers middleware

**Model Catalog Service**

- Model creation with ownership tracking
- Version registration with duplicate prevention
- Latest version querying
- Direct PostgreSQL integration
- Duplicate name and version handling
- **Ownership API**: `/models/{model_id}/ownership` endpoint for RBAC permission checks
- **Event Publishing**: ModelCreated events to RabbitMQ
- **Event Consumer**: Listens to `artifact.committed` events and auto-registers model versions

**Upload/Download Service**

- Multipart upload workflow with presigned URLs
- Content-addressed storage (SHA-256 based deduplication)
- PostgreSQL metadata management for artifacts and sessions
- **RBAC Authorization**:
  - **Downloads**: Public downloads allowed (no auth required), authenticated users checked for ownership/model access
  - **Uploads**: Authentication required (uploads must be signed in)
  - Authorization happens before presigned URL generation (secure + performant)
  - Owner can always download their artifacts
  - Model-level permissions (users with model access can download artifacts)
  - Cross-service permission checks (queries Model Catalog for model ownership)
- Presigned URL generation for secure downloads
- Upload session lifecycle (start, complete, abort)
- **Event Publishing**: ArtifactCommitted events to RabbitMQ
- Integration with Model Catalog for automatic version registration

**RabbitMQ Integration**

- Event publishers in Model Catalog and Upload/Download services
- Topic exchanges (`model_events`, `artifact_events`) for event routing
- **ModelCreated** event publishing on model creation
- **ArtifactCommitted** event publishing on artifact upload completion
- Event consumer in Model Catalog for artifact-driven version registration
- Graceful degradation if RabbitMQ unavailable
- Persistent messages (survive broker restarts)
- Connection retry logic with automatic reconnection

**Training Service**

- RabbitMQ consumer listening to `training_jobs` queue
- Downloads artifacts (config JSON, dataset JSON, model weights .pth) from Upload/Download Service
- Runs DQN training using PyTorch Agent
- Saves trained weights and uploads back to MinIO
- Automatic model version registration after training
- User-scoped artifact downloads with proper authorization

**Collaboration Service**

- RESTful API for model comments (GET, POST, PUT, DELETE)
- MongoDB replica set with CP configuration (write concern "majority")
- Redis caching for comment queries (5-minute TTL)
- Event consumer listening to `model_events` exchange (ModelCreated, ModelDeleted)
- Automatic creator badge detection for comment authors
- Nested comment threads with recursive deletion
- Comment archiving on model deletion

**Integration**

- Gateway → Catalog authentication flow
- Gateway → Upload/Download Service authentication flow
- Gateway → Training Jobs endpoint (POST `/api/training-jobs`)
- Gateway → Collaboration Service (comments endpoints)
- **Public Catalog Browsing**: GET `/api/models*` endpoints are public (no auth required)
- **Protected Write Operations**: POST/PUT/DELETE operations require valid JWT
- Public routes (`/public/*`) open for discovery
- User ID propagation (`X-User-Id` header) to all backend services
- **Admin Scope Support**: JWT tokens with `api:admin` scope enable admin operations
- **RBAC Integration**: Cross-service permission checks (Upload/Download → Model Catalog)
- **Async Messaging**: Model and artifact lifecycle events via RabbitMQ
- **Training Job Flow**: Upload/Download Service → RabbitMQ → Training Service → MinIO → Model Catalog
- **Collaboration Flow**: Model Catalog → RabbitMQ → Collaboration Service (event-driven model metadata caching)
- **Event-Driven Workflows**: Artifact uploads auto-register as model versions
- **Fault Tolerance**: Circuit breakers, retry with exponential backoff
- **Security**: Authorization at application layer, direct client-to-MinIO transfers for performance
- **IP-Based Rate Limiting**: Unauthenticated users rate-limited by IP address

## Setup and Installation

### Prerequisites

- **Docker** (v20.10+)
- **Docker Compose** (v2.0+)
- **Python 3.9+** (for running tests)
- **Node.js** (v18+) and **npm** (for frontend)

Kubernetes:

- **kubectl**
- **Kind** (local K8s cluster)

### Quick Start Docker Compose

1. Clone the repository:

```bash
git clone <repository-url>
cd rllabs
```

2. Start all services (including frontend):

```bash
docker compose up --build
```

This starts:

- **Frontend** on http://localhost:5173 (Vite dev server with hot reload)
- **API Gateway** on http://localhost:8080 (single entry point for all API requests)
- **Model Catalog** (accessible via API Gateway at http://localhost:8080/api/models)
- **Upload/Download Service** (accessible via API Gateway at http://localhost:8080/api/uploads and /api/downloads)
- **Training Service** (RabbitMQ consumer, no HTTP port)
- **Collaboration Service** (accessible via API Gateway at http://localhost:8080/api/comments)
- **PostgreSQL** (internal only - use `docker compose exec postgres_db psql -U rllabs -d model_catalog_db` for debugging)
- **MongoDB Replica Set** (internal only - use `docker compose exec mongo1 mongosh --port 27017` for debugging)
- **Redis** (internal only - use `docker compose exec redis_cache redis-cli` for debugging)
- **RabbitMQ** (internal only - use `docker compose exec rabbitmq_messaging rabbitmq-diagnostics status` for debugging)
- **MinIO** on http://localhost:9000 (console: http://localhost:9001 - required for presigned URLs)

**Note**: The frontend is included in docker-compose and will be available at http://localhost:5173 after the build completes. For development with hot reload, the frontend uses Vite's dev server.

3. Verify services are healthy:

**Docker Compose:**
```bash
# All services accessed through API Gateway (security best practice)
curl http://localhost:8080/health  # Gateway
# Backend services are not directly accessible - all requests must go through API Gateway
# This enforces centralized authentication, rate limiting, and circuit breakers
# Training Service doesn't expose HTTP endpoint (RabbitMQ consumer only)
```

**Kind/Kubernetes:**
```bash
# All services accessed through API Gateway (port-forward required)
curl http://localhost:8080/health  # Gateway
# Backend services are not directly accessible - use API Gateway endpoints
# Training Service doesn't expose HTTP endpoint (RabbitMQ consumer only)
```

**Security Note:** Backend services (Model Catalog, Upload/Download, Collaboration) are not exposed directly in either deployment. All client requests must go through the API Gateway at `http://localhost:8080`, which enforces:
- JWT-based authentication
- Rate limiting (user-based and IP-based)
- Circuit breakers for fault tolerance
- User context propagation (`X-User-Id` header)
- Centralized security policies

5. Check Training Service is running:

**Docker Compose:**
```bash
docker compose logs model-train-service
# Should see: "Waiting for messages on queue 'training_jobs'..."
```

**Kind/Kubernetes:**
```bash
kubectl logs -l app=model-train-service --tail=50
# Should see: "Waiting for messages on queue 'training_jobs'..."
```

6. Stop and clean up:

**Docker Compose:**
```bash
docker compose down -v
```

**Kind/Kubernetes:**
```bash
# Delete cluster
kind delete cluster --name rllabs

# Or delete specific resources
kubectl delete -k kubernetes
```

### Docker Compose vs Kind/Kubernetes Differences

**Service Access:**
- **Docker Compose**: All application services are accessed through the API Gateway at `http://localhost:8080` (security best practice)
- **Kind/Kubernetes**: All services are accessed through the API Gateway at `http://localhost:8080`. Backend services require port-forwards for direct access
- **Both deployments**: Enforce the same security architecture - centralized authentication, rate limiting, and circuit breakers through the API Gateway

**Health Checks:**
- **Docker Compose**: Can check individual service health endpoints directly
- **Kind/Kubernetes**: Health checks go through API Gateway; individual service health requires port-forwards or kubectl exec

**Port Forwards:**
- **Docker Compose**: Not needed - services bind directly to localhost ports
- **Kind/Kubernetes**: Port-forwards are automatically set up by `start_everything.sh`:
  - API Gateway: `8080:8080`
  - Frontend: `5173:80`
  - MinIO: `9000:9000`
  - Grafana: `3000:3000`
  - Prometheus: `9090:9090`
  - Jaeger: `16686:16686`
  - Alertmanager: `9093:9093`
  - Loki: `3100:3100`

**Manual Testing:**
- Both deployments support the same API endpoints through the API Gateway
- All manual tests from the README work identically in both environments
- Use `python3 scripts/run_manual_tests.py` to verify functionality in either environment
- The script automatically detects Docker Compose or Kubernetes and runs appropriate checks
- **All 14 tests passing**: Health checks, JWT tokens, model CRUD, uploads/downloads, training service, RabbitMQ connectivity, and more

### Running Tests

**Prerequisites:**
Ensure all services are running via Docker Compose:

```bash
docker compose up -d
```

**Test Architecture:**
All tests are integrated with the API Gateway and use JWT authentication. Tests route through `http://localhost:8080/api/*` endpoints, ensuring:
- Centralized authentication and authorization
- Consistent rate limiting
- Proper user context forwarding (`X-User-Id` header)
- Fault tolerance through circuit breakers
- End-to-end validation of the gateway integration

**Test Status:**
**All 131 tests passing** - Comprehensive test coverage across all services

**Unit Tests (Catalog Service)**

```bash
pytest tests/test_catalog_service.py -v
```

Tests model CRUD operations, versioning, and error handling through the API Gateway:
- Model creation, retrieval, and deletion
- Version registration and latest version queries
- Ownership endpoint for RBAC checks
- Public browsing (no auth required for GET)
- Admin delete functionality
- All requests go through `/api/models` via gateway

**Gateway Tests**

```bash
pytest tests/test_gateway.py -v
```

Tests JWT authentication, protected routes, public routes, and rate limiting:

- Public catalog browsing (GET `/api/models*` without auth)
- Protected write operations (POST/DELETE require auth)
- IP-based rate limiting for unauthenticated users
- User-based rate limiting for authenticated users
- Token validation and error handling

**Integration Tests**

```bash
pytest tests/test_integration.py -v
```

Tests full workflows including:

- Gateway → Catalog communication
- JWT authentication flow
- RabbitMQ connectivity
- Event publishing (ModelCreated events)
- Graceful degradation when RabbitMQ is unavailable
- All API calls routed through gateway

**Upload/Download Service Tests**

```bash
pytest tests/test_upload_download_service.py -v
```

Tests Upload/Download Service integration through the gateway:

- Gateway routing for `/api/uploads` and `/api/downloads`
- JWT authentication enforcement (required for uploads, optional for downloads)
- User ID header forwarding (X-User-Id) from gateway
- Upload session creation, completion, and abortion
- Multipart upload with presigned URLs
- Content-addressed storage and deduplication
- **RBAC Authorization**:
  - Public downloads (no auth required - unauthenticated users can download any artifact)
  - Owner-based and model-level permission checks for authenticated users
  - Authenticated non-owners receive 403 Forbidden
- Download authorization and presigned URL generation
- RabbitMQ event publishing (ArtifactCommitted events)
- Graceful degradation when RabbitMQ is unavailable
- End-to-end upload workflow

**Training Flow Tests**

```bash
pytest tests/test_training_flow.py -v
```

Tests complete training workflow:

- Model creation via gateway
- Artifact uploads (config, dataset, weights)
- Training job triggering through gateway
- Training execution and monitoring
- Trained weights upload and version registration
- Download of trained models
- Multiple training runs (versioning)
- Error handling scenarios
- Model catalog queries

**RBAC Authorization Tests**

```bash
pytest tests/test_rbac_authorization.py -v
```

Tests comprehensive RBAC authorization through the gateway:

- Model Catalog ownership endpoint (via `/api/models/{id}/ownership`)
- Upload/Download Service authorization checks
- Owner access (positive cases)
- Non-owner denial (403 Forbidden) for authenticated users
- Public downloads (unauthenticated access allowed)
- Model-level permission checks
- Cross-service permission queries
- Error handling (404, 400, 401, 403)
- Edge cases and concurrent requests

**Collaboration Service Tests**

```bash
pytest tests/test_collaboration_service.py -v
```

Tests collaboration features through the gateway:

- Comment CRUD operations (via `/api/models/{id}/comments`)
- Nested comment threads
- Creator badge detection
- Event-driven integration (ModelCreated/ModelDeleted)
- Redis caching behavior
- Error handling scenarios
- API Gateway routing verification

**Admin Delete Tests**

```bash
# Model deletion tests
pytest tests/test_catalog_service.py::test_delete_model_* -v

# Artifact deletion tests
pytest tests/test_upload_download_service.py::test_delete_artifact_* -v
```

Tests admin and owner delete functionality:

- Owner can delete their models/artifacts
- Admin can delete any model/artifact (with `api:admin` scope)
- Non-owner non-admin cannot delete
- Error handling (404, 403, 401)

**RabbitMQ Event Testing**
The integration tests verify async messaging:

- `test_rabbitmq_connectivity`: Verifies RabbitMQ is accessible
- `test_model_created_event_published`: Tests ModelCreated event publishing
- `test_rabbitmq_artifact_event`: Tests ArtifactCommitted event publishing
- `test_rabbitmq_graceful_degradation`: Ensures services work without RabbitMQ

**Event Consumer Testing**

```bash
pytest tests/test_event_consumer.py -v
```

Tests event consumer functionality:

- Model Catalog event consumer listening to artifact events
- Auto-registration of model versions from artifact uploads
- Error handling and reconnection logic
- All model creation goes through gateway

**All Tests**

```bash
pytest tests/ -v
```

Runs all 131 tests across gateway, catalog, integration, upload/download, training, collaboration, and RBAC suites. All tests use the API Gateway for consistent authentication and routing.

**Test Dependencies:**
Install all test dependencies:

```bash
pip install -r tests/requirements.txt
```

**Test Coverage Summary:**
- API Gateway: Authentication, routing, rate limiting
- Model Catalog: CRUD, versioning, ownership, events
- Upload/Download: Multipart uploads, RBAC, presigned URLs
- Training Service: End-to-end training workflow
- Collaboration Service: Comments, threads, events
- RBAC: Authorization across all services
- Integration: Cross-service workflows
- Event System: RabbitMQ messaging

## API Usage

### Generating a JWT Token

The platform uses Bearer JWT authentication. Use the provided token generation script:

```bash
# Regular user token
python generate_token.py

# Admin token (includes api:admin scope)
python generate_token.py --admin

# Custom user ID
python generate_token.py --user my-user-id

# Admin with custom user ID
python generate_token.py --admin --user admin-1

# Decode a token
python generate_token.py --decode <token>
```

Or generate programmatically using PyJWT:

```python
import jwt
from datetime import datetime, timedelta, timezone

SECRET_KEY = "your-secret-key"  # Match api_gateway/jwt_auth.py

# Regular user token
payload = {
    "sub": "user-123",  # User ID
    "scopes": ["api:read", "api:write"],
    "iat": int(datetime.now(timezone.utc).timestamp()),
    "exp": int((datetime.now(timezone.utc) + timedelta(minutes=30)).timestamp())
}
token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")

# Admin token
admin_payload = {
    "sub": "admin-user",
    "scopes": ["api:read", "api:write", "api:admin"],  # Include api:admin scope
    "iat": int(datetime.now(timezone.utc).timestamp()),
    "exp": int((datetime.now(timezone.utc) + timedelta(minutes=30)).timestamp())
}
admin_token = jwt.encode(admin_payload, SECRET_KEY, algorithm="HS256")
```

### Creating a Model (via Gateway)

```bash
curl -X POST http://localhost:8080/api/models \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-rl-model",
    "description": "DQN agent for Atari"
  }'
```

Response:

```json
{
  "id": 1,
  "name": "my-rl-model",
  "description": "DQN agent for Atari",
  "created_by": "user-123"
}
```

### Registering a Model Version

```bash
curl -X POST http://localhost:8080/api/models/1/versions \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "version": 1,
    "storage_path": "models/my-rl-model/v1.weights",
    "content_hash": "sha256:abc123..."
  }'
```

### Querying the Latest Version

```bash
curl http://localhost:8080/api/models/1/latest \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

Response:

```json
{
  "storage_path": "models/my-rl-model/v1.weights"
}
```

### Uploading a Model File

```bash
# Start upload session
# Note: Requires JSON body with file_hash (SHA-256 hash of file content)
curl -X POST http://localhost:8080/api/uploads \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "model.weights",
    "file_size": 1048576,
    "file_hash": "sha256:abc123...",
    "chunk_size": 5242880,
    "artifact_type": "model",
    "model_id": 1
  }'
```

Response:

```json
{
  "upload_id": "uuid-here",
  "presigned_urls": [
    {
      "part_number": 1,
      "url": "https://presigned-url-for-part-1",
      "expires_at": "2024-01-01T12:00:00Z"
    }
  ],
  "session_expires_at": "2024-01-01T13:00:00Z"
}
```

After uploading parts to presigned URLs, complete the upload:

```bash
curl -X POST http://localhost:8080/api/uploads/{upload_id}/complete \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "parts": [
      {
        "part_number": 1,
        "etag": "etag-from-upload-response"
      }
    ]
  }'
```

This triggers an `ArtifactCommitted` event, which the Model Catalog consumes to auto-register a version.

### Downloading an Artifact

**Public Downloads (No Authentication Required):**

```bash
# Public download (no auth required)
curl http://localhost:8080/api/downloads/{artifact_id}?expires_in=3600
```

**Authenticated Downloads (Optional):**

```bash
# Authenticated download (optional - same result as public)
curl http://localhost:8080/api/downloads/{artifact_id}?expires_in=3600 \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

**Authorization Rules:**

- **Public Access**: Unauthenticated users can download any artifact (public downloads enabled)
- **Authenticated Users**: If authenticated, service checks:
  1. If user is artifact owner (via upload session) - always allowed
  2. If artifact belongs to model, checks model-level permissions (queries Model Catalog)
  3. Returns 403 if unauthorized (for authenticated users), 404 if artifact not found
- **Flow**: Authorization check happens before presigned URL generation (secure + performant)

Response:

```json
{
  "download_url": "https://presigned-url-for-download",
  "expires_at": "2024-01-01T12:00:00Z",
  "file_size": 1048576,
  "filename": "model.weights"
}
```

**Error Responses:**

- `400 Bad Request`: Invalid artifact_id format
- `403 Forbidden`: Authenticated user does not have permission to download (public downloads are always allowed)
- `404 Not Found`: Artifact does not exist in system

### Deleting Models (Owner or Admin)

```bash
# Delete a model (owner or admin only)
curl -X DELETE http://localhost:8080/api/models/{model_id} \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

**Authorization:**

- Model owner can delete their models
- Admins (with `api:admin` scope) can delete any model
- Returns `403 Forbidden` if user is neither owner nor admin
- Returns `204 No Content` on success

### Deleting Artifacts (Owner or Admin)

```bash
# Delete an artifact (owner or admin only)
# Note: Gateway must have /api/artifacts route configured (see api_gateway/config.py)
curl -X DELETE http://localhost:8080/api/artifacts/{artifact_id} \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

**Note:** The API Gateway configuration includes `/api/artifacts` route. If you modify the gateway config, restart the gateway service to pick up changes:
- **Docker Compose**: `docker compose restart api-gateway`
- **Kind/Kubernetes**: `kubectl rollout restart deployment/api-gateway`

**Authorization:**

- Artifact owner can delete their artifacts
- Admins (with `api:admin` scope) can delete any artifact
- Returns `403 Forbidden` if user is neither owner nor admin
- Returns `404 Not Found` if artifact doesn't exist
- Returns `204 No Content` on success

**Note**: This is a hard delete - the artifact will be permanently removed from MinIO storage.

### Triggering a Training Job

Training jobs are triggered by uploading the required artifacts (config, dataset, model weights) and then submitting a training job request.

**Step 1: Upload Training Artifacts**

First, upload the three required artifacts:

```bash
# 1. Upload training config JSON (DQN architecture config)
# 2. Upload dataset config JSON (maze/grid parameters)
# 3. Upload model weights .pth file (pre-trained model)
```

See "Uploading a Model File" section above for upload instructions. Make note of the `artifact_id` (SHA-256 hash) returned for each upload.

**Step 2: Trigger Training Job**

```bash
curl -X POST http://localhost:8080/api/training-jobs \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "config_artifact_id": "sha256:abc123...",
    "dataset_artifact_id": "sha256:def456...",
    "model_artifact_id": "sha256:ghi789..."
  }'
```

Response:

```json
{
  "job_id": "job-uuid-here",
  "status": "queued",
  "message": "Training job has been queued for processing"
}
```

**Training Flow:**

1. Upload/Download Service validates all artifacts exist
2. Service queries database to get `model_id` from model artifact
3. Service publishes job message to RabbitMQ `training_jobs` queue
4. Training Service consumes message and downloads artifacts
5. Training Service runs training with Agent
6. Training Service saves trained weights and uploads to MinIO
7. New model version is automatically registered in Model Catalog

**Monitoring Training Jobs:**

```bash
# Check Training Service logs
docker compose logs -f model-train-service

# Check RabbitMQ queue
# Docker Compose:
curl -u admin:admin_password http://localhost:15672/api/queues/%2F/training_jobs

# Kubernetes/Kind (requires port-forward):
# kubectl port-forward service/rabbitmq 15672:15672
# Then: curl -u admin:admin_password http://localhost:15672/api/queues/%2F/training_jobs

# View training progress in logs
docker compose logs model-train-service | grep "Training job"
```

**After Training Completes:**

The trained model weights are automatically uploaded and registered as a new version. You can query the model versions:

```bash
# List all versions for a model
curl http://localhost:8080/api/models/{model_id}/versions

# Get latest version (should be the newly trained one)
curl http://localhost:8080/api/models/{model_id}/latest
```

### Collaboration Service (Comments)

**Create a Comment on a Model:**

```bash
curl -X POST http://localhost:8080/api/models/{model_id}/comments \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Great model! How did you tune the hyperparameters?",
    "authorId": "user-123",
    "authorName": "Alice",
    "parentId": null
  }'
```

Response:

```json
{
  "id": "507f1f77bcf86cd799439011",
  "modelId": "1",
  "content": "Great model! How did you tune the hyperparameters?",
  "authorId": "user-123",
  "authorName": "Alice",
  "isCreator": false,
  "parentId": null,
  "createdAt": "2024-01-01T12:00:00Z",
  "updatedAt": "2024-01-01T12:00:00Z",
  "replies": []
}
```

**Reply to a Comment:**

```bash
curl -X POST http://localhost:8080/api/models/{model_id}/comments \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "I used grid search with learning rate 0.001",
    "authorId": "user-456",
    "authorName": "Bob",
    "parentId": "507f1f77bcf86cd799439011"
  }'
```

**Get Comments for a Model:**

```bash
# Get paginated comments (nested tree structure)
curl http://localhost:8080/api/models/{model_id}/comments?page=1&limit=50
```

Response:

```json
{
  "data": [
    {
      "id": "507f1f77bcf86cd799439011",
      "content": "Great model!",
      "authorId": "user-123",
      "authorName": "Alice",
      "isCreator": false,
      "replies": [
        {
          "id": "507f1f77bcf86cd799439012",
          "content": "I used grid search...",
          "authorId": "user-456",
          "authorName": "Bob",
          "replies": []
        }
      ]
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 50,
    "total": 1,
    "hasMore": false
  }
}
```

**Update a Comment:**

```bash
curl -X PUT http://localhost:8080/api/comments/{comment_id} \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Updated comment content"
  }'
```

**Delete a Comment (and all replies):**

```bash
curl -X DELETE http://localhost:8080/api/comments/{comment_id} \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

**Features:**
- Nested comment threads (replies to replies)
- Creator badge detection (automatically marks model creator's comments)
- Automatic comment archiving when models are deleted
- Redis caching (5-minute TTL) for performance
- Pagination on top-level comments

### Public Model Discovery

**Browse models without authentication:**

```bash
# List all models (public endpoint)
curl http://localhost:8080/api/models

# Get model details (public endpoint)
curl http://localhost:8080/api/models/1

# Get latest version path (public endpoint)
curl http://localhost:8080/api/models/1/latest
```

All GET `/api/models*` endpoints are public (no authentication required). This enables easy model discovery and browsing.

**Note**: Write operations (POST/PUT/DELETE) still require authentication.

### Testing RabbitMQ Events

**Verify RabbitMQ is running:**

**Docker Compose:**
```bash
curl -u admin:admin_password http://localhost:15672/api/overview
```

**Kubernetes/Kind:**
```bash
# First, set up port-forward for RabbitMQ management UI
kubectl port-forward service/rabbitmq 15672:15672

# Then in another terminal:
curl -u admin:admin_password http://localhost:15672/api/overview
```

**View events via Management UI:**

**Docker Compose:**
1. Open http://localhost:15672 in your browser
2. Login with `admin` / `admin_password`
3. Navigate to **Exchanges** → `model_events`
4. Check **Bindings** to see queue subscriptions
5. Monitor published messages in real-time

**Kubernetes/Kind:**
1. Set up port-forward: `kubectl port-forward service/rabbitmq 15672:15672`
2. Open http://localhost:15672 in your browser
3. Login with `admin` / `admin_password`
4. Navigate to **Exchanges** → `model_events`
5. Check **Bindings** to see queue subscriptions
6. Monitor published messages in real-time

**Testing event publishing programmatically:**

**Docker Compose:**
```python
import pika
import json

# Connect to RabbitMQ
credentials = pika.PlainCredentials('admin', 'admin_password')
connection = pika.BlockingConnection(
    pika.ConnectionParameters('localhost', 5672, credentials=credentials)
)
```

**Kubernetes/Kind:**
```python
import pika
import json

# Note: Services connect via service name "rabbitmq" on port 5672 (internal cluster DNS)
# For external access, use port-forward: kubectl port-forward service/rabbitmq 5672:5672
# Then connect to localhost:5672
credentials = pika.PlainCredentials('admin', 'admin_password')
connection = pika.BlockingConnection(
    pika.ConnectionParameters('localhost', 5672, credentials=credentials)  # Requires port-forward
)
channel = connection.channel()

# Declare exchange
channel.exchange_declare(exchange='model_events', exchange_type='topic', durable=True)

# Create queue and bind
result = channel.queue_declare(queue='test-queue', exclusive=True)
queue_name = result.method.queue
channel.queue_bind(exchange='model_events', queue=queue_name, routing_key='model.created')

# Create a model (via API) - this will trigger event publishing
# ... your model creation code ...

# Consume event
method, properties, body = channel.basic_get(queue=queue_name, auto_ack=True)
if method:
    event = json.loads(body)
    print(f"Received event: {event['event_type']}")
    print(f"Model: {event['model_name']}")

connection.close()
```

**Monitoring events in tests:**
Integration tests automatically verify event publishing. See `tests/test_integration.py`:

- `test_rabbitmq_connectivity`: Verifies RabbitMQ is accessible
- `test_model_created_event_published`: Tests ModelCreated event flow
- `test_rabbitmq_graceful_degradation`: Ensures services work without RabbitMQ

## Distributed Systems Architecture

### Communication Patterns

**Synchronous HTTP (Request/Response)**

- **API Gateway → Backend Services**: Synchronous HTTP with JWT validation
- **Upload/Download → Model Catalog**: Synchronous HTTP for RBAC permission checks
- **Trade-off**: Strong consistency, but tight coupling and potential latency
- **Use Case**: Critical operations requiring immediate consistency (model creation, permission checks)

**Asynchronous Messaging (Event-Driven)**

- **Services → RabbitMQ**: Event publishing for non-critical operations
- **RabbitMQ → Event Consumers**: Async processing of model/artifact lifecycle events
- **Trade-off**: Loose coupling and scalability, but eventual consistency
- **Use Case**: Non-blocking operations (event notifications, auto-registration)

**Direct Client-to-Storage (Presigned URLs)**

- **Client → MinIO**: Direct file transfers bypassing backend services
- **Trade-off**: High performance (no backend bottleneck), but requires pre-authorization
- **Use Case**: Large file uploads/downloads (GB-scale model files)

### CAP Theorem Trade-offs

**Consistency vs Availability vs Partition Tolerance**

| Service                   | Primary Choice                                    | Rationale                                                                                                       |
| ------------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| **Model Catalog**   | **CP** (Consistency + Partition Tolerance)  | Model metadata must be consistent. If database is partitioned, we prefer to fail rather than serve stale data.  |
| **Upload/Download** | **CP** (Consistency + Partition Tolerance)  | Artifact metadata must be consistent. Authorization checks require consistent data.                             |
| **API Gateway**     | **AP** (Availability + Partition Tolerance) | Gateway should remain available even if backend services are down. Uses circuit breakers to degrade gracefully. |
| **RabbitMQ Events** | **AP** (Availability + Partition Tolerance) | Events are eventually consistent. System continues operating even if message broker is partitioned.             |

**Key Decisions:**

- **Strong Consistency for Metadata**: Model and artifact ownership must be consistent across services
- **Eventual Consistency for Events**: Model lifecycle events can be processed asynchronously
- **Graceful Degradation**: Services continue operating with reduced functionality when dependencies fail

### Fault Tolerance Mechanisms

**Circuit Breakers** (API Gateway)

- Prevents cascading failures when backend services are down
- Opens circuit after failure threshold, allows retry after cooldown
- **Trade-off**: Fast failure detection, but may block legitimate requests during recovery

**Retry with Exponential Backoff** (API Gateway)

- Automatic retry for transient failures
- Exponential backoff prevents overwhelming failing services
- **Trade-off**: Improves reliability, but increases latency for failed requests

**Rate Limiting** (API Gateway)

- **Authenticated Users**: Rate limited by user ID (Redis-backed)
- **Unauthenticated Users**: Rate limited by IP address (Redis-backed)
- **Graceful Degradation**: Falls back to in-memory rate limiting if Redis unavailable
- **Trade-off**: Prevents abuse, but may block legitimate users during high traffic

**Graceful Degradation**

- **RabbitMQ Unavailable**: Services continue operating, events are skipped (logged)
- **Redis Unavailable**: Gateway falls back to in-memory rate limiting
- **Model Catalog Unavailable**: Upload/Download Service fails authorization checks (fail-closed security)
- **Trade-off**: System remains operational, but with reduced functionality

**Health Checks and Monitoring**

- Each service exposes `/health` endpoint
- Docker Compose health checks ensure services are ready before routing traffic
- **Trade-off**: Adds overhead, but enables automatic recovery and load balancing

### Security Architecture

**Authentication** (API Gateway)

- JWT-based authentication at gateway layer
- User context (`X-User-Id`) propagated to all backend services
- **Trade-off**: Centralized auth simplifies backend, but gateway becomes single point of failure

**Authorization** (Application Layer)

- **RBAC at Service Level**: Authorization checks in Upload/Download Service before generating presigned URLs
- **Public Downloads**: Unauthenticated users can download any artifact (no auth required)
- **Authenticated Downloads**: Owner-based and model-level permission checks for authenticated users
- **Upload Authentication**: All uploads require authentication (must be signed in)
- **Cross-Service Permission Checks**: Upload/Download queries Model Catalog for model ownership
- **Admin Scope**: JWT tokens with `api:admin` scope enable admin operations (delete models/artifacts)
- **Owner or Admin**: Both owners and admins can delete resources
- **Fail-Closed Security**: Deny access on errors (secure by default)
- **Trade-off**: Authorization in application layer (where business logic exists) vs. storage layer (simpler but less flexible)

**Direct Storage Access** (Presigned URLs)

- Clients upload/download directly to/from MinIO (bypasses backend)
- Authorization happens before URL generation (secure)
- **Trade-off**: High performance (no backend bottleneck) + Security (authorization before access)

**Network Security**

**Current Implementation:**
- Application services (Model Catalog, Upload/Download, Collaboration) are NOT directly exposed
- All client requests must go through API Gateway (port 8080)
- **Docker Compose**: Infrastructure services (PostgreSQL, Redis, RabbitMQ, MongoDB) are exposed for development/debugging
- **Kubernetes**: Infrastructure services are secured (ClusterIP) - NOT exposed externally
- MinIO is exposed for presigned URL direct transfers (authorization happens before URL generation)

**Docker Compose vs Kubernetes Security:**
- **Docker Compose**: Services secured by removing `ports:` mappings - services communicate via Docker network only. Infrastructure services exposed via port mappings.
- **Kubernetes**: Services secured by default (ClusterIP) - only accessible within cluster, no external exposure. Infrastructure services use ClusterIP (not exposed).
- See `docs/SECURITY_COMPARISON.md` for detailed comparison

**Production Security Recommendations:**
- **Remove infrastructure service port exposures** - Use network policies, firewalls, or VPN for admin access
- **Implement mTLS (mutual TLS)** for inter-service communication:
  - API Gateway ↔ Backend Services: mTLS for service-to-service authentication
  - Prevents service impersonation and man-in-the-middle attacks
  - Each service has its own certificate signed by a trusted CA
  - Service mesh (Istio, Linkerd) can provide automatic mTLS
- **Network Policies**: Restrict which pods can communicate with each other (Kubernetes only)
- **Secrets Management**: Use Kubernetes Secrets or external secret managers (Vault) for credentials
- **MinIO Security**: In production, consider:
  - MinIO behind a reverse proxy with TLS termination
  - IP allowlisting for presigned URL access
  - Shorter presigned URL expiration times
  - Audit logging for all storage access

**Future Enhancements:**
- Service mesh with automatic mTLS (Istio, Linkerd)
- Certificate-based service authentication
- Network segmentation with Kubernetes Network Policies
- Encrypted inter-service communication
- Zero-trust network architecture

### Data Consistency Strategies

**Strong Consistency** (PostgreSQL)

- Model metadata: ACID transactions ensure consistency
- Artifact ownership: Database transactions ensure upload sessions are atomic
- **Trade-off**: Strong consistency requires synchronous operations (higher latency)

**Eventual Consistency** (RabbitMQ Events)

- Model lifecycle events: Processed asynchronously
- Auto-registration: Artifact uploads trigger model version creation via events
- **Trade-off**: Eventual consistency allows high throughput, but introduces delay

**Idempotency** (Redis + Database)

- Upload deduplication: Redis idempotency keys prevent duplicate uploads
- Database unique constraints: Prevent duplicate model versions
- **Trade-off**: Idempotency improves reliability, but requires additional storage

### Performance

**Content-Addressed Storage**

- Artifacts stored by SHA-256 hash (deduplication)
- **Trade-off**: Storage efficiency vs. hash computation overhead

**Presigned URLs**

- Direct client-to-MinIO transfers (bypasses backend)
- **Trade-off**: High performance vs. time-limited URLs (security)

**Database Indexing**

- Indexed on `user_id`, `model_id`, `file_hash` for fast authorization checks
- **Trade-off**: Fast queries vs. write performance overhead

### Scalability

**Horizontal Scaling**

- Stateless services (API Gateway, Model Catalog, Upload/Download) can scale horizontally
- Database (PostgreSQL) requires replication for read scaling
- **Trade-off**: Horizontal scaling improves availability, but requires distributed coordination

**Vertical Scaling**

- MinIO can scale vertically (larger storage)
- PostgreSQL can scale vertically (more memory/CPU)
- **Trade-off**: Vertical scaling is simpler, but has hardware limits

**Current Limitations**

- Single PostgreSQL instance (no replication)
- Single MinIO instance (no distributed mode)
- **Future Enhancements**: Read replicas, distributed MinIO, service mesh for inter-service communication

## Service Communication Patterns

### Current Implementation

**API Gateway → Model Catalog**

```
Client → Gateway (JWT auth) → Catalog (with X-User-Id)
```

Example request flow:

1. Client sends `POST /api/models` with `Authorization: Bearer <jwt>`
2. Gateway validates JWT, extracts `user_id` from `sub` claim
3. Gateway forwards to `http://model_catalog_service:8000/models`
4. Gateway adds `X-User-Id: <user_id>` header
5. Catalog creates model with `created_by = user_id`
6. Catalog returns model JSON → Gateway → Client

**API Gateway → Upload/Download Service (with RBAC)**

```
Client → Gateway (JWT auth) → Upload/Download (with X-User-Id)
                                      ↓
                              Authorization Check
                                      ↓
                              Model Catalog (permission check)
                                      ↓
                              Presigned URL Generation
                                      ↓
                              Client → MinIO (direct transfer)
```

Example download flow:

1. Client requests `GET /api/downloads/{artifact_id}` (JWT optional - public downloads enabled)
2. Gateway forwards to Upload/Download Service (with `X-User-Id` if authenticated, or anonymous)
3. Upload/Download Service checks authorization:
   - **Public downloads**: If no user_id, allow download (public access)
   - **Authenticated users**: Queries database for upload session (artifact ownership)
   - If artifact belongs to model, queries Model Catalog for model permissions
   - Returns 403 if unauthorized (authenticated users only), 404 if not found
4. If authorized, generates presigned URL
5. Client downloads directly from MinIO (bypasses backend)

### Asynchronous Communication (RabbitMQ)

**Model Catalog → RabbitMQ → Event Consumers**

```
Model Catalog → Publish ModelCreated event
                ↓
         RabbitMQ Exchange (model_events)
                ↓
         Queue Bindings (model.created, model.deleted)
                ↓
         Consumers:
         - Collaboration Service (cache model metadata, archive comments)
         - Future: Notification Service (notify subscribers)
         - Future: Audit Service (log model lifecycle)
```

**Implemented Events:**

- `ModelCreated`: Published when new model is created (Model Catalog → RabbitMQ)
- `ArtifactCommitted`: Published when artifact upload completes (Upload/Download Service → RabbitMQ)
- `ModelDeleted`: Published when model is deleted (Model Catalog → RabbitMQ)
- `TrainingJob`: Published when training job is triggered (Upload/Download Service → RabbitMQ → Training Service)
- `ArtifactUploaded`: Published when artifact upload completes (Upload/Download Service → RabbitMQ)
- `ArtifactDownloaded`: Published when artifact is downloaded (Upload/Download Service → RabbitMQ)

**Example Flow (Model Creation):**

1. Client creates model via Gateway → Catalog
2. Catalog saves to PostgreSQL (ACID transaction)
3. Catalog publishes `ModelCreated` event to RabbitMQ
4. Event consumers receive notification asynchronously
5. Response returned to client immediately (non-blocking)

**Example Flow (Artifact Upload with Auto-Registration):**

1. Client uploads artifact via Gateway → Upload/Download Service
2. Upload/Download Service stores file in MinIO (content-addressed storage)
3. Upload/Download Service saves metadata to PostgreSQL
4. Upload/Download Service publishes `ArtifactCommitted` event to RabbitMQ (includes `model_id` if provided)
5. Model Catalog event consumer receives `ArtifactCommitted` event
6. Model Catalog automatically creates model version (if `model_id` present)
7. Response returned to client immediately (non-blocking)

**Example Flow (Training Job):**

1. Client uploads three artifacts: config JSON, dataset JSON, model weights .pth
2. Client triggers training job via `POST /api/training-jobs` with artifact IDs
3. Upload/Download Service validates artifacts exist and queries `model_id` from database
4. Upload/Download Service publishes training job message to RabbitMQ `training_jobs` queue
5. Training Service consumes message from queue
6. Training Service downloads all three artifacts from Upload/Download Service (with user authorization)
7. Training Service runs training using Agent class (PyTorch DQN)
8. Training Service saves trained weights to temporary file
9. Training Service uploads trained weights to MinIO via Upload/Download Service
10. Upload/Download Service registers new model version with Model Catalog
11. Trained model is available as new version of original model

**Accessing Infrastructure Services for Debugging:**

**Docker Compose:**
- Infrastructure services (PostgreSQL, Redis, RabbitMQ, MongoDB) are **not exposed** to the host network for security
- Use `docker compose exec` to access services:
  ```bash
  # PostgreSQL
  docker compose exec postgres_db psql -U rllabs -d model_catalog_db
  
  # Redis
  docker compose exec redis_cache redis-cli
  
  # RabbitMQ (CLI)
  docker compose exec rabbitmq_messaging rabbitmq-diagnostics status
  docker compose exec rabbitmq_messaging rabbitmqctl list_queues
  
  # MongoDB
  docker compose exec mongo1 mongosh --port 27017
  ```

- **MinIO** remains exposed at http://localhost:9000 (required for presigned URLs)

**Kubernetes/Kind:**
- Infrastructure services use ClusterIP (internal only)
- Use `kubectl port-forward` for temporary access:
  ```bash
  # PostgreSQL
  kubectl port-forward svc/postgres-primary 5432:5432
  
  # Redis
  kubectl port-forward svc/redis 6379:6379
  
  # RabbitMQ Management UI
  kubectl port-forward svc/rabbitmq 15672:15672
  # Then access: http://localhost:15672 (admin/admin_password)
  
  # MongoDB
  kubectl port-forward svc/mongodb 27017:27017
  ```
- Or use `kubectl exec` for direct access:
  ```bash
  kubectl exec -it postgres-primary-0 -- psql -U rllabs -d model_catalog_db
  kubectl exec -it redis-master-0 -- redis-cli
  ```

## Kubernetes Deployment

See `kubernetes/` for production manifests. Deploy with:

```bash
kubectl apply -k kubernetes
```

Services include ConfigMaps, Secrets, Deployments, Services, and Ingress resources.

### Observability Stack Deployment

Deploy the full observability stack (Prometheus, Grafana, Jaeger, Loki, Alertmanager):

```bash
# Quick deploy
./scripts/deploy_observability.sh

# Rebuild services with OpenTelemetry tracing
./scripts/rebuild_services_with_otel.sh
```

**Access the UIs:**

| Service | Command | URL |
|---------|---------|-----|
| Grafana | `kubectl port-forward svc/grafana 3000:3000` | http://localhost:3000 (admin/admin) |
| Jaeger | `kubectl port-forward svc/jaeger-query 16686:16686` | http://localhost:16686 |
| Prometheus | `kubectl port-forward svc/prometheus 9090:9090` | http://localhost:9090 |
| Alertmanager | `kubectl port-forward svc/alertmanager 9093:9093` | http://localhost:9093 |

**The Debugging Journey:**
1. **ALERT** fires → You know **WHAT** is wrong (Prometheus/Alertmanager)
2. Find **TRACE** → You know **WHERE** the problem is (Jaeger)
3. Query **LOGS** → You know **WHY** it failed (Loki via Grafana)

See `OBSERVABILITY_GUIDE.md` for comprehensive documentation.

## Configuration

### Environment Variables

**API Gateway**

- `SECRET_KEY`: JWT signing secret
- `REDIS_HOST`: Redis connection host
- `REDIS_PORT`: Redis port
- `RATE_LIMIT_REQUESTS`: Max requests per window (default: 100)
- `RATE_LIMIT_WINDOW`: Time window in seconds (default: 60)

**Model Catalog Service**

- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_HOST`: Redis host for caching
- `RABBITMQ_HOST`: RabbitMQ connection host
- `RABBITMQ_PORT`: RabbitMQ port
- `RABBITMQ_USER`: RabbitMQ username
- `RABBITMQ_PASS`: RabbitMQ password

**Upload/Download Service**

- `DATABASE_URL`: PostgreSQL connection string (separate database: `upload_download_db`)
- `MINIO_ENDPOINT`: MinIO endpoint URL
- `MINIO_ACCESS_KEY`: MinIO access key
- `MINIO_SECRET_KEY`: MinIO secret key
- `MINIO_BUCKET`: MinIO bucket name (`models`)
- `MINIO_USE_SSL`: Whether to use SSL (false for local MinIO)
- `MODEL_CATALOG_URL`: Model Catalog Service URL for RBAC permission checks
- `RABBITMQ_HOST`: RabbitMQ connection host
- `RABBITMQ_PORT`: RabbitMQ port
- `RABBITMQ_USER`: RabbitMQ username
- `RABBITMQ_PASS`: RabbitMQ password
- `REDIS_HOST`: Redis host for idempotency
- `REDIS_PORT`: Redis port

**Training Service**

- `UPLOAD_DOWNLOAD_SERVICE_URL`: Upload/Download Service URL (default: `http://upload-download-service:8002`)
- `RABBITMQ_HOST`: RabbitMQ connection host (default: `rabbitmq`)
- `RABBITMQ_PORT`: RabbitMQ port (default: `5672`)
- `RABBITMQ_USER`: RabbitMQ username (default: `admin`)
- `RABBITMQ_PASS`: RabbitMQ password (default: `admin_password`)
- `RABBITMQ_QUEUE`: Queue name for training jobs (default: `training_jobs`)
- `NUM_EPISODES`: Number of training episodes (default: `50`)

**Collaboration Service**

- `MONGO_URI`: MongoDB connection string with replica set (default: `mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0`)
- `REDIS_HOST`: Redis host for caching (default: `redis`)
- `REDIS_PORT`: Redis port (default: `6379`)
- `RABBITMQ_HOST`: RabbitMQ connection host (default: `rabbitmq`)
- `RABBITMQ_PORT`: RabbitMQ port (default: `5672`)
- `RABBITMQ_USER`: RabbitMQ username (default: `admin`)
- `RABBITMQ_PASS`: RabbitMQ password (default: `admin_password`)
- `MODEL_CATALOG_URL`: Model Catalog Service URL for fallback queries (default: `http://model-catalog-service:8000`)

**Observability (All Services)**

- `OTEL_SERVICE_NAME`: Service name for traces (e.g., `api-gateway`)
- `OTEL_EXPORTER_OTLP_ENDPOINT`: Jaeger OTLP endpoint (default: `http://jaeger:4317`)
- `TRACING_ENABLED`: Enable/disable distributed tracing (default: `true`)
- `LOG_LEVEL`: Logging level (default: `INFO`)

**MongoDB**

- Replica set `rs0` with 3 nodes (mongo1, mongo2, mongo3)
- Automatic replica set initialization on startup
- CP (Consistency + Partition Tolerance) configuration

**MinIO**

- `MINIO_ROOT_USER`: Admin username
- `MINIO_ROOT_PASSWORD`: Admin password

See `docker-compose.yml` for full configuration.

## Performance Optimization

### Phase 5 Infrastructure Optimizations

The system has been optimized through 5 phases of performance improvements:

**PostgreSQL Configuration:**
- `work_mem`: 16MB (prevents disk spills for complex queries)
- `maintenance_work_mem`: 128MB (faster VACUUM/INDEX operations)
- `shared_buffers`: 256MB (25% of memory, improved caching)
- `effective_cache_size`: 768MB (75% of memory, better query planning)
- Read replicas configured with same performance parameters
- `max_connections`: 300 (supports 3 pods × 75 connections each)

**Redis Configuration:**
- `maxmemory`: 100mb (master), 50mb (replicas) - prevents OOM kills
- `maxmemory-policy`: allkeys-lru - evicts least recently used keys
- `maxclients`: 10000 - explicit connection limit
- High availability: Master + 2 replicas + 3 Sentinels

**MinIO Configuration:**
- CPU limits: 1000m (increased from 500m for erasure coding)
- CPU requests: 200m (increased from 100m)
- 4-node distributed deployment (erasure coding for redundancy)

### Load Test Results

**30 Users Test (After Phase 5 Optimizations):**
- **Success Rate:** 93.16% (96%+ at 20 users)
- **Throughput:** 65.20 req/s
- **P95 Latency:** 1,963ms (at 50 users), ~180ms (at 20 users)
- **P99 Latency:** 5,816ms (at 50 users), ~450ms (at 20 users)
- **Cache Hit Rate:** 90.93% (application-level tracking)

**20 Users Test (Baseline):**
- **Success Rate:** 96.38%
- **Throughput:** 147.52 req/s
- **P95 Latency:** 220.47ms [OK]
- **P99 Latency:** 561.16ms
- **Mean:** 70.84ms
- **Median:** 27.47ms

**Performance Improvements:**
- P95 latency: 20-30% improvement (220ms → 180ms at 20 users)
- P99 latency: 20-40% improvement (561ms → 450ms at 20 users)
- Cache hit rate: 90%+ with per-endpoint tracking
- Connection pools: Optimized to prevent exhaustion

### Monitoring Endpoints

**Model Catalog Service:**
- `GET /cache/stats` - Cache hit rate statistics per endpoint
- `GET /database/pool-stats` - Connection pool usage (primary + replicas)
- `GET /models/{model_id}/diagnostics` - Model-specific diagnostics

See `OBSERVABILITY_GUIDE.md` and `OBSERVABILITY_REPORT.md` for detailed monitoring information.
