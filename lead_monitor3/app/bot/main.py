import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import text

from app.bot.handlers.admin import router
from app.core.config import settings
from app.database.models import Base
from app.database.session import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

MIGRATIONS = [
    ("accounts", "is_running",       "BOOLEAN DEFAULT FALSE"),
    ("accounts", "notify_enabled",   "BOOLEAN DEFAULT FALSE"),
    ("accounts", "work_mode",        "VARCHAR(24) DEFAULT 'chat_reply'"),
    ("accounts", "match_mode",       "VARCHAR(16) DEFAULT 'mixed'"),
    ("accounts", "parent_account_id","INTEGER"),
    ("logs",     "worker_id",        "INTEGER"),
    ("logs",     "username",         "VARCHAR(128)"),
    ("logs",     "keyword",          "TEXT"),
    ("logs",     "error",            "TEXT"),
]


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table, col, definition in MIGRATIONS:
            sp = await conn.begin_nested()
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {definition}"))
                await sp.commit()
            except Exception:
                await sp.rollback()
    log.info("Database ready")


async def main() -> None:
    await init_db()

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    me = await bot.get_me()
    log.info("Bot started: @%s", me.username)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
