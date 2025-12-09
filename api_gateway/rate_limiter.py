import redis.asyncio as redis
from redis.sentinel import Sentinel
import logging
import time
import os
from fastapi import HTTPException, status
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

class RateLimitExceeded(HTTPException):
    """Exception raised when rate limit is exceeded"""
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Try again in {retry_after} seconds"
        )

REDIS_SENTINEL_HOSTS = os.getenv("REDIS_SENTINEL_HOSTS", "")
REDIS_SENTINEL_MASTER_NAME = os.getenv("REDIS_SENTINEL_MASTER_NAME", "mymaster")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

redis_client: Optional[redis.Redis] = None
sentinel: Optional[Sentinel] = None

async def get_redis_client() -> Optional[redis.Redis]:
    """Get or create async Redis client (lazy initialization)"""
    global redis_client
    
    if redis_client is not None:
        try:
            await redis_client.ping()
            return redis_client
        except Exception:
            # Connection lost, recreate
            try:
                await redis_client.aclose()
            except Exception:
                pass
            redis_client = None
    
    try:
        redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            decode_responses=True,
            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
            socket_timeout=0.5,  
            socket_connect_timeout=0.5,
            retry_on_timeout=False, 
            health_check_interval=30,
            max_connections=50
        )
        await redis_client.ping()
        logger.info(f"Using async Redis connection to {settings.REDIS_HOST}:{settings.REDIS_PORT}")
        return redis_client
    except Exception as e:
        logger.warning(f"Failed to initialize async Redis: {e}")
        return None

_in_memory_rate_limits = {}

_local_rate_limit_cache: dict = {}
_local_cache_ttl = 2.0  
_last_cache_cleanup = time.time()
_cache_cleanup_interval = 60.0  

def _cleanup_local_cache():
    """Remove expired entries from local cache"""
    global _last_cache_cleanup
    current_time = time.time()
    
    if current_time - _last_cache_cleanup < _cache_cleanup_interval:
        return
    
    _last_cache_cleanup = current_time
    expired_keys = [
        key for key, (_, exp_time) in _local_rate_limit_cache.items()
        if exp_time < current_time
    ]
    for key in expired_keys:
        _local_rate_limit_cache.pop(key, None)

async def check_rate_limit(identifier: str, endpoint: str) -> bool:
    """
    Check if user/IP has exceeded rate limit (ASYNC)
    Uses sliding window algorithm with async Redis, with in-memory fallback
    
    Args:
        identifier: User ID (e.g., "user-123") or IP address (e.g., "ip:192.168.1.1")
        endpoint: Endpoint path for rate limiting
    
    Failure mode: Fail-open with degraded in-memory protection
    """
    key = f"rate_limit:{identifier}:{endpoint}"
    cache_key = (identifier, endpoint)
    current_time = time.time()
    
    _cleanup_local_cache()
    if cache_key in _local_rate_limit_cache:
        cached_count, exp_time = _local_rate_limit_cache[cache_key]
        if exp_time > current_time:
            if cached_count < settings.RATE_LIMIT_REQUESTS:
                return True
    
    try:
        client = await get_redis_client()
        if client is None:
            raise redis.ConnectionError("Redis client not available")
        
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, settings.RATE_LIMIT_WINDOW)
        results = await pipe.execute()
        current_requests = int(results[0] or 0)
        
        if current_requests >= settings.RATE_LIMIT_REQUESTS:
            logger.warning(f"Rate limit exceeded for {identifier} on {endpoint}: {current_requests}/{settings.RATE_LIMIT_REQUESTS}")
            raise RateLimitExceeded(retry_after=settings.RATE_LIMIT_WINDOW)
        
        _local_rate_limit_cache[cache_key] = (current_requests, current_time + _local_cache_ttl)
        
        if key in _in_memory_rate_limits:
            del _in_memory_rate_limits[key]
        
        return True
        
    except (redis.TimeoutError, redis.ConnectionError, redis.RedisError) as e:
        logger.debug(f"Redis rate limiting failed ({type(e).__name__}): {e}. Using in-memory fallback.")
        
        if key not in _in_memory_rate_limits:
            _in_memory_rate_limits[key] = {
                'count': 0,
                'window_start': time.time()
            }
        
        if time.time() - _in_memory_rate_limits[key]['window_start'] > settings.RATE_LIMIT_WINDOW:
            _in_memory_rate_limits[key] = {
                'count': 0,
                'window_start': time.time()
            }
        
        _in_memory_rate_limits[key]['count'] += 1
        
        in_memory_limit = settings.RATE_LIMIT_REQUESTS * 2
        
        if _in_memory_rate_limits[key]['count'] > in_memory_limit:
            logger.warning(f"In-memory rate limit exceeded for {identifier} on {endpoint}")
            return True
        
        return True
    except RateLimitExceeded:
        raise
    except Exception as e:
        # Unexpectd error - log but don't block requests
        logger.error(f"Unexpected error in rate limiter: {e}", exc_info=True)
        return True

async def close_redis_client():
    """Close Redis client on shutdown"""
    global redis_client
    if redis_client:
        await redis_client.aclose()
        redis_client = None