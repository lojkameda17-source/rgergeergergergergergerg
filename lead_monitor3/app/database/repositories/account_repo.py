from sqlalchemy import select, update, delete
from app.database.models import Account
from app.database.session import async_session


class AccountRepo:
    async def get(self, account_id: int) -> Account | None:
        async with async_session() as s:
            return (await s.execute(select(Account).where(Account.id == account_id))).scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> Account | None:
        async with async_session() as s:
            return (await s.execute(select(Account).where(Account.phone == phone))).scalar_one_or_none()

    async def list_by_role(self, role: str) -> list[Account]:
        async with async_session() as s:
            res = await s.execute(select(Account).where(Account.role == role).order_by(Account.id))
            return list(res.scalars().all())

    async def list_workers_for(self, main_id: int) -> list[Account]:
        async with async_session() as s:
            res = await s.execute(
                select(Account)
                .where(Account.role == "worker", Account.parent_account_id == main_id)
                .order_by(Account.id)
            )
            return list(res.scalars().all())

    async def count_by_role(self, role: str) -> int:
        async with async_session() as s:
            res = await s.execute(select(Account).where(Account.role == role))
            return len(res.scalars().all())

    async def save(self, phone: str, session_string: str, role: str = "main") -> Account:
        async with async_session() as s:
            existing = (await s.execute(select(Account).where(Account.phone == phone))).scalar_one_or_none()
            if existing:
                existing.session_string = session_string
                existing.role = role
                await s.commit()
                await s.refresh(existing)
                return existing
            acc = Account(phone=phone, session_string=session_string, role=role)
            s.add(acc)
            await s.commit()
            await s.refresh(acc)
            return acc

    async def update_field(self, account_id: int, **kwargs) -> None:
        async with async_session() as s:
            await s.execute(update(Account).where(Account.id == account_id).values(**kwargs))
            await s.commit()

    async def link_worker(self, worker_id: int, main_id: int | None) -> None:
        async with async_session() as s:
            await s.execute(update(Account).where(Account.id == worker_id).values(parent_account_id=main_id))
            await s.commit()

    async def unlink_workers_for(self, main_id: int) -> None:
        async with async_session() as s:
            await s.execute(
                update(Account)
                .where(Account.role == "worker", Account.parent_account_id == main_id)
                .values(parent_account_id=None)
            )
            await s.commit()

    async def delete(self, account_id: int) -> None:
        async with async_session() as s:
            await s.execute(delete(Account).where(Account.id == account_id))
            await s.commit()
