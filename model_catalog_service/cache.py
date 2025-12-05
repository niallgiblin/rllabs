"""
Redis Cache Module for Model Catalog Service
=============================================

Implements the Cache-Aside (Lazy Loading) pattern:
1. Check cache first on reads
2. On cache miss, load from database and populate cache
3. Invalidate cache on writes

Key Design Decisions:
- TTL-based expiration (5 min default) for automatic staleness management
- Prefix-based keys for easy pattern-based invalidation
- JSON serialization for complex objects
- Fail-open: Cache failures don't break the service

Cache Key Patterns:
- models:list           -> List of all models
- models:{id}           -> Single model details
- models:{id}:versions  -> List of versions for a model
- models:{id}:latest    -> Latest version path for a model
- versions:hash:{hash}  -> Version lookup by content hash
"""

import redis
import json
import os
import logging
from typing import Optional, Any, List
from functools import wraps

# Use orjson for faster JSON serialization (20-40% CPU reduction)
try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    ORJSON_AVAILABLE = False
    import json as orjson  # Fallback to standard json

logger = logging.getLogger(__name__)

# Redis configuration
REDIS_HOST = os.getenv("REDIS_HOST", "redis-master")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_SENTINEL_HOSTS = os.getenv("REDIS_SENTINEL_HOSTS", "")
REDIS_SENTINEL_MASTER_NAME = os.getenv("REDIS_SENTINEL_MASTER_NAME", "mymaster")
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))  # 5 minutes default - balance between freshness and performance

# Different TTLs for different data types (optimized for read-heavy workloads)
MODEL_CACHE_TTL = int(os.getenv("MODEL_CACHE_TTL", "1800"))  # 30 minutes for models (read-heavy, rarely change)
VERSION_CACHE_TTL = int(os.getenv("VERSION_CACHE_TTL", "1800"))  # 30 minutes for versions (read-heavy, rarely change)
LIST_CACHE_TTL = int(os.getenv("LIST_CACHE_TTL", "300"))  # 5 minutes for lists (more dynamic)
OWNERSHIP_CACHE_TTL = int(os.getenv("OWNERSHIP_CACHE_TTL", "1800"))  # 30 minutes for ownership checks

# Cache key prefixes
PREFIX = "model_catalog"
MODELS_LIST_KEY = f"{PREFIX}:models:list"
MODEL_KEY = f"{PREFIX}:models"  # + :{id}
VERSIONS_KEY = f"{PREFIX}:versions"  # + :model:{id} or :hash:{hash}


class CacheClient:
    """
    Redis cache client with connection pooling and fail-open semantics.
    
    Implements Circuit Breaker pattern: after N failures, temporarily
    bypasses cache to avoid cascading failures.
    """
    
    def __init__(self):
        self._client: Optional[redis.Redis] = None
        self._failure_count = 0
        self._max_failures = 5
        self._circuit_open = False
        # Cache hit/miss tracking for monitoring
        self._hit_count = 0
        self._miss_count = 0
        self._hit_count_by_endpoint = {}  # endpoint -> hit count
        self._miss_count_by_endpoint = {}  # endpoint -> miss count
        
    @property
    def client(self) -> Optional[redis.Redis]:
        """Lazy initialization of Redis client with connection pooling (Sentinel-aware)"""
        if self._client is None:
            try:
                # Try Sentinel first if configured
                if REDIS_SENTINEL_HOSTS and REDIS_SENTINEL_MASTER_NAME:
                    from redis.sentinel import Sentinel
                    
                    sentinel_hosts = []
                    for host_port in REDIS_SENTINEL_HOSTS.split(","):
                        host_port = host_port.strip()
                        if ":" in host_port:
                            host, port = host_port.split(":")
                            sentinel_hosts.append((host, int(port)))
                        else:
                            sentinel_hosts.append((host_port, 26379))
                    
                    sentinel = Sentinel(
                        sentinel_hosts,
                        socket_timeout=0.5,  # Reduced timeout for faster failures
                        socket_connect_timeout=0.5,
                        password=REDIS_PASSWORD if REDIS_PASSWORD else None
                    )
                    
                    # Get master connection - Redis Sentinel handles connection pooling automatically
                    # The master_for() method returns a client with built-in connection pooling
                    self._client = sentinel.master_for(
                        REDIS_SENTINEL_MASTER_NAME,
                        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                        db=0,
                        decode_responses=True,
                        socket_timeout=0.5,  # Fast timeout for cache lookups
                        socket_connect_timeout=0.5,
                        retry_on_timeout=False
                    )
                    
                    # Test connection
                    self._client.ping()
                    logger.info(f"✅ Cache connected via Redis Sentinel ({len(sentinel_hosts)} sentinels), master: {REDIS_SENTINEL_MASTER_NAME}")
                else:
                    # Fallback to direct connection - Redis client has built-in connection pooling
                    # Create connection pool explicitly for better performance
                    from redis.connection import ConnectionPool
                    pool = ConnectionPool(
                        host=REDIS_HOST,
                        port=REDIS_PORT,
                        password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                        max_connections=50,
                        retry_on_timeout=False,
                        socket_timeout=0.5,
                        socket_connect_timeout=0.5,
                        decode_responses=True
                    )
                    self._client = redis.Redis(connection_pool=pool)
                    # Test connection
                    self._client.ping()
                    logger.info(f"Cache connected to Redis at {REDIS_HOST}:{REDIS_PORT} with connection pooling")
                
                self._failure_count = 0
                self._circuit_open = False
            except Exception as e:
                logger.warning(f"Cache connection failed: {e}")
                self._client = None
        return self._client
    
    def _record_failure(self):
        """Track failures for circuit breaker"""
        self._failure_count += 1
        if self._failure_count >= self._max_failures:
            self._circuit_open = True
            logger.warning(f"Cache circuit breaker OPEN after {self._failure_count} failures")
    
    def _record_success(self):
        """Reset failure count on success"""
        if self._failure_count > 0:
            self._failure_count = 0
            if self._circuit_open:
                self._circuit_open = False
                logger.info("Cache circuit breaker CLOSED")
    
    def get(self, key: str, endpoint: str = None) -> Optional[Any]:
        """
        Get value from cache.
        Returns None on cache miss or failure (fail-open).
        
        Args:
            key: Cache key
            endpoint: Optional endpoint name for tracking (e.g., "GET /models/{id}")
        """
        if self._circuit_open:
            return None
            
        try:
            client = self.client
            if client is None:
                return None
                
            value = client.get(key)
            if value:
                self._record_success()
                # Increment counters (thread-safe increment)
                self._hit_count += 1
                if endpoint:
                    if endpoint not in self._hit_count_by_endpoint:
                        self._hit_count_by_endpoint[endpoint] = 0
                    self._hit_count_by_endpoint[endpoint] += 1
                logger.debug(f"Cache HIT: {key} (total hits: {self._hit_count})")
                # Use orjson for faster deserialization
                if ORJSON_AVAILABLE:
                    # orjson.loads expects bytes, but Redis with decode_responses=True returns str
                    # Encode to bytes if it's a string
                    if isinstance(value, str):
                        value = value.encode('utf-8')
                    return orjson.loads(value)
                else:
                    return json.loads(value)
            # Cache miss - increment counters
            self._miss_count += 1
            if endpoint:
                if endpoint not in self._miss_count_by_endpoint:
                    self._miss_count_by_endpoint[endpoint] = 0
                self._miss_count_by_endpoint[endpoint] += 1
            logger.debug(f"Cache MISS: {key} (total misses: {self._miss_count})")
            return None
        except Exception as e:
            logger.warning(f"Cache GET failed for {key}: {e}")
            self._record_failure()
            return None
    
    def set(self, key: str, value: Any, ttl: int = None) -> bool:
        """
        Set value in cache with TTL.
        Returns False on failure (fail-open).
        """
        if self._circuit_open:
            return False
            
        try:
            client = self.client
            if client is None:
                return False
                
            ttl = ttl or CACHE_TTL
            # Use orjson for faster serialization (20-40% CPU reduction)
            if ORJSON_AVAILABLE:
                serialized = orjson.dumps(value, default=str).decode('utf-8')
            else:
                serialized = json.dumps(value, default=str)
            client.setex(key, ttl, serialized)
            self._record_success()
            logger.debug(f"Cache SET: {key} (TTL: {ttl}s)")
            return True
        except Exception as e:
            logger.warning(f"Cache SET failed for {key}: {e}")
            self._record_failure()
            return False
    
    def delete(self, key: str) -> bool:
        """Delete a specific key"""
        if self._circuit_open:
            return False
            
        try:
            client = self.client
            if client is None:
                return False
                
            client.delete(key)
            self._record_success()
            logger.debug(f"Cache DELETE: {key}")
            return True
        except Exception as e:
            logger.warning(f"Cache DELETE failed for {key}: {e}")
            self._record_failure()
            return False
    
    def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern.
        Uses SCAN for production-safe iteration.
        """
        if self._circuit_open:
            return 0
            
        try:
            client = self.client
            if client is None:
                return 0
                
            deleted = 0
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
                if keys:
                    client.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
            
            self._record_success()
            if deleted > 0:
                logger.info(f"Cache INVALIDATE: {pattern} ({deleted} keys)")
            return deleted
        except Exception as e:
            logger.warning(f"Cache DELETE PATTERN failed for {pattern}: {e}")
            self._record_failure()
            return 0
    
    def get_stats(self) -> dict:
        """Get cache statistics for monitoring"""
        try:
            client = self.client
            if client is None:
                return {"status": "disconnected"}
            
            info = client.info("stats")
            total_requests = self._hit_count + self._miss_count
            hit_rate = round(
                self._hit_count / max(total_requests, 1) * 100,
                2
            ) if total_requests > 0 else 0.0
            
            # Calculate hit rates per endpoint
            endpoint_stats = {}
            for endpoint in set(list(self._hit_count_by_endpoint.keys()) + list(self._miss_count_by_endpoint.keys())):
                hits = self._hit_count_by_endpoint.get(endpoint, 0)
                misses = self._miss_count_by_endpoint.get(endpoint, 0)
                total = hits + misses
                endpoint_stats[endpoint] = {
                    "hits": hits,
                    "misses": misses,
                    "total": total,
                    "hit_rate": round(hits / max(total, 1) * 100, 2) if total > 0 else 0.0
                }
            
            return {
                "status": "connected",
                "circuit_breaker": "open" if self._circuit_open else "closed",
                "failure_count": self._failure_count,
                "hits": info.get("keyspace_hits", 0),
                "misses": info.get("keyspace_misses", 0),
                "hit_rate": round(
                    info.get("keyspace_hits", 0) / 
                    max(info.get("keyspace_hits", 0) + info.get("keyspace_misses", 0), 1) * 100, 
                    2
                ),
                "application_hits": self._hit_count,
                "application_misses": self._miss_count,
                "application_hit_rate": hit_rate,
                "endpoint_stats": endpoint_stats
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}


# Singleton cache client
_cache_client: Optional[CacheClient] = None

def get_cache() -> CacheClient:
    """Get or create singleton cache client"""
    global _cache_client
    if _cache_client is None:
        _cache_client = CacheClient()
    return _cache_client


# ============================================================================
# Cache Key Helpers
# ============================================================================

def model_key(model_id: int) -> str:
    """Cache key for a single model"""
    return f"{MODEL_KEY}:{model_id}"

def model_versions_key(model_id: int) -> str:
    """Cache key for model versions list"""
    return f"{VERSIONS_KEY}:model:{model_id}"

def model_latest_key(model_id: int) -> str:
    """Cache key for latest version path"""
    return f"{MODEL_KEY}:{model_id}:latest"

def version_hash_key(content_hash: str) -> str:
    """Cache key for version lookup by hash"""
    # Normalize hash
    if content_hash.startswith("sha256:"):
        content_hash = content_hash[7:]
    return f"{VERSIONS_KEY}:hash:{content_hash}"


# ============================================================================
# Cache Operations (High-Level API)
# ============================================================================

def cache_models_list(models: List[dict]) -> None:
    """Cache the models list (legacy - for backward compatibility)"""
    get_cache().set(MODELS_LIST_KEY, models)

def get_cached_models_list() -> Optional[List[dict]]:
    """Get cached models list (legacy - for backward compatibility)"""
    return get_cache().get(MODELS_LIST_KEY, endpoint="GET /models")

def cache_models_list_page(page: int, page_size: int, models: List[dict], total: int, ttl: int = LIST_CACHE_TTL) -> None:
    """Cache a paginated page of models"""
    cache_key = f"{MODELS_LIST_KEY}:page:{page}:size:{page_size}"
    cache_data = {
        "items": models,
        "total": total,
        "page": page,
        "page_size": page_size
    }
    get_cache().set(cache_key, cache_data, ttl=ttl)

def get_cached_models_list_page(page: int, page_size: int) -> Optional[dict]:
    """Get cached paginated page of models"""
    cache_key = f"{MODELS_LIST_KEY}:page:{page}:size:{page_size}"
    return get_cache().get(cache_key, endpoint=f"GET /models?page={page}&page_size={page_size}")

def cache_model(model_id: int, model_data: dict, ttl: int = MODEL_CACHE_TTL) -> None:
    """Cache a single model"""
    get_cache().set(model_key(model_id), model_data, ttl=ttl)

def get_cached_model(model_id: int) -> Optional[dict]:
    """Get cached model"""
    return get_cache().get(model_key(model_id), endpoint=f"GET /models/{model_id}")

def cache_model_versions(model_id: int, versions: List[dict], ttl: int = VERSION_CACHE_TTL) -> None:
    """Cache model versions list"""
    get_cache().set(model_versions_key(model_id), versions, ttl=ttl)

def get_cached_model_versions(model_id: int) -> Optional[List[dict]]:
    """Get cached model versions"""
    return get_cache().get(model_versions_key(model_id), endpoint=f"GET /models/{model_id}/versions")

def cache_model_latest(model_id: int, latest_path: dict, ttl: int = VERSION_CACHE_TTL) -> None:
    """Cache latest version path"""
    get_cache().set(model_latest_key(model_id), latest_path, ttl=ttl)

def get_cached_model_latest(model_id: int) -> Optional[dict]:
    """Get cached latest version path"""
    return get_cache().get(model_latest_key(model_id), endpoint=f"GET /models/{model_id}/latest")

def cache_version_by_hash(content_hash: str, version_data: dict, ttl: int = VERSION_CACHE_TTL) -> None:
    """Cache version by content hash"""
    get_cache().set(version_hash_key(content_hash), version_data, ttl=ttl)

def get_cached_version_by_hash(content_hash: str) -> Optional[dict]:
    """Get cached version by hash"""
    return get_cache().get(version_hash_key(content_hash), endpoint="GET /versions/by-hash/{hash}")

def model_ownership_key(model_id: int, user_id: str) -> str:
    """Cache key for model ownership check"""
    return f"{PREFIX}:ownership:model:{model_id}:user:{user_id}"

def cache_model_ownership(model_id: int, user_id: str, ownership_data: dict, ttl: int = OWNERSHIP_CACHE_TTL) -> None:
    """Cache model ownership check result"""
    get_cache().set(model_ownership_key(model_id, user_id), ownership_data, ttl=ttl)

def get_cached_model_ownership(model_id: int, user_id: str) -> Optional[dict]:
    """Get cached model ownership check result"""
    return get_cache().get(model_ownership_key(model_id, user_id), endpoint=f"GET /models/{model_id}/ownership")


# ============================================================================
# Cache Invalidation
# ============================================================================

def invalidate_models_list() -> None:
    """Invalidate the models list cache"""
    get_cache().delete(MODELS_LIST_KEY)
    logger.info("Invalidated models list cache")

def invalidate_model(model_id: int) -> None:
    """Invalidate all caches for a specific model"""
    cache = get_cache()
    cache.delete(model_key(model_id))
    cache.delete(model_latest_key(model_id))
    cache.delete(model_versions_key(model_id))
    # Also invalidate the list since model data changed
    cache.delete(MODELS_LIST_KEY)
    logger.info(f"Invalidated cache for model {model_id}")

def invalidate_model_versions(model_id: int) -> None:
    """Invalidate version-related caches for a model"""
    cache = get_cache()
    cache.delete(model_latest_key(model_id))
    cache.delete(model_versions_key(model_id))
    logger.info(f"Invalidated version cache for model {model_id}")

def invalidate_model_ownership(model_id: int) -> None:
    """Invalidate ownership caches for a model (when ownership changes)"""
    # Use pattern matching to delete all ownership entries for this model
    cache = get_cache()
    pattern = f"{PREFIX}:ownership:model:{model_id}:user:*"
    cache.delete_pattern(pattern)
    logger.info(f"Invalidated ownership cache for model {model_id}")


# ============================================================================
# Decorator for Cache-Aside Pattern
# ============================================================================

def cached(key_func, ttl: int = None):
    """
    Decorator implementing cache-aside pattern.
    
    Usage:
        @cached(lambda model_id: model_key(model_id))
        def get_model(model_id: int):
            return db.query(Model).get(model_id)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = key_func(*args, **kwargs)
            
            # Try cache first
            cached_value = get_cache().get(cache_key)
            if cached_value is not None:
                return cached_value
            
            # Cache miss - call function
            result = func(*args, **kwargs)
            
            # Store in cache
            if result is not None:
                get_cache().set(cache_key, result, ttl)
            
            return result
        return wrapper
    return decorator

