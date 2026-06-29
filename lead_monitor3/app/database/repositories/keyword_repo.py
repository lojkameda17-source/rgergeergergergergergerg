from sqlalchemy import delete, select, update
from app.database.models import Keyword
from app.database.session import async_session


class KeywordRepo:
    async def list_for(self, account_id: int) -> list[Keyword]:
        async with async_session() as s:
            res = await s.execute(
                select(Keyword).where(Keyword.account_id == account_id).order_by(Keyword.id.desc())
            )
            return list(res.scalars().all())

    async def add_bulk(self, account_id: int, values: list[str]) -> int:
        clean = list({v.strip() for v in values if v.strip()})
        if not clean:
            return 0
        async with async_session() as s:
            for v in clean:
                s.add(Keyword(account_id=account_id, value=v))
            await s.commit()
        return len(clean)

    async def delete_one(self, keyword_id: int) -> None:
        async with async_session() as s:
            await s.execute(delete(Keyword).where(Keyword.id == keyword_id))
            await s.commit()

    async def clear_for(self, account_id: int) -> int:
        async with async_session() as s:
            res = await s.execute(delete(Keyword).where(Keyword.account_id == account_id))
            await s.commit()
            return res.rowcount or 0

    async def bump_trigger(self, keyword_id: int) -> None:
        async with async_session() as s:
            await s.execute(
                update(Keyword)
                .where(Keyword.id == keyword_id)
                .values(triggers_count=Keyword.triggers_count + 1)
            )
            await s.commit()
