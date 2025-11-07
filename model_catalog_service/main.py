from fastapi import FastAPI, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import text, desc
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from contextlib import asynccontextmanager
import logging

logger = logging.getLogger(__name__)

import database
from pydantic import BaseModel

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

# Healthcheck
@app.get("/health", tags=["Monitoring"])
async def health_check(db: Session = Depends(database.get_db)):
    """
    Provides the health status of the service.
    """
    try:
        db.execute(text('SELECT 1'))
        db_status = "online"
    except Exception:
        db_status = "offline"
    
    return {"service_status": "ok", "dependencies": {"database": db_status}}

# API endpoints
@app.post("/models/{model_id}/versions", response_model=ModelVersion, status_code=201, tags=["Models"])
async def register_model_version(model_id: int, version_data: ModelVersionCreate, db: Session = Depends(database.get_db)):
    """
    Registers a new model version. This is called by the upload service after a file is successfully uploaded.
    """
    db_model = db.query(database.Model).filter(database.Model.id == model_id).first()
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")

    try:
        db_version = database.ModelVersion(**version_data.model_dump(), model_id=model_id)
        db.add(db_version)
        db.commit()
        db.refresh(db_version)
        return db_version
    except IntegrityError as e:
        print(f"IntegrityError caught in register_model_version: {e}")
        db.rollback()
        raise HTTPException(status_code=409, detail="This model version or content hash already exists for this model.")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"An unexpected database error occurred: {e}")

@app.get("/models", response_model=List[Model], tags=["Models"])
async def list_models(db: Session = Depends(database.get_db)):
    """
    Lists all models.
    """
    try:
        models = db.query(database.Model).all()
        return models
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database is unavailable: {e}")

@app.post("/models", response_model=Model, status_code=201, tags=["Models"])
async def create_model(model: ModelCreate, db: Session = Depends(database.get_db), user_id: str = Header(..., alias="X-User-Id", required=True)):
    """
    Creates a new model entry.
    """
    try:
        db_model = database.Model(**model.model_dump(), created_by=user_id)
        db.add(db_model)
        db.commit()
        db.refresh(db_model)
        
        # Publish ModelCreated event asynchronously
        if EVENT_PUBLISHING_ENABLED:
            try:
                publisher = get_event_publisher()
                if publisher:
                    publisher.publish_model_created(db_model.id, db_model.name, db_model.created_by)
            except Exception as e:
                print(f"Failed to publish ModelCreated event: {e}")
                # Don't fail the request if event publishing fails
        
        return db_model
    except IntegrityError as e:
        print(f"IntegrityError caught in create_model: {e}")
        db.rollback()
        raise HTTPException(status_code=409, detail="Model with this name already exists.")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"An unexpected database error occurred: {e}")

@app.get("/models/{model_id}", response_model=Model, tags=["Models"])
async def get_model_details(model_id: int, db: Session = Depends(database.get_db)):
    """
    Retrieves details for a specific model.
    """
    db_model = db.query(database.Model).filter(database.Model.id == model_id).first()

    if db_model is None:
        raise HTTPException(status_code=404, detail="Model not found")

    return db_model

@app.get("/models/{model_id}/latest", response_model=LatestModelPath, tags=["Models"])
async def get_latest_model_path(model_id: int, db: Session = Depends(database.get_db)):
    """
    Gets the storage path of the latest version for a given model.
    """
    try:
        latest_version = (
            db.query(database.ModelVersion)
            .filter(database.ModelVersion.model_id == model_id)
            .order_by(desc(database.ModelVersion.version))
            .first()
        )

        if not latest_version:
            raise HTTPException(status_code=404, detail="No versions found for this model.")

        return {"storage_path": latest_version.storage_path}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=503, detail=f"Database is unavailable: {e}")

@app.get("/models/{model_id}/ownership", tags=["Models"])
async def check_model_ownership(
    model_id: int,
    user_id: str = Header(..., alias="X-User-Id"),
    db: Session = Depends(database.get_db)
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
    db_model = db.query(database.Model).filter(database.Model.id == model_id).first()
    
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    is_owner = db_model.created_by == user_id
    has_access = is_owner
    
    return {
        "has_access": has_access,
        "is_owner": is_owner,
        "model_id": model_id
    }

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
        
        # Delete model (cascades to versions via foreign key)
        db.delete(db_model)
        db.commit()
        
        # Publish ModelDeleted event asynchronously
        if EVENT_PUBLISHING_ENABLED:
            try:
                publisher = get_event_publisher()
                if publisher:
                    publisher.publish_model_deleted(model_id, model_name)
            except Exception as e:
                logger.error(f"Failed to publish ModelDeleted event: {e}")
                # Don't fail the request if event publishing fails
        
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