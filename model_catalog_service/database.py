import os
import logging
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, Index, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, timezone
import random

logger = logging.getLogger(__name__)

# This module sets up the database connection and defines the data model schema
# for the model catalog.

# Primary database URL (for writes)
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rllabs:rllabs_password@localhost/model_catalog_db")

# Read replica URLs (comma-separated, for reads)
# If not set, falls back to primary for both reads and writes
DATABASE_REPLICA_URLS = os.getenv("DATABASE_REPLICA_URLS", "")

# Configure connection pooling for better performance under load
# Optimized for production: balance between performance and connection limits
# Reduced pool sizes to prevent connection exhaustion at scale
# With 3 pods, each pod uses 25 connections (15 base + 10 overflow) for primary
# Total per pod: 25 (primary) + 25×2 (replicas) = 75 connections per pod
# At 3 pods: 75 × 3 = 225 connections (75% of max_connections=300 - safe)
# Primary engine (for writes)
engine = create_engine(
    DATABASE_URL,
    pool_size=15,  # Reduced from 20 - base pool size
    max_overflow=10,  # Reduced from 20 - total 25 per pod for primary
    pool_pre_ping=True,  # Verify connections before using (handles network issues)
    pool_recycle=3600,  # Recycle connections after 1 hour
    pool_timeout=3,  # REDUCED from 5s - fail faster if pool is exhausted (prevents queuing delays)
    connect_args={
        "connect_timeout": 3,  # Connection timeout
        "application_name": "model_catalog_service",
        "options": "-c statement_timeout=2000"  # REDUCED to 2s - fail fast on slow writes (was 3s)
    },
    echo=False  # Disable SQL logging in production for performance
)

# Read replica engines (for reads)
replica_engines = []
if DATABASE_REPLICA_URLS:
    replica_urls = [url.strip() for url in DATABASE_REPLICA_URLS.split(",") if url.strip()]
    for replica_url in replica_urls:
        replica_engine = create_engine(
            replica_url,
            pool_size=15,  # Reduced from 25 - base pool size
            max_overflow=10,  # Reduced from 15 - total 25 per pod per replica
            pool_pre_ping=True,
            pool_recycle=1800,  # 30 min recycle
            pool_timeout=3,  # REDUCED from 5s - fail faster if pool is exhausted (prevents long waits)
            connect_args={
                "connect_timeout": 2,  # Connection timeout for reads
                "application_name": "model_catalog_service_read",
                "options": "-c statement_timeout=2000"  # REDUCED from 2500ms - fail fast on slow queries
            },
            echo=False  # Disable SQL logging in production for performance
        )
        replica_engines.append(replica_engine)
    logger.info(f"Configured {len(replica_engines)} read replica(s) for load balancing")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create sessionmakers for read replicas once (not on every request)
# This is critical for performance - creating sessionmakers is expensive
ReadSessionLocals = []
if replica_engines:
    for replica_engine in replica_engines:
        ReadSessionLocals.append(sessionmaker(autocommit=False, autoflush=False, bind=replica_engine))
    logger.info(f"Created {len(ReadSessionLocals)} read replica sessionmaker(s)")

def get_read_db():
    """
    Get a database session for read-only queries.
    Uses read replicas if available, otherwise falls back to primary.
    """
    if ReadSessionLocals:
        # Randomly select a replica sessionmaker for load balancing
        # Sessionmakers are pre-created, so this is just selecting which one to use
        ReadSessionLocal = random.choice(ReadSessionLocals)
        db_source = "read_replica"
        # Log which replica is being used (only at debug level to reduce noise)
        if logger.isEnabledFor(logging.DEBUG):
            replica_index = ReadSessionLocals.index(ReadSessionLocal)
            logger.debug(f"Using read replica #{replica_index + 1} (total replicas: {len(ReadSessionLocals)})")
    else:
        # Fallback to primary if no replicas configured
        ReadSessionLocal = SessionLocal
        db_source = "primary"
        if logger.isEnabledFor(logging.WARNING):
            logger.warning("Read replica not configured - using primary database for reads (not optimal)")
    
    db = ReadSessionLocal()
    try:
        # Add connection info to session for debugging
        # This helps verify which database is actually being used
        if logger.isEnabledFor(logging.DEBUG):
            try:
                # Get connection info (without executing a query)
                conn = db.connection()
                db_url = str(conn.engine.url).replace(conn.engine.url.password or '', '***') if hasattr(conn, 'engine') else "unknown"
                logger.debug(f"Read DB session created from {db_source} (connection: {db_url[:50]}...)")
            except Exception:
                pass  # Don't fail if we can't get connection info
        yield db
    finally:
        db.close()

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
    
    __table_args__ = (
        # Composite index for common query patterns (filtering + ordering)
        Index('idx_models_id_created_by', 'id', 'created_by'),
        # Index for ordering by creation date (used in list queries)
        Index('idx_models_created_at', 'created_at'),
    )

class ModelVersion(Base):
    __tablename__ = "model_versions"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(Integer, ForeignKey("models.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    storage_path = Column(String, nullable=False)
    content_hash = Column(String, nullable=False) # SHA-256 of the model file
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    model = relationship("Model", back_populates="versions")

    __table_args__ = (
        UniqueConstraint('model_id', 'version', name='_model_version_uc'),
        UniqueConstraint('model_id', 'content_hash', name='_model_content_hash_uc'),
        # Performance indexes for common queries
        Index('idx_model_version_model_id', 'model_id'),  # For /api/models/{id}/versions queries
        Index('idx_model_version_version', 'version'),  # For ordering by version
        Index('idx_model_version_model_version', 'model_id', 'version'),  # Composite for filtered+ordered queries
        Index('idx_model_version_content_hash', 'content_hash'),  # For hash lookups
    )


def get_db():
    """
    To get a DB session for a request (writes go to primary).
    For read-only queries, use get_read_db() instead.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_db_and_tables():
    """
    Creates the database tables and indexes. 
    Called on startup.
    """
    try:
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully.")
        
        # Ensure indexes are created (SQLAlchemy should handle this, but explicit check)
        with engine.connect() as conn:
            # Check if indexes exist, create if missing (idempotent)
            indexes_to_check = [
                # ModelVersion indexes (already defined in __table_args__)
                ('idx_model_version_model_id', 'model_versions', 'model_id'),
                ('idx_model_version_version', 'model_versions', 'version'),
                ('idx_model_version_model_version', 'model_versions', 'model_id, version'),
                ('idx_model_version_content_hash', 'model_versions', 'content_hash'),
                # Additional indexes for performance
                ('idx_models_created_by', 'models', 'created_by'),  # For filtering by creator
                ('idx_models_id_created_by', 'models', 'id, created_by'),  # Composite for common queries
                ('idx_models_created_at', 'models', 'created_at'),  # For ordering by creation date
            ]
            
            # Special indexes that need DESC ordering (created manually)
            desc_indexes = [
                ('idx_model_version_model_id_version_desc', 'model_versions', 'model_id', 'version DESC'),
                ('idx_models_created_at_desc', 'models', 'created_at', 'DESC'),
            ]
            
            for idx_name, table_name, columns in indexes_to_check:
                try:
                    # Check if index exists
                    result = conn.execute(text(
                        f"SELECT 1 FROM pg_indexes WHERE indexname = '{idx_name}'"
                    ))
                    if result.fetchone() is None:
                        print(f"Creating index {idx_name} on {table_name}({columns})...")
                        conn.execute(text(
                            f"CREATE INDEX {idx_name} ON {table_name}({columns})"
                        ))
                        conn.commit()
                        print(f"✅ Created index: {idx_name}")
                    else:
                        print(f"✅ Index {idx_name} already exists")
                except Exception as idx_error:
                    print(f"⚠️  Could not create/verify index {idx_name}: {idx_error}")
                    # Continue - index might already exist or table might not be ready
            
            # Create DESC indexes manually (for optimized latest version queries)
            for idx_tuple in desc_indexes:
                try:
                    idx_name, table_name, col1, col2_desc = idx_tuple
                    result = conn.execute(text(
                        f"SELECT 1 FROM pg_indexes WHERE indexname = '{idx_name}'"
                    ))
                    if result.fetchone() is None:
                        if col2_desc == 'DESC':
                            # Single column DESC index
                            print(f"Creating DESC index {idx_name} on {table_name}({col1} DESC)...")
                            conn.execute(text(
                                f"CREATE INDEX {idx_name} ON {table_name}({col1} DESC)"
                            ))
                        else:
                            # Composite index with DESC on second column
                            print(f"Creating DESC index {idx_name} on {table_name}({col1}, {col2_desc})...")
                            conn.execute(text(
                                f"CREATE INDEX {idx_name} ON {table_name}({col1}, {col2_desc})"
                            ))
                        conn.commit()
                        print(f"✅ Created DESC index: {idx_name}")
                    else:
                        print(f"✅ DESC index {idx_name} already exists")
                except Exception as idx_error:
                    print(f"⚠️  Could not create/verify DESC index {idx_name}: {idx_error}")
                    # Continue - index might already exist or table might not be ready
    except Exception as e:
        print(f"Error creating database tables: {e}")