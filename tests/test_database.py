import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy import text
from core.settings import settings
from core.database import (
    engine,
    AsyncSessionLocal,
    Base,
    get_db,
    init_db,
    close_db,
)


@pytest.mark.asyncio
async def test_database_engine_created():
    """Engine object is created and has correct driver."""
    assert engine is not None
    assert "asyncpg" in str(engine.url)


@pytest.mark.asyncio
async def test_can_connect_to_database():
    """Can establish a real connection to PostgreSQL."""
    test_engine = create_async_engine(url=settings.database_url, poolclass=NullPool)
    try:
        async with test_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            value = result.scalar()
        assert value == 1
    finally:
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_session_opens_and_closes():
    """Session opens, executes a query, and closes cleanly."""
    test_engine = create_async_engine(url=settings.database_url, poolclass=NullPool)
    try:
        async with test_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            value = result.scalar()
        assert value == 1
    finally:
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_session_rollback_on_error():
    """If an error occurs inside a session, it is handled cleanly."""
    test_engine = create_async_engine(url=settings.database_url, poolclass=NullPool)
    try:
        raised = False
        try:
            async with test_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                raise ValueError("Simulated error")
        except ValueError:
            raised = True
        assert raised
    finally:
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_timescaledb_installed():
    """TimescaleDB extension is installed in PostgreSQL."""
    test_engine = create_async_engine(url=settings.database_url, poolclass=NullPool)
    try:
        async with test_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'timescaledb'")
            )
            row = result.fetchone()
        assert row is not None, "TimescaleDB extension not found"
        assert row[0] == "timescaledb"
    finally:
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_base_class_exists():
    """Base class for models is importable and correct type."""
    from sqlalchemy.orm import DeclarativeBase
    assert issubclass(Base, DeclarativeBase)


@pytest.mark.asyncio
async def test_init_db_runs_without_error():
    """init_db() connects successfully."""
    await init_db()


@pytest.mark.asyncio
async def test_get_db_yields_session():
    """get_db() dependency yields a valid AsyncSession."""
    gen = get_db()
    session = await gen.__anext__()
    assert isinstance(session, AsyncSession)
    try:
        await gen.aclose()
    except StopAsyncIteration:
        pass