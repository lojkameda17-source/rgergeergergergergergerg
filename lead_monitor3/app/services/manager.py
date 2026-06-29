"""AccountManager — auth flows + monitor registry."""
import asyncio
import logging
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, PasswordHashInvalidError,
    PhoneCodeExpiredError, PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from app.core.config import settings
from app.database.repositories.account_repo import AccountRepo
from app.services.monitor import Monitor

log = logging.getLogger(__name__)


@dataclass
class _PhonePending:
    client: TelegramClient
    phone: str
    hash: str
    role: str


@dataclass
class _QRPending:
    client: TelegramClient
    qr_login: object
    task: asyncio.Task
    role: str


Res = tuple[str, object]  # ('ok' | 'error' | 'flood' | 'need_2fa' | 'pending', detail)


class AccountManager:
    def __init__(self) -> None:
        self._monitors: dict[int, Monitor] = {}
        self._phone: dict[int, _PhonePending] = {}
        self._qr: dict[int, _QRPending] = {}
        self._repo = AccountRepo()

    # ── monitor control ───────────────────────────────────────────────────────

    async def start(self, account_id: int) -> str:
        """'ok' | 'already' | 'not_found' | 'invalid_session' | 'error:...'"""
        m = self._monitors.get(account_id)
        if m and m.alive():
            return "already"
        acc = await self._repo.get(account_id)
        if not acc:
            return "not_found"
        if acc.role != "main":
            return "error: not a main account"
        try:
            m = Monitor(account_id, acc.session_string)
            ok = await m.start()
            if not ok:
                return "invalid_session"
            self._monitors[account_id] = m
            return "ok"
        except Exception as e:
            log.exception("start account %d: %s", account_id, e)
            return f"error:{e}"

    async def stop(self, account_id: int) -> None:
        m = self._monitors.pop(account_id, None)
        if m:
            await m.stop()

    def is_running(self, account_id: int) -> bool:
        m = self._monitors.get(account_id)
        return m is not None and m.alive()

    def invalidate(self, account_id: int) -> None:
        if m := self._monitors.get(account_id):
            m.invalidate()

    def stats(self) -> dict:
        return {
            "running": sum(1 for m in self._monitors.values() if m.alive()),
            "total": len(self._monitors),
        }

    # ── phone auth ────────────────────────────────────────────────────────────

    async def phone_begin(self, uid: int, phone: str, role: str) -> Res:
        await self._cancel(uid)
        phone = _clean(phone)
        if len(phone) < 7:
            return "error", "invalid phone number"
        client = TelegramClient(StringSession(), settings.telegram_api_id, settings.telegram_api_hash)
        await client.connect()
        try:
            sent = await client.send_code_request(phone, force_sms=False)
        except FloodWaitError as e:
            await client.disconnect()
            return "flood", e.seconds
        except Exception as e:
            await client.disconnect()
            log.error("send_code_request: %s", e)
            return "error", str(e)
        self._phone[uid] = _PhonePending(client=client, phone=phone, hash=sent.phone_code_hash, role=role)
        log.info("Code requested: phone=%s role=%s", phone, role)
        return "ok", None

    async def phone_resend(self, uid: int) -> Res:
        p = self._phone.get(uid)
        if not p:
            return "error", "no active session — start over"
        try:
            sent = await p.client.send_code_request(p.phone, force_sms=True)
            p.hash = sent.phone_code_hash
            return "ok", None
        except FloodWaitError as e:
            return "flood", e.seconds
        except Exception as e:
            return "error", str(e)

    async def phone_code(self, uid: int, code: str) -> Res:
        p = self._phone.get(uid)
        if not p:
            return "error", "session expired — start over"
        try:
            await p.client.sign_in(phone=p.phone, code=_clean_code(code), phone_code_hash=p.hash)
        except SessionPasswordNeededError:
            return "need_2fa", None
        except PhoneCodeInvalidError:
            return "error", "wrong code, try again"
        except PhoneCodeExpiredError:
            return "error", "code expired — request a new one"
        except Exception as e:
            return "error", str(e)
        return await self._save_phone(uid)

    async def phone_2fa(self, uid: int, password: str) -> Res:
        p = self._phone.get(uid)
        if not p:
            return "error", "session expired"
        try:
            await p.client.sign_in(password=password)
        except PasswordHashInvalidError:
            return "error", "wrong 2FA password"
        except Exception as e:
            return "error", str(e)
        return await self._save_phone(uid)

    async def _save_phone(self, uid: int) -> Res:
        p = self._phone.pop(uid, None)
        if not p:
            return "error", "session lost"
        ss = p.client.session.save()
        acc = await self._repo.save(p.phone, ss, role=p.role)
        await p.client.disconnect()
        log.info("Saved account id=%d phone=%s role=%s", acc.id, acc.phone, p.role)
        return "ok", acc.id

    # ── QR auth ───────────────────────────────────────────────────────────────

    async def qr_begin(self, uid: int, role: str) -> str:
        await self._cancel(uid)
        client = TelegramClient(StringSession(), settings.telegram_api_id, settings.telegram_api_hash)
        await client.connect()
        qr = await client.qr_login()

        async def _wait() -> None:
            await qr.wait()

        task = asyncio.create_task(_wait())
        self._qr[uid] = _QRPending(client=client, qr_login=qr, task=task, role=role)
        log.info("QR started uid=%d role=%s", uid, role)
        return qr.url

    async def qr_check(self, uid: int) -> Res:
        q = self._qr.get(uid)
        if not q:
            return "error", "QR session expired — start again"

        if not q.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(q.task), timeout=4.0)
            except (asyncio.TimeoutError, Exception):
                pass

        if not q.task.done():
            return "pending", None

        if not q.task.cancelled() and q.task.exception():
            exc = q.task.exception()
            if isinstance(exc, SessionPasswordNeededError):
                return "need_2fa", None
            self._qr.pop(uid, None)
            try:
                await q.client.disconnect()
            except Exception:
                pass
            return "error", str(exc)

        return await self._save_qr(uid)

    async def qr_2fa(self, uid: int, password: str) -> Res:
        q = self._qr.get(uid)
        if not q:
            return "error", "QR session expired"
        try:
            await q.client.sign_in(password=password)
        except PasswordHashInvalidError:
            return "error", "wrong 2FA password"
        except Exception as e:
            return "error", str(e)
        return await self._save_qr(uid)

    async def _save_qr(self, uid: int) -> Res:
        q = self._qr.pop(uid, None)
        if not q:
            return "error", "session lost"
        try:
            me = await q.client.get_me()
            phone = getattr(me, "phone", None) or f"id:{me.id}"
            ss = q.client.session.save()
            acc = await self._repo.save(phone, ss, role=q.role)
            await q.client.disconnect()
            log.info("QR saved id=%d phone=%s role=%s", acc.id, phone, q.role)
            return "ok", acc.id
        except Exception as e:
            log.exception("QR save: %s", e)
            try:
                await q.client.disconnect()
            except Exception:
                pass
            return "error", str(e)

    # ── cleanup ───────────────────────────────────────────────────────────────

    async def _cancel(self, uid: int) -> None:
        if p := self._phone.pop(uid, None):
            try:
                await p.client.disconnect()
            except Exception:
                pass
        if q := self._qr.pop(uid, None):
            q.task.cancel()
            try:
                await q.client.disconnect()
            except Exception:
                pass


def _clean(phone: str) -> str:
    phone = phone.strip()
    plus = "+" if phone.startswith("+") else ""
    return plus + "".join(c for c in phone if c.isdigit())


def _clean_code(code: str) -> str:
    return "".join(c for c in code if c.isdigit())
