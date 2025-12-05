FROM python:3.11-slim
WORKDIR /app

# Copy requirements first for better caching
COPY api_gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared observability module
COPY shared/ /app/shared/

# Copy application code
COPY api_gateway/ /app/

# Set Python path to include shared module
ENV PYTHONPATH=/app:/app/shared
# Optimized Uvicorn configuration:
# - workers: 1 (HPA handles scaling, not workers)
# - loop uvloop: Faster async event loop
# - log-level warning: Reduce logging overhead
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--loop", "uvloop", "--log-level", "warning"]