"""SQLAlchemy async engine + sync engine for Celery."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import settings


def _async_connect_args() -> dict:
    """asyncpg + PgBouncer transaction pool: disable prepared statement cache
    so reconnects via PgBouncer don't reuse stale statement plans.

    `statement_cache_size=0` is the asyncpg-level keyword; SQLAlchemy's
    `prepared_statement_cache_size` is NOT an asyncpg.connect kwarg and is
    rejected by the asyncpg dialect when placed in connect_args.
    """
    return {
        "statement_cache_size": 0,
    }


_async_engine = create_async_engine(
    str(settings.DATABASE_URL),
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,
    future=True,
    connect_args=_async_connect_args(),
)

_async_replica_engine = (
    create_async_engine(
        str(settings.DATABASE_REPLICA_URL),
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=True,
        future=True,
        connect_args=_async_connect_args(),
    )
    if settings.DATABASE_REPLICA_URL
    else None
)

_sync_engine = create_engine(
    str(settings.SYNC_DATABASE_URL),
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    _async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

SyncSessionLocal = sessionmaker(
    _sync_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def get_async_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_async_db_context() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@contextmanager
def get_sync_db() -> Iterator[Session]:
    session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def get_replica_db() -> AsyncIterator[AsyncSession]:
    engine = _async_replica_engine or _async_engine
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session


async def close_engines() -> None:
    await _async_engine.dispose()
    if _async_replica_engine is not None:
        await _async_replica_engine.dispose()
    _sync_engine.dispose()
