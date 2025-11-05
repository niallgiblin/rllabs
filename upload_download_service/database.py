"""
Database Configuration and Models
==================================

PostgreSQL stores upload session metadata.

Architectural Decision: Why PostgreSQL for sessions?
- Trade-off: More complex than Redis-only, but provides:
  1. Persistent audit trail of all uploads
  2. Complex queries (e.g., "show all uploads by user X")
  3. Transactional consistency
  4. Relationship with Model Catalog database

Alternative considered: Redis-only
- Pros: Simpler, faster
- Cons: No persistence, limited querying, no transactions
- Decision: Use PostgreSQL for metadata, Redis for ephemeral data (idempotency)
"""

import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum as SQLEnum, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone
import enum

# Database connection
# NOTE: In production, use connection pooling and read replicas for scalability
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://rllabs:rllabs_password@postgres/upload_download_db"
)

engine = create_engine(
    DATABASE_URL,
    pool_size=10,  # Connection pool for handling concurrent requests
    max_overflow=20,  # Allow extra connections under load
    pool_pre_ping=True  # Verify connections before using (handles network issues)
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class UploadStatus(enum.Enum):
    """
    Upload session status lifecycle
    
    INITIATED -> COMPLETED (success path)
    INITIATED -> FAILED (error path)
    INITIATED -> ABORTED (user cancellation)
    """
    INITIATED = "initiated"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"

class UploadSession(Base):
    """
    Upload Session Model
    
    Tracks the lifecycle of a multipart upload.
    Each upload creates one session with multiple parts.
    
    """
    __tablename__ = "upload_sessions"

    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(String, unique=True, index=True, nullable=False)  # UUID
    minio_upload_id = Column(String, nullable=False)  # MinIO's multipart upload ID
    
    # File metadata
    filename = Column(String, nullable=False) # Original filename
    file_size = Column(Integer, nullable=False) # Expected total size in bytes
    file_hash = Column(String, nullable=False, index=True)  # sha256:...hash for integrity verification
    chunk_size = Column(Integer, nullable=False) # Size of each uploaded chunk in bytes
    artifact_type = Column(String, nullable=False)  # model, environment, dataset
    
    # Relationships
    model_id = Column(Integer, nullable=False, index=True)  # Links to Model Catalog
    
    # Storage
    storage_path = Column(String, nullable=True)  # Final S3 path after completion
    
    # Current upload status
    status = Column(
        SQLEnum(UploadStatus),
        default=UploadStatus.INITIATED,
        nullable=False,
        index=True
    )
    
    # Audit fields
    user_id = Column(String, nullable=False, index=True) # Who initiated the upload
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False) # When session was created
    completed_at = Column(DateTime, nullable=True) # When upload finished (or failed)
    error_message = Column(Text, nullable=True) # Error details if status=FAILED


def get_db():
    """
    Database dependency for FastAPI
    
    Creates a new database session for each request.
    Automatically closes the session after the request completes.
    
    Usage:
        @app.get("/endpoint")
        def my_endpoint(db: Session = Depends(get_db)):
            # Use db here
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_db_and_tables():
    """
    Create database tables on application startup
    
    SQLAlchemy checks if tables exist before creating.
    Safe to call multiple times (idempotent).
    
    NOTE: In production, use Alembic (db migration tool) for database migrations instead.
    """
    try:
        Base.metadata.create_all(bind=engine)
        print("✓ Database tables created successfully")
    except Exception as e:
        print(f"✗ Error creating database tables: {e}")
        raise
