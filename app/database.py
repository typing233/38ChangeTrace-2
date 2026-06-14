import os
import logging

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import settings
from app.models import Base

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.DB_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    os.makedirs(settings.SCREENSHOTS_DIR, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            __import__("sqlalchemy").text("PRAGMA journal_mode=WAL")
        )
    await run_migrations()
    logger.info("Database initialized")


async def run_migrations():
    from app.migration import migrate
    async with engine.begin() as conn:
        await migrate(conn)
