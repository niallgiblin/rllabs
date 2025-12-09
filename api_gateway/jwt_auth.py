import jwt
from datetime import datetime, timedelta
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, List, Dict
import hashlib
import time

SECRET_KEY = "your-secret-key" #Hardcoded in local, in production this would use a .env var
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

bearer_scheme = HTTPBearer(auto_error=False)

# JWT verification cache (in-memory LRU cache)
# Cache key: SHA-256 hash of token (for security, not storing tokens)
# Cache value: (payload, expiration_timestamp)
# TTL: Token expiration time (from JWT payload)
_jwt_cache: Dict[str, tuple] = {}
_jwt_cache_max_size = 1000  # Max 1000 cached tokens
_jwt_cache_cleanup_interval = 300  # Clean up expired tokens every 5 minutes
_last_cache_cleanup = time.time()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """
    Creates a new JWT access token.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def _get_token_hash(token: str) -> str:
    """Get SHA-256 hash of token for cache key (security: don't store tokens)"""
    return hashlib.sha256(token.encode()).hexdigest()

def _cleanup_expired_tokens():
    """Remove expired tokens from cache (periodic cleanup)"""
    global _last_cache_cleanup
    current_time = time.time()
    
    # Only cleanup every N seconds to avoid overhead
    if current_time - _last_cache_cleanup < _jwt_cache_cleanup_interval:
        return
    
    _last_cache_cleanup = current_time
    expired_keys = [
        key for key, (_, exp_time) in _jwt_cache.items()
        if exp_time < current_time
    ]
    for key in expired_keys:
        _jwt_cache.pop(key, None)

def verify_token(token: str) -> dict:
    """
    Verifies a JWT token and returns its payload.
    Uses in-memory LRU cache to avoid re-verifying the same token.
    
    Raises HTTPException if the token is invalid or expired.
    """
    # Get cache key (hash of token for security)
    cache_key = _get_token_hash(token)
    current_time = time.time()
    
    # Check cache first
    if cache_key in _jwt_cache:
        payload, exp_time = _jwt_cache[cache_key]
        # Check if token is still valid (not expired)
        if exp_time > current_time:
            return payload
        else:
            # Token expired, remove from cache
            _jwt_cache.pop(cache_key, None)
    
    # Cache miss or expired - verify token
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Extract expiration time from payload
        exp_timestamp = payload.get("exp")
        if exp_timestamp:
            exp_time = float(exp_timestamp)
        else:
            # No expiration in payload, use default TTL
            exp_time = current_time + (ACCESS_TOKEN_EXPIRE_MINUTES * 60)
        
        # Only cache if token is not expired
        if exp_time > current_time:
            # Cleanup expired tokens periodically
            _cleanup_expired_tokens()
            
            # Enforce max cache size (LRU eviction)
            if len(_jwt_cache) >= _jwt_cache_max_size:
                # Remove oldest 10% of entries (simple eviction)
                sorted_items = sorted(_jwt_cache.items(), key=lambda x: x[1][1])
                for key, _ in sorted_items[:max(1, _jwt_cache_max_size // 10)]:
                    _jwt_cache.pop(key, None)
            
            # Cache the verified token
            _jwt_cache[cache_key] = (payload, exp_time)
        
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    """
    To get the current user from a JWT token.
    """
    if credentials is None or not credentials.scheme or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    payload = verify_token(token)
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"user_id": user_id, "scopes": payload.get("scopes", [])}

def has_permission(required_scopes: List[str]):
    """
    To check if the current user has the required scopes.
    """
    async def _has_permission(current_user: dict = Depends(get_current_user)):
        user_scopes: List[str] = current_user.get("scopes", [])
        if not all(scope in user_scopes for scope in required_scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user
    return _has_permission

def is_admin(user_scopes: List[str]) -> bool:
    """
    Check if user has admin privileges.
    
    Args:
        user_scopes: List of scopes from JWT token (e.g., ["api:read", "api:write", "api:admin"])
    
    Returns:
        True if user has admin scope, False otherwise
    """
    return "api:admin" in user_scopes if user_scopes else False
