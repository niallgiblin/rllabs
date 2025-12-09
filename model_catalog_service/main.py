"""
Model Catalog Service - Main Application
========================================
Manages model metadata and version history.
"""

# Observability Setup
import os
import sys

shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'shared'))
if os.path.exists(shared_path) and shared_path not in sys.path:
    sys.path.insert(0, shared_path)

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "model-catalog-service")

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
        PREFIX  
    )
    CACHING_ENABLED = True
    logger.info("Redis caching enabled")
except ImportError as e:
    CACHING_ENABLED = False
    PREFIX = "model_catalog"  
    logger.warning(f"Redis caching disabled: {e}")

try:
    from event_publisher import get_event_publisher
    EVENT_PUBLISHING_ENABLED = True
except ImportError:
    EVENT_PUBLISHING_ENABLED = False
    def get_event_publisher():
        return None

try:
    from event_consumer import get_event_consumer
    EVENT_CONSUMING_ENABLED = True
except ImportError:
    EVENT_CONSUMING_ENABLED = False
    def get_event_consumer():
        return None

class ModelVersionBase(BaseModel):
    version: int
    storage_path: str
    content_hash: str

class ModelVersionCreate(BaseModel):
    version: Optional[int] = None  
    storage_path: str
    content_hash: str

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
    created_by: str 
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
    database.create_db_and_tables()
    
    if EVENT_CONSUMING_ENABLED:
        try:
            consumer = get_event_consumer()
            if consumer:
                consumer.start_consuming()
                print("Event consumer started for ArtifactCommitted events")
        except Exception as e:
            print(f"Failed to start event consumer: {e}")
    
    if CACHING_ENABLED and os.getenv("CACHE_WARMING_ENABLED", "false").lower() == "true":
        try:
            logger.info("Warming cache with recent models...")
            db_gen = database.get_read_db()
            db = next(db_gen)
            try:
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
                            "versions": []  
                        }
                        cache_model(model.id, model_data)
                        warmed_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to warm cache for model {model.id}: {e}")
                        continue
                logger.info(f"Warmed cache with {warmed_count} models")
            finally:
                db.close()
                try:
                    next(db_gen, None)
                except StopIteration:
                    pass
        except Exception as e:
            logger.warning(f"Cache warming failed: {e}")
    
    yield
    
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

MAX_CONCURRENT_READS = int(os.getenv("MAX_CONCURRENT_READS", "20")) 
read_throttle_semaphore = asyncio.Semaphore(MAX_CONCURRENT_READS)

try:
    from prometheus_fastapi_instrumentator import Instrumentator
    instrumentator = Instrumentator()
    instrumentator.instrument(app).expose(app)
    logger.info("Prometheus metrics enabled")
except ImportError:
    logger.warning("prometheus-fastapi-instrumentator not available - metrics disabled")

@app.get("/health", tags=["Monitoring"])
async def health_check():
    """
    Fast health check endpoint for Kubernetes readiness/liveness probes.
    Returns immediately without checking dependencies to avoid connection pool exhaustion.
    """
    return {"status": "ok"}

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
        
        model = db.query(database.Model).filter(database.Model.id == model_id).first()
        if model:
            diagnostics["model_exists"] = True
            diagnostics["model_name"] = model.name
            diagnostics["created_by"] = model.created_by
            diagnostics["created_at"] = model.created_at.isoformat() if model.created_at else None
        
        version_count = db.query(func.count(database.ModelVersion.id)).filter(
            database.ModelVersion.model_id == model_id
        ).scalar()
        diagnostics["version_count"] = version_count or 0
        
        try:
            versions_plan = db.execute(text(
                f"EXPLAIN ANALYZE SELECT * FROM model_versions "
                f"WHERE model_id = {model_id} ORDER BY version DESC LIMIT 20"
            )).fetchall()
            diagnostics["query_plans"]["versions_query"] = [str(row) for row in versions_plan]
            
            latest_plan = db.execute(text(
                f"EXPLAIN ANALYZE SELECT * FROM model_versions "
                f"WHERE model_id = {model_id} ORDER BY version DESC LIMIT 1"
            )).fetchall()
            diagnostics["query_plans"]["latest_query"] = [str(row) for row in latest_plan]
        except Exception as e:
            diagnostics["query_plans"]["error"] = str(e)
        
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
    If version is provided, it will be used (for backward compatibility), but it's recommended
    to let Model Catalog calculate it automatically to ensure sequential numbering.
    Content hash (SHA-256) is used for deduplication - same hash cannot be registered twice.
    
    Args:
        model_id: ID of the parent model
        version_data: Version information (version number optional, storage_path, content_hash)
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
        version = version_data.version
        if version is None:
            from sqlalchemy import func
            max_version = db.query(func.max(database.ModelVersion.version)).filter(
                database.ModelVersion.model_id == model_id
            ).scalar()
            version = (max_version or 0) + 1
            logger.info(f"Auto-calculated version {version} for model {model_id} (max existing: {max_version})")
        else:
            existing = db.query(database.ModelVersion).filter(
                database.ModelVersion.model_id == model_id,
                database.ModelVersion.version == version
            ).first()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"Version {version} already exists for this model"
                )
        
        version_dict = version_data.model_dump()
        version_dict['version'] = version
        db_version = database.ModelVersion(**version_dict, model_id=model_id)
        db.add(db_version)
        db.commit()
        db.refresh(db_version)
        
        if CACHING_ENABLED:
            try:
                invalidate_model_versions(model_id)
                invalidate_model(model_id) 
                invalidate_models_list()  
                try:
                    get_cache().delete(f"{PREFIX}:models:count")
                except Exception:
                    pass  
                logger.info(f"Cache invalidated after registering version for model {model_id} (including models list)")
            except Exception as e:
                logger.warning(f"Failed to invalidate cache after registering version: {e}")
        
        logger.info(
            f"Registered version {db_version.version} for model {model_id} "
            f"(content_hash: {db_version.content_hash[:16]}...)"
        )
        
        return db_version
    except HTTPException as e:
        db.rollback()
        raise e
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
    
    Optimised: By default, versions are NOT loaded (much faster).
    Set include_versions=true to load versions (slower but complete).
    
    Pagination: Default page size is 50, max is 200 per page.
    """
    import time
    start_time = time.time()
    offset = (page - 1) * page_size
    
    cache_time = 0
    if CACHING_ENABLED:
        cache_start = time.time()
        cached_page = get_cached_models_list_page(page, page_size)
        cache_time = time.time() - cache_start
        if cached_page is not None:
            models = []
            for item in cached_page["items"]:
                model = database.Model(
                    id=item["id"],
                    name=item["name"],
                    description=item.get("description"),
                    created_by=item["created_by"]
                )
                if include_versions and item.get("versions"):
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
            
            total = cached_page.get("total", len(cached_page.get("items", [])))
            total_pages = (total + page_size - 1) // page_size if total > 0 else 0
            
            total_time = time.time() - start_time
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"GET /models: Cache HIT (page {page}/{total_pages}) in {total_time*1000:.2f}ms "
                    f"(cache_check: {cache_time*1000:.2f}ms, items: {len(models)}/{total})"
                )
            else:
                logger.info(f"GET /models: Cache HIT (page {page}/{total_pages}, {len(models)} items)")
            
            return PaginatedModels(
                items=models,
                total=total,
                page=cached_page.get("page", page),
                page_size=cached_page.get("page_size", page_size),
                total_pages=total_pages
            )
        logger.debug(f"GET /models: Cache MISS (cache_check: {cache_time*1000:.2f}ms)")
    
    try:
        async with read_throttle_semaphore:
            query_start = time.time()
            
            total = None
            if CACHING_ENABLED:
                cache_key = f"{PREFIX}:models:count"
                try:
                    cached_count = get_cache().get(cache_key)
                    if cached_count is not None:
                        total = int(cached_count)
                        logger.debug(f"Using cached model count: {total}")
                except Exception:
                    pass  
            
            if total is None:
                total = db.query(func.count(database.Model.id)).scalar()
                if CACHING_ENABLED:
                    try:
                        get_cache().set(f"{PREFIX}:models:count", str(total), ttl=60)
                    except Exception:
                        pass  
            
            total_pages = (total + page_size - 1) // page_size if total > 0 else 0
            
            query = db.query(database.Model).order_by(database.Model.id)
            if include_versions:
                query = query.options(selectinload(database.Model.versions))
            models = query.offset(offset).limit(page_size).all()
            
            query_time = time.time() - query_start
            
            cache_populate_time = 0
            if CACHING_ENABLED:
                cache_populate_start = time.time()
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
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"GET /models: Completed (page {page}/{total_pages}) in {total_time*1000:.2f}ms "
                    f"(query: {query_time*1000:.2f}ms, cache_check: {cache_time*1000:.2f}ms, "
                    f"cache_populate: {cache_populate_time*1000:.2f}ms, "
                    f"include_versions: {include_versions}, items: {len(models)}, total: {total})"
                )
            else:
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
        
        if CACHING_ENABLED:
            try:
                cached_list = get_cached_models_list()
                if cached_list is not None:
                    new_model_data = {
                        "id": db_model.id,
                        "name": db_model.name,
                        "description": db_model.description,
                        "created_by": db_model.created_by,
                        "versions": [] 
                    }
                    cached_list.append(new_model_data)
                    cache_models_list(cached_list)
                    logger.info(f"Cache updated (write-through) after creating model {db_model.id}")
                else:
                    invalidate_models_list()
                    logger.info(f"Cache invalidated (cache was empty) after creating model {db_model.id}")
                try:
                    get_cache().delete(f"{PREFIX}:models:count")
                except Exception:
                    pass  
            except Exception as e:
                logger.warning(f"Failed to update cache after creating model: {e}")
                try:
                    invalidate_models_list()
                    get_cache().delete(f"{PREFIX}:models:count")
                except Exception:
                    pass  
        
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
    
    if CACHING_ENABLED:
        cached = get_cached_model(model_id)
        if cached is not None:
            model = database.Model(
                id=cached["id"],
                name=cached["name"],
                description=cached.get("description"),
                created_by=cached["created_by"]
            )
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
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"GET /models/{model_id}: Cache HIT in {total_time*1000:.2f}ms (include_versions: {include_versions})")
            return model
    
    async with read_throttle_semaphore:
        query_start = time.time()
        query = db.query(database.Model).filter(database.Model.id == model_id)
        if include_versions:
            query = query.options(selectinload(database.Model.versions))
        db_model = query.first()
        query_time = time.time() - query_start
        
        high_latency_models = [7154, 7192, 7181, 7182]  
        if query_time > 0.5 or model_id in high_latency_models:
            logger.warning(
                f"Slow query detected: GET /models/{model_id} "
                f"(query_time: {query_time*1000:.2f}ms, include_versions: {include_versions})"
            )
            if model_id in high_latency_models:
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

    cache_populate_time = 0
    if CACHING_ENABLED:
        cache_start = time.time()
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
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            f"GET /models/{model_id}: Cache MISS in {total_time*1000:.2f}ms "
            f"(query: {query_time*1000:.2f}ms, cache_populate: {cache_populate_time*1000:.2f}ms, "
            f"include_versions: {include_versions})"
        )
    else:
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
    
    if CACHING_ENABLED:
        cached = get_cached_model_latest(model_id)
        if cached is not None:
            total_time = time.time() - start_time
            logger.info(f"GET /models/{model_id}/latest: Cache HIT in {total_time*1000:.2f}ms")
            return cached
    
    try:
        query_start = time.time()
        latest_version = (
            db.query(database.ModelVersion)
            .filter(database.ModelVersion.model_id == model_id)
            .order_by(desc(database.ModelVersion.version))
            .first()
        )
        query_time = time.time() - query_start
        
        high_latency_models = [7154, 7192, 7181, 7182] 
        if query_time > 0.5 or model_id in high_latency_models:  
            logger.warning(
                f"Slow query detected: GET /models/{model_id}/latest "
                f"(query_time: {query_time*1000:.2f}ms)"
            )
            if model_id in high_latency_models:
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
            model_exists = db.query(database.Model.id).filter(database.Model.id == model_id).scalar()
            if not model_exists:
                raise HTTPException(status_code=404, detail="Model not found")
            raise HTTPException(status_code=404, detail="No versions found for this model.")

        result = {"storage_path": latest_version.storage_path}
        
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
    
    if CACHING_ENABLED and offset == 0:
        cached = get_cached_model_versions(model_id)
        if cached is not None:
            paginated_cached = cached[:limit]
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
    
    try:
        async with read_throttle_semaphore:
            query_start = time.time()
            versions = (
                db.query(database.ModelVersion)
                .filter(database.ModelVersion.model_id == model_id)
                .order_by(desc(database.ModelVersion.version))
                .offset(offset)
                .limit(limit)
                .all()
            )
            query_time = time.time() - query_start
            
            high_latency_models = [7154, 7192, 7181, 7182] 
            if query_time > 0.5 or model_id in high_latency_models:
                version_count = len(versions)
                logger.warning(
                    f"Slow query detected: GET /models/{model_id}/versions "
                    f"(query_time: {query_time*1000:.2f}ms, versions_returned: {version_count}, "
                    f"limit: {limit}, offset: {offset})"
                )
                if model_id in high_latency_models:
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
            
            if not versions and offset == 0:
                model_exists = db.query(database.Model.id).filter(database.Model.id == model_id).scalar()
                if not model_exists:
                    raise HTTPException(status_code=404, detail="Model not found")
        
        cache_populate_time = 0
        if CACHING_ENABLED and offset == 0:
            cache_start = time.time()
            versions_data = [
                {"id": v.id, "version": v.version, "storage_path": v.storage_path,
                    "content_hash": v.content_hash, "model_id": v.model_id}
                for v in versions
            ]
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
    if content_hash.startswith("sha256:"):
        normalized_hash = content_hash
        hash_without_prefix = content_hash[7:]  
    else:
        normalized_hash = f"sha256:{content_hash}"
        hash_without_prefix = content_hash
    
    if CACHING_ENABLED:
        cached = get_cached_version_by_hash(normalized_hash)
        if cached is not None:
            logger.debug(f"Cache HIT for version hash {normalized_hash[:20]}...")
            return cached
    
    try:
        version = (
            db.query(database.ModelVersion)
            .filter(database.ModelVersion.content_hash == normalized_hash)
            .first()
        )
        
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
    
    Returns:
        {
            "has_access": bool,      # True if user can access the model
            "is_owner": bool,         # True if user created the model
            "model_id": int           # The model ID
        }
    """
    import time
    start_time = time.time()
    
    if CACHING_ENABLED:
        cached = get_cached_model_ownership(model_id, user_id)
        if cached is not None:
            total_time = time.time() - start_time
            logger.info(f"GET /models/{model_id}/ownership: Cache HIT in {total_time*1000:.2f}ms")
            return cached
    
    try:
        query_start = time.time()
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
    if is_admin_header:
        return is_admin_header.lower() == 'true'
    
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
        
        versions = db.query(database.ModelVersion).filter(
            database.ModelVersion.model_id == model_id
        ).all()
        for version in versions:
            db.delete(version)
        
        db.delete(db_model)
        db.commit()
        db.expire_all()
        
        if CACHING_ENABLED:
            try:
                invalidate_models_list()  
                invalidate_model(model_id)
                invalidate_model_ownership(model_id)
                get_cache().delete(f"{PREFIX}:models:count")
                logger.info(f"All caches invalidated after deleting model {model_id}")
                
                try:
                    cached_list = get_cached_models_list()
                    if cached_list is not None:
                        cached_list = [m for m in cached_list if m.get("id") != model_id]
                        cache_models_list(cached_list)
                        logger.info(f"Cache updated (write-through) after deleting model {model_id}")
                except Exception:
                    pass 
            except Exception as e:
                logger.warning(f"Failed to update cache after deleting model: {e}")
                try:
                    invalidate_models_list()
                    invalidate_model(model_id)
                    invalidate_model_ownership(model_id)
                    get_cache().delete(f"{PREFIX}:models:count")
                except Exception:
                    pass  
        
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