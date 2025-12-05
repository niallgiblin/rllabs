"""
Model Catalog Service - Main Application
========================================
Manages model metadata and version history.
"""

# =============================================================================
# OBSERVABILITY SETUP (must be first, before other imports)
# =============================================================================
import os
import sys

# Add shared module to path
shared_path = os.path.join(os.path.dirname(__file__), 'shared')
if os.path.exists(shared_path) and shared_path not in sys.path:
    sys.path.insert(0, shared_path)

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "model-catalog-service")

# Initialize structured logging and tracing
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

# =============================================================================
# APPLICATION IMPORTS
# =============================================================================
from fastapi import FastAPI, Depends, HTTPException, Header, Query, BackgroundTasks
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import text, desc, func
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from contextlib import asynccontextmanager
import asyncio
import logging

import database
from pydantic import BaseModel

# Import Redis cache module
try:
    from cache import (
        get_cache,
        cache_models_list, get_cached_models_list,
        cache_models_list_page, get_cached_models_list_page,
        cache_model, get_cached_model,
        cache_model_versions, get_cached_model_versions,
        cache_model_latest, get_cached_model_latest,
        cache_version_by_hash, get_cached_version_by_hash,
        cache_model_ownership, get_cached_model_ownership,
        invalidate_models_list, invalidate_model, invalidate_model_versions,
        invalidate_model_ownership,
        PREFIX  # Cache key prefix for count caching
    )
    CACHING_ENABLED = True
    logger.info("Redis caching enabled")
except ImportError as e:
    CACHING_ENABLED = False
    PREFIX = "model_catalog"  # Fallback prefix
    logger.warning(f"Redis caching disabled: {e}")

# Try to import event publisher, but fail gracefully if RabbitMQ unavailable
try:
    from event_publisher import get_event_publisher
    EVENT_PUBLISHING_ENABLED = True
except ImportError:
    EVENT_PUBLISHING_ENABLED = False
    def get_event_publisher():
        return None

# Try to import event consumer, but fail gracefully if RabbitMQ unavailable
try:
    from event_consumer import get_event_consumer
    EVENT_CONSUMING_ENABLED = True
except ImportError:
    EVENT_CONSUMING_ENABLED = False
    def get_event_consumer():
        return None

# Pydantic models for request/response validation
class ModelVersionBase(BaseModel):
    version: int
    storage_path: str
    content_hash: str

class ModelVersionCreate(ModelVersionBase):
    pass

class ModelVersion(ModelVersionBase):
    id: int
    model_id: int
    class Config:
        from_attributes = True

class ModelBase(BaseModel):
    name: str
    description: Optional[str] = None

class ModelCreate(ModelBase):
    pass

class Model(ModelBase):
    id: int
    created_by: str # Changed from Optional[str] = None to str
    versions: List[ModelVersion] = []
    class Config:
        from_attributes = True

class LatestModelPath(BaseModel):
    storage_path: str

class PaginatedModels(BaseModel):
    """Paginated response for models list"""
    items: List[Model]
    total: int
    page: int
    page_size: int
    total_pages: int
    class Config:
        from_attributes = True

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create DB tables on startup
    database.create_db_and_tables()
    
    # Start event consumer if enabled
    if EVENT_CONSUMING_ENABLED:
        try:
            consumer = get_event_consumer()
            if consumer:
                consumer.start_consuming()
                print("Event consumer started for ArtifactCommitted events")
        except Exception as e:
            print(f"Failed to start event consumer: {e}")
    
    # Warm cache with frequently accessed models (optional, can be disabled)
    if CACHING_ENABLED and os.getenv("CACHE_WARMING_ENABLED", "false").lower() == "true":
        try:
            logger.info("Warming cache with recent models...")
            # Use proper context manager pattern instead of next()
            db_gen = database.get_read_db()
            db = next(db_gen)
            try:
                # Load most recent 50 models
                recent_models = db.query(database.Model).order_by(desc(database.Model.created_at)).limit(50).all()
                warmed_count = 0
                for model in recent_models:
                    try:
                        model_data = {
                            "id": model.id,
                            "name": model.name,
                            "description": model.description,
                            "created_by": model.created_by,
                            "created_at": model.created_at.isoformat() if model.created_at else None,
                            "versions": []  # Don't load versions during warmup (too expensive)
                        }
                        cache_model(model.id, model_data)
                        warmed_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to warm cache for model {model.id}: {e}")
                        continue
                logger.info(f"✅ Warmed cache with {warmed_count} models")
            finally:
                db.close()
                # Ensure generator is properly closed
                try:
                    next(db_gen, None)
                except StopIteration:
                    pass
        except Exception as e:
            logger.warning(f"Cache warming failed: {e}")
    
    yield
    
    # Stop event consumer on shutdown
    if EVENT_CONSUMING_ENABLED:
        try:
            consumer = get_event_consumer()
            if consumer:
                consumer.stop_consuming()
                print("Event consumer stopped")
        except Exception as e:
            print(f"Error stopping event consumer: {e}")

app = FastAPI(
    title="Model Catalog Service",
    description="A service for managing model metadata.",
    lifespan=lifespan
)

# Request throttling: Smart throttling based on connection pool capacity
# Connection pool per pod: Replica has 25 base + 15 overflow = 40 connections per pod
# With 3 pods: 120 total connections available
# Use 80% of base capacity to allow good parallelism: 20 concurrent reads per pod (allows 5 for overflow)
# This allows 60 concurrent reads across all pods, sufficient for 30 users
# Optimized to reduce queuing while preventing connection pool exhaustion
MAX_CONCURRENT_READS = int(os.getenv("MAX_CONCURRENT_READS", "20"))  # 80% of 25 base connections per pod
read_throttle_semaphore = asyncio.Semaphore(MAX_CONCURRENT_READS)

# Ownership checks are very lightweight (single indexed row lookup) - no throttling needed
# Connection pool naturally limits concurrency, semaphore would just add unnecessary queuing
# Removed separate semaphore - ownership checks run directly without throttling

# Add Prometheus metrics
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    instrumentator = Instrumentator()
    instrumentator.instrument(app).expose(app)
    logger.info("Prometheus metrics enabled")
except ImportError:
    logger.warning("prometheus-fastapi-instrumentator not available - metrics disabled")

# Healthcheck - Fast endpoint for Kubernetes probes (no database dependency)
@app.get("/health", tags=["Monitoring"])
async def health_check():
    """
    Fast health check endpoint for Kubernetes readiness/liveness probes.
    Returns immediately without checking dependencies to avoid connection pool exhaustion.
    """
    return {"status": "ok"}

# Detailed health check with dependency verification
@app.get("/health/detailed", tags=["Monitoring"])
async def detailed_health_check(db: Session = Depends(database.get_read_db)):
    """
    Detailed health check endpoint with dependency verification.
    Use this for monitoring dashboards, not for Kubernetes probes.
    """
    try:
        db.execute(text('SELECT 1'))
        db_status = "online"
    except Exception:
        db_status = "offline"
    
    # Check cache status
    cache_status = "disabled"
    if CACHING_ENABLED:
        try:
            cache = get_cache()
            if cache.client and cache.client.ping():
                cache_status = "online"
            else:
                cache_status = "offline"
        except Exception:
            cache_status = "offline"
    
    return {
        "service_status": "ok", 
        "dependencies": {
            "database": db_status,
            "cache": cache_status
        }
    }

@app.get("/cache/stats", tags=["Monitoring"])
async def cache_stats():
    """
    Get cache statistics for monitoring.
    
    Returns hit rate, connection status, and circuit breaker state.
    """
    if not CACHING_ENABLED:
        return {"status": "disabled"}
    
    return get_cache().get_stats()

@app.get("/database/pool-stats", tags=["Monitoring"])
async def database_pool_stats():
    """
    Get database connection pool statistics for monitoring.
    
    Returns pool size, active connections, and pool usage.
    """
    try:
        stats = {
            "primary": {
                "pool_size": database.engine.pool.size(),
                "checked_out": database.engine.pool.checkedout(),
                "overflow": database.engine.pool.overflow(),
                "checked_in": database.engine.pool.checkedin(),
                "total_connections": database.engine.pool.size() + database.engine.pool.overflow()
            },
            "replicas": []
        }
        
        for i, replica_engine in enumerate(database.replica_engines if hasattr(database, 'replica_engines') else []):
            stats["replicas"].append({
                "replica_index": i + 1,
                "pool_size": replica_engine.pool.size(),
                "checked_out": replica_engine.pool.checkedout(),
                "overflow": replica_engine.pool.overflow(),
                "checked_in": replica_engine.pool.checkedin(),
                "total_connections": replica_engine.pool.size() + replica_engine.pool.overflow()
            })
        
        return stats
    except Exception as e:
        logger.error(f"Error getting pool stats: {e}")
        return {"error": str(e)}

@app.get("/models/{model_id}/diagnostics", tags=["Monitoring"])
async def get_model_diagnostics(
    model_id: int,
    db: Session = Depends(database.get_read_db)
):
    """
    Get diagnostic information for a model to investigate performance issues.
    
    Returns version count, query execution plans, and other diagnostic data.
    """
    try:
        diagnostics = {
            "model_id": model_id,
            "model_exists": False,
            "version_count": 0,
            "indexes_used": [],
            "query_plans": {}
        }
        
        # Check if model exists
        model = db.query(database.Model).filter(database.Model.id == model_id).first()
        if model:
            diagnostics["model_exists"] = True
            diagnostics["model_name"] = model.name
            diagnostics["created_by"] = model.created_by
            diagnostics["created_at"] = model.created_at.isoformat() if model.created_at else None
        
        # Get version count
        version_count = db.query(func.count(database.ModelVersion.id)).filter(
            database.ModelVersion.model_id == model_id
        ).scalar()
        diagnostics["version_count"] = version_count or 0
        
        # Get query execution plans for common queries
        try:
            # Plan for versions query
            versions_plan = db.execute(text(
                f"EXPLAIN ANALYZE SELECT * FROM model_versions "
                f"WHERE model_id = {model_id} ORDER BY version DESC LIMIT 20"
            )).fetchall()
            diagnostics["query_plans"]["versions_query"] = [str(row) for row in versions_plan]
            
            # Plan for latest query
            latest_plan = db.execute(text(
                f"EXPLAIN ANALYZE SELECT * FROM model_versions "
                f"WHERE model_id = {model_id} ORDER BY version DESC LIMIT 1"
            )).fetchall()
            diagnostics["query_plans"]["latest_query"] = [str(row) for row in latest_plan]
        except Exception as e:
            diagnostics["query_plans"]["error"] = str(e)
        
        # Check indexes
        try:
            indexes = db.execute(text(
                f"SELECT indexname, indexdef FROM pg_indexes "
                f"WHERE tablename = 'model_versions' AND indexdef LIKE '%model_id%'"
            )).fetchall()
            diagnostics["indexes_used"] = [{"name": row[0], "definition": row[1]} for row in indexes]
        except Exception as e:
            diagnostics["indexes_error"] = str(e)
        
        return diagnostics
    except Exception as e:
        logger.error(f"Error getting model diagnostics: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting diagnostics: {e}")

# API endpoints
@app.post("/models/{model_id}/versions", response_model=ModelVersion, status_code=201, tags=["Models"])
async def register_model_version(
    model_id: int, 
    version_data: ModelVersionCreate, 
    db: Session = Depends(database.get_db),
    user_id: Optional[str] = Header(None, alias="X-User-Id")
):
    """
    Registers a new model version.
    
    This endpoint is called by the Upload/Download Service after a file is successfully uploaded.
    It can also be called when trained model weights are uploaded by the Training Service.
    
    Flow:
    1. Upload/Download Service completes artifact upload
    2. Upload/Download Service calls this endpoint to register the version
    3. Model Catalog creates a new version record
    
    The version number is automatically assigned based on existing versions for the model.
    Content hash (SHA-256) is used for deduplication - same hash cannot be registered twice.
    
    Args:
        model_id: ID of the parent model
        version_data: Version information (version number, storage_path, content_hash)
        user_id: User ID from API Gateway (optional, for audit logging)
    
    Returns:
        Created model version with assigned ID
    
    Raises:
        404: Model not found
        409: Version or content hash already exists for this model
    """
    db_model = db.query(database.Model).filter(database.Model.id == model_id).first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")

    try:
        db_version = database.ModelVersion(**version_data.model_dump(), model_id=model_id)
        db.add(db_version)
        db.commit()
        db.refresh(db_version)
        
        # Invalidate version-related caches (new version changes latest) - fail-open
        # IMPORTANT: Also invalidate models list cache so new models appear immediately
        if CACHING_ENABLED:
            try:
                invalidate_model_versions(model_id)
                invalidate_model(model_id)  # Model details include versions
                # CRITICAL: Invalidate models list cache so newly created models appear
                # When a model is created and first version is registered, the list cache needs to be cleared
                invalidate_models_list()  # This invalidates both paginated and non-paginated caches
                # Also invalidate count cache (new version doesn't change model count, but be safe)
                try:
                    get_cache().delete(f"{PREFIX}:models:count")
                except Exception:
                    pass  # Best effort
                logger.info(f"Cache invalidated after registering version for model {model_id} (including models list)")
            except Exception as e:
                logger.warning(f"Failed to invalidate cache after registering version: {e}")
                # Don't fail the request if cache invalidation fails
        
        logger.info(
            f"Registered version {db_version.version} for model {model_id} "
            f"(content_hash: {db_version.content_hash[:16]}...)"
        )
        
        return db_version
    except IntegrityError as e:
        logger.warning(f"IntegrityError in register_model_version: {e}")
        db.rollback()
        raise HTTPException(
            status_code=409, 
            detail="This model version or content hash already exists for this model."
        )
    except Exception as e:
        logger.error(
            f"Error registering model version: {e}",
            exc_info=True,
            extra={
                "model_id": model_id,
                "user_id": user_id,
                "error_type": type(e).__name__
            }
        )
        db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"An unexpected database error occurred: {e}"
        )

@app.get("/models", response_model=PaginatedModels, tags=["Models"])
async def list_models(
    db: Session = Depends(database.get_read_db),
    include_versions: bool = False,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=200, description="Number of items per page (max 200)")
):
    """
    Lists models with pagination.
    
    Uses cache-aside pattern:
    1. Check Redis cache first
    2. On miss, query database and populate cache
    
    Optimized: By default, versions are NOT loaded (much faster).
    Set include_versions=true to load versions (slower but complete).
    
    Pagination: Default page size is 50, max is 200 per page.
    """
    import time
    start_time = time.time()
    offset = (page - 1) * page_size
    
    # Try paginated cache first (much faster than loading all models)
    cache_time = 0
    if CACHING_ENABLED:
        cache_start = time.time()
        cached_page = get_cached_models_list_page(page, page_size)
        cache_time = time.time() - cache_start
        if cached_page is not None:
            # Cache hit - return cached page (very fast)
            # Convert cached dicts back to ORM-like objects for response
            models = []
            for item in cached_page["items"]:
                model = database.Model(
                    id=item["id"],
                    name=item["name"],
                    description=item.get("description"),
                    created_by=item["created_by"]
                )
                if include_versions and item.get("versions"):
                    # Load versions if requested and available in cache
                    model.versions = [
                        database.ModelVersion(
                            id=v["id"],
                            version=v["version"],
                            storage_path=v["storage_path"],
                            content_hash=v["content_hash"],
                            model_id=v["model_id"]
                        ) for v in item["versions"]
                    ]
                models.append(model)
            
            # Calculate total_pages from total and page_size
            total = cached_page.get("total", len(cached_page.get("items", [])))
            total_pages = (total + page_size - 1) // page_size if total > 0 else 0
            
            total_time = time.time() - start_time
            # Reduced verbosity: only log detailed metrics at debug level
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"GET /models: Cache HIT (page {page}/{total_pages}) in {total_time*1000:.2f}ms "
                    f"(cache_check: {cache_time*1000:.2f}ms, items: {len(models)}/{total})"
                )
            else:
                # Summary log at info level (less verbose)
                logger.info(f"GET /models: Cache HIT (page {page}/{total_pages}, {len(models)} items)")
            
            return PaginatedModels(
                items=models,
                total=total,
                page=cached_page.get("page", page),
                page_size=cached_page.get("page_size", page_size),
                total_pages=total_pages
            )
        logger.debug(f"GET /models: Cache MISS (cache_check: {cache_time*1000:.2f}ms)")
    
    # Cache miss - query database with smart throttling
    # Throttle prevents connection pool exhaustion while allowing good parallelism
    try:
        async with read_throttle_semaphore:
            query_start = time.time()
            
            # Optimize COUNT query: Use cached count if available, otherwise query
            # COUNT queries can be slow on large tables, so we cache the result
            total = None
            if CACHING_ENABLED:
                cache_key = f"{PREFIX}:models:count"
                try:
                    cached_count = get_cache().get(cache_key)
                    if cached_count is not None:
                        total = int(cached_count)
                        logger.debug(f"Using cached model count: {total}")
                except Exception:
                    pass  # Fallback to query
            
            if total is None:
                # Query count (this can be slow, but necessary for pagination)
                total = db.query(func.count(database.Model.id)).scalar()
                # Cache the count for 60 seconds (models don't change that frequently)
                if CACHING_ENABLED:
                    try:
                        get_cache().set(f"{PREFIX}:models:count", str(total), ttl=60)
                    except Exception:
                        pass  # Best effort caching
            
            total_pages = (total + page_size - 1) // page_size if total > 0 else 0
            
            # Always query only the requested page (never load all models)
            # Use indexed column (id) for ordering - much faster than created_at
            query = db.query(database.Model).order_by(database.Model.id)
            if include_versions:
                query = query.options(selectinload(database.Model.versions))
            models = query.offset(offset).limit(page_size).all()
            
            query_time = time.time() - query_start
            
            # Cache this specific page (paginated caching)
            cache_populate_time = 0
            if CACHING_ENABLED:
                cache_populate_start = time.time()
                # Serialize models for this page
                models_data = [
                    {
                        "id": m.id,
                        "name": m.name,
                        "description": m.description,
                        "created_by": m.created_by,
                        "versions": [
                            {"id": v.id, "version": v.version, "storage_path": v.storage_path, 
                             "content_hash": v.content_hash, "model_id": v.model_id}
                            for v in m.versions
                        ] if include_versions and hasattr(m, 'versions') and m.versions else []
                    }
                    for m in models
                ]
                cache_models_list_page(page, page_size, models_data, total)
                cache_populate_time = time.time() - cache_populate_start
                logger.debug(f"Cache POPULATED for models list page {page} ({len(models_data)} items)")
            
            total_time = time.time() - start_time
            # Reduced verbosity: detailed metrics at debug level
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"GET /models: Completed (page {page}/{total_pages}) in {total_time*1000:.2f}ms "
                    f"(query: {query_time*1000:.2f}ms, cache_check: {cache_time*1000:.2f}ms, "
                    f"cache_populate: {cache_populate_time*1000:.2f}ms, "
                    f"include_versions: {include_versions}, items: {len(models)}, total: {total})"
                )
            else:
                # Summary log at info level (less verbose)
                logger.info(f"GET /models: Completed (page {page}/{total_pages}, {len(models)} items, {total_time*1000:.0f}ms)")
            
            return PaginatedModels(
                items=models,
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages
            )
    except Exception as e:
        total_time = time.time() - start_time
        logger.error(
            f"Error listing models after {total_time*1000:.2f}ms: {e}",
            exc_info=True
        )
        raise HTTPException(status_code=503, detail=f"Database is unavailable: {e}")

@app.post("/models", response_model=Model, status_code=201, tags=["Models"])
async def create_model(
    model: ModelCreate, 
    background_tasks: BackgroundTasks,
    db: Session = Depends(database.get_db), 
    user_id: str = Header(..., alias="X-User-Id", required=True)
):
    """
    Creates a new model entry.
    
    Invalidates the models list cache after creation.
    """
    try:
        db_model = database.Model(**model.model_dump(), created_by=user_id)
        db.add(db_model)
        db.commit()
        db.refresh(db_model)
        
        # Write-through cache: Update cache instead of invalidating
        if CACHING_ENABLED:
            try:
                cached_list = get_cached_models_list()
                if cached_list is not None:
                    # Cache exists - append new model to cached list (write-through)
                    new_model_data = {
                        "id": db_model.id,
                        "name": db_model.name,
                        "description": db_model.description,
                        "created_by": db_model.created_by,
                        "versions": []  # New model has no versions yet
                    }
                    cached_list.append(new_model_data)
                    cache_models_list(cached_list)
                    logger.info(f"Cache updated (write-through) after creating model {db_model.id}")
                else:
                    # Cache miss - invalidate to force refresh on next read
                    invalidate_models_list()
                    logger.info(f"Cache invalidated (cache was empty) after creating model {db_model.id}")
                # Invalidate count cache (new model changes total count)
                try:
                    get_cache().delete(f"{PREFIX}:models:count")
                except Exception:
                    pass  # Best effort
            except Exception as e:
                logger.warning(f"Failed to update cache after creating model: {e}")
                # Fallback to invalidation
                try:
                    invalidate_models_list()
                    get_cache().delete(f"{PREFIX}:models:count")
                except Exception:
                    pass  # Best effort
        
        # Publish ModelCreated event in background - fail-open (non-blocking)
        if EVENT_PUBLISHING_ENABLED:
            def publish_event():
                try:
                    publisher = get_event_publisher()
                    if publisher:
                        publisher.publish_model_created(db_model.id, db_model.name, db_model.created_by)
                except Exception as e:
                    logger.warning(f"Failed to publish ModelCreated event: {e}")
            background_tasks.add_task(publish_event)
        
        return db_model
    except IntegrityError as e:
        logger.warning(f"IntegrityError caught in create_model: {e}")
        db.rollback()
        raise HTTPException(status_code=409, detail="Model with this name already exists.")
    except Exception as e:
        logger.error(
            f"Error creating model: {e}",
            exc_info=True,
            extra={
                "user_id": user_id,
                "model_name": model.name if hasattr(model, 'name') else None,
                "error_type": type(e).__name__
            }
        )
        db.rollback()
        raise HTTPException(status_code=500, detail=f"An unexpected database error occurred: {e}")

@app.get("/models/{model_id}", response_model=Model, tags=["Models"])
async def get_model_details(
    model_id: int, 
    include_versions: bool = Query(False, description="Include versions in response"),
    db: Session = Depends(database.get_read_db)
):
    """
    Retrieves details for a specific model.
    
    Uses cache-aside pattern with per-model caching.
    By default, versions are NOT loaded (much faster).
    Set include_versions=true to load versions (slower but complete).
    """
    import time
    start_time = time.time()
    
    # Try cache first (no throttling needed for cache hits)
    if CACHING_ENABLED:
        cached = get_cached_model(model_id)
        if cached is not None:
            # Convert cached dict back to Model object
            model = database.Model(
                id=cached["id"],
                name=cached["name"],
                description=cached.get("description"),
                created_by=cached["created_by"]
            )
            # Load versions if requested and available in cache
            if include_versions and cached.get("versions"):
                model.versions = [
                    database.ModelVersion(
                        id=v["id"],
                        version=v["version"],
                        storage_path=v["storage_path"],
                        content_hash=v["content_hash"],
                        model_id=v["model_id"]
                    ) for v in cached["versions"]
                ]
            total_time = time.time() - start_time
            # Reduced verbosity: detailed metrics at debug level
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"GET /models/{model_id}: Cache HIT in {total_time*1000:.2f}ms (include_versions: {include_versions})")
            return model
    
    # Cache miss - query database with smart throttling
    # Throttle prevents connection pool exhaustion while allowing good parallelism
    async with read_throttle_semaphore:
        query_start = time.time()
        # Only load versions if explicitly requested (much faster for most requests)
        query = db.query(database.Model).filter(database.Model.id == model_id)
        if include_versions:
            query = query.options(selectinload(database.Model.versions))
        db_model = query.first()
        query_time = time.time() - query_start
        
        # Log slow queries and high-latency models for investigation
        high_latency_models = [7154, 7192, 7181, 7182]  # Models showing extreme latency
        if query_time > 0.5 or model_id in high_latency_models:  # Log queries >500ms or high-latency models
            logger.warning(
                f"Slow query detected: GET /models/{model_id} "
                f"(query_time: {query_time*1000:.2f}ms, include_versions: {include_versions})"
            )
            if model_id in high_latency_models:
                # For high-latency models, get additional diagnostic info
                try:
                    if db_model:
                        version_count = len(db_model.versions) if hasattr(db_model, 'versions') and db_model.versions else 0
                        logger.warning(
                            f"Model {model_id} diagnostic: version_count={version_count}, "
                            f"query_time={query_time*1000:.2f}ms, include_versions={include_versions}"
                        )
                except Exception as e:
                    logger.debug(f"Could not get Model {model_id} diagnostic info: {e}")

    if db_model is None:
        raise HTTPException(status_code=404, detail="Model not found")

    # Serialize and cache (always cache model data, with or without versions)
    cache_populate_time = 0
    if CACHING_ENABLED:
        cache_start = time.time()
        # Always cache model data - if versions weren't loaded, cache without them
        # This ensures cache is populated for all model queries, improving cache hit rate
        model_data = {
            "id": db_model.id,
            "name": db_model.name,
            "description": db_model.description,
            "created_by": db_model.created_by,
            "created_at": db_model.created_at.isoformat() if db_model.created_at else None,
            "versions": [
                {"id": v.id, "version": v.version, "storage_path": v.storage_path,
                 "content_hash": v.content_hash, "model_id": v.model_id}
                for v in (db_model.versions if include_versions and hasattr(db_model, 'versions') and db_model.versions else [])
            ]
        }
        cache_model(model_id, model_data)
        cache_populate_time = time.time() - cache_start
        logger.debug(f"Cache POPULATED for model {model_id} (include_versions: {include_versions})")
    
    total_time = time.time() - start_time
    # Reduced verbosity: detailed metrics at debug level
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            f"GET /models/{model_id}: Cache MISS in {total_time*1000:.2f}ms "
            f"(query: {query_time*1000:.2f}ms, cache_populate: {cache_populate_time*1000:.2f}ms, "
            f"include_versions: {include_versions})"
        )
    else:
        # Summary log at info level
        logger.info(f"GET /models/{model_id}: Cache MISS ({total_time*1000:.0f}ms)")

    return db_model

@app.get("/models/{model_id}/latest", response_model=LatestModelPath, tags=["Models"])
async def get_latest_model_path(model_id: int, db: Session = Depends(database.get_read_db)):
    """
    Gets the storage path of the latest version for a given model.
    
    Uses cache-aside pattern - this is a frequently accessed endpoint
    for inference services that need the latest model weights.
    """
    import time
    start_time = time.time()
    
    # Try cache first (no throttling needed for cache hits)
    if CACHING_ENABLED:
        cached = get_cached_model_latest(model_id)
        if cached is not None:
            total_time = time.time() - start_time
            logger.info(f"GET /models/{model_id}/latest: Cache HIT in {total_time*1000:.2f}ms")
            return cached
    
    try:
        # Cache miss - query database directly (no semaphore throttling)
        # Connection pool naturally limits concurrency
        # Optimize: Query directly - if model doesn't exist, versions will be empty
        # This removes unnecessary model existence check, improving performance
        query_start = time.time()
        latest_version = (
            db.query(database.ModelVersion)
            .filter(database.ModelVersion.model_id == model_id)
            .order_by(desc(database.ModelVersion.version))
            .first()
        )
        query_time = time.time() - query_start
        
        # Log slow queries and high-latency models for investigation
        high_latency_models = [7154, 7192, 7181, 7182]  # Models showing extreme latency
        if query_time > 0.5 or model_id in high_latency_models:  # Log queries >500ms or high-latency models
            logger.warning(
                f"Slow query detected: GET /models/{model_id}/latest "
                f"(query_time: {query_time*1000:.2f}ms)"
            )
            if model_id in high_latency_models:
                # For high-latency models, get additional diagnostic info
                try:
                    total_versions = db.query(func.count(database.ModelVersion.id)).filter(
                        database.ModelVersion.model_id == model_id
                    ).scalar()
                    logger.warning(
                        f"Model {model_id} diagnostic: total_versions={total_versions}, "
                        f"query_time={query_time*1000:.2f}ms"
                    )
                except Exception as e:
                    logger.debug(f"Could not get Model {model_id} diagnostic info: {e}")

        if not latest_version:
            # Only check model existence if no versions found (for better error message)
            model_exists = db.query(database.Model.id).filter(database.Model.id == model_id).scalar()
            if not model_exists:
                raise HTTPException(status_code=404, detail="Model not found")
            raise HTTPException(status_code=404, detail="No versions found for this model.")

        result = {"storage_path": latest_version.storage_path}
        
        # Cache the result
        cache_populate_time = 0
        if CACHING_ENABLED:
            cache_start = time.time()
            cache_model_latest(model_id, result)
            cache_populate_time = time.time() - cache_start
            logger.debug(f"Cache POPULATED for model {model_id} latest")
        
        total_time = time.time() - start_time
        logger.info(
            f"GET /models/{model_id}/latest: Cache MISS in {total_time*1000:.2f}ms "
            f"(query: {query_time*1000:.2f}ms, cache_populate: {cache_populate_time*1000:.2f}ms)"
        )
        
        return result
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=503, detail=f"Database is unavailable: {e}")

@app.get("/models/{model_id}/versions", response_model=List[ModelVersion], tags=["Models"])
async def list_model_versions(
    model_id: int,
    limit: int = Query(50, ge=1, le=200, description="Maximum number of versions to return"),
    offset: int = Query(0, ge=0, description="Number of versions to skip"),
    db: Session = Depends(database.get_read_db)
):
    """
    Lists versions for a given model with pagination.
    
    Uses cache-aside pattern for version lists.
    By default, returns the 50 most recent versions (ordered by version DESC).
    Use limit and offset for pagination.
    """
    import time
    start_time = time.time()
    
    # Try cache first (only for first page without offset, for simplicity)
    if CACHING_ENABLED and offset == 0:
        cached = get_cached_model_versions(model_id)
        if cached is not None:
            # Apply pagination to cached data
            paginated_cached = cached[:limit]
            # Convert cached dicts back to ModelVersion objects
            versions = [
                database.ModelVersion(
                    id=v["id"],
                    version=v["version"],
                    storage_path=v["storage_path"],
                    content_hash=v["content_hash"],
                    model_id=v["model_id"]
                ) for v in paginated_cached
            ]
            total_time = time.time() - start_time
            logger.info(f"GET /models/{model_id}/versions: Cache HIT in {total_time*1000:.2f}ms (limit: {limit}, offset: {offset})")
            return versions
    
    # Cache miss - query database with smart throttling
    # Throttle prevents connection pool exhaustion while allowing good parallelism
    try:
        async with read_throttle_semaphore:
            query_start = time.time()
            # Query with pagination - no need to check model existence first
            # If model doesn't exist, versions will be empty (foreign key ensures model_id exists)
            # This removes one unnecessary query, improving performance
            versions = (
                db.query(database.ModelVersion)
                .filter(database.ModelVersion.model_id == model_id)
                .order_by(desc(database.ModelVersion.version))
                .offset(offset)
                .limit(limit)
                .all()
            )
            query_time = time.time() - query_start
            
            # Log slow queries and high-latency models for investigation
            high_latency_models = [7154, 7192, 7181, 7182]  # Models showing extreme latency
            if query_time > 0.5 or model_id in high_latency_models:  # Log queries >500ms or high-latency models
                version_count = len(versions)
                logger.warning(
                    f"Slow query detected: GET /models/{model_id}/versions "
                    f"(query_time: {query_time*1000:.2f}ms, versions_returned: {version_count}, "
                    f"limit: {limit}, offset: {offset})"
                )
                if model_id in high_latency_models:
                    # For high-latency models, get additional diagnostic info
                    try:
                        total_versions = db.query(func.count(database.ModelVersion.id)).filter(
                            database.ModelVersion.model_id == model_id
                        ).scalar()
                        logger.warning(
                            f"Model {model_id} diagnostic: total_versions={total_versions}, "
                            f"query_time={query_time*1000:.2f}ms, limit={limit}, offset={offset}"
                        )
                    except Exception as e:
                        logger.debug(f"Could not get Model {model_id} diagnostic info: {e}")
            
            # Only check model existence if no versions found (for better error message)
            if not versions and offset == 0:
                # Quick existence check only if we got no results
                model_exists = db.query(database.Model.id).filter(database.Model.id == model_id).scalar()
                if not model_exists:
                    raise HTTPException(status_code=404, detail="Model not found")
        
        # Serialize and cache (only first page, outside semaphore - cache operations are fast)
        cache_populate_time = 0
        if CACHING_ENABLED and offset == 0:
            cache_start = time.time()
            versions_data = [
                {"id": v.id, "version": v.version, "storage_path": v.storage_path,
                 "content_hash": v.content_hash, "model_id": v.model_id}
                for v in versions
            ]
            # Note: We cache only the first page. For full list caching, we'd need to load all versions
            # which defeats the purpose of pagination. Cache what we have.
            cache_model_versions(model_id, versions_data)
            cache_populate_time = time.time() - cache_start
            logger.debug(f"Cache POPULATED for model {model_id} versions (first {len(versions_data)} versions)")
        
        total_time = time.time() - start_time
        logger.info(
            f"GET /models/{model_id}/versions: Cache MISS in {total_time*1000:.2f}ms "
            f"(query: {query_time*1000:.2f}ms, cache_populate: {cache_populate_time*1000:.2f}ms, "
            f"limit: {limit}, offset: {offset})"
        )
        
        return versions
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing model versions: {e}")
        raise HTTPException(status_code=503, detail=f"Database is unavailable: {e}")

@app.get("/versions/by-hash/{content_hash}", response_model=ModelVersion, tags=["Models"])
async def get_version_by_hash(content_hash: str, db: Session = Depends(database.get_read_db)):
    """
    Get a model version by its content hash (SHA-256).
    
    Uses cache-aside pattern - hash lookups are common for deduplication.
    """
    # Normalize hash format (accept with or without 'sha256:' prefix)
    # Database may store hashes with or without prefix, so check both formats
    if content_hash.startswith("sha256:"):
        normalized_hash = content_hash
        hash_without_prefix = content_hash[7:]  # Remove "sha256:" prefix
    else:
        normalized_hash = f"sha256:{content_hash}"
        hash_without_prefix = content_hash
    
    # Try cache first
    if CACHING_ENABLED:
        cached = get_cached_version_by_hash(normalized_hash)
        if cached is not None:
            logger.debug(f"Cache HIT for version hash {normalized_hash[:20]}...")
            return cached
    
    try:
        # Try with prefix first (most common format)
        version = (
            db.query(database.ModelVersion)
            .filter(database.ModelVersion.content_hash == normalized_hash)
            .first()
        )
        
        # If not found, try without prefix (for backwards compatibility)
        if not version:
            version = (
                db.query(database.ModelVersion)
                .filter(database.ModelVersion.content_hash == hash_without_prefix)
                .first()
            )
        
        if not version:
            raise HTTPException(
                status_code=404, 
                detail=f"No model version found with content hash: {content_hash}"
            )
        
        # Cache the result
        if CACHING_ENABLED:
            version_data = {
                "id": version.id, "version": version.version, 
                "storage_path": version.storage_path,
                "content_hash": version.content_hash, "model_id": version.model_id
            }
            cache_version_by_hash(normalized_hash, version_data)
            logger.debug(f"Cache POPULATED for version hash {normalized_hash[:20]}...")
        
        return version
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying version by hash: {e}")
        raise HTTPException(status_code=503, detail=f"Database is unavailable: {e}")

@app.get("/models/{model_id}/ownership", tags=["Models"])
async def check_model_ownership(
    model_id: int,
    user_id: str = Header(..., alias="X-User-Id"),
    db: Session = Depends(database.get_read_db)
):
    """
    Check if a user owns or has access to a model.
    
    Used by Upload/Download Service for RBAC authorisation.
    This endpoint allows other services to check model-level permissions
    before granting access to artifacts.
    
    Uses cache-aside pattern for performance - ownership checks are frequent
    and rarely change, making them ideal for caching.
    
    Returns:
        {
            "has_access": bool,      # True if user can access the model
            "is_owner": bool,         # True if user created the model
            "model_id": int           # The model ID
        }
    """
    import time
    start_time = time.time()
    
    # Try cache first (no throttling needed for cache hits)
    if CACHING_ENABLED:
        cached = get_cached_model_ownership(model_id, user_id)
        if cached is not None:
            total_time = time.time() - start_time
            logger.info(f"GET /models/{model_id}/ownership: Cache HIT in {total_time*1000:.2f}ms")
            return cached
    
    # Cache miss - query database directly (no semaphore throttling)
    # Ownership checks are very lightweight (single indexed row lookup by primary key)
    # Connection pool naturally limits concurrency, semaphore would just add unnecessary queuing
    # Optimize: Only fetch created_by column (not full model) - fastest possible query
    # Query timeout is enforced via statement_timeout in connection pool (2s for reads)
    try:
        query_start = time.time()
        # Use scalar() with only() to get just the string value directly
        # This is the fastest possible query - single column, indexed lookup by primary key
        # The read database has statement_timeout=2000ms set in connection pool
        created_by = db.query(database.Model.created_by).filter(database.Model.id == model_id).scalar()
        query_time = time.time() - query_start
        
        if created_by is None:
            raise HTTPException(status_code=404, detail="Model not found")
        
        is_owner = created_by == user_id
        has_access = is_owner
        
        result = {
            "has_access": has_access,
            "is_owner": is_owner,
            "model_id": model_id
        }
        
        # Cache the result (outside semaphore - cache operations are fast)
        cache_populate_time = 0
        if CACHING_ENABLED:
            cache_start = time.time()
            cache_model_ownership(model_id, user_id, result)
            cache_populate_time = time.time() - cache_start
        
        total_time = time.time() - start_time
        logger.info(
            f"GET /models/{model_id}/ownership: Cache MISS in {total_time*1000:.2f}ms "
            f"(query: {query_time*1000:.2f}ms, cache_populate: {cache_populate_time*1000:.2f}ms)"
        )
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking model ownership: {e}")
        raise HTTPException(status_code=503, detail=f"Database is unavailable: {e}")

# Admin utilities
def is_admin(user_scopes: Optional[str] = None, is_admin_header: Optional[str] = None) -> bool:
    """
    Check if user has admin privileges.
    
    Prefers X-Is-Admin header (from API Gateway) for centralized admin checking.
    Falls back to parsing X-Scope header if X-Is-Admin not provided.
    
    Args:
        user_scopes: Space-separated scopes from X-Scope header (e.g., "api:read api:write api:admin")
        is_admin_header: X-Is-Admin header value from API Gateway ("true" or "false")
    
    Returns:
        True if user has admin scope, False otherwise
    """
    # Prefer API Gateway's admin check
    if is_admin_header:
        return is_admin_header.lower() == 'true'
    
    # Fallback to parsing scopes (for direct service access or backward compatibility)
    if not user_scopes:
        return False
    
    scopes = user_scopes.split() if isinstance(user_scopes, str) else []
    return "api:admin" in scopes

@app.delete("/models/{model_id}", status_code=204, tags=["Models"])
async def delete_model(
    model_id: int,
    background_tasks: BackgroundTasks,
    user_id: str = Header(..., alias="X-User-Id"),
    user_scopes: Optional[str] = Header(None, alias="X-Scope"),
    is_admin_header: Optional[str] = Header(None, alias="X-Is-Admin"),
    db: Session = Depends(database.get_db)
):
    """
    Delete a model. Only the owner or an admin can delete.
    
    This will also cascade delete all model versions.
    Note: This does NOT delete the actual artifact files in MinIO.
    To delete artifacts, use the DELETE /artifacts/{artifact_id} endpoint.
    """
    db_model = db.query(database.Model).filter(database.Model.id == model_id).first()
    
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Check authorisation: owner OR admin
    is_owner = db_model.created_by == user_id
    is_admin_user = is_admin(user_scopes, is_admin_header)
    
    if not (is_owner or is_admin_user):
        logger.warning(
            f"User {user_id} denied delete access to model {model_id} "
            f"(owner: {db_model.created_by}, is_admin: {is_admin_user})"
        )
        raise HTTPException(
            status_code=403,
            detail="Only the model owner or an admin can delete this model"
        )
    
    try:
        model_name = db_model.name
        
        # Delete all versions first (to avoid foreign key constraint issues)
        versions = db.query(database.ModelVersion).filter(
            database.ModelVersion.model_id == model_id
        ).all()
        for version in versions:
            db.delete(version)
        
        # Delete model
        db.delete(db_model)
        db.commit()
        # Refresh the session to ensure the deletion is immediately visible
        db.expire_all()
        
        # Write-through cache: Remove model from cached list
        if CACHING_ENABLED:
            try:
                # CRITICAL: Always invalidate ALL caches first (including paginated)
                # This ensures the deleted model name is immediately available for reuse
                invalidate_models_list()  # This now invalidates both paginated and non-paginated
                invalidate_model(model_id)
                invalidate_model_ownership(model_id)
                # Invalidate count cache (deleted model changes total count)
                get_cache().delete(f"{PREFIX}:models:count")
                logger.info(f"All caches invalidated after deleting model {model_id}")
                
                # Optional: Try to update cached list if it exists (write-through optimization)
                # But don't rely on this - invalidation is more important
                try:
                    cached_list = get_cached_models_list()
                    if cached_list is not None:
                        # Cache exists - remove model from cached list (write-through)
                        cached_list = [m for m in cached_list if m.get("id") != model_id]
                        cache_models_list(cached_list)
                        logger.info(f"Cache updated (write-through) after deleting model {model_id}")
                except Exception:
                    pass  # Best effort - invalidation already happened
            except Exception as e:
                logger.warning(f"Failed to update cache after deleting model: {e}")
                # Fallback to invalidation
                try:
                    invalidate_models_list()
                    invalidate_model(model_id)
                    invalidate_model_ownership(model_id)
                    get_cache().delete(f"{PREFIX}:models:count")
                except Exception:
                    pass  # Best effort
        
        # Publish ModelDeleted event in background - fail-open (non-blocking)
        if EVENT_PUBLISHING_ENABLED:
            def publish_event():
                try:
                    publisher = get_event_publisher()
                    if publisher:
                        publisher.publish_model_deleted(model_id, model_name)
                except Exception as e:
                    logger.error(f"Failed to publish ModelDeleted event: {e}")
            background_tasks.add_task(publish_event)
        
        logger.info(
            f"Model {model_id} ({model_name}) deleted by "
            f"{'admin' if is_admin_user else 'owner'} {user_id}"
        )
        
        return None
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting model {model_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred while deleting the model: {str(e)}"
        )