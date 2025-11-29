#!/bin/bash
# Script to start a training job (assumes services are already running)

set -e

echo "=== Starting Training Job ==="
echo ""

# Check if services are accessible
echo "Checking service health..."
if ! curl -s http://localhost:8001/health > /dev/null 2>&1; then
    echo "ERROR: Model Catalog Service (port 8001) is not accessible"
    exit 1
fi

if ! curl -s http://localhost:8002/health > /dev/null 2>&1; then
    echo "ERROR: Upload/Download Service (port 8002) is not accessible"
    exit 1
fi

if ! curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo "ERROR: API Gateway (port 8080) is not accessible"
    exit 1
fi

echo "✓ All services are accessible"
echo ""

# Step 1: Generate Token
echo "=== Step 1: Generate Token ==="

# Check if TOKEN is already set
if [ ! -z "$TOKEN" ] && [ "$TOKEN" != "" ]; then
    echo "Using TOKEN from environment variable"
else
    # Try to generate token using Python with PyJWT
    # First check if we can import jwt
    if python3 -c "import jwt" 2>/dev/null; then
        echo "Generating token locally with PyJWT..."
        TOKEN=$(python3 << 'PYEOF'
import jwt
from datetime import datetime, timedelta, timezone

SECRET_KEY = "your-secret-key"
ALGORITHM = "HS256"

payload = {
    "sub": "test-user-1",
    "scopes": ["api:read", "api:write"],
    "iat": datetime.now(timezone.utc),
    "exp": datetime.now(timezone.utc) + timedelta(minutes=30)
}

token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
print(token)
PYEOF
)
    elif python -c "import jwt" 2>/dev/null; then
        echo "Generating token locally with PyJWT..."
        TOKEN=$(python << 'PYEOF'
import jwt
from datetime import datetime, timedelta, timezone

SECRET_KEY = "your-secret-key"
ALGORITHM = "HS256"

payload = {
    "sub": "test-user-1",
    "scopes": ["api:read", "api:write"],
    "iat": datetime.now(timezone.utc),
    "exp": datetime.now(timezone.utc) + timedelta(minutes=30)
}

token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
print(token)
PYEOF
)
    fi
    
    # Fallback: try using generate_token.py script
    if [ -z "$TOKEN" ] || [ "$TOKEN" = "" ]; then
        echo "Trying generate_token.py script..."
        if [ -f "generate_token.py" ]; then
            if command -v python3 &> /dev/null && python3 -c "import jwt" 2>/dev/null; then
                TOKEN=$(python3 generate_token.py --user test-user-1 2>/dev/null | tail -1 | tr -d '\r\n')
            elif command -v python &> /dev/null && python -c "import jwt" 2>/dev/null; then
                TOKEN=$(python generate_token.py --user test-user-1 2>/dev/null | tail -1 | tr -d '\r\n')
            fi
        fi
    fi
    
    if [ -z "$TOKEN" ] || [ "$TOKEN" = "" ]; then
        echo "ERROR: Failed to generate token"
        echo ""
        echo "To fix this, install PyJWT and try again:"
        echo "  pip install PyJWT"
        echo "  # or"
        echo "  pip3 install PyJWT"
        echo ""
        echo "Alternatively, generate token manually:"
        echo "  1. Install PyJWT: pip install PyJWT"
        echo "  2. Run: python generate_token.py --user test-user-1"
        echo "  3. Copy the token and run: export TOKEN='your-token-here'"
        echo "  4. Run this script again: ./start_training_job.sh"
        exit 1
    fi
fi

# Verify token is not empty and looks like a JWT
if [ ${#TOKEN} -lt 20 ]; then
    echo "ERROR: Token appears to be invalid (too short)"
    echo "Token length: ${#TOKEN}"
    exit 1
fi

echo "✓ Token generated (length: ${#TOKEN} chars)"
echo ""

# Step 2: Create or Get Model
echo "=== Step 2: Create or Get Model ==="
echo "Making request with token (first 20 chars: ${TOKEN:0:20}...)"

# Try to create model with unique name
TIMESTAMP=$(date +%s)
MODEL_NAME="test-dqn-model-${TIMESTAMP}"

MODEL_RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST http://localhost:8080/api/models \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"$MODEL_NAME\",
    \"description\": \"Test model for training integration\"
  }")

HTTP_CODE=$(echo "$MODEL_RESPONSE" | grep "HTTP_CODE:" | cut -d: -f2)
MODEL_RESPONSE=$(echo "$MODEL_RESPONSE" | grep -v "HTTP_CODE:")

if [ "$HTTP_CODE" != "200" ] && [ "$HTTP_CODE" != "201" ]; then
    # If 409 (conflict), try to get existing model
    if [ "$HTTP_CODE" = "409" ]; then
        echo "Model with name exists, trying to find existing model..."
        # List models and get the first one
        MODELS_RESPONSE=$(curl -s -X GET http://localhost:8080/api/models \
          -H "Authorization: Bearer $TOKEN" 2>/dev/null)
        
        MODEL_ID=$(echo "$MODELS_RESPONSE" | python -c "import sys, json; data = json.load(sys.stdin); models = data if isinstance(data, list) else data.get('items', []); print(models[0]['id'] if models else '')" 2>/dev/null)
        
        if [ ! -z "$MODEL_ID" ] && [ "$MODEL_ID" != "" ]; then
            echo "✓ Using existing model with ID: $MODEL_ID"
        else
            echo "ERROR: Could not find existing model"
            exit 1
        fi
    else
        echo "ERROR: Failed to create model (HTTP $HTTP_CODE)"
        echo "Response: $MODEL_RESPONSE"
        if [ "$HTTP_CODE" = "401" ]; then
            echo ""
            echo "Authentication failed. The token may be invalid."
            echo "Try generating a fresh token:"
            echo "  docker compose exec \$(docker compose ps --format '{{.Name}}' | grep gateway | head -1) python /app/generate_token.py --user test-user-1"
        fi
        exit 1
    fi
else
    MODEL_ID=$(echo "$MODEL_RESPONSE" | python -c "import sys, json; data = json.load(sys.stdin); print(data.get('id', ''))" 2>/dev/null)
    
    if [ -z "$MODEL_ID" ]; then
        echo "ERROR: Could not extract model ID from response"
        echo "Response was: $MODEL_RESPONSE"
        exit 1
    fi
    
    echo "✓ Created model with ID: $MODEL_ID"
fi
echo ""

# Step 3: Create Sample Model Weights (if needed)
echo "=== Step 3: Prepare Model Weights ==="
if [ ! -f "sample_model.pth" ]; then
    echo "Creating sample_model.pth in container..."
    docker compose exec -T model-train-service python -c "
import torch
import torch.nn as nn
layers = [
    nn.Conv2d(in_channels=3, out_channels=16, kernel_size=3, padding=1),
    nn.ReLU(),
    nn.Flatten(),
    nn.Linear(in_features=16 * 10 * 10, out_features=128),
    nn.ReLU(),
    nn.Linear(in_features=128, out_features=4)
]
model = nn.Sequential(*layers)
torch.save(model.state_dict(), '/tmp/sample_model.pth')
print('Created /tmp/sample_model.pth')
" > /dev/null
    docker compose cp model-train-service:/tmp/sample_model.pth ./sample_model.pth
    echo "✓ Created sample_model.pth"
else
    echo "✓ Using existing sample_model.pth"
fi
echo ""

# Step 4: Upload Artifacts
echo "=== Step 4: Upload Training Artifacts ==="

# Helper function to extract artifact ID from upload output
extract_artifact_id() {
    local output="$1"
    local file_desc="$2"
    
    # Try to get from "Artifact ID:" line (successful upload)
    local artifact_id=$(echo "$output" | grep "Artifact ID:" | sed 's/.*Artifact ID: //' | tr -d '\r\n' | xargs)
    
    # If that failed, extract from hash line (idempotency - already uploaded)
    if [ -z "$artifact_id" ] || [ "$artifact_id" = "ID:" ]; then
        local hash=$(echo "$output" | grep "Hash: sha256:" | sed 's/.*Hash: sha256: //' | tr -d '\r\n' | xargs)
        if [ ! -z "$hash" ]; then
            artifact_id="sha256:$hash"
        fi
    fi
    
    # If still empty, check for idempotency message
    if [ -z "$artifact_id" ] || [ "$artifact_id" = "ID:" ]; then
        if echo "$output" | grep -q "Idempotency hit\|already uploaded"; then
            # Extract hash from idempotency message or use the hash from output
            local hash=$(echo "$output" | grep -o "sha256:[a-f0-9]\{64\}" | head -1)
            if [ ! -z "$hash" ]; then
                artifact_id="$hash"
            fi
        fi
    fi
    
    if [ -z "$artifact_id" ] || [ "$artifact_id" = "ID:" ]; then
        echo "ERROR: Failed to extract artifact ID for $file_desc" >&2
        echo "Output: $output" >&2
        return 1
    fi
    
    echo "$artifact_id"
}

echo "Uploading training_config.json..."
CONFIG_OUTPUT=$(python upload_download_service/test_simple_client.py upload training_config.json --model-id $MODEL_ID --user-id test-user-1 2>&1)
CONFIG_ARTIFACT=$(extract_artifact_id "$CONFIG_OUTPUT" "training_config.json")
if [ $? -ne 0 ]; then
    exit 1
fi
echo "✓ Config artifact: $CONFIG_ARTIFACT"

echo "Uploading dataset_config.json..."
DATASET_OUTPUT=$(python upload_download_service/test_simple_client.py upload dataset_config.json --model-id $MODEL_ID --user-id test-user-1 2>&1)
DATASET_ARTIFACT=$(extract_artifact_id "$DATASET_OUTPUT" "dataset_config.json")
if [ $? -ne 0 ]; then
    exit 1
fi
echo "✓ Dataset artifact: $DATASET_ARTIFACT"

echo "Uploading sample_model.pth..."
MODEL_OUTPUT=$(python upload_download_service/test_simple_client.py upload sample_model.pth --model-id $MODEL_ID --user-id test-user-1 2>&1)
MODEL_ARTIFACT=$(extract_artifact_id "$MODEL_OUTPUT" "sample_model.pth")
if [ $? -ne 0 ]; then
    exit 1
fi
echo "✓ Model artifact: $MODEL_ARTIFACT"
echo ""

# Step 5: Trigger Training Job
echo "=== Step 5: Trigger Training Job ==="
JOB_RESPONSE=$(curl -s -X POST http://localhost:8080/api/training-jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"config_artifact_id\": \"$CONFIG_ARTIFACT\",
    \"dataset_artifact_id\": \"$DATASET_ARTIFACT\",
    \"model_artifact_id\": \"$MODEL_ARTIFACT\",
    \"model_id\": $MODEL_ID
  }")

if [ $? -ne 0 ] || [ -z "$JOB_RESPONSE" ]; then
    echo "ERROR: Failed to trigger training job"
    echo "Response: $JOB_RESPONSE"
    exit 1
fi

echo "$JOB_RESPONSE" | python -m json.tool 2>/dev/null || echo "$JOB_RESPONSE"
echo ""

JOB_ID=$(echo "$JOB_RESPONSE" | python -c "import sys, json; data = json.load(sys.stdin); print(data.get('job_id', ''))" 2>/dev/null)

if [ -z "$JOB_ID" ]; then
    echo "WARNING: Could not extract job_id from response"
    echo "Response was: $JOB_RESPONSE"
    JOB_ID="unknown"
fi

echo "=== Training Job Started Successfully ==="
echo "Job ID: $JOB_ID"
echo "Model ID: $MODEL_ID"
echo ""
echo "Monitor training progress with:"
echo "  docker compose logs -f model-train-service"
echo ""
echo "Or filter for this job:"
echo "  docker compose logs model-train-service | grep '$JOB_ID'"
echo ""
echo "Check model versions after training:"
echo "  curl http://localhost:8080/api/models/$MODEL_ID/versions"

