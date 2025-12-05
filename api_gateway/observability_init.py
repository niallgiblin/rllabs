"""
Observability Initialization for API Gateway
=============================================

This module shows how to integrate the shared observability module.
Import this at the very start of main.py, BEFORE creating the FastAPI app.

Usage in main.py:
    # At the TOP of the file, before other imports
    from observability_init import logger
    
    # Then create your FastAPI app (it will be auto-instrumented)
    app = FastAPI(...)
"""

import os
import sys

# Add shared module to Python path
# This allows importing from shared/observability
shared_path = os.path.join(os.path.dirname(__file__), '..', 'shared')
if shared_path not in sys.path:
    sys.path.insert(0, shared_path)

# Service configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "api-gateway")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
TRACING_ENABLED = os.getenv("TRACING_ENABLED", "true").lower() == "true"

# Initialize structured logging
try:
    from observability import setup_logging, get_logger
    
    # Use JSON logging in production (Kubernetes), human-readable locally
    json_output = os.getenv("KUBERNETES_SERVICE_HOST") is not None
    
    setup_logging(
        service_name=SERVICE_NAME,
        level=LOG_LEVEL,
        json_output=json_output
    )
    logger = get_logger(__name__)
    logger.info(f"Structured logging initialized for {SERVICE_NAME}")
    
except ImportError:
    # Fallback to basic logging if shared module not available
    import logging
    logging.basicConfig(level=LOG_LEVEL)
    logger = logging.getLogger(__name__)
    logger.warning("Shared observability module not found, using basic logging")

# Initialize distributed tracing
if TRACING_ENABLED:
    try:
        from observability import setup_tracing
        
        tracing_initialized = setup_tracing(
            service_name=SERVICE_NAME,
            sample_rate=1.0  # Sample all traces in development
        )
        
        if tracing_initialized:
            logger.info(f"Distributed tracing initialized for {SERVICE_NAME}")
        else:
            logger.info("Distributed tracing disabled or unavailable")
            
    except ImportError:
        logger.warning("OpenTelemetry packages not installed, tracing disabled")
else:
    logger.info("Distributed tracing disabled via TRACING_ENABLED=false")


# Export logger for use in main.py
__all__ = ['logger', 'SERVICE_NAME']

