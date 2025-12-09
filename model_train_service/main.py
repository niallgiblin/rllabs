"""
Training Service Entry Point

Starts the RabbitMQ consumer to process training jobs and FastAPI server for health checks.
"""
import os
import sys
from contextlib import asynccontextmanager

shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'shared'))
if os.path.exists(shared_path) and shared_path not in sys.path:
    sys.path.insert(0, shared_path)

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "training-service")

try:
    from observability import setup_logging, setup_tracing, get_logger
    
    json_output = os.getenv("KUBERNETES_SERVICE_HOST") is not None
    setup_logging(service_name=SERVICE_NAME, json_output=json_output)
    setup_tracing(service_name=SERVICE_NAME)
    logger = get_logger(__name__)
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.warning("Observability module not available")

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
from model_trainer import (
    start_consumer,
    get_rabbitmq_connection_status,
    get_http_client_status,
    JobStatusTracker
)

_consumer_thread = None
_consumer_running = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle"""
    global _consumer_thread, _consumer_running
    
    logger.info("Training Service starting up...")
    
    import threading
    _consumer_running = True
    _consumer_thread = threading.Thread(
        target=start_consumer,
        daemon=True,
        name="RabbitMQConsumer"
    )
    _consumer_thread.start()
    logger.info("RabbitMQ consumer thread started")
    
    yield
    
    logger.info("Training Service shutting down...")
    _consumer_running = False


app = FastAPI(
    title="Training Service",
    description="Processes training jobs from RabbitMQ",
    version="1.0.0",
    lifespan=lifespan
)

try:
    from prometheus_fastapi_instrumentator import Instrumentator
    instrumentator = Instrumentator()
    instrumentator.instrument(app).expose(app)
    logger.info("Prometheus metrics enabled")
except ImportError:
    logger.warning("prometheus-fastapi-instrumentator not available - metrics disabled")

@app.get("/health", tags=["Monitoring"], include_in_schema=False)
async def health_check():
    """
    Fast health check endpoint for Kubernetes probes.
    Returns immediately without checking dependencies to avoid unnecessary network calls.
    """
    return {"status": "ok"}

@app.get("/health/detailed", tags=["Monitoring"], include_in_schema=False)
async def detailed_health_check():
    """
    Detailed health check endpoint with dependency verification.
    Use this for monitoring dashboards, not for Kubernetes probes.
    
    Checks:
    - RabbitMQ connection status
    - HTTP client connectivity
    """
    try:
        rabbitmq_status = get_rabbitmq_connection_status()
        http_status = get_http_client_status()
        
        dependencies = {
            "rabbitmq": rabbitmq_status,
            "http_client": http_status
        }
        
        overall_status = "ok" if rabbitmq_status == "online" else "degraded"
        
        return {
            "service_status": overall_status,
            "dependencies": dependencies
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "service_status": "error",
                "error": str(e)
            }
        )

@app.get("/", tags=["Info"])
async def root():
    """Service info"""
    return {
        "service": "training-service",
        "version": "1.0.0",
        "status": "running"
    }

@app.get("/jobs/{job_id}/status", tags=["Jobs"])
async def get_job_status(job_id: str):
    """
    Get training job status
    
    Returns job status including:
    - status: queued, processing, completed, failed
    - progress: 0.0 to 1.0
    - timestamps: created_at, started_at, completed_at
    - error: error message if failed
    """
    tracker = JobStatusTracker()
    status = tracker.get_status(job_id)
    if status:
        return status
    else:
        raise HTTPException(status_code=404, detail="Job not found")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8003"))
    host = os.getenv("HOST", "0.0.0.0")
    
    logger.info(f"Starting Training Service on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_config=None)
