import os
import logging
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, Index, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, timezone
import random

logger = logging.getLogger(__name__)


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rllabs:rllabs_password@localhost/model_catalog_db")

DATABASE_REPLICA_URLS = os.getenv("DATABASE_REPLICA_URLS", "")

engine = create_engine(
    DATABASE_URL,
    pool_size=15,  
    max_overflow=10, 
    pool_pre_ping=True,  
    pool_recycle=3600,  
    pool_timeout=3,  
    connect_args={
        "connect_timeout": 3,  
        "application_name": "model_catalog_service",
        "options": "-c statement_timeout=2000"  
    },
    echo=False  
)

replica_engines = []
if DATABASE_REPLICA_URLS:
    replica_urls = [url.strip() for url in DATABASE_REPLICA_URLS.split(",") if url.strip()]
    for replica_url in replica_urls:
        replica_engine = create_engine(
            replica_url,
            pool_size=15,  
            max_overflow=10,  
            pool_pre_ping=True,
            pool_recycle=1800,  
            pool_timeout=3,  
            connect_args={
                "connect_timeout": 2, 
                "application_name": "model_catalog_service_read",
                "options": "-c statement_timeout=2000"  
            },
            echo=False  
        )
        replica_engines.append(replica_engine)
    logger.info(f"Configured {len(replica_engines)} read replica(s) for load balancing")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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
        ReadSessionLocal = random.choice(ReadSessionLocals)
        db_source = "read_replica"
        if logger.isEnabledFor(logging.DEBUG):
            replica_index = ReadSessionLocals.index(ReadSessionLocal)
            logger.debug(f"Using read replica #{replica_index + 1} (total replicas: {len(ReadSessionLocals)})")
    else:
        ReadSessionLocal = SessionLocal
        db_source = "primary"
        if logger.isEnabledFor(logging.WARNING):
            logger.warning("Read replica not configured - using primary database for reads (not optimal)")
    
    db = ReadSessionLocal()
    try:
        if logger.isEnabledFor(logging.DEBUG):
            try:
                conn = db.connection()
                db_url = str(conn.engine.url).replace(conn.engine.url.password or '', '***') if hasattr(conn, 'engine') else "unknown"
                logger.debug(f"Read DB session created from {db_source} (connection: {db_url[:50]}...)")
            except Exception:
                pass  
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
    created_by = Column(String, index=True) 
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    versions = relationship("ModelVersion", back_populates="model")
    
    __table_args__ = (
        Index('idx_models_id_created_by', 'id', 'created_by'),
        Index('idx_models_created_at', 'created_at'),
    )

class ModelVersion(Base):
    __tablename__ = "model_versions"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(Integer, ForeignKey("models.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    storage_path = Column(String, nullable=False)
    content_hash = Column(String, nullable=False) 
    created_at = Column(DateTime, default=datetime.now(timezone.utc))

    model = relationship("Model", back_populates="versions")

    __table_args__ = (
        UniqueConstraint('model_id', 'version', name='_model_version_uc'),
        UniqueConstraint('model_id', 'content_hash', name='_model_content_hash_uc'),
        Index('idx_model_version_model_id', 'model_id'),  
        Index('idx_model_version_version', 'version'), 
        Index('idx_model_version_model_version', 'model_id', 'version'),  
        Index('idx_model_version_content_hash', 'content_hash'),  
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
        
        with engine.connect() as conn:
            indexes_to_check = [
                ('idx_model_version_model_id', 'model_versions', 'model_id'),
                ('idx_model_version_version', 'model_versions', 'version'),
                ('idx_model_version_model_version', 'model_versions', 'model_id, version'),
                ('idx_model_version_content_hash', 'model_versions', 'content_hash'),
                ('idx_models_created_by', 'models', 'created_by'), 
                ('idx_models_id_created_by', 'models', 'id, created_by'),  
                ('idx_models_created_at', 'models', 'created_at'), 
            ]
            
            desc_indexes = [
                ('idx_model_version_model_id_version_desc', 'model_versions', 'model_id', 'version DESC'),
                ('idx_models_created_at_desc', 'models', 'created_at', 'DESC'),
            ]
            
            for idx_name, table_name, columns in indexes_to_check:
                try:
                    result = conn.execute(text(
                        f"SELECT 1 FROM pg_indexes WHERE indexname = '{idx_name}'"
                    ))
                    if result.fetchone() is None:
                        print(f"Creating index {idx_name} on {table_name}({columns})...")
                        conn.execute(text(
                            f"CREATE INDEX {idx_name} ON {table_name}({columns})"
                        ))
                        conn.commit()
                        print(f"Created index: {idx_name}")
                    else:
                        print(f"Index {idx_name} already exists")
                except Exception as idx_error:
                    print(f"Could not create/verify index {idx_name}: {idx_error}")
            
            for idx_tuple in desc_indexes:
                try:
                    idx_name, table_name, col1, col2_desc = idx_tuple
                    result = conn.execute(text(
                        f"SELECT 1 FROM pg_indexes WHERE indexname = '{idx_name}'"
                    ))
                    if result.fetchone() is None:
                        if col2_desc == 'DESC':
                            print(f"Creating DESC index {idx_name} on {table_name}({col1} DESC)...")
                            conn.execute(text(
                                f"CREATE INDEX {idx_name} ON {table_name}({col1} DESC)"
                            ))
                        else:
                            print(f"Creating DESC index {idx_name} on {table_name}({col1}, {col2_desc})...")
                            conn.execute(text(
                                f"CREATE INDEX {idx_name} ON {table_name}({col1}, {col2_desc})"
                            ))
                        conn.commit()
                        print(f"Created DESC index: {idx_name}")
                    else:
                        print(f"DESC index {idx_name} already exists")
                except Exception as idx_error:
                    print(f"Could not create/verify DESC index {idx_name}: {idx_error}")
    except Exception as e:
        print(f"Error creating database tables: {e}")