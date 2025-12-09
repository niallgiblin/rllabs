"""
Distributed Tracing Module
==========================

Provides OpenTelemetry-based distributed tracing with Jaeger export.

This module sets up:
1. Automatic FastAPI instrumentation (creates spans for all HTTP requests)
2. Automatic httpx instrumentation (traces outgoing HTTP calls)
3. Automatic SQLAlchemy instrumentation (traces database queries)
4. Trace context propagation (W3C Trace Context headers)

How Distributed Tracing Works:
------------------------------
1. When a request enters the API Gateway, a unique trace_id is generated
2. This trace_id is propagated via headers (traceparent) to downstream services
3. Each service creates "spans" representing operations (HTTP calls, DB queries)
4. All spans with the same trace_id are collected and visualised in Jaeger

Usage:
    from shared.observability import setup_tracing, get_tracer
    
    # At service startup (before FastAPI app is created)
    setup_tracing(service_name="api-gateway")
    
    # In the code (for custom spans)
    tracer = get_tracer(__name__)
    
    with tracer.start_as_current_span("custom_operation") as span:
        span.set_attribute("user.id", user_id)
        # ... the code ...

Environment Vars:
    OTEL_EXPORTER_OTLP_ENDPOINT: Jaeger OTLP endpoint (default: http://jaeger:4317)
    OTEL_TRACES_SAMPLER: Sampling strategy (default: always_on)
    TRACING_ENABLED: Set to "false" to disable tracing
"""

import os
import logging
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_tracer_provider = None
_initialized = False


def setup_tracing(
    service_name: str,
    jaeger_endpoint: Optional[str] = None,
    sample_rate: float = 1.0
) -> bool:
    """
    Configure distributed tracing with Jaeger export.
    
    Call this once at service startup, BEFORE creating the FastAPI app.
    
    Args:
        service_name: Name of the service (e.g., "api-gateway")
        jaeger_endpoint: OTLP endpoint for Jaeger (default from env or http://jaeger:4317)
        sample_rate: Fraction of traces to sample (0.0 to 1.0, default 1.0 = all)
    
    Returns:
        True if tracing was initialised, False if disabled or unavailable
    
    Example:
        # At the very start of main.py
        from shared.observability import setup_tracing
        
        setup_tracing(service_name="api-gateway")
        
        app = FastAPI(...)  # Now auto-instrumented
    """
    global _tracer_provider, _initialized
    
    if os.getenv("TRACING_ENABLED", "true").lower() == "false":
        logger.info("Distributed tracing is disabled (TRACING_ENABLED=false)")
        return False
    
    if os.getenv("TRACING_ENABLED", "true").lower() == "false":
        logger.info("Tracing disabled via TRACING_ENABLED=false")
        return False
    
    if _initialized:
        logger.warning("Tracing already initialized, skipping")
        return True
    
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
        
        endpoint = jaeger_endpoint or os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://jaeger:4317"
        )
        
        resource = Resource.create({
            SERVICE_NAME: service_name,
            "deployment.environment": os.getenv("ENVIRONMENT", "development")
        })
        
        sampler = TraceIdRatioBased(sample_rate)
        
        _tracer_provider = TracerProvider(
            resource=resource,
            sampler=sampler
        )
        
        otlp_exporter = OTLPSpanExporter(
            endpoint=endpoint,
            insecure=True,  
            timeout=1.0  
        )
        
        _tracer_provider.add_span_processor(
            BatchSpanProcessor(
                otlp_exporter,
                max_queue_size=2048, 
                export_timeout_millis=1000, 
                schedule_delay_millis=5000  
            )
        )
        
        trace.set_tracer_provider(_tracer_provider)
        
        _instrument_libraries()
        
        _initialized = True
        logger.info(
            f"Distributed tracing initialized",
            extra={
                "event": "tracing_initialized",
                "jaeger_endpoint": endpoint,
                "sample_rate": sample_rate
            }
        )
        
        return True
        
    except ImportError as e:
        logger.warning(
            f"OpenTelemetry packages not installed, tracing disabled: {e}"
        )
        return False
    except Exception as e:
        logger.error(f"Failed to initialize tracing: {e}")
        return False


def _instrument_libraries() -> None:
    """
    Auto-instrument common libraries for automatic span creation.
    
    This adds tracing to:
    - FastAPI (incoming HTTP requests)
    - httpx (outgoing HTTP requests)
    - SQLAlchemy (database queries)
    - Redis (cache operations)
    - pika (RabbitMQ operations)
    """
    
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor().instrument()
        logger.debug("FastAPI auto-instrumentation enabled")
    except ImportError:
        logger.debug("FastAPI instrumentation not available")
    except Exception as e:
        logger.warning(f"Failed to instrument FastAPI: {e}")
    
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
        logger.debug("httpx auto-instrumentation enabled")
    except ImportError:
        logger.debug("httpx instrumentation not available")
    except Exception as e:
        logger.warning(f"Failed to instrument httpx: {e}")
    
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        SQLAlchemyInstrumentor().instrument()
        logger.debug("SQLAlchemy auto-instrumentation enabled")
    except ImportError:
        logger.debug("SQLAlchemy instrumentation not available")
    except Exception as e:
        logger.warning(f"Failed to instrument SQLAlchemy: {e}")
    
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
        logger.debug("Redis auto-instrumentation enabled")
    except ImportError:
        logger.debug("Redis instrumentation not available")
    except Exception as e:
        logger.warning(f"Failed to instrument Redis: {e}")
    
    try:
        from opentelemetry.instrumentation.pika import PikaInstrumentor
        PikaInstrumentor().instrument()
        logger.debug("Pika (RabbitMQ) auto-instrumentation enabled")
    except ImportError:
        logger.debug("Pika instrumentation not available")
    except Exception as e:
        logger.warning(f"Failed to instrument Pika: {e}")


def get_tracer(name: str):
    """
    Get a tracer instance for creating custom spans.
    
    Use this when you want to trace specific operations beyond
    the auto-instrumented ones (HTTP, DB, etc.)
    
    Args:
        name: Tracer name (typically __name__)
    
    Returns:
        OpenTelemetry Tracer instance, or NoOpTracer if tracing disabled
    
    Example:
        tracer = get_tracer(__name__)
        
        with tracer.start_as_current_span("process_file") as span:
            span.set_attribute("file.size", file_size)
            span.set_attribute("file.name", filename)
            # ... process file ...
    """
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


class _NoOpTracer:
    """No-op tracer for when OpenTelemetry is not available."""
    
    @contextmanager
    def start_as_current_span(self, name: str, **kwargs):
        yield _NoOpSpan()
    
    def start_span(self, name: str, **kwargs):
        return _NoOpSpan()


class _NoOpSpan:
    """No-op span for when OpenTelemetry is not available."""
    
    def set_attribute(self, key: str, value) -> None:
        pass
    
    def set_status(self, status) -> None:
        pass
    
    def record_exception(self, exception) -> None:
        pass
    
    def end(self) -> None:
        pass
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass


def inject_trace_context(headers: dict) -> dict:
    """
    Inject trace context into outgoing request headers.
    
    Use this when making HTTP calls without httpx (which is auto-instrumented).
    
    Args:
        headers: Existing headers dict to modify
    
    Returns:
        Headers dict with trace context added
    
    Example:
        headers = {"Authorization": "Bearer ..."}
        headers = inject_trace_context(headers)
        response = requests.get(url, headers=headers)
    """
    try:
        from opentelemetry.propagate import inject
        inject(headers)
    except ImportError:
        pass
    return headers


def extract_trace_context(headers: dict):
    """
    Extract trace context from incoming request headers.
    
    Typically not needed as FastAPI instrumentation handles this automatically.
    
    Args:
        headers: Request headers dict
    
    Returns:
        OpenTelemetry Context object
    """
    try:
        from opentelemetry.propagate import extract
        return extract(headers)
    except ImportError:
        return None
