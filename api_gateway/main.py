from fastapi import FastAPI, Request, HTTPException, status, Form, Depends, APIRouter
from fastapi.responses import Response, RedirectResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import logging
import time
import json
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

# Try to import optional dependencies with fallbacks
try:
    from config import settings
except ImportError:
    # Fallback settings
    class Settings:
        GATEWAY_URL = "http://localhost:8080"
        ALLOWED_ORIGINS = ["http://localhost:3000", "http://localhost:8080"]
        MONITORING_ENABLED = True
        MONITORING_ENABLED = True
    
    settings = Settings()

try:
    from rate_limiter import check_rate_limit, RateLimitExceeded
except ImportError:
    # Fallback rate limiter
    class RateLimitExceeded(Exception):
        def __init__(self, retry_after: int = 60):
            self.retry_after = retry_after
            super().__init__(f"Rate limit exceeded. Try again in {retry_after} seconds")
    
    def check_rate_limit(user_id: str, endpoint: str, method: str = "GET"):
        # Basic no-op rate limiter for development
        pass

try:
    from proxy import proxy_request, get_target_service, ServiceUnavailableError
except ImportError:
    # Fallback proxy
    class ServiceUnavailableError(Exception):
        def __init__(self, service_name: str):
            self.service_name = service_name
            super().__init__(f"Service unavailable: {service_name}")
    
    async def proxy_request(request: Request, target_service: str, user_id: str = None, extra_headers: dict = None):
        # Fallback response
        return JSONResponse(
            content={"error": "Proxy service not configured", "service": target_service},
            status_code=503
        )
    
    def get_target_service(path: str) -> Optional[str]:
        # Simple routing logic
        if path.startswith("/api/models"):
            return "http://model_catalog_service:8000" 
        elif path.startswith("/api/uploads") or path.startswith("/api/downloads"):
            return "http://upload_download_service:8002" 

# Placeholder for internal router if no mtls
internal_router = APIRouter()

@internal_router.get("/health")
async def internal_health():
    return {"status": "internal service healthy"}

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Application Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info("API Gateway starting...")
    logger.info(f"Monitoring: {settings.MONITORING_ENABLED}")
    
    yield
    
    # Shutdown
    logger.info("API Gateway shutting down...")

# FastAPI App
app = FastAPI(
    title="API Gateway", 
    version="1.0.0",
    description="Enhanced API Gateway with Rate Limiting",
    lifespan=lifespan
)

# Include routers
app.include_router(internal_router, prefix="/internal", tags=["internal"])

# Basic health endpoint for liveness/readiness probes
@app.get("/health")
async def health():
    return {"status": "ok"}

# Try to mount static files
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
    templates = Jinja2Templates(directory="templates")
except Exception:
    logger.warning("Static files or templates not available - running in minimal mode")
    templates = None

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses"""
    start_time = time.time()
    
    response = await call_next(request)
    
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    
    # Performance monitoring
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    
    # Log request
    logger.info(f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")
    
    return response

# Error Handlers
@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded errors"""
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "error": "rate_limit_exceeded",
            "error_description": f"Rate limit exceeded. Try again in {exc.retry_after} seconds",
            "retry_after": exc.retry_after
        },
        headers={"Retry-After": str(exc.retry_after)}
    )

@app.exception_handler(ServiceUnavailableError)
async def service_unavailable_handler(request: Request, exc: ServiceUnavailableError):
    """Handle service unavailable errors"""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": "service_unavailable",
            "error_description": f"Backend service unavailable: {exc.service_name}",
            "service": exc.service_name
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail.lower().replace(" ", "_") if exc.detail else "unknown_error",
            "error_description": exc.detail
        }
    )

# Protected Route Proxy
from jwt_auth import get_current_user

@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
)
async def proxy_to_service(
    request: Request,
    path: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Protected proxy - requires authentication
    Routes requests to appropriate backend service
    """
    # Authenticated context
    user_id = current_user.get("user_id", "anonymous")
    client_id = user_id
    scopes = current_user.get("scopes", [])
    scope = " ".join(scopes) if isinstance(scopes, list) else str(scopes)
    
    # Check rate limit
    endpoint = f"/{path.split('/')[0]}" if path else "/"
    check_rate_limit(user_id, endpoint)
    
    # Determine target service
    full_path = f"/api/{path}"
    target_service = get_target_service(full_path)
    
    if not target_service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found"
        )
    
    # Log request
    logger.info(
        f"User {user_id} (client: {client_id}) -> {request.method} {full_path} -> {target_service}"
    )
    
    try:
        # Proxy request with user context
        response = await proxy_request(
            request=request,
            target_service=target_service,
            user_id=user_id,
            extra_headers={
                'X-User-ID': user_id,
                'X-User-Id': user_id,
                'X-Client-ID': client_id,
                'X-Scope': scope,
                'X-Forwarded-For': request.headers.get('X-Forwarded-For', ''),
                'X-Real-IP': request.client.host if request.client else ''
            }
        )
        
        # Return response
        content_bytes = getattr(response, "content", None)
        if content_bytes is None:
            content_bytes = getattr(response, "body", b"")
        return Response(
            content=content_bytes,
            status_code=response.status_code,
            headers=dict(response.headers)
        )
        
    except ServiceUnavailableError as e:
        logger.error(f"Service unavailable: {target_service} - {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Proxy error for {target_service}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Error connecting to backend service: {str(e)}"
        )


# Public Route Proxy
@app.api_route(
    "/public/{path:path}",
    methods=["GET", "OPTIONS", "HEAD"]
)
async def public_proxy(request: Request, path: str):
    """
    Public proxy - no authentication required
    Use for public endpoints like GET /models or health checks
    """
    full_path = f"/api/{path}"
    target_service = get_target_service(full_path)
    
    if not target_service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found"
        )
    
    # Check rate limit for public endpoints by IP
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(f"ip:{client_ip}", f"/public/{path}")
    
    logger.info(f"Public request from {client_ip} -> {request.method} {full_path} -> {target_service}")
    
    try:
        response = await proxy_request(
            request=request,
            target_service=target_service,
            extra_headers={
                'X-Forwarded-For': request.headers.get('X-Forwarded-For', ''),
                'X-Real-IP': client_ip
            }
        )
        
        content_bytes = getattr(response, "content", None)
        if content_bytes is None:
            content_bytes = getattr(response, "body", b"")
        return Response(
            content=content_bytes,
            status_code=response.status_code,
            headers=dict(response.headers)
        )
        
    except ServiceUnavailableError as e:
        logger.error(f"Service unavailable: {target_service} - {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Public proxy error for {target_service}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Error connecting to backend service: {str(e)}"
        )


# Documentation
@app.get("/")
async def root():
    """Redirect to API docs"""
    return RedirectResponse(url="/docs")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8080,
        log_level="info",
        access_log=True
    )