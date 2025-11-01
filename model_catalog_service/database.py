import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, timezone

# This module sets up the database connection and defines the data model schema
# for the model catalog.

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rllabs:rllabs_password@localhost/model_catalog_db")

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# ORM Models
class Model(Base):
    __tablename__ = "models"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(String)
    created_by = Column(String, index=True) # User ID from the API Gateway
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    versions = relationship("ModelVersion", back_populates="model")

class ModelVersion(Base):
    __tablename__ = "model_versions"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(Integer, ForeignKey("models.id"), nullable=False)
    version = Column(Integer, nullable=False)
    storage_path = Column(String, nullable=False)
    content_hash = Column(String, nullable=False) # SHA-256 of the model file
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    model = relationship("Model", back_populates="versions")

    __table_args__ = (UniqueConstraint('model_id', 'version', name='_model_version_uc'),
                      UniqueConstraint('model_id', 'content_hash', name='_model_content_hash_uc'))


def get_db():
    """
    To get a DB session for a request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_db_and_tables():
    """
    Creates the database tables. 
    Called on startup.
    """
    try:
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully.")
    except Exception as e:
        print(f"Error creating database tables: {e}")