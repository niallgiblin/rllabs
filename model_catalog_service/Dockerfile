FROM python:3.11-slim
WORKDIR /app

# Copy requirements first for better caching
COPY model_catalog_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared observability module
COPY shared/ /app/shared/

# Copy application code
COPY model_catalog_service/ /app/

# Set Python path to include shared module
ENV PYTHONPATH=/app:/app/shared

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
