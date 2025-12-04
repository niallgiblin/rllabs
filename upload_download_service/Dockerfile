FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better caching
COPY upload_download_service/requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared observability module
COPY shared/ /app/shared/

# Copy application code
COPY upload_download_service/ /app/

# Set Python path to include shared module
ENV PYTHONPATH=/app:/app/shared

# Expose port
EXPOSE 8002

# Run the application with optimized settings
# - workers: 1 (HPA handles scaling, not workers)
# - loop uvloop: Faster event loop implementation (installed with uvicorn[standard])
# - log-level warning: Reduce logging overhead during health checks
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1", "--loop", "uvloop", "--log-level", "warning"]