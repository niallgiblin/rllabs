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

# Global HTTP client with connection pooling for better performance
# This is reused across all requests instead of creating new clients each time
_http_client: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    """Get or create the shared HTTP client with connection pooling"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                30.0,      # Total timeout (kept from Phase 1 - needed for upload operations)
                connect=5.0  # Connection timeout (kept from Phase 1)
            ),
            limits=httpx.Limits(
                max_connections=200,      # REVERTED: Back to original (was 500 in Phase 2)
                max_keepalive_connections=100,  # REVERTED: Back to original (was 250 in Phase 2)
                keepalive_expiry=30.0     # Keep connections alive for 30s
            ),
            http2=True  # HTTP/2 for better multiplexing (requires httpx[http2])
        )
    return _http_client

async def close_http_client():
    """Close the HTTP client (call on shutdown)"""
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None

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
        
        # Retry with exponential backoff for transient failures
        # Reduced retries to prevent cascading delays - fail fast instead
        max_retries = 1  # Reduced from 3: fail fast instead of retrying into timeout
        for attempt in range(max_retries):
            try:
                response = await request_func(
                    target_url=target_url,
                    headers=headers,
                    body=body,
                    method=request.method
                )
                
                # Don't retry on 5xx errors - fail fast to prevent timeout cascades
                # Backend services should handle retries internally if needed
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
    except httpx.HTTPStatusError as e:
        # Backend returned an error status code
        logger.error(
            f"Backend service {service_name} returned error: {e.response.status_code}",
            extra={
                "service": service_name,
                "status_code": e.response.status_code,
                "url": target_url,
                "response_text": e.response.text[:500] if hasattr(e.response, 'text') else None
            }
        )
        # Pass through the backend's status code
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Backend service error: {e.response.status_code}"
        )
    except Exception as e:
        logger.error(
            f"Unexpected error proxying to {service_name}: {e}",
            exc_info=True,
            extra={
                "service": service_name,
                "url": target_url,
                "error_type": type(e).__name__
            }
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error connecting to service: {str(e)}"
        )

async def _make_request(target_url: str, headers: dict, body: bytes, method: str) -> httpx.Response:
    """Internal helper to make HTTP request using shared connection pool"""
    client = get_http_client()
    response = await client.request(
        method=method,
        url=target_url,
        headers=headers,
        content=body,
    )
    
    # Log 5xx errors for debugging
    if response.status_code >= 500:
        logger.error(
            f"Backend service returned {response.status_code} for {method} {target_url}",
            extra={
                "status_code": response.status_code,
                "method": method,
                "url": target_url,
                "response_preview": response.text[:500] if hasattr(response, 'text') else None
            }
        )
    
    return response

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