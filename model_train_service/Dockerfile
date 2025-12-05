FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY model_train_service/requirements.txt .
COPY shared/requirements.txt shared_requirements.txt

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r shared_requirements.txt || true

# Copy service code
COPY model_train_service/ .

# Copy shared observability module
COPY shared/ /app/shared/

# The training service runs both FastAPI (for health checks) and RabbitMQ consumer
CMD ["python", "main.py"]

