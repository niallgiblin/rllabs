"""
API Gateway - Main Application
==============================
Central entry point for all API requests with authentication, rate limiting, and routing.
"""

# Observability Setup
import os
import sys

shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'shared'))
if os.path.exists(shared_path) and shared_path not in sys.path:
    sys.path.insert(0, shared_path)

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "api-gateway")

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
    logger.warning("Observability module not available, using basic logging")



from fastapi import FastAPI, Request, HTTPException, status, Depends, APIRouter
from fastapi.responses import Response, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import time
import asyncio
import logging
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

try:
    from config import settings
except ImportError:
    class Settings:
        GATEWAY_URL = "http://localhost:8080"
        ALLOWED_ORIGINS = [
            "http://localhost:3000", 
            "http://localhost:8080",
            "http://localhost:5173",
            "http://127.0.0.1:5173"
        ]
        MONITORING_ENABLED = True
        MONITORING_ENABLED = True
    
    settings = Settings()

try:
    from rate_limiter import check_rate_limit, RateLimitExceeded
except ImportError:
    class RateLimitExceeded(Exception):
        def __init__(self, retry_after: int = 60):
            self.retry_after = retry_after
            super().__init__(f"Rate limit exceeded. Try again in {retry_after} seconds")
    
    def check_rate_limit(user_id: str, endpoint: str, method: str = "GET"):
        pass

try:
    from proxy import proxy_request, get_target_service, ServiceUnavailableError
except (ImportError, Exception) as e:
    class ServiceUnavailableError(Exception):
        def __init__(self, service_name: str):
            self.service_name = service_name
            super().__init__(f"Service unavailable: {service_name}")
    
    async def proxy_request(request: Request, target_service: str, user_id: str = None, extra_headers: dict = None):
        return JSONResponse(
            content={"error": "Proxy service not configured", "service": target_service},
            status_code=503
        )
    
    def get_target_service(path: str) -> Optional[str]:
        if "/comments" in path and path.startswith("/api/models/"):
            return "http://collaboration-service:8000"
        elif path.startswith("/api/versions"):
            return "http://model-catalog-service:8000"
        elif path.startswith("/api/comments"):
            return "http://collaboration-service:8000"
        elif path.startswith("/api/models"):
            return "http://model-catalog-service:8000"
        elif path.startswith("/api/uploads"):
            return "http://upload-download-service:8002"
        elif path.startswith("/api/downloads"):
            return "http://upload-download-service:8002"
        elif path.startswith("/api/training-jobs"):
            return "http://upload-download-service:8002" 

internal_router = APIRouter()

@internal_router.get("/health")
async def internal_health():
    return {"status": "internal service healthy"}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    
    logger.info("API Gateway starting...")
    logger.info(f"Monitoring: {settings.MONITORING_ENABLED}")
    
    yield
    
    from proxy import close_http_client
    await close_http_client()
    logger.info("HTTP client pool closed")
    
    try:
        from rate_limiter import close_redis_client
        await close_redis_client()
        logger.info("Redis client closed")
    except Exception as e:
        logger.warning(f"Error closing Redis client: {e}")
    
    logger.info("API Gateway shutting down...")


app = FastAPI(
    title="API Gateway", 
    version="1.0.0",
    description="Enhanced API Gateway with Rate Limiting",
    lifespan=lifespan
)

@app.get("/health", include_in_schema=False)
async def health():
    """Lightweight health check for Kubernetes probes"""
    return {"status": "ok"}

try:
    from prometheus_fastapi_instrumentator import Instrumentator
    instrumentator = Instrumentator()
    instrumentator.instrument(app).expose(app)
    logger.info("Prometheus metrics enabled")
except ImportError:
    logger.warning("prometheus-fastapi-instrumentator not available - metrics disabled")

app.include_router(internal_router, prefix="/internal", tags=["internal"])


try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
    templates = Jinja2Templates(directory="templates")
except Exception:
    logger.warning("Static files or templates not available - running in minimal mode")
    templates = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses with request timeout"""
    start_time = time.time()
    
    path = request.url.path
    if path in ["/health", "/internal/health"]:
        timeout = 1.0  
    elif path == "/metrics":
        timeout = 5.0  
    elif path.startswith("/api/uploads") and request.method == "POST":
        if "/complete" in path:
            timeout = 60.0  
        else:
            timeout = 45.0  
    elif "/downloads/" in path:
        timeout = 30.0  
    elif "/training-jobs" in path:
        timeout = 15.0  
    elif path.startswith("/api/models") and request.method == "GET":
        timeout = 30.0  
    else:
        timeout = 10.0 
    
    try:
        response = await asyncio.wait_for(
            call_next(request),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning(f"{request.method} {request.url.path} - Request timeout (>{timeout}s)")
        return JSONResponse(
            status_code=504,
            content={"error": "Request timeout", "detail": f"Request took longer than {timeout} seconds"}
        )
    
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    
    if process_time > 0.5 and request.url.path not in ["/health", "/metrics", "/internal/health"]:
        logger.info(f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")
    
    return response

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

from jwt_auth import get_current_user, bearer_scheme, create_access_token
from fastapi.security import HTTPAuthorizationCredentials

import hashlib
import json
from pathlib import Path
from typing import Optional, Dict

_users_file = Path(__file__).parent / "users.json"

def _load_users() -> Dict[str, Dict]:
    """Load users from file or return empty dict"""
    if _users_file.exists():
        try:
            with open(_users_file, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def _save_users(users: Dict[str, Dict]):
    """Save users to file"""
    try:
        with open(_users_file, 'w') as f:
            json.dump(users, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save users to file: {e}")

def _hash_password(password: str) -> str:
    """Simple password hashing (for dev - use bcrypt in production)"""
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(username: str, email: str, password: str, is_admin: bool = False) -> Dict:
    """Create a new user"""
    users = _load_users()
    
    if username in users:
        raise ValueError("Username already exists")
    
    for user in users.values():
        if user.get('email') == email:
            raise ValueError("Email already exists")
    
    user = {
        'username': username,
        'email': email,
        'password_hash': _hash_password(password),
        'is_admin': is_admin,
    }
    
    users[username] = user
    _save_users(users)
    
    return {
        'username': username,
        'email': email,
        'is_admin': is_admin
    }

def authenticate_user(username: str, password: str) -> Optional[Dict]:
    """Authenticate a user and return user info"""
    users = _load_users()
    
    if username not in users:
        return None
    
    user = users[username]
    password_hash = _hash_password(password)
    
    if user['password_hash'] != password_hash:
        return None
    
    return {
        'username': username,
        'email': user['email'],
        'is_admin': user.get('is_admin', False)
    }

def get_user(username: str) -> Optional[Dict]:
    """Get user by username"""
    users = _load_users()
    if username in users:
        user = users[username].copy()
        user.pop('password_hash', None) 
        return user
    return None

from pydantic import BaseModel, EmailStr

class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    is_admin: bool = False

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/auth/register")
async def register(request: RegisterRequest):
    """Register a new user"""
    try:
        user = create_user(
            username=request.username,
            email=request.email,
            password=request.password,
            is_admin=request.is_admin
        )
        
        scopes = ["api:read", "api:write"]
        if request.is_admin:
            scopes.append("api:admin")
        
        token = create_access_token({
            "sub": request.username,
            "scopes": scopes
        })
        
        return {
            "user": user,
            "token": token,
            "token_type": "bearer"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@app.post("/api/auth/login")
async def login(request: LoginRequest):
    """Login and get JWT token"""
    user = authenticate_user(request.username, request.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    scopes = ["api:read", "api:write"]
    if user.get('is_admin'):
        scopes.append("api:admin")
    
    token = create_access_token({
        "sub": user['username'],
        "scopes": scopes
    })
    
    return {
        "user": {
            "username": user['username'],
            "email": user['email'],
            "is_admin": user.get('is_admin', False)
        },
        "token": token,
        "token_type": "bearer"
    }

@app.get("/api/auth/me")
async def get_current_user_info(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Get current user info"""
    username = current_user.get("user_id")
    user = get_user(username)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return {
        "username": user['username'],
        "email": user['email'],
        "is_admin": user.get('is_admin', False),
        "scopes": current_user.get("scopes", [])
    }

def get_optional_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> Optional[Dict[str, Any]]:
    """
    Optional authentication - returns user if token is valid, None otherwise
    Used for endpoints that allow both authenticated and unauthenticated access
    """
    if credentials is None or not credentials.scheme or credentials.scheme.lower() != "bearer":
        return None
    
    try:
        from jwt_auth import verify_token
        payload = verify_token(credentials.credentials)
        user_id = payload.get("sub")
        if user_id is None:
            return None
        return {"user_id": user_id, "scopes": payload.get("scopes", [])}
    except Exception:
        return None

@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
)
async def proxy_to_service(
    request: Request,
    path: str,
    current_user: Optional[Dict[str, Any]] = Depends(get_optional_user)
):
    """
    Smart proxy - conditionally requires authentication
    - GET /api/models* : Public (no auth required)
    - GET /api/downloads* : Public (no auth required)
    - POST/PUT/DELETE /api/models* : Requires auth
    - All /api/uploads* : Requires auth
    - DELETE /api/downloads* : Requires auth (for deletion)
    """
    full_path = f"/api/{path}"
    
    requires_auth = False
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        requires_auth = True
    elif path.startswith("uploads"):
        requires_auth = True
    elif path.startswith("downloads") and request.method == "DELETE":
        requires_auth = True
    elif path.startswith("models/") and "/ownership" in path:
        requires_auth = True
    
    if requires_auth:
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_id = current_user.get("user_id", "anonymous")
        client_id = user_id
        scopes = current_user.get("scopes", [])
        scope = " ".join(scopes) if isinstance(scopes, list) else str(scopes)
    else:
        if current_user is not None:
            user_id = current_user.get("user_id", None)
        else:
            user_id = None
        client_id = request.client.host if request.client else "unknown"
        scopes = []
        scope = ""
    
    endpoint = full_path  
    rate_limit_key = user_id if user_id else f"ip:{client_id}"
    try:
        await asyncio.wait_for(
            check_rate_limit(rate_limit_key, endpoint),
            timeout=0.05  
        )
    except asyncio.TimeoutError:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Rate limiter timeout for {rate_limit_key} - allowing request")
    except RateLimitExceeded:
        raise
    except Exception as e:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Rate limiter error (allowing request): {e}")
    
    try:
        target_service = get_target_service(full_path)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[ROUTING] Service lookup for '{full_path}': {target_service}")
    except Exception as e:
        logger.error(f"[ROUTING] Error in get_target_service: {e}", exc_info=True)
        target_service = None
    
    if not target_service:
        logger.error(f"[ROUTING] No service found for path '{full_path}' (original path: '{path}')")
        logger.error(f"[ROUTING] Available services: {list(settings.SERVICES.keys())}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found"
        )
    
    if logger.isEnabledFor(logging.DEBUG):
        auth_status = "authenticated" if user_id else "anonymous"
        auth_status_detail = f"User {user_id}" if user_id else f"Anonymous (IP: {client_id})"
        logger.debug(
            f"[{auth_status.upper()}] {auth_status_detail} -> {request.method} {full_path} -> {target_service}"
        )
    
    try:
        extra_headers = {
            'X-Client-ID': client_id,
            'X-Forwarded-For': request.headers.get('X-Forwarded-For', ''),
            'X-Real-IP': request.client.host if request.client else ''
        }
        
        if user_id:
            from jwt_auth import is_admin
            is_admin_user = is_admin(scopes)
            extra_headers.update({
                'X-User-ID': user_id,
                'X-User-Id': user_id,
                'X-Scope': scope,
                'X-Is-Admin': 'true' if is_admin_user else 'false',
            })
        
        response = await proxy_request(
            request=request,
            target_service=target_service,
            user_id=user_id,
            extra_headers=extra_headers
        )
        
        if response.status_code >= 500:
            logger.error(
                f"Backend {target_service} returned {response.status_code} for {request.method} {full_path}",
                extra={
                    "status_code": response.status_code,
                    "method": request.method,
                    "path": full_path,
                    "target_service": target_service,
                    "response_preview": (response.text[:200] if hasattr(response, 'text') else None)
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
        logger.error(f"Proxy error for {target_service}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Error connecting to backend service: {str(e)}"
        )


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
    
    client_ip = request.client.host if request.client else "unknown"
    try:
        await asyncio.wait_for(
            check_rate_limit(f"ip:{client_ip}", f"/public/{path}"),
            timeout=0.05  
        )
    except asyncio.TimeoutError:
        logger.debug(f"Rate limiter timeout for ip:{client_ip} - allowing request")
    except RateLimitExceeded:
        raise
    except Exception as e:
        logger.debug(f"Rate limiter error (allowing request): {e}")
    
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