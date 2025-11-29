import asyncio
import httpx
from fastapi import Request, HTTPException, status
from typing import Optional
import logging

# Try to import circuit breaker, but provide fallback if not available
try:
    from circuitbreaker import circuit, CircuitBreakerError
    CIRCUIT_BREAKER_AVAILABLE = True
except ImportError:
    # Fallback: Create a no-op circuit breaker if package not installed
    CIRCUIT_BREAKER_AVAILABLE = False
    
    class CircuitBreakerError(Exception):
        """Fallback exception if circuitbreaker package not available"""
        pass
    
    def circuit(*args, **kwargs):
        """Fallback no-op circuit breaker decorator"""
        def decorator(func):
            return func
        return decorator

from config import settings

logger = logging.getLogger(__name__)

# Exception for service unavailable errors
class ServiceUnavailableError(HTTPException):
    """Raised when a backend service is unavailable"""
    def __init__(self, service_name: str, detail: str = None):
        self.service_name = service_name
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail or f"Service {service_name} unavailable"
        )

# Circuit breaker configuration per service
service_circuit_breakers = {}

def get_circuit_breaker(service_name: str):
    """Get or create circuit breaker for a service"""
    if service_name not in service_circuit_breakers:
        service_circuit_breakers[service_name] = circuit(
            failure_threshold=5,
            recovery_timeout=30,
            expected_exception=httpx.RequestError
        )
    return service_circuit_breakers[service_name]

async def proxy_request(
    request: Request,
    target_service: str,
    user_id: Optional[str] = None,
    extra_headers: Optional[dict] = None
) -> httpx.Response:
    """
    Forward request to target service with fault tolerance:
    - Circuit breaker to prevent cascading failures
    - Retry with exponential backoff
    - Automatic recovery after failures
    """
    # Build target URL
    path = request.url.path
    # Handle both /api and /public routes
    # Both should result in the same backend path (without /api or /public prefixes)
    if path.startswith("/public/"):
        # Public routes: remove /public prefix (backend doesn't need /api prefix)
        service_path = path.replace("/public", "", 1)
    else:
        # Protected routes: remove /api prefix
        service_path = path.replace("/api", "", 1)
    target_url = f"{target_service}{service_path}"
    
    # Add query parameters
    if request.url.query:
        target_url += f"?{request.url.query}"
    
    # Prepare the headers
    headers = dict(request.headers)
    # Remove host header to avoid conflicts
    headers.pop("host", None)
    
    # Add user context if authenticated
    if user_id:
        headers["X-User-Id"] = user_id

    # Add extra headers if provided
    if extra_headers:
        headers.update(extra_headers)
    
    # Get request body
    body = await request.body()
    
    # Extract service name from URL for circuit breaker
    service_name = target_service.split("//")[-1].split(":")[0] if "//" in target_service else "unknown"
    
    try:
        # Apply circuit breaker (if available)
        if CIRCUIT_BREAKER_AVAILABLE:
            circuit_breaker = get_circuit_breaker(service_name)
            request_func = circuit_breaker(_make_request)
        else:
            # Fallback: use function directly without circuit breaker
            request_func = _make_request
        
        # Retry with exponential backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await request_func(
                    target_url=target_url,
                    headers=headers,
                    body=body,
                    method=request.method
                )
                return response
            except CircuitBreakerError as e:
                logger.warning(f"Circuit breaker open for {service_name}: {e}")
                raise ServiceUnavailableError(
                    service_name=service_name,
                    detail=f"Service {service_name} unavailable (circuit breaker open)"
                )
            except httpx.RequestError as e:
                if attempt < max_retries - 1:
                    # Exponential backoff 1s 2s 4s
                    backoff = 2 ** attempt
                    logger.warning(f"Request to {service_name} failed (attempt {attempt + 1}/{max_retries}): {e}")
                    await asyncio.sleep(backoff)
                    continue
                else:
                    logger.error(f"Request to {service_name} failed after {max_retries} attempts: {e}")
                    raise ServiceUnavailableError(
                        service_name=service_name,
                        detail=f"Service {service_name} unavailable: {str(e)}"
                    )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error proxying to {service_name}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error connecting to service: {str(e)}"
        )

async def _make_request(target_url: str, headers: dict, body: bytes, method: str) -> httpx.Response:
    """Internal helper to make HTTP request"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.request(
            method=method,
            url=target_url,
            headers=headers,
            content=body,
        )

def get_target_service(path: str) -> Optional[str]:
    """
    Determine which backend service to route to based on path
    Special handling: /api/models/{id}/comments routes to collaboration service
    """
    # Special case: model comments go to collaboration service
    if "/comments" in path and path.startswith("/api/models/"):
        return "http://collaboration-service:8000"
    
    # Standard routing: match by prefix
    for route_prefix, service_url in settings.SERVICES.items():
        if path.startswith(route_prefix):
            return service_url
    return None