import asyncio
import sys
import pytest
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from core.settings import settings

# Switch Windows to SelectorEventLoop — stable with asyncio libraries
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_engine():
    """
    Test-only engine using NullPool.
    NullPool never reuses connections — each operation gets a fresh one.
    This avoids the 'event loop is closed' error on Windows + Python 3.13.
    """
    engine = create_async_engine(
        url=settings.database_url,
        poolclass=NullPool,
        echo=False,
    )
    yield engine


@pytest.fixture(scope="session")
def test_session_factory(test_engine):
    """Session factory bound to the test engine."""
    return async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )