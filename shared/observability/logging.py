"""
Structured Logging Module
=========================

Provides JSON-structured logging with automatic trace context injection.
This enables powerful log queries in Loki/Grafana:
- Filter by trace_id to see all logs for a request
- Filter by user_id, service, endpoint, etc.
- Correlate logs with traces in Jaeger

Usage:
    from shared.observability import setup_logging, get_logger
    
    # At service startup
    setup_logging(service_name="api-gateway")
    
    # In your code
    logger = get_logger(__name__)
    logger.info("User logged in", extra={"user_id": "123", "action": "login"})

Output (JSON):
    {
        "timestamp": "2024-01-15T10:30:00.123Z",
        "level": "INFO",
        "service": "api-gateway",
        "message": "User logged in",
        "user_id": "123",
        "action": "login",
        "trace_id": "abc123...",
        "span_id": "def456..."
    }
"""

import logging
import json
import sys
import os
from datetime import datetime, timezone
from typing import Optional, Any, Dict


# Global service name (set by setup_logging)
_SERVICE_NAME: str = "unknown"


class StructuredJsonFormatter(logging.Formatter):
    """
    Custom formatter that outputs JSON-structured logs.
    
    Automatically includes:
    - timestamp (ISO 8601 UTC)
    - level (INFO, ERROR, etc.)
    - service name
    - message
    - trace_id and span_id (from OpenTelemetry context)
    - Any extra fields passed to the logger
    """
    
    def format(self, record: logging.LogRecord) -> str:
        # Base log structure
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": _SERVICE_NAME,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add source location for errors
        if record.levelno >= logging.WARNING:
            log_entry["source"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName
            }
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        # Inject trace context from OpenTelemetry (if available)
        trace_context = _get_trace_context()
        if trace_context:
            log_entry.update(trace_context)
        
        # Add any extra fields passed to the logger
        # Skip internal logging fields
        skip_fields = {
            'name', 'msg', 'args', 'created', 'filename', 'funcName',
            'levelname', 'levelno', 'lineno', 'module', 'msecs',
            'pathname', 'process', 'processName', 'relativeCreated',
            'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
            'message', 'taskName'
        }
        
        for key, value in record.__dict__.items():
            if key not in skip_fields and not key.startswith('_'):
                # Handle non-serializable objects
                try:
                    json.dumps(value)
                    log_entry[key] = value
                except (TypeError, ValueError):
                    log_entry[key] = str(value)
        
        return json.dumps(log_entry)


def _get_trace_context() -> Optional[Dict[str, str]]:
    """
    Extract trace_id and span_id from OpenTelemetry context.
    
    This allows correlating logs with traces in Jaeger.
    Returns None if OpenTelemetry is not installed or no active trace.
    """
    try:
        from opentelemetry import trace
        
        span = trace.get_current_span()
        if span and span.is_recording():
            ctx = span.get_span_context()
            if ctx.is_valid:
                return {
                    "trace_id": format(ctx.trace_id, '032x'),
                    "span_id": format(ctx.span_id, '016x'),
                }
    except ImportError:
        pass  # OpenTelemetry not installed
    except Exception:
        pass  # Any other error, fail silently
    
    return None


def setup_logging(
    service_name: str,
    level: str = "INFO",
    json_output: bool = True
) -> None:
    """
    Configure structured logging for the service.
    
    Call this once at service startup, before creating any loggers.
    
    Args:
        service_name: Name of the service (e.g., "api-gateway")
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_output: If True, output JSON. If False, use human-readable format.
    
    Example:
        # At the start of main.py
        from shared.observability import setup_logging
        setup_logging(service_name="api-gateway", level="INFO")
    """
    global _SERVICE_NAME
    _SERVICE_NAME = service_name
    
    # Get the root logger
    root_logger = logging.getLogger()
    
    # Clear any existing handlers
    root_logger.handlers.clear()
    
    # Set log level (allow override from environment)
    env_level = os.getenv("LOG_LEVEL", level).upper()
    root_logger.setLevel(getattr(logging, env_level, logging.INFO))
    
    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(root_logger.level)
    
    if json_output:
        # Use JSON formatter for production (Loki-friendly)
        handler.setFormatter(StructuredJsonFormatter())
    else:
        # Human-readable format for local development
        formatter = logging.Formatter(
            f'%(asctime)s | {service_name} | %(levelname)s | %(name)s | %(message)s'
        )
        handler.setFormatter(formatter)
    
    root_logger.addHandler(handler)
    
    # Reduce noise from common libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("aiobotocore").setLevel(logging.WARNING)
    # Suppress pika's verbose internal error logging (deque errors are handled gracefully)
    logging.getLogger("pika").setLevel(logging.WARNING)
    
    # Log startup message
    root_logger.info(
        f"Structured logging initialized",
        extra={
            "event": "logging_initialized",
            "log_level": env_level,
            "json_output": json_output
        }
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the given name.
    
    Use __name__ to get a logger named after your module:
        logger = get_logger(__name__)
    
    Args:
        name: Logger name (typically __name__)
    
    Returns:
        Logger instance with structured formatting
    """
    return logging.getLogger(name)


# Convenience class for adding context to all logs in a scope
class LogContext:
    """
    Context manager for adding fields to all logs within a scope.
    
    Usage:
        with LogContext(request_id="abc123", user_id="user1"):
            logger.info("Processing request")  # Includes request_id and user_id
    """
    
    _context: Dict[str, Any] = {}
    
    def __init__(self, **kwargs):
        self.fields = kwargs
        self.old_factory = None
    
    def __enter__(self):
        self.old_factory = logging.getLogRecordFactory()
        fields = self.fields
        
        def record_factory(*args, **kwargs):
            record = self.old_factory(*args, **kwargs)
            for key, value in fields.items():
                setattr(record, key, value)
            return record
        
        logging.setLogRecordFactory(record_factory)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        logging.setLogRecordFactory(self.old_factory)
        return False

