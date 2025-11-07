# Getting Started with Upload/Download Service

##  Overiview

Quick quide to the upload_download_service. Includes info on: 
- how to build with docker-compose
- how to verify successful upload / download (Currently only working by bypassing API gateway)
- how to verify successful event publication to rabbit mq
- Troubleshooting i've encountered with tips to overcome them

##  Next Steps for MVP

1. **Complete integration with other srvices** - API gatewawy / frontend 
2. **Add RBAC** - Implement permission checks for downloads

##  Files

```
upload_download_service/
├── main.py                 # FastAPI application with all endpoints
├── models.py               # Pydantic models for request/response validation
├── database.py             # PostgreSQL configuration and ORM models
├── storage.py              # MinIO/S3 operations wrapper
├── session_manager.py      # Upload session orchestration logic
├── event_publisher.py      # RabbitMQ event publishing
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container configuration
├── README.md               # Comprehensive service documentation
├── test_simple_client.py   # Example Python client for testing (bypasses API gateway)
├── test_gateway_client.py  # Example Python client for testing (goes through API gateway)
└── test_event_consumer.py  # Listens to RabbitMQ for artifact events and prints them

postgres-init/
└── create-multiple-databases.sh  # PostgreSQL initialization script

api_gateway/
└── config.py               # Updated with upload/download routes

docker-compose.yml          # Updated with upload_download_service
```

## Quick Start

### 1. Start All Services

From your project root directory:

```bash
# Start all services
docker-compose up -d

# Check that all services are healthy
docker-compose ps

# View logs
docker-compose logs -f upload_download_service
```

### 2. Verify Service is Running

```bash
# Health check
curl http://localhost:8002/health

# Should return:
# {"service_status": "ok",dependencies": { "database": "online", "storage": "online"} }
```

### 3. Simple Test Upload (Bypassing API Gateway)

The `simple_test_client.py` script is designed for local testing and bypasses API Gateway authentication. There is a test upload that you uses API gateway below you can also follow. 

#### Step 3.1: Add MinIO to hosts file

Since the presigned URLs use the hostname `minio`, add it to your hosts file:

```bash
# Add minio to hosts file (required for testing from host machine)
sudo sh -c 'echo "127.0.0.1 minio" >> /etc/hosts'

# Verify it was added
cat /etc/hosts | grep minio
# Should show: 127.0.0.1 minio
```

#### Step 3.2: Create a model in Model Catalog

Before uploading artifacts, you need a model to attach them to:

```bash
# Create a model
curl -X POST http://localhost:8001/models \
  -H "Content-Type: application/json" \
  -H "X-User-Id: test_user" \
  -d '{
    "name": "my-rl-model",
    "description": "My reinforcement learning model"
  }'

# Response will show the model ID (usually 1 for first model)

# Example response:
 {
   "name": "my-rl-model",
   "description": "My reinforcement learning model",
   "id": 1,
   "created_by": "test_user",
   "versions": []
 }
```

#### Step 3.3: Create and upload a test file

```bash
# Create a test file (if you don't already have one)
echo "This is a test model file" > test_model.pkl

# Upload it using simple_test.py
cd upload_download_service
python test_simple_client.py upload ../test_model.pkl --model-id 1

# You should see:
# --- Uploading ../test_model.pkl (xx bytes)
# --- Calculating SHA-256 hash...
# --- Hash: sha256:0c1b38e80d6871b6178f1f75a081bf836d514510ad96f896a1c6d1c79d50430e
# --- Initiating upload session...
# --- Upload session created: 241265b1-f8cc-4147-8bdd-2ccad787b099
# --- Uploading x chunks...
# --- Uploading chunk x/y...
# --- Completing upload...
# --- Upload complete!
#    Artifact ID: sha256:0c1b38e80d6871b6178f1f75a081bf836d514510ad96f896a1c6d1c79d50430e
#    Version: X
#    Storage Path: models/x/vX
#    Registered with catalog: True
```

**Note the Artifact ID** - you'll need it for downloading!

#### Step 3.4: Verify the upload

**Option A: Check MinIO Console (Visual)**
```bash
# Open MinIO Console
open http://localhost:9001

# Login: minioadmin / minioadmin_password
# Navigate: Buckets → models → Browse
# You'll see your file stored by its SHA-256 hash
```

**Option B: Check via API**
```bash
# View the model with its versions
curl http://localhost:8001/models/1

# Response shows the new version:
# {
#   "name": "my-rl-model",
#   "versions": [
#     {
#       "version": 1,
#       "storage_path": "models/1/v1",
#       "content_hash": "sha256:0c1b38e80d6871b6178f1f75a081bf836d514510ad96f896a1c6d1c79d50430e",
#       "id": 1,
#       "model_id": 1
#     }
#   ]
# }
```

**Option C: Check Database**
```bash
# Connect to upload database
docker exec -it postgres_db psql -U rllabs -d upload_download_db

# View upload sessions
SELECT upload_id, filename, status, storage_path, file_hash 
FROM upload_sessions 
ORDER BY created_at DESC 
LIMIT 5;

# Exit
\q
```

**Option D: Check MinIO via CLI**
```bash
# List files in models bucket
docker exec -it minio_storage mc ls local/models/

# You should see your file:
# [2025-11-04 16:00:00 UTC]    26B STANDARD sha256:0c1b38e...
```

### 4. Test Download 

```bash
# Use the artifact_id from the upload response
python test_simple_client.py download sha256:<paste the upload sha returned>

# You should see:
# --- Downloading sha256:0c1b38e80d6871b6178f1f75a081bf836d514510ad96f896a1c6d1c79d50430e...
# --- Download URL obtained
# --- Downloading to sha256:0c1b38e... (26 bytes)...
# --- Progress: 100.0%
# --- Download complete: sha256:0c1b38e80d6871b6178f1f75a081bf836d514510ad96f896a1c6d1c79d50430e

# Verify the downloaded file
cat sha256:<paste the upload sha returned>
# Should show: This is a test model file
```

### 5. Test with Larger Files

```bash
# Create a larger test file (5MB)
dd if=/dev/urandom of=large_model.bin bs=1m count=5

# Upload it (will use multiple chunks)
python test_simple_client.py upload large_model.bin --model-id 1

# You should see multiple chunks being uploaded:
# --- Uploading 1 chunks...  (for files < 5MB)
# or
# --- Uploading 2 chunks...  (for files 5-10MB)
```

##  Configuration

### Environment Variables

Key settings (already configured for Docker):
- `MINIO_ENDPOINT=minio:9000`
- `DATABASE_URL=postgresql://rllabs:rllabs_password@postgres/upload_download_db`
- `MODEL_CATALOG_URL=http://model_catalog_service:8000`

Ask me for my .env file if you need it!

### Database Setup

The service automatically creates its database tables on startup. You can verify:

```bash
# Connect to PostgreSQL
docker exec -it postgres_db psql -U rllabs -d upload_download_db

# List tables
\dt

# Should show:
# upload_sessions

# Exit
\q
```

##  API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8002/docs
- **ReDoc**: http://localhost:8002/redoc

### Via API Gateway (Recommended)

The service should be accessed through the API Gateway in production:
- **Upload**: http://localhost:8080/api/uploads
- **Download**: http://localhost:8080/api/downloads

Authentication is handled by the API Gateway (JWT token → X-User-Id header).

## Testing the Integration (Having Issues with integrating with API Gateway 05.11.25)

### Test 1: Upload → Model Catalog Integration

```bash
# Step 1: Generate JWT token (simulates user login)
cd /path/to/rllabs
export JWT_TOKEN=$(python generate_token.py) 

# 1. Create a model in Model Catalog
curl -X POST http://localhost:8080/api/models \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -d '{
    "name": "test-model",
    "description": "Test model for upload service"
  }'

# Note the model ID from response

# Step 3: Upload via API Gateway (simulates frontend)
cd upload_download_service
python test_gateway_client.py upload ../test_model.pkl --model-id 1

# Step 4. Verify version was registered in Model Catalog
curl http://localhost:8080/api/models/<MODEL_ID>/versions
```

### Test 2A: Event Publishing

```bash
# 1. Access RabbitMQ Management UI
open http://localhost:15672
# Login: admin / admin_password

# 2. Go to Exchanges → artifact_events
# 3. Check that exchange exists and is type "topic"

# 4. Create a test queue and bind it:
#    - Queue name: test_queue
#    - Binding: artifact.uploaded

# 5. Upload a file (see Test 1)

# 6. Check test_queue - should have 1 message with upload details
```

### Test 2B: Event Publishing

# Terminal 1 - Create model 2
curl -X POST http://localhost:8001/models \
  -H "Content-Type: application/json" \
  -H "X-User-Id: test_user" \
  -d '{"name":"test-model-2","description":"Second test model"}'

# Terminal 2 - Create a new file
```bash
cd ..
echo "Another test model" > test_model_new.pkl
```

# Terminal 1 - Run event listener
```bash
cd upload_download_service
python test_event_consumer.py # event listening
```

# Terminal 2 - Upload
```bash
python test_simple_client.py ../test_model.pkl --model-id 2 # upload file to model
```

# Terminal 1 - Output
Expected output
================================================================================
--- EVENT RECEIVED
================================================================================

--- Routing Key: artifact.uploaded
--- Event Type: ArtifactUploaded

--- Event Data:
{
  "event_type": "ArtifactUploaded",
  "artifact_id": "sha256:b4e20e2d6294170a445d67ea1f5aa782014bcd6b73bf9ea8d6b475d27b798ab5",
  "model_id": 1,
  "version": 5,
  "storage_path": "models/1/v5",
  "uploaded_by": "test_user",
  "file_size": 22,
  "filename": "test_model_newv2.pkl", # name of file uploaded
  "timestamp": "2025-11-04T22:10:50.687863+00:00Z"
}

--- Highlights:
   - Artifact ID: sha256:b4e20e2d6294170a445d67ea1f5aa782014bcd6b73bf9ea8d6b475d27b798ab5
   - Model ID: 1
   - Version: 5
   - Uploaded by: test_user
   - File size: 22 bytes

================================================================================

### Test 3: Idempotency

```bash
# Upload the same file twice
python test_example_client.py upload test_model.pkl --model-id 1 --user-id test_user
python test_example_client.py upload test_model.pkl --model-id 1 --user-id test_user

# Second upload should be faster (idempotency hit)
# Check logs to see "Returning existing upload session"
```

##  Troubleshooting

### Issue: "Service unavailable"

**Check dependencies:**
```bash
docker-compose ps

# All services should be "healthy" or "running"
# If any are down:
docker-compose logs <service_name>
```

### Issue: "Database connection failed"

**Check PostgreSQL:**
```bash
# View logs
docker-compose logs postgres

# Restart if needed
docker-compose restart postgres
docker-compose restart upload_download_service
```

### Issue: "Failed to connect to MinIO"

**Check MinIO:**
```bash
# View logs
docker-compose logs minio

# Check bucket exists
docker exec -it minio_storage mc ls local/

# Should show: models/
```

### Issue: "Failed to resolve 'minio'" (when using test_simple_client.py)

**This happens when testing from your host machine.** Add minio to your hosts file:

```bash
# Add minio to hosts file
sudo sh -c 'echo "127.0.0.1 minio" >> /etc/hosts'

# Verify
cat /etc/hosts | grep minio
```

**Why this happens:**
- Inside Docker: Services use `minio:9000` (Docker's internal DNS)
- Outside Docker: Your Mac needs to resolve `minio` to `localhost`
- Presigned URLs contain the hostname that MinIO uses

### Issue: "Model Catalog registration failed" or "404 error"

**The model doesn't exist yet.** Create it first:

```bash
# Create a model before uploading artifacts
curl -X POST http://localhost:8001/models \
  -H "Content-Type: application/json" \
  -H "X-User-Id: test_user" \
  -d '{"name":"my-model","description":"My model"}'

# Then retry upload with the returned model ID
python test_simple_client.py upload test.pkl --model-id 1
```

### Issue: "Not authenticated" error

**This happens with example_client.py going through API Gateway.**

For local testing, use `test_simple_client.py` instead:
```bash
# Simple test (no JWT needed)
python simple_test.py upload test.pkl --model-id 1

# Example client (needs JWT via API Gateway)
python test_simple_client.py upload test.pkl --model-id 1 --user-id test_user
```

### Issue: Database "upload_download_db does not exist"

**The db-init service didn't run or failed.**

```bash
# Check db-init logs
docker-compose logs db-init

# Manually create database
docker exec -it postgres_db psql -U rllabs -d postgres -c "CREATE DATABASE upload_download_db;"

# Restart upload service
docker-compose restart upload_download_service
```

## Monitoring

### View Logs

```bash
# All services
docker-compose logs -f

# Just upload service
docker-compose logs -f upload_download_service

# Last 100 lines
docker-compose logs --tail=100 upload_download_service
```

### Check Upload Sessions

```bash
# Connect to database
docker exec -it postgres_db psql -U rllabs -d upload_download_db

# View all upload sessions
SELECT upload_id, filename, status, created_at 
FROM upload_sessions 
ORDER BY created_at DESC 
LIMIT 10;

# Count by status
SELECT status, COUNT(*) 
FROM upload_sessions 
GROUP BY status;
```

### Check MinIO Storage

```bash
# List files in models bucket
docker exec -it minio_storage mc ls local/models/

# Check storage usage
docker exec -it minio_storage mc du local/models/
```



