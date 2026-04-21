import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from typing import AsyncGenerator

from core.settings import settings

log = structlog.get_logger()


# ============================================================
# DATABASE ENGINE
# The engine is the core connection factory.
# Created once when the application starts.
# Never created per-request — that would be too expensive.
# ============================================================
engine: AsyncEngine = create_async_engine(
    url=settings.database_url,
    echo=settings.app_env == "development",
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=settings.db_pool_recycle,
    pool_timeout=30,
    connect_args={
        "server_settings": {
            "application_name": "llm-dashboard"
        }
    },
)

# ============================================================
# SESSION FACTORY
# Creates new session objects on demand.
# A session = one unit of work with the database.
# expire_on_commit=False means objects stay usable after commit.
# ============================================================
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Alias for backwards compatibility / external imports.
async_session_factory = AsyncSessionLocal


# ============================================================
# BASE CLASS FOR ALL DATABASE MODELS
# Every table definition in the project inherits from this.
# SQLAlchemy uses it to track all tables and their structure.
# ============================================================
class Base(DeclarativeBase):
    pass


# ============================================================
# DATABASE DEPENDENCY FOR FASTAPI
# Used in API endpoints like:
#   async def my_endpoint(db: AsyncSession = Depends(get_db)):
# FastAPI opens a session before the endpoint runs.
# FastAPI closes it after — even if an error occurs.
# ============================================================
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ============================================================
# STARTUP AND SHUTDOWN HELPERS
# Called when the application starts and stops.
# ============================================================
async def init_db() -> None:
    """
    Called on application startup.
    Verifies the database is reachable.
    Does NOT create tables — Alembic handles that.
    """
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        log.info("database.connected", url=settings.database_url)
    except Exception as e:
        log.error("database.connection_failed", error=str(e))
        raise


async def close_db() -> None:
    """
    Called on application shutdown.
    Closes all connections in the pool cleanly.
    """
    await engine.dispose()
    log.info("database.disconnected")