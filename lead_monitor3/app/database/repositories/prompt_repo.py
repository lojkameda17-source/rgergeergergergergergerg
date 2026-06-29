from sqlalchemy import select
from app.database.models import Prompt
from app.database.session import async_session


class PromptRepo:
    async def get(self, account_id: int, scope: str, default: str) -> str:
        async with async_session() as s:
            res = await s.execute(
                select(Prompt).where(Prompt.account_id == account_id, Prompt.scope == scope)
            )
            row = res.scalar_one_or_none()
            return row.content if row else default

    async def upsert(self, account_id: int, scope: str, content: str) -> None:
        async with async_session() as s:
            res = await s.execute(
                select(Prompt).where(Prompt.account_id == account_id, Prompt.scope == scope)
            )
            row = res.scalar_one_or_none()
            if row:
                row.content = content
            else:
                s.add(Prompt(account_id=account_id, scope=scope, content=content))
            await s.commit()

    async def list_for(self, account_id: int) -> list[Prompt]:
        async with async_session() as s:
            res = await s.execute(select(Prompt).where(Prompt.account_id == account_id))
            return list(res.scalars().all())
