# app/database.py
# -----------------------------------------------------------------------------
# SQLAlchemy session/engine setup and a convenience contextmanager for
# transactional scopes.
#
# Responsibilities
# - Define the declarative Base and global Engine bound to DATABASE_URL.
# - Provide a configured session factory (SessionLocal) with sensible defaults.
# - Expose a `create_db_and_tables()` helper to initialize schema from models.
# - Offer `session_scope()` for "unit of work" style DB interactions that
#   commit on success and rollback on exceptions, then always close the session.
#
# Notes for maintainers:
# - `pool_pre_ping=True` proactively tests connections from the pool, reducing
#   "MySQL server has gone away" / stale connection errors.
# - `autocommit=False` & `autoflush=False` give callers explicit control over
#   transaction boundaries and when pending changes are sent to the DB.
# - `create_db_and_tables()` imports models to ensure all table metadata is
#   registered on Base before calling `create_all`.
# - `session_scope()` is intended to be used as:
#       with session_scope() as db:
#           ... use `db` ...
#   It will commit if no exception is raised, otherwise rollback and re-raise.
# -----------------------------------------------------------------------------

from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from .settings import settings

# SQLAlchemy base and engine
Base = declarative_base()
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def create_db_and_tables() -> None:
    """Create all tables discovered via Base metadata.
    Models are imported by modules that reference Base (see app.models).

    Usage
    -----
    Call once at application startup or during migrations to ensure the schema
    exists. This is a convenience for small services; larger deployments should
    prefer explicit migrations (e.g., Alembic).
    """
    # Import models to register tables with Base before create_all
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope():
    """
    Provide a transactional scope around a series of database operations.

    Behavior
    --------
    - Yields a new SessionLocal instance.
    - On normal exit, commits the transaction.
    - On exception, rolls back and re-raises the error.
    - Always closes the session in a finally block.

    This pattern centralizes transaction handling and ensures resources
    are cleaned up reliably, reducing boilerplate in route/service code.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
