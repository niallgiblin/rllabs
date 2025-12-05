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
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum as SQLEnum, Text, Index, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone
import enum
import random

# Database connection
# NOTE: In production, use connection pooling and read replicas for scalability
# Primary database URL (for writes)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://rllabs:rllabs_password@postgres/upload_download_db"
)

# Read replica URLs (comma-separated, for reads)
# If not set, falls back to primary for both reads and writes
DATABASE_REPLICA_URLS = os.getenv("DATABASE_REPLICA_URLS", "")

# Primary engine (for writes)
# Configure connection pooling for better performance under load
# Optimized for 30-100 user load: increased pool sizes to handle concurrent uploads
# With 3 pods, each pod can use up to 40 connections (20 base + 20 overflow)
# Total: 3 pods × 40 = 120 connections (may require PostgreSQL max_connections > 100)
engine = create_engine(
    DATABASE_URL,
    pool_size=20,  # Base pool size - sufficient for normal load
    max_overflow=20,  # Total 40 per pod - handles burst traffic
    pool_pre_ping=True,  # Verify connections before using (handles network issues)
    pool_recycle=3600,  # Recycle connections after 1 hour (prevents stale connections)
    pool_timeout=3,  # REDUCED from 5s - fail faster if pool is exhausted (prevents queuing delays)
    connect_args={
        "connect_timeout": 10,  # Connection timeout in seconds
        "application_name": "upload_download_service",
        "options": "-c statement_timeout=2000"  # REDUCED to 2s - fail fast if database is slow (was 3s)
    }
)

# Read replica engines (for reads)
replica_engines = []
if DATABASE_REPLICA_URLS:
    replica_urls = [url.strip() for url in DATABASE_REPLICA_URLS.split(",") if url.strip()]
    for replica_url in replica_urls:
        replica_engine = create_engine(
            replica_url,
            pool_size=10,  # INCREASED from 5 - base pool size
            max_overflow=10,  # INCREASED from 5 - total 20 per pod per replica
            pool_pre_ping=True,
            pool_recycle=3600,  # Recycle connections after 1 hour
            pool_timeout=5,  # INCREASED from default - allow more time for connection acquisition (reduces queuing)
            connect_args={
                "connect_timeout": 10,
                "application_name": "upload_download_service_read"
            }
        )
        replica_engines.append(replica_engine)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_read_db():
    """
    Get a database session for read-only queries.
    Uses read replicas if available, otherwise falls back to primary.
    """
    if replica_engines:
        # Randomly select a replica for load balancing
        replica_engine = random.choice(replica_engines)
        ReadSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=replica_engine)
    else:
        # Fallback to primary if no replicas configured
        ReadSessionLocal = SessionLocal
    
    db = ReadSessionLocal()
    try:
        yield db
    finally:
        db.close()

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
    
    __table_args__ = (
        # Composite indexes for common query patterns
        Index('idx_upload_sessions_status_user_id', 'status', 'user_id'),
        Index('idx_upload_sessions_user_id_created_at', 'user_id', 'created_at'),
        Index('idx_upload_sessions_user_id_upload_id', 'user_id', 'upload_id'),  # Composite index for faster lookups by user_id and upload_id
    )


def get_db():
    """
    Database dependency for FastAPI (writes go to primary).
    For read-only queries, use get_read_db() instead.
    
    Creates a new database session for each request.
    Automatically closes the session after the request completes.
    
    Usage:
        @app.get("/endpoint")
        def my_endpoint(db: Session = Depends(get_db)):
            # Use db here for writes
        @app.get("/read-endpoint")
        def my_read_endpoint(db: Session = Depends(get_read_db)):
            # Use db here for reads
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
        
        # Create additional indexes with DESC ordering (not supported in SQLAlchemy Index())
        with engine.connect() as conn:
            indexes_to_create = [
                ('idx_upload_sessions_status_user_id', 'upload_sessions', 'status, user_id'),
                ('idx_upload_sessions_user_id_created_at', 'upload_sessions', 'user_id, created_at DESC'),
                ('idx_upload_sessions_user_id_upload_id', 'upload_sessions', 'user_id, upload_id'),  # Composite index for faster lookups
            ]
            
            for idx_name, table_name, columns in indexes_to_create:
                try:
                    # Check if index exists
                    result = conn.execute(text(
                        f"SELECT 1 FROM pg_indexes WHERE indexname = '{idx_name}'"
                    ))
                    if result.fetchone() is None:
                        print(f"Creating index {idx_name} on {table_name}({columns})...")
                        conn.execute(text(
                            f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name}({columns})"
                        ))
                        conn.commit()
                        print(f"✅ Created index: {idx_name}")
                    else:
                        print(f"✅ Index {idx_name} already exists")
                except Exception as idx_error:
                    print(f"⚠️  Could not create/verify index {idx_name}: {idx_error}")
                    # Continue - index might already exist or table might not be ready
    except Exception as e:
        print(f"✗ Error creating database tables: {e}")
        raise
