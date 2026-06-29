"""
Monitor — one per running main Account.
Listens for NewMessage, matches keywords, replies or delegates to workers.
"""
import asyncio
import logging
from html import escape

from aiogram import Bot
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from app.ai.groq_client import GroqClient
from app.core.config import settings
from app.database.repositories.account_repo import AccountRepo
from app.database.repositories.keyword_repo import KeywordRepo
from app.database.repositories.log_repo import LogRepo
from app.database.repositories.prompt_repo import PromptRepo
from app.services.antispam import AntiSpam
from app.services.matcher import Matcher, build_matcher

log = logging.getLogger(__name__)

# ── Default prompts (English for better AI quality, override in bot UI) ────────
PROMPT_CHAT = """You are a real person in a Telegram group chat. Reply ONLY in the same language as the user's message.
Someone posted a message that matches our services keyword. Write a SHORT natural reply (max 12 words) that casually suggests writing to you in DM.
Rules: no greetings, no bot/AI mentions, no spam feel, sound human."""

PROMPT_DM = """You are a real sales person replying to a DM. Reply ONLY in the same language as the user's message.
Our services: Telegram mass DMs, group chat blasts, member invites, database parsing, software development, targeted ads, neuromarketing.
Write ONE sentence max 80 chars. Recommend @TellTraffSoft naturally. Never reveal you are AI."""

PROMPT_WORKER_DM = """You write FIRST to a person who posted in a Telegram group. Reply ONLY in the same language as their message.
Be brief, friendly, human. Max 2 short sentences.
Mention that @TellTraffSoft can help with their specific need based on their message. Never say you are a bot or AI."""

# Shared worker client pool (class-level)
_worker_pool: dict[int, TelegramClient] = {}


class Monitor:
    def __init__(self, account_id: int, session_string: str) -> None:
        self.account_id = account_id
        self._session = session_string
        self._client: TelegramClient | None = None

        self._ai = GroqClient.get()
        self._spam = AntiSpam()

        # caches
        self._matcher = Matcher()
        self._matcher_ts = 0.0
        self._work_mode = "chat_reply"
        self._match_mode = "mixed"
        self._notify = False
        self._settings_ts = 0.0

        self._worker_rr = 0
        self._sem = asyncio.Semaphore(20)

        self._acc_repo = AccountRepo()
        self._kw_repo = KeywordRepo()
        self._log_repo = LogRepo()
        self._pr_repo = PromptRepo()

    # ─────────────────────────────── lifecycle ────────────────────────────────

    async def start(self) -> bool:
        """Returns True if started successfully."""
        client = TelegramClient(
            StringSession(self._session),
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )

        @client.on(events.NewMessage(incoming=True))
        async def _on(ev: events.NewMessage.Event) -> None:
            asyncio.create_task(self._safe_handle(ev))

        await client.connect()

        if not await client.is_user_authorized():
            log.error("Account %d: session INVALID — not authorized", self.account_id)
            await client.disconnect()
            return False

        me = await client.get_me()
        log.info(
            "Account %d STARTED — @%s (%s)",
            self.account_id,
            me.username or "no_username",
            me.phone or "?",
        )
        self._client = client
        await self._acc_repo.update_field(self.account_id, is_running=True)
        asyncio.create_task(client.run_until_disconnected())
        return True

    async def stop(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        await self._acc_repo.update_field(self.account_id, is_running=False)
        log.info("Account %d STOPPED", self.account_id)

    def alive(self) -> bool:
        return self._client is not None and self._client.is_connected()

    def invalidate(self) -> None:
        self._matcher_ts = 0.0
        self._settings_ts = 0.0
        log.debug("Account %d cache invalidated", self.account_id)

    # ─────────────────────────────── caches ──────────────────────────────────

    async def _load_keywords(self) -> None:
        now = asyncio.get_event_loop().time()
        ttl = 30 if not self._matcher.is_empty() else 10
        if now - self._matcher_ts < ttl:
            return
        rows = await self._kw_repo.list_for(self.account_id)
        self._matcher = build_matcher(rows)
        self._matcher_ts = now
        log.debug("Account %d: %d keywords loaded", self.account_id, len(rows))

    async def _load_settings(self) -> None:
        now = asyncio.get_event_loop().time()
        if now - self._settings_ts < 20:
            return
        acc = await self._acc_repo.get(self.account_id)
        if acc:
            self._work_mode = acc.work_mode or "chat_reply"
            self._match_mode = acc.match_mode or "mixed"
            self._notify = bool(acc.notify_enabled)
        self._settings_ts = now

    # ─────────────────────────── event handling ───────────────────────────────

    async def _safe_handle(self, ev: events.NewMessage.Event) -> None:
        async with self._sem:
            try:
                await self._dispatch(ev)
            except Exception as exc:
                log.exception("Account %d handler error: %s", self.account_id, exc)

    async def _dispatch(self, ev: events.NewMessage.Event) -> None:
        if not settings.monitor_enabled:
            return

        text = (ev.raw_text or "").strip()
        if not text:
            return

        sender_id: int = ev.sender_id or 0
        chat_id: int = ev.chat_id or 0

        if not sender_id:
            return

        # Private DM to the monitored account
        if ev.is_private:
            await self._on_dm(ev, sender_id, chat_id, text)
            return

        # Group/channel: keyword matching
        await self._load_keywords()
        await self._load_settings()

        if self._matcher.is_empty():
            return

        hit = self._matcher.match(text, self._match_mode)
        if not hit:
            return

        log.info(
            "Account %d HIT keyword=%r user=%d chat=%d",
            self.account_id, hit, sender_id, chat_id,
        )

        if not self._spam.is_allowed(sender_id):
            log.info("Account %d: user %d blocked by antispam", self.account_id, sender_id)
            return

        self._spam.register(sender_id)

        if self._work_mode == "worker_dm":
            await self._on_worker_dm(ev, sender_id, chat_id, text, hit)
        else:
            await self._on_chat_reply(ev, sender_id, chat_id, text, hit)

    # ─────────────────────────── handlers ─────────────────────────────────────

    async def _on_dm(self, ev, sender_id: int, chat_id: int, text: str) -> None:
        prompt = await self._pr_repo.get(self.account_id, "dm", PROMPT_DM)
        await AntiSpam.human_delay(1500, 3500)
        answer = await self._ai.complete(prompt, text)
        if not answer:
            log.warning("Account %d: empty AI response for DM from %d", self.account_id, sender_id)
            return
        try:
            await ev.reply(answer)
        except Exception as e:
            log.error("Account %d: DM reply failed: %s", self.account_id, e)
            await self._log_repo.add(
                account_id=self.account_id, chat_id=chat_id, user_id=sender_id,
                incoming=text, outgoing="", error=str(e),
            )
            return
        await self._log_repo.add(
            account_id=self.account_id, chat_id=chat_id, user_id=sender_id,
            incoming=text, outgoing=answer,
        )
        log.info("Account %d: DM reply sent to %d", self.account_id, sender_id)

    async def _on_chat_reply(
        self, ev, sender_id: int, chat_id: int, text: str, keyword: str
    ) -> None:
        prompt = await self._pr_repo.get(self.account_id, "chat", PROMPT_CHAT)
        await AntiSpam.human_delay(2000, 5000)
        answer = await self._ai.complete(prompt, f"Keyword: {keyword}\nMessage: {text}")
        if not answer:
            log.warning("Account %d: empty AI response for chat reply", self.account_id)
            return
        # Ensure DM hint exists
        dm_words = ["лс", "dm", "direct", "личн", "написа", "пиши"]
        if not any(w in answer.lower() for w in dm_words):
            answer += " — напиши в ЛС."
        try:
            await ev.reply(answer)
        except Exception as e:
            log.error("Account %d: chat reply failed: %s", self.account_id, e)
            await self._log_repo.add(
                account_id=self.account_id, chat_id=chat_id, user_id=sender_id,
                keyword=keyword, incoming=text, outgoing="", error=str(e),
            )
            return
        await self._log_repo.add(
            account_id=self.account_id, chat_id=chat_id, user_id=sender_id,
            keyword=keyword, incoming=text, outgoing=answer,
        )
        await self._notify_lead(None, keyword, text, answer, str(sender_id))
        log.info("Account %d: chat reply sent (kw=%r user=%d)", self.account_id, keyword, sender_id)

    async def _on_worker_dm(
        self, ev, sender_id: int, chat_id: int, text: str, keyword: str
    ) -> None:
        sender = await ev.get_sender()
        username = getattr(sender, "username", None)
        target = username or sender_id  # Telethon accepts both

        workers = await self._acc_repo.list_workers_for(self.account_id)
        if not workers:
            err = f"No workers attached to account #{self.account_id}"
            log.warning(err)
            await self._log_repo.add(
                account_id=self.account_id, chat_id=chat_id, user_id=sender_id,
                username=username, keyword=keyword, incoming=text, outgoing="", error=err,
            )
            return

        n = len(workers)
        start = self._worker_rr % n
        ordered = workers[start:] + workers[:start]

        for worker in ordered:
            try:
                wclient = await self._get_worker_client(worker)
            except Exception as e:
                log.error("Worker %d connect failed: %s", worker.id, e)
                await self._log_repo.add(
                    account_id=worker.id, chat_id=chat_id, user_id=sender_id,
                    keyword=keyword, incoming=text, outgoing="",
                    error=f"connect failed: {e}",
                )
                continue

            prompt = await self._pr_repo.get(self.account_id, "worker_dm", PROMPT_WORKER_DM)
            answer = await self._ai.complete(
                prompt, f"User message: {text}\nTopic/keyword: {keyword}"
            )
            if not answer:
                answer = f"Hi! Saw your message about '{keyword}'. @TellTraffSoft can definitely help."

            await AntiSpam.human_delay(1500, 4000)
            try:
                await wclient.send_message(target, answer)
            except Exception as e:
                log.error("Worker %d send_message failed to %s: %s", worker.id, target, e)
                await self._log_repo.add(
                    account_id=worker.id, chat_id=chat_id, user_id=sender_id,
                    username=username, keyword=keyword, incoming=text, outgoing="",
                    error=str(e),
                )
                continue

            # success
            self._worker_rr = (start + workers.index(worker) + 1) % n
            await self._log_repo.add(
                account_id=worker.id, chat_id=chat_id, user_id=sender_id,
                worker_id=worker.id, username=username,
                keyword=keyword, incoming=text, outgoing=answer,
            )
            await self._log_repo.add(
                account_id=self.account_id, chat_id=chat_id, user_id=sender_id,
                worker_id=worker.id, username=username,
                keyword=keyword,
                incoming=f"[dispatched to worker #{worker.id}] {text}",
                outgoing=answer,
            )
            await self._notify_lead(worker.id, keyword, text, answer, str(target))
            log.info(
                "Account %d: worker %d sent DM to %s (kw=%r)",
                self.account_id, worker.id, target, keyword,
            )
            return

        err = "All workers failed to send DM"
        log.error("Account %d: %s", self.account_id, err)
        await self._log_repo.add(
            account_id=self.account_id, chat_id=chat_id, user_id=sender_id,
            username=username, keyword=keyword, incoming=text, outgoing="", error=err,
        )

    async def _get_worker_client(self, worker) -> TelegramClient:
        existing = _worker_pool.get(worker.id)
        if existing and existing.is_connected():
            return existing
        client = TelegramClient(
            StringSession(worker.session_string),
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(f"Worker #{worker.id} session not authorized")
        me = await client.get_me()
        _worker_pool[worker.id] = client
        asyncio.create_task(client.run_until_disconnected())
        log.info("Worker %d connected as @%s", worker.id, me.username or me.phone)
        return client

    # ─────────────────────────── notifications ────────────────────────────────

    async def _notify_lead(
        self,
        worker_id: int | None,
        keyword: str,
        incoming: str,
        outgoing: str,
        target: str,
    ) -> None:
        if not self._notify or not settings.admin_id_list:
            return
        acc = await self._acc_repo.get(self.account_id)
        if not acc:
            return
        mode = "♻️ worker DM" if worker_id else "💬 chat reply"
        text = (
            "✅ <b>Новый лид!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Аккаунт: <b>#{self.account_id}</b> <code>{escape(acc.phone)}</code>\n"
            f"🔄 Режим: {mode}"
            + (f" (worker <b>#{worker_id}</b>)" if worker_id else "")
            + "\n"
            f"🔑 Ключ: <code>{escape(keyword)}</code>\n"
            f"🎯 Цель: <code>{escape(target)}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 <i>{escape(incoming[:350])}</i>\n\n"
            f"📤 <i>{escape(outgoing[:350])}</i>"
        )
        bot = Bot(settings.bot_token)
        try:
            for aid in settings.admin_id_list:
                try:
                    await bot.send_message(aid, text, parse_mode="HTML")
                except Exception as e:
                    log.warning("Notify admin %d failed: %s", aid, e)
        finally:
            await bot.session.close()
