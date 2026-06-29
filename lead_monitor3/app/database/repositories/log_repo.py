from sqlalchemy import delete, func, select
from app.database.models import Log
from app.database.session import async_session


class LogRepo:
    async def add(self, **kw) -> Log:
        async with async_session() as s:
            row = Log(**kw)
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row

    async def list_for(self, account_id: int, limit: int = 300) -> list[Log]:
        async with async_session() as s:
            res = await s.execute(
                select(Log).where(Log.account_id == account_id).order_by(Log.id.desc()).limit(limit)
            )
            return list(res.scalars().all())

    async def list_all(self, limit: int = 300) -> list[Log]:
        async with async_session() as s:
            res = await s.execute(select(Log).order_by(Log.id.desc()).limit(limit))
            return list(res.scalars().all())

    async def count_for(self, account_id: int) -> int:
        async with async_session() as s:
            res = await s.execute(select(func.count()).where(Log.account_id == account_id))
            return res.scalar_one() or 0

    async def clear_for(self, account_id: int) -> int:
        async with async_session() as s:
            res = await s.execute(delete(Log).where(Log.account_id == account_id))
            await s.commit()
            return res.rowcount or 0
