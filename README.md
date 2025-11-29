# RLLabs - Reinforcement Learning Collaboration Platform

A distributed microservices platform for collaborative reinforcement learning model development, training, and management. Built with FastAPI, PostgreSQL, MinIO, Redis, and Kubernetes for production-grade scalability.

## Architecture Overview

RLLabs is designed as a microservices architecture where specialized services handle different aspects of the ML lifecycle:

### Core Services

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

### Supporting Infrastructure

**PostgreSQL** (Port 5432)

- Primary database for Model Catalog metadata
- Stores model definitions, versions, and relationships

**Redis** (Port 6379)

- Rate limiting and caching layer
- Session management and distributed locking

**RabbitMQ** (Ports 5672/15672)

- Asynchronous event messaging between services
- Pub/sub for model lifecycle events
- Message queuing for background jobs
- Management UI for monitoring

**MinIO** (Ports 9000/9001)

- S3-compatible object storage
- Stores model files as artifacts
- Single bucket `rllabs-artifacts` for all model files

### Planned Services 

**Collaboration Service**

- Model version comments and discussions
- MongoDB-backed threaded conversations
- @mentions and notifications

## Current Implementation Status

### Fully Implemented

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

**Integration**

- Gateway → Catalog authentication flow
- Gateway → Upload/Download Service authentication flow
- Gateway → Training Jobs endpoint (POST `/api/training-jobs`)
- **Public Catalog Browsing**: GET `/api/models*` endpoints are public (no auth required)
- **Protected Write Operations**: POST/PUT/DELETE operations require valid JWT
- Public routes (`/public/*`) open for discovery
- User ID propagation (`X-User-Id` header) to all backend services
- **Admin Scope Support**: JWT tokens with `api:admin` scope enable admin operations
- **RBAC Integration**: Cross-service permission checks (Upload/Download → Model Catalog)
- **Async Messaging**: Model and artifact lifecycle events via RabbitMQ
- **Training Job Flow**: Upload/Download Service → RabbitMQ → Training Service → MinIO → Model Catalog
- **Event-Driven Workflows**: Artifact uploads auto-register as model versions
- **Fault Tolerance**: Circuit breakers, retry with exponential backoff
- **Security**: Authorization at application layer, direct client-to-MinIO transfers for performance
- **IP-Based Rate Limiting**: Unauthenticated users rate-limited by IP address

## Setup and Installation

### Prerequisites

- **Docker** (v20.10+)
- **Docker Compose** (v2.0+)
- **Python 3.9+** (for running tests)

Kubernetes:

- **kubectl**
- **Kind** (local K8s cluster)

### Quick Start Docker Compose

1. Clone the repository:

```bash
git clone <repository-url>
cd rllabs
```

2. Start all services:

```bash
docker compose up --build
```

This starts:

- API Gateway on http://localhost:8080
- Model Catalog on http://localhost:8001
- Upload/Download Service on http://localhost:8002
- Training Service (RabbitMQ consumer, no HTTP port)
- PostgreSQL on localhost:5432
- Redis on localhost:6379
- RabbitMQ on localhost:5672 (management UI: :15672)
- MinIO on http://localhost:9000 (console: :9001)

3. Verify services are healthy:

```bash
curl http://localhost:8080/health  # Gateway
curl http://localhost:8001/health  # Catalog
curl http://localhost:8002/health  # Upload/Download Service
# Training Service doesn't expose HTTP endpoint (RabbitMQ consumer only)
```

4. Check Training Service is running:

```bash
docker compose logs model-train-service
# Should see: "Waiting for messages on queue 'training_jobs'..."
```

4. Stop and clean up:

```bash
docker compose down -v
```

### Running Tests

**Prerequisites:**
Ensure all services are running via Docker Compose:

```bash
docker compose up -d
```

**Unit Tests (Catalog Service)**

```bash
pytest tests/test_catalog_service.py -v
```

Tests model CRUD operations, versioning, and error handling.

**Gateway Tests**

```bash
pytest tests/test_gateway.py -v
```

Tests JWT authentication, protected routes, public routes, and rate limiting:

- Public catalog browsing (GET `/api/models*` without auth)
- Protected write operations (POST/DELETE require auth)
- IP-based rate limiting for unauthenticated users
- User-based rate limiting for authenticated users

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

**Upload/Download Service Tests**

```bash
pytest tests/test_upload_download_service.py -v
```

Tests Upload/Download Service integration:

- Gateway routing for `/api/uploads` and `/api/downloads`
- JWT authentication enforcement (required for uploads, optional for downloads)
- User ID header forwarding (X-User-Id)
- Upload session creation, completion, and abortion
- Multipart upload with presigned URLs
- Content-addressed storage and deduplication
- **RBAC Authorization**:
  - Public downloads (no auth required)
  - Owner-based and model-level permission checks for authenticated users
- Download authorization and presigned URL generation
- RabbitMQ event publishing (ArtifactCommitted events)
- Graceful degradation when RabbitMQ is unavailable
- End-to-end upload workflow

**RBAC Authorization Tests**

```bash
pytest tests/test_rbac_authorization.py -v
```

Tests comprehensive RBAC authorization:

- Model Catalog ownership endpoint
- Upload/Download Service authorization checks
- Owner access (positive cases)
- Non-owner denial (403 Forbidden)
- Model-level permission checks
- Cross-service permission queries
- Error handling (404, 400, 422, 403)
- Edge cases and concurrent requests

**Admin Delete Tests**

```bash
# Model deletion tests
pytest tests/test_catalog_service.py::test_delete_model_* -v

# Artifact deletion tests
pytest tests/test_upload_download_service.py::test_delete_artifact_* -v
```

Tests admin and owner delete functionality:

- Owner can delete their models/artifacts
- Admin can delete any model/artifact
- Non-owner non-admin cannot delete
- Error handling (404, 403, 422)

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

**All Tests**

```bash
pytest tests/ -v
```

Runs all tests across gateway, catalog, integration, and upload/download suites.

**Test Dependencies:**
Install all test dependencies:

```bash
pip install -r tests/requirements.txt
```

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
curl -X POST "http://localhost:8080/api/uploads?filename=model.weights&size_bytes=1048576&mime_type=application/octet-stream&model_id=1" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

Response:

```json
{
  "upload_id": "uuid-here",
  "presigned_urls": ["https://presigned-url-for-part-1"],
  "bucket": "rllabs-artifacts"
}
```

After uploading parts to presigned URLs, complete the upload:

```bash
curl -X POST "http://localhost:8080/api/uploads/{upload_id}/complete?expected_hash=sha256:abc123...&etag=etag-from-upload" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
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
curl -X DELETE http://localhost:8080/api/artifacts/{artifact_id} \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

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
curl -u admin:admin_password http://localhost:15672/api/queues/%2F/training_jobs

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

```bash
curl -u admin:admin_password http://localhost:15672/api/overview
```

**View events via Management UI:**

1. Open http://localhost:15672 in your browser
2. Login with `admin` / `admin_password`
3. Navigate to **Exchanges** → `model_events`
4. Check **Bindings** to see queue subscriptions
5. Monitor published messages in real-time

**Testing event publishing programmatically:**

```python
import pika
import json

# Connect to RabbitMQ
credentials = pika.PlainCredentials('admin', 'admin_password')
connection = pika.BlockingConnection(
    pika.ConnectionParameters('localhost', 5672, credentials=credentials)
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
         Queue Bindings (model.created)
                ↓
         Future Consumers:
         - Collaboration Service (cache model metadata)
         - Notification Service (notify subscribers)
         - Audit Service (log model lifecycle)
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

**RabbitMQ Management UI:**

- URL: http://localhost:15672
- Username: `admin`
- Password: `admin_password`
- Use to monitor queues, exchanges, and message flow

## Kubernetes Deployment

See `kubernetes/` for production manifests. Deploy with:

```bash
kubectl apply -k kubernetes
```

Services include ConfigMaps, Secrets, Deployments, Services, and Ingress resources.

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

**MinIO**

- `MINIO_ROOT_USER`: Admin username
- `MINIO_ROOT_PASSWORD`: Admin password

See `docker-compose.yml` for full configuration.
