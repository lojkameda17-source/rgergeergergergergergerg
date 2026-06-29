from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
