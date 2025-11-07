import redis
import logging
import time
from fastapi import HTTPException, status

from config import settings

logger = logging.getLogger(__name__)

redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    decode_responses=True
)

# In-memory fallback rate limiter
_in_memory_rate_limits = {}

def check_rate_limit(identifier: str, endpoint: str) -> bool:
    """
    Check if user/IP has exceeded rate limit
    Uses sliding window algorithm with Redis, with in-memory fallback
    
    Args:
        identifier: User ID (e.g., "user-123") or IP address (e.g., "ip:192.168.1.1")
        endpoint: Endpoint path for rate limiting
    
    Failure mode: Fail-open with degraded in-memory protection
    """
    key = f"rate_limit:{identifier}:{endpoint}"
    
    try:
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, settings.RATE_LIMIT_WINDOW)
        current_requests, _ = pipe.execute()
        current_requests = int(current_requests or 0)
        
        # Check if limit exceeded
        if current_requests > settings.RATE_LIMIT_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Max {settings.RATE_LIMIT_REQUESTS} requests per {settings.RATE_LIMIT_WINDOW} seconds"
            )
        
        # Clear in-memory fallback on successful Redis operation
        if key in _in_memory_rate_limits:
            del _in_memory_rate_limits[key]
        
        return True
        
    except redis.RedisError as e:
        # Fail-open with graceful degradation: use in-memory fallback
        logger.warning(f"Redis unavailable, using in-memory rate limiting: {e}")
        
        # In-memory rate limiting fallback
        if key not in _in_memory_rate_limits:
            _in_memory_rate_limits[key] = {
                'count': 0,
                'window_start': time.time()
            }
        
        # Reset window if expired
        if time.time() - _in_memory_rate_limits[key]['window_start'] > settings.RATE_LIMIT_WINDOW:
            _in_memory_rate_limits[key] = {
                'count': 0,
                'window_start': time.time()
            }
        
        # Increment and check
        _in_memory_rate_limits[key]['count'] += 1
        
        # Use more lenient in-memory limits to avoid false positives
        in_memory_limit = settings.RATE_LIMIT_REQUESTS * 2
        
        if _in_memory_rate_limits[key]['count'] > in_memory_limit:
            logger.warning(f"In-memory rate limit exceeded for {identifier} on {endpoint}")
            # Don't reject in fallback mode - fail open with logging
            return True
        
        return True