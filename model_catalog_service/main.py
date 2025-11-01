from fastapi import FastAPI, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import text, desc
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from contextlib import asynccontextmanager

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

# FastAPI app
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create DB tables on startup
    database.create_db_and_tables()
    yield

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
