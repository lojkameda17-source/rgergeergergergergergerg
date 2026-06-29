"""
Telegram Admin Bot — полный UI через inline-кнопки.
Язык: русский. Стиль: чистый, информативный, MVP.
"""
import asyncio
import logging
from io import BytesIO

import qrcode
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.database.repositories.account_repo import AccountRepo
from app.database.repositories.keyword_repo import KeywordRepo
from app.database.repositories.log_repo import LogRepo
from app.database.repositories.prompt_repo import PromptRepo
from app.services.manager import AccountManager
from app.services.monitor import PROMPT_CHAT, PROMPT_DM, PROMPT_WORKER_DM

log = logging.getLogger(__name__)
router = Router()

# ── Синглтоны ─────────────────────────────────────────────────────────────────
_acc = AccountRepo()
_kw  = KeywordRepo()
_log = LogRepo()
_pr  = PromptRepo()
manager = AccountManager()

# ── Сессионное состояние ──────────────────────────────────────────────────────
_sel: dict[int, int] = {}    # user_id → account_id
_role: dict[int, str] = {}   # user_id → 'main' | 'worker'
_kw_pg: dict[int, int] = {}
_log_pg: dict[int, int] = {}
PAGE = 10

PROMPT_INFO = {
    "chat":       ("💬 Промпт для чата",           PROMPT_CHAT),
    "dm":         ("📩 Промпт для личных сообщений", PROMPT_DM),
    "worker_dm":  ("♻️ Промпт расходника (DM)",    PROMPT_WORKER_DM),
}

WORK_MODE_LABELS = {
    "chat_reply": "💬 Ответ в чат",
    "worker_dm":  "♻️ Расходник пишет в ЛС",
}

MATCH_MODE_LABELS = {
    "mixed":  "🔡 Слова + фразы",
    "phrase": "🔤 Только фразы",
}


# ── FSM ───────────────────────────────────────────────────────────────────────
class AuthFSM(StatesGroup):
    phone  = State()
    code   = State()
    pw     = State()
    qr_pw  = State()

class KwFSM(StatesGroup):
    values = State()

class PromptFSM(StatesGroup):
    scope = State()
    body  = State()


# ── Утилиты ───────────────────────────────────────────────────────────────────
def _btn(*items: tuple[str, str], cols: int = 1) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for label, data in items:
        b.button(text=label, callback_data=data)
    b.adjust(cols)
    return b.as_markup()


async def _edit(msg: Message, text: str, kb: InlineKeyboardMarkup) -> None:
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")


def _sep(n: int = 24) -> str:
    return "─" * n


def _clear_selected_account(account_id: int) -> None:
    for uid, selected_id in list(_sel.items()):
        if selected_id == account_id:
            _sel.pop(uid, None)


# ── Дашборд / Главное меню ────────────────────────────────────────────────────
async def _dashboard(target: Message | CallbackQuery) -> None:
    uid = target.from_user.id
    sid = _sel.get(uid)

    # Собираем статистику
    mains   = await _acc.list_by_role("main")
    workers = await _acc.list_by_role("worker")
    running = sum(1 for a in mains if manager.is_running(a.id))

    sel_info = ""
    if sid:
        acc = await _acc.get(sid)
        if acc:
            kws    = await _kw.list_for(sid)
            logs_n = await _log.count_for(sid)
            st = "🟢 запущен" if manager.is_running(sid) else "🔴 остановлен"
            wm = WORK_MODE_LABELS.get(acc.work_mode, acc.work_mode)
            sel_info = (
                f"\n{_sep()}\n"
                f"<b>Выбранный аккаунт</b>  #{sid}\n"
                f"📱 <code>{acc.phone}</code>  {st}\n"
                f"⚙️ Режим: {wm}\n"
                f"🔑 Ключей: <b>{len(kws)}</b>   📜 Логов: <b>{logs_n}</b>"
            )

    text = (
        "🤖 <b>Lead Monitor</b>\n"
        f"{_sep()}\n"
        f"👤 Основных аккаунтов: <b>{len(mains)}</b>  "
        f"(запущено: <b>{running}</b>)\n"
        f"♻️ Расходников: <b>{len(workers)}</b>"
        f"{sel_info}"
    )

    kb = InlineKeyboardBuilder()
    # Строка управления выбранным аккаунтом
    if sid:
        if manager.is_running(sid):
            kb.button(text="⏹ Остановить",   callback_data="ctrl:stop")
        else:
            kb.button(text="▶️ Запустить",    callback_data="ctrl:start")
        kb.button(text="🔄 Сменить аккаунт", callback_data="ctrl:pick")
        kb.adjust(2)
    else:
        kb.button(text="👤 Выбрать аккаунт", callback_data="ctrl:pick")
        kb.adjust(1)

    # Основное меню — 2 кнопки в ряд
    kb.button(text="📱 Аккаунты",      callback_data="m:accounts")
    kb.button(text="♻️ Расходники",    callback_data="m:workers")
    kb.button(text="🔑 Ключевые слова", callback_data="m:keywords")
    kb.button(text="🧠 Промпты",        callback_data="m:prompts")
    kb.button(text="📜 Логи",           callback_data="m:logs")
    kb.button(text="ℹ️ Справка",        callback_data="m:help")
    kb.adjust(2)

    if isinstance(target, CallbackQuery):
        await _edit(target.message, text, kb.as_markup())
    else:
        await target.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@router.message(Command("start"))
async def cmd_start(m: Message) -> None:
    await _dashboard(m)


@router.callback_query(F.data == "m:home")
async def cb_home(c: CallbackQuery) -> None:
    await _dashboard(c)
    await c.answer()


# ── Справка ───────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "m:help")
async def cb_help(c: CallbackQuery) -> None:
    text = (
        "ℹ️ <b>Как пользоваться</b>\n"
        f"{_sep()}\n"
        "<b>1. Добавь основной аккаунт</b>\n"
        "   Раздел 📱 Аккаунты → Добавить\n"
        "   Войди по номеру или QR-коду\n\n"
        "<b>2. Выбери аккаунт</b>\n"
        "   Нажми «Выбрать» в списке аккаунтов\n\n"
        "<b>3. Добавь ключевые слова</b>\n"
        "   Раздел 🔑 Ключевые слова → Добавить\n"
        "   Каждое слово/фраза с новой строки\n\n"
        "<b>4. Запусти мониторинг</b>\n"
        "   Кнопка ▶️ Запустить на главном экране\n\n"
        "<b>5. Режимы работы</b>\n"
        "   💬 Ответ в чат — аккаунт отвечает в группу\n"
        "   ♻️ Расходник в ЛС — расходник пишет лиду\n\n"
        f"{_sep()}\n"
        "💡 <i>Код не приходит? Используй вход по QR — он всегда работает.</i>"
    )
    await _edit(c.message, text, _btn(("⬅️ Назад", "m:home")))
    await c.answer()


# ── Управление выбранным аккаунтом ───────────────────────────────────────────
@router.callback_query(F.data == "ctrl:start")
async def cb_ctrl_start(c: CallbackQuery) -> None:
    aid = _sel.get(c.from_user.id)
    if not aid:
        await c.answer("Сначала выбери аккаунт", show_alert=True)
        return
    await c.answer("Запускаю...")
    result = await manager.start(aid)
    match result:
        case "ok":            await c.answer(f"✅ Аккаунт #{aid} запущен!", show_alert=True)
        case "already":       await c.answer(f"Аккаунт #{aid} уже запущен", show_alert=True)
        case "invalid_session": await c.answer("❌ Сессия недействительна — войди заново", show_alert=True)
        case "not_found":     await c.answer("❌ Аккаунт не найден", show_alert=True)
        case _:               await c.answer(f"❌ Ошибка: {result}", show_alert=True)
    await _dashboard(c)


@router.callback_query(F.data == "ctrl:stop")
async def cb_ctrl_stop(c: CallbackQuery) -> None:
    aid = _sel.get(c.from_user.id)
    if not aid:
        await c.answer("Сначала выбери аккаунт", show_alert=True)
        return
    await manager.stop(aid)
    await c.answer(f"⛔ Аккаунт #{aid} остановлен", show_alert=True)
    await _dashboard(c)


@router.callback_query(F.data == "ctrl:pick")
async def cb_ctrl_pick(c: CallbackQuery) -> None:
    await _show_acc_list(c, pick_mode=True)


# ── Раздел: Аккаунты ─────────────────────────────────────────────────────────
def _kb_accounts() -> InlineKeyboardMarkup:
    return _btn(
        ("➕ Добавить по номеру", "acc:add:phone"),
        ("📷 Добавить по QR",     "acc:add:qr"),
        ("📋 Список аккаунтов",  "acc:list"),
        ("⬅️ Назад",              "m:home"),
    )


@router.callback_query(F.data == "m:accounts")
async def cb_accounts(c: CallbackQuery) -> None:
    mains = await _acc.list_by_role("main")
    run   = sum(1 for a in mains if manager.is_running(a.id))
    text  = (
        f"📱 <b>Основные аккаунты</b>\n"
        f"{_sep()}\n"
        f"Всего: <b>{len(mains)}</b>   Запущено: <b>{run}</b>"
    )
    await _edit(c.message, text, _kb_accounts())
    await c.answer()


@router.callback_query(F.data == "acc:list")
async def cb_acc_list(c: CallbackQuery) -> None:
    await _show_acc_list(c, pick_mode=False)


async def _show_acc_list(c: CallbackQuery, pick_mode: bool = False) -> None:
    rows = await _acc.list_by_role("main")
    uid  = c.from_user.id
    b    = InlineKeyboardBuilder()

    if not rows:
        await _edit(
            c.message,
            "📱 <b>Аккаунтов пока нет</b>\n\nДобавь первый аккаунт!",
            _btn(("➕ Добавить", "acc:add:phone"), ("📷 QR-вход", "acc:add:qr"), ("⬅️ Назад", "m:accounts")),
        )
        await c.answer()
        return

    lines = [f"📋 <b>{'Выбери аккаунт' if pick_mode else 'Список аккаунтов'}</b>\n"]
    for a in rows:
        st  = "🟢" if manager.is_running(a.id) else "🔴"
        cur = " ✅" if _sel.get(uid) == a.id else ""
        wm  = "чат" if a.work_mode == "chat_reply" else "DM"
        lines.append(f"{st} <b>#{a.id}</b>  <code>{a.phone}</code>  [{wm}]{cur}")

        b.button(text=f"{'✅ ' if _sel.get(uid) == a.id else ''}#{a.id} Выбрать",
                 callback_data=f"acc:sel:{a.id}")
        if manager.is_running(a.id):
            b.button(text=f"⏹ #{a.id}", callback_data=f"acc:stop:{a.id}")
        else:
            b.button(text=f"▶️ #{a.id}", callback_data=f"acc:start:{a.id}")
        b.button(text=f"⚙️ #{a.id}", callback_data=f"acc:cfg:{a.id}")
        b.button(text=f"🗑 #{a.id}", callback_data=f"acc:del:ask:{a.id}")

    b.button(text="⬅️ Назад", callback_data="m:accounts" if not pick_mode else "m:home")
    b.adjust(*(4 for _ in rows), 1)

    await _edit(c.message, "\n".join(lines), b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("acc:sel:"))
async def cb_acc_sel(c: CallbackQuery) -> None:
    aid = int(c.data.split(":")[-1])
    _sel[c.from_user.id] = aid
    await c.answer(f"Аккаунт #{aid} выбран ✅", show_alert=True)
    await _dashboard(c)


@router.callback_query(F.data.startswith("acc:start:"))
async def cb_acc_start(c: CallbackQuery) -> None:
    aid = int(c.data.split(":")[-1])
    await c.answer("Запускаю...")
    result = await manager.start(aid)
    msgs = {
        "ok": f"✅ #{aid} запущен!",
        "already": f"#{aid} уже запущен",
        "invalid_session": "❌ Сессия недействительна",
        "not_found": "❌ Не найден",
    }
    await c.answer(msgs.get(result, f"❌ {result}"), show_alert=True)
    await _show_acc_list(c)


@router.callback_query(F.data.startswith("acc:stop:"))
async def cb_acc_stop(c: CallbackQuery) -> None:
    aid = int(c.data.split(":")[-1])
    await manager.stop(aid)
    await c.answer(f"⛔ #{aid} остановлен", show_alert=True)
    await _show_acc_list(c)


@router.callback_query(F.data.startswith("acc:cfg:"))
async def cb_acc_cfg(c: CallbackQuery) -> None:
    aid = int(c.data.split(":")[-1])
    await _show_acc_config(c, aid)


async def _show_acc_config(c: CallbackQuery, aid: int) -> None:
    acc = await _acc.get(aid)
    if not acc:
        await c.answer("Не найден", show_alert=True)
        return
    kws  = await _kw.list_for(aid)
    wrks = await _acc.list_workers_for(aid)
    st   = "🟢 запущен" if manager.is_running(aid) else "🔴 остановлен"
    wm   = WORK_MODE_LABELS.get(acc.work_mode, acc.work_mode)
    mm   = MATCH_MODE_LABELS.get(acc.match_mode, acc.match_mode)
    ntf  = "🔔 вкл" if acc.notify_enabled else "🔕 выкл"

    text = (
        f"⚙️ <b>Настройки аккаунта #{aid}</b>\n"
        f"{_sep()}\n"
        f"📱 <code>{acc.phone}</code>  {st}\n"
        f"🔄 Режим работы: {wm}\n"
        f"🔍 Режим поиска: {mm}\n"
        f"🔔 Уведомления: {ntf}\n"
        f"🔑 Ключей: <b>{len(kws)}</b>   ♻️ Расходников: <b>{len(wrks)}</b>"
    )

    cur_wm = acc.work_mode
    cur_mm = acc.match_mode
    b = InlineKeyboardBuilder()

    # Work mode toggle
    if cur_wm == "chat_reply":
        b.button(text="♻️ Режим → Расходник в ЛС", callback_data=f"cfg:wm:{aid}:worker_dm")
    else:
        b.button(text="💬 Режим → Ответ в чат",    callback_data=f"cfg:wm:{aid}:chat_reply")

    # Match mode toggle
    if cur_mm == "mixed":
        b.button(text="🔤 Поиск → Только фразы", callback_data=f"cfg:mm:{aid}:phrase")
    else:
        b.button(text="🔡 Поиск → Слова+фразы",  callback_data=f"cfg:mm:{aid}:mixed")

    # Notify toggle
    if acc.notify_enabled:
        b.button(text="🔕 Выкл уведомления", callback_data=f"cfg:ntf:{aid}:0")
    else:
        b.button(text="🔔 Вкл уведомления",  callback_data=f"cfg:ntf:{aid}:1")

    b.button(text="🗑 Удалить аккаунт", callback_data=f"acc:del:ask:{aid}")
    b.button(text="⬅️ Назад", callback_data="acc:list")
    b.adjust(1)
    await _edit(c.message, text, b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("acc:del:ask:"))
async def cb_acc_delete_ask(c: CallbackQuery) -> None:
    aid = int(c.data.split(":")[-1])
    acc = await _acc.get(aid)
    if not acc or acc.role != "main":
        await c.answer("Аккаунт не найден", show_alert=True)
        return
    wrks = await _acc.list_workers_for(aid)
    text = (
        f"🗑 <b>Удалить основной аккаунт #{aid}?</b>\n"
        f"{_sep()}\n"
        f"📱 <code>{acc.phone}</code>\n"
        f"♻️ Привязанных расходников: <b>{len(wrks)}</b>\n\n"
        "Мониторинг будет остановлен, аккаунт удалится из системы, "
        "а расходники будут откреплены."
    )
    await _edit(
        c.message,
        text,
        _btn(("✅ Да, удалить", f"acc:del:yes:{aid}"), ("⬅️ Отмена", "acc:list")),
    )
    await c.answer()


@router.callback_query(F.data.startswith("acc:del:yes:"))
async def cb_acc_delete_yes(c: CallbackQuery) -> None:
    aid = int(c.data.split(":")[-1])
    acc = await _acc.get(aid)
    if not acc or acc.role != "main":
        await c.answer("Аккаунт уже удалён", show_alert=True)
        await _show_acc_list(c)
        return
    await manager.stop(aid)
    await _acc.unlink_workers_for(aid)
    await _acc.delete(aid)
    _clear_selected_account(aid)
    await c.answer(f"🗑 Аккаунт #{aid} удалён", show_alert=True)
    await _show_acc_list(c)


@router.callback_query(F.data.startswith("cfg:wm:"))
async def cb_cfg_wm(c: CallbackQuery) -> None:
    _, _, aid, mode = c.data.split(":")
    await _acc.update_field(int(aid), work_mode=mode)
    manager.invalidate(int(aid))
    await c.answer(f"Режим: {WORK_MODE_LABELS.get(mode, mode)} ✅", show_alert=True)
    await _show_acc_config(c, int(aid))


@router.callback_query(F.data.startswith("cfg:mm:"))
async def cb_cfg_mm(c: CallbackQuery) -> None:
    _, _, aid, mode = c.data.split(":")
    await _acc.update_field(int(aid), match_mode=mode)
    manager.invalidate(int(aid))
    await c.answer(f"Поиск: {MATCH_MODE_LABELS.get(mode, mode)} ✅", show_alert=True)
    await _show_acc_config(c, int(aid))


@router.callback_query(F.data.startswith("cfg:ntf:"))
async def cb_cfg_ntf(c: CallbackQuery) -> None:
    _, _, aid, val = c.data.split(":")
    enabled = val == "1"
    await _acc.update_field(int(aid), notify_enabled=enabled)
    manager.invalidate(int(aid))
    await c.answer("🔔 Уведомления включены" if enabled else "🔕 Уведомления выключены", show_alert=True)
    await _show_acc_config(c, int(aid))


# ── Авторизация — телефон ─────────────────────────────────────────────────────
def _kb_code() -> InlineKeyboardMarkup:
    return _btn(
        ("🔁 Запросить SMS",        "auth:resend"),
        ("📷 Войти по QR вместо кода", "auth:to_qr"),
        ("❌ Отмена",                "m:home"),
    )


def _kb_qr() -> InlineKeyboardMarkup:
    return _btn(
        ("✅ Я отсканировал",  "auth:qr_ok"),
        ("🔄 Новый QR-код",   "auth:qr_new"),
        ("❌ Отмена",          "m:home"),
    )


async def _begin_phone(c: CallbackQuery, state: FSMContext, role: str) -> None:
    _role[c.from_user.id] = role
    await state.set_state(AuthFSM.phone)
    label = "основного аккаунта" if role == "main" else "расходника"
    await c.message.answer(
        f"📱 <b>Добавление {label}</b>\n\n"
        f"Введи номер телефона в формате:\n<code>+79001234567</code>",
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "acc:add:phone")
async def cb_acc_add_phone(c: CallbackQuery, state: FSMContext) -> None:
    await _begin_phone(c, state, "main")


@router.callback_query(F.data == "wrk:add:phone")
async def cb_wrk_add_phone(c: CallbackQuery, state: FSMContext) -> None:
    await _begin_phone(c, state, "worker")


@router.message(AuthFSM.phone)
async def fsm_phone(m: Message, state: FSMContext) -> None:
    role = _role.get(m.from_user.id, "main")
    status, detail = await manager.phone_begin(m.from_user.id, m.text or "", role)
    if status == "ok":
        await state.set_state(AuthFSM.code)
        await m.answer(
            "📨 <b>Код отправлен!</b>\n\n"
            "Проверь <b>Telegram на телефоне или ПК</b> — "
            "код обычно приходит в приложение, не в SMS.\n\n"
            "Введи код ниже 👇",
            reply_markup=_kb_code(),
            parse_mode="HTML",
        )
    elif status == "flood":
        await m.answer(f"⏳ FloodWait: подожди <b>{detail}</b> сек.\nПока предлагаю войти через QR:")
        await _send_qr(m, m.from_user.id, role, state)
    else:
        await m.answer(f"❌ Ошибка: {detail}\n\nПробую QR-вход:")
        await _send_qr(m, m.from_user.id, role, state)


@router.callback_query(F.data == "auth:resend")
async def cb_resend(c: CallbackQuery) -> None:
    status, detail = await manager.phone_resend(c.from_user.id)
    if status == "ok":
        await c.answer("📨 Код повторно запрошен. Проверь Telegram/SMS.", show_alert=True)
    elif status == "flood":
        await c.answer(f"⏳ FloodWait: {detail} сек.", show_alert=True)
    else:
        await c.answer(f"❌ {detail}", show_alert=True)


@router.callback_query(F.data == "auth:to_qr")
async def cb_to_qr(c: CallbackQuery, state: FSMContext) -> None:
    role = _role.get(c.from_user.id, "main")
    await _send_qr(c.message, c.from_user.id, role, state)
    await c.answer()


@router.message(AuthFSM.code)
async def fsm_code(m: Message, state: FSMContext) -> None:
    role   = _role.get(m.from_user.id, "main")
    status, detail = await manager.phone_code(m.from_user.id, (m.text or "").strip())
    if status == "ok":
        await _on_auth_ok(m, state, int(detail), role)
    elif status == "need_2fa":
        await state.set_state(AuthFSM.pw)
        await m.answer("🔐 <b>Включена двухфакторная аутентификация.</b>\n\nВведи пароль 2FA:", parse_mode="HTML")
    else:
        await m.answer(f"❌ {detail}", reply_markup=_kb_code())


@router.message(AuthFSM.pw)
async def fsm_pw(m: Message, state: FSMContext) -> None:
    role   = _role.get(m.from_user.id, "main")
    status, detail = await manager.phone_2fa(m.from_user.id, (m.text or "").strip())
    if status == "ok":
        await _on_auth_ok(m, state, int(detail), role)
    else:
        await m.answer(f"❌ {detail}\n\nПопробуй ещё раз:")


# ── Авторизация — QR ──────────────────────────────────────────────────────────
async def _send_qr(target: Message, uid: int, role: str, state: FSMContext | None = None) -> None:
    _role[uid] = role
    url = await manager.qr_begin(uid, role)
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    label = "основного аккаунта" if role == "main" else "расходника"
    await target.answer_photo(
        BufferedInputFile(buf.getvalue(), filename="qr.png"),
        caption=(
            f"📷 <b>QR-вход {label}</b>\n\n"
            "1. Открой Telegram на телефоне\n"
            "2. Настройки → Устройства → Подключить устройство\n"
            "3. Отсканируй QR\n"
            "4. Система сама проверит вход; кнопку можно нажать вручную для проверки.\n\n"
            "💡 <i>Если обычный код не приходит — QR обычно надёжнее.</i>"
        ),
        reply_markup=_kb_qr(),
        parse_mode="HTML",
    )
    asyncio.create_task(_watch_qr_login(target, uid, role, state))


async def _watch_qr_login(target: Message, uid: int, role: str, state: FSMContext | None) -> None:
    for _ in range(30):
        await asyncio.sleep(3)
        status, detail = await manager.qr_check(uid)
        if status == "pending":
            continue
        if status == "ok":
            await _on_auth_ok(target, state, int(detail), role)
            return
        if status == "need_2fa":
            if state:
                await state.set_state(AuthFSM.qr_pw)
            await target.answer(
                "🔐 <b>QR отсканирован, требуется 2FA.</b>\n\n"
                "Введи пароль двухфакторной аутентификации:",
                parse_mode="HTML",
            )
            return
        if status == "error" and detail == "session lost":
            return
        await target.answer(f"❌ QR-вход не завершён: {detail}\nСоздай новый QR-код.")
        return


@router.callback_query(F.data == "acc:add:qr")
async def cb_acc_add_qr(c: CallbackQuery, state: FSMContext) -> None:
    await _send_qr(c.message, c.from_user.id, "main", state)
    await c.answer()


@router.callback_query(F.data == "wrk:add:qr")
async def cb_wrk_add_qr(c: CallbackQuery, state: FSMContext) -> None:
    await _send_qr(c.message, c.from_user.id, "worker", state)
    await c.answer()


@router.callback_query(F.data == "auth:qr_new")
async def cb_qr_new(c: CallbackQuery, state: FSMContext) -> None:
    role = _role.get(c.from_user.id, "main")
    await _send_qr(c.message, c.from_user.id, role, state)
    await c.answer("🔄 Новый QR создан")


@router.callback_query(F.data == "auth:qr_ok")
async def cb_qr_ok(c: CallbackQuery, state: FSMContext) -> None:
    await c.answer("Проверяю...")
    status, detail = await manager.qr_check(c.from_user.id)
    role = _role.get(c.from_user.id, "main")
    if status == "ok":
        await _on_auth_ok(c, state, int(detail), role)
    elif status == "need_2fa":
        await state.set_state(AuthFSM.qr_pw)
        await c.message.answer("🔐 <b>Требуется 2FA.</b>\n\nВведи пароль двухфакторной аутентификации:", parse_mode="HTML")
    elif status == "pending":
        await c.answer("⏳ QR ещё не отсканирован.\nОтсканируй и подожди 2–3 сек.", show_alert=True)
    else:
        await c.answer(f"❌ {detail} — создай новый QR.", show_alert=True)


@router.message(AuthFSM.qr_pw)
async def fsm_qr_pw(m: Message, state: FSMContext) -> None:
    role   = _role.get(m.from_user.id, "main")
    status, detail = await manager.qr_2fa(m.from_user.id, (m.text or "").strip())
    if status == "ok":
        await _on_auth_ok(m, state, int(detail), role)
    else:
        await m.answer(f"❌ {detail}\n\nПопробуй снова:")


async def _on_auth_ok(
    target: Message | CallbackQuery,
    state: FSMContext | None,
    acc_id: int,
    role: str,
) -> None:
    uid = target.from_user.id
    if role == "main":
        _sel[uid] = acc_id
    _role.pop(uid, None)
    if state:
        await state.clear()
    label = "Основной аккаунт" if role == "main" else "Расходник"
    emoji = "✅" if role == "main" else "♻️"
    msg   = (
        f"{emoji} <b>{label} #{acc_id} добавлен!</b>\n\n"
        + ("Аккаунт выбран как текущий." if role == "main" else "Можешь прикрепить его к основному аккаунту.")
    )
    if isinstance(target, CallbackQuery):
        await target.message.answer(msg, reply_markup=_btn(("🏠 Главное меню", "m:home")), parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(msg, reply_markup=_btn(("🏠 Главное меню", "m:home")), parse_mode="HTML")


# ── Раздел: Расходники ────────────────────────────────────────────────────────
def _kb_workers() -> InlineKeyboardMarkup:
    return _btn(
        ("➕ Добавить по номеру", "wrk:add:phone"),
        ("📷 Добавить по QR",     "wrk:add:qr"),
        ("🧩 Привязки",           "wrk:links"),
        ("📋 Все расходники",     "wrk:list"),
        ("⬅️ Назад",              "m:home"),
    )


@router.callback_query(F.data == "m:workers")
async def cb_workers(c: CallbackQuery) -> None:
    workers = await _acc.list_by_role("worker")
    mains   = await _acc.list_by_role("main")
    free    = sum(1 for w in workers if not w.parent_account_id)
    text = (
        f"♻️ <b>Расходные аккаунты</b>\n"
        f"{_sep()}\n"
        f"Всего расходников: <b>{len(workers)}</b>\n"
        f"Свободных: <b>{free}</b>  |  Основных: <b>{len(mains)}</b>\n\n"
        "Расходники прикрепляются к основным аккаунтам\n"
        "и пишут найденным лидам в личные сообщения."
    )
    await _edit(c.message, text, _kb_workers())
    await c.answer()


@router.callback_query(F.data == "wrk:list")
async def cb_wrk_list(c: CallbackQuery) -> None:
    rows = await _acc.list_by_role("worker")
    b    = InlineKeyboardBuilder()
    if not rows:
        await _edit(
            c.message,
            "♻️ <b>Расходников пока нет</b>\n\nДобавь первый расходник!",
            _kb_workers(),
        )
        await c.answer()
        return
    lines = [f"♻️ <b>Список расходников</b>\n{_sep()}\n"]
    for w in rows:
        linked = f"→ аккаунт #{w.parent_account_id}" if w.parent_account_id else "свободен"
        lines.append(f"<b>#{w.id}</b>  <code>{w.phone}</code>  <i>({linked})</i>")
        if w.parent_account_id:
            b.button(text=f"❌ Открепить #{w.id}", callback_data=f"wrk:unlink:0:{w.id}")
        b.button(text=f"🗑 Удалить #{w.id}", callback_data=f"wrk:del:ask:{w.id}")
    b.button(text="🧩 Управление привязками", callback_data="wrk:links")
    b.button(text="⬅️ Назад", callback_data="m:workers")
    b.adjust(1)
    await _edit(c.message, "\n".join(lines), b.as_markup())
    await c.answer()


@router.callback_query(F.data == "wrk:links")
async def cb_wrk_links(c: CallbackQuery) -> None:
    mains = await _acc.list_by_role("main")
    b     = InlineKeyboardBuilder()
    if not mains:
        await _edit(
            c.message,
            "🧩 <b>Привязки расходников</b>\n\nСначала добавь основной аккаунт.",
            _btn(("➕ Добавить аккаунт", "acc:add:phone"), ("⬅️ Назад", "m:workers")),
        )
        await c.answer()
        return
    lines = [f"🧩 <b>Выбери основной аккаунт</b>\n{_sep()}\n"]
    for m2 in mains:
        wrks = await _acc.list_workers_for(m2.id)
        st   = "🟢" if manager.is_running(m2.id) else "🔴"
        lines.append(f"{st} <b>#{m2.id}</b>  <code>{m2.phone}</code>  ♻️ {len(wrks)}")
        b.button(text=f"⚙️ #{m2.id} · {len(wrks)} расходников", callback_data=f"wrk:main:{m2.id}")
    b.button(text="⬅️ Назад", callback_data="m:workers")
    b.adjust(1)
    await _edit(c.message, "\n".join(lines), b.as_markup())
    await c.answer()


async def _show_main_workers(c: CallbackQuery, main_id: int) -> None:
    main = await _acc.get(main_id)
    if not main:
        await c.answer("Не найден", show_alert=True)
        return
    linked   = await _acc.list_workers_for(main_id)
    all_wrks = await _acc.list_by_role("worker")
    free     = [w for w in all_wrks if w.parent_account_id != main_id]
    st   = "🟢 запущен" if manager.is_running(main_id) else "🔴 остановлен"
    wm   = WORK_MODE_LABELS.get(main.work_mode, main.work_mode)
    ntf  = "🔔" if main.notify_enabled else "🔕"

    lines = [
        f"🧩 <b>Аккаунт #{main_id}</b>  {st}",
        f"<code>{main.phone}</code>",
        f"Режим: {wm}   Уведомления: {ntf}",
        f"{_sep()}",
    ]
    b = InlineKeyboardBuilder()

    if linked:
        lines.append(f"<b>Прикреплено ({len(linked)}):</b>")
        for w in linked:
            lines.append(f"  ♻️ #{w.id}  <code>{w.phone}</code>")
            b.button(text=f"❌ Открепить #{w.id}", callback_data=f"wrk:unlink:{main_id}:{w.id}")
            b.button(text=f"🗑 Удалить #{w.id}", callback_data=f"wrk:del:ask:{w.id}")
    else:
        lines.append("Расходников ещё нет")

    if free:
        lines.append(f"\n<b>Свободные ({len(free)}):</b>")
        for w in free:
            tag = f"у #{w.parent_account_id}" if w.parent_account_id else "свободен"
            lines.append(f"  ♻️ #{w.id}  <code>{w.phone}</code>  <i>({tag})</i>")
            b.button(text=f"➕ Прикрепить #{w.id}", callback_data=f"wrk:link:{main_id}:{w.id}")
            b.button(text=f"🗑 Удалить #{w.id}", callback_data=f"wrk:del:ask:{w.id}")

    b.button(text="♻️ → Расходник в ЛС", callback_data=f"wrk:wm:{main_id}:worker_dm")
    b.button(text="💬 → Ответ в чат",    callback_data=f"wrk:wm:{main_id}:chat_reply")
    if main.notify_enabled:
        b.button(text="🔕 Выкл уведомления", callback_data=f"wrk:ntf:{main_id}:0")
    else:
        b.button(text="🔔 Вкл уведомления",  callback_data=f"wrk:ntf:{main_id}:1")
    b.button(text="⬅️ Назад", callback_data="wrk:links")
    b.adjust(1)
    await _edit(c.message, "\n".join(lines)[:3900], b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("wrk:del:ask:"))
async def cb_wrk_delete_ask(c: CallbackQuery) -> None:
    wid = int(c.data.split(":")[-1])
    worker = await _acc.get(wid)
    if not worker or worker.role != "worker":
        await c.answer("Расходник не найден", show_alert=True)
        return
    linked = f"прикреплён к #{worker.parent_account_id}" if worker.parent_account_id else "свободен"
    text = (
        f"🗑 <b>Удалить расходник #{wid}?</b>\n"
        f"{_sep()}\n"
        f"📱 <code>{worker.phone}</code>\n"
        f"Статус: <i>{linked}</i>\n\n"
        "Аккаунт удалится из системы и больше не будет использоваться для DM."
    )
    await _edit(
        c.message,
        text,
        _btn(("✅ Да, удалить", f"wrk:del:yes:{wid}"), ("⬅️ Отмена", "wrk:list")),
    )
    await c.answer()


@router.callback_query(F.data.startswith("wrk:del:yes:"))
async def cb_wrk_delete_yes(c: CallbackQuery) -> None:
    wid = int(c.data.split(":")[-1])
    worker = await _acc.get(wid)
    if not worker or worker.role != "worker":
        await c.answer("Расходник уже удалён", show_alert=True)
        await cb_wrk_list(c)
        return
    await _acc.delete(wid)
    await c.answer(f"🗑 Расходник #{wid} удалён", show_alert=True)
    await cb_wrk_list(c)


@router.callback_query(F.data.startswith("wrk:main:"))
async def cb_wrk_main(c: CallbackQuery) -> None:
    await _show_main_workers(c, int(c.data.split(":")[-1]))


@router.callback_query(F.data.startswith("wrk:link:"))
async def cb_wrk_link(c: CallbackQuery) -> None:
    _, _, main_id, wrk_id = c.data.split(":")
    await _acc.link_worker(int(wrk_id), int(main_id))
    await c.answer(f"♻️ #{wrk_id} прикреплён к #{main_id} ✅", show_alert=True)
    await _show_main_workers(c, int(main_id))


@router.callback_query(F.data.startswith("wrk:unlink:"))
async def cb_wrk_unlink(c: CallbackQuery) -> None:
    _, _, main_id, wrk_id = c.data.split(":")
    await _acc.link_worker(int(wrk_id), None)
    await c.answer(f"♻️ #{wrk_id} откреплён ✅", show_alert=True)
    mid = int(main_id)
    if mid:
        await _show_main_workers(c, mid)
    else:
        await cb_wrk_list(c)


@router.callback_query(F.data.startswith("wrk:wm:"))
async def cb_wrk_wm(c: CallbackQuery) -> None:
    _, _, main_id, mode = c.data.split(":")
    await _acc.update_field(int(main_id), work_mode=mode)
    manager.invalidate(int(main_id))
    await c.answer(f"{WORK_MODE_LABELS.get(mode, mode)} ✅", show_alert=True)
    await _show_main_workers(c, int(main_id))


@router.callback_query(F.data.startswith("wrk:ntf:"))
async def cb_wrk_ntf(c: CallbackQuery) -> None:
    _, _, main_id, val = c.data.split(":")
    enabled = val == "1"
    await _acc.update_field(int(main_id), notify_enabled=enabled)
    manager.invalidate(int(main_id))
    await c.answer("🔔 Уведомления включены" if enabled else "🔕 Уведомления выключены", show_alert=True)
    await _show_main_workers(c, int(main_id))


# ── Раздел: Ключевые слова ────────────────────────────────────────────────────
@router.callback_query(F.data == "m:keywords")
async def cb_kw_menu(c: CallbackQuery) -> None:
    aid = _sel.get(c.from_user.id)
    if not aid:
        await c.answer("Сначала выбери аккаунт на главном экране", show_alert=True)
        return
    acc  = await _acc.get(aid)
    rows = await _kw.list_for(aid)
    mm   = MATCH_MODE_LABELS.get(acc.match_mode if acc else "mixed", "")
    text = (
        f"🔑 <b>Ключевые слова</b>  —  аккаунт #{aid}\n"
        f"{_sep()}\n"
        f"Режим поиска: {mm}\n"
        f"Ключей добавлено: <b>{len(rows)}</b>"
    )
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить ключи",    callback_data="kw:add")
    b.button(text="📋 Список ключей",      callback_data="kw:list")
    b.button(text="🧹 Очистить все",       callback_data="kw:clear")
    if acc and acc.match_mode == "mixed":
        b.button(text="🔤 → Только фразы", callback_data="kw:mm:phrase")
    else:
        b.button(text="🔡 → Слова+фразы",  callback_data="kw:mm:mixed")
    b.button(text="⬅️ Назад", callback_data="m:home")
    b.adjust(2, 2, 1)
    await _edit(c.message, text, b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("kw:mm:"))
async def cb_kw_mm(c: CallbackQuery) -> None:
    aid = _sel.get(c.from_user.id)
    if not aid:
        await c.answer("Сначала выбери аккаунт", show_alert=True)
        return
    mode = c.data.split(":")[-1]
    await _acc.update_field(aid, match_mode=mode)
    manager.invalidate(aid)
    await c.answer(f"{MATCH_MODE_LABELS.get(mode, mode)} ✅", show_alert=True)
    await cb_kw_menu(c)


@router.callback_query(F.data == "kw:add")
async def cb_kw_add(c: CallbackQuery, state: FSMContext) -> None:
    if not _sel.get(c.from_user.id):
        await c.answer("Сначала выбери аккаунт", show_alert=True)
        return
    await state.set_state(KwFSM.values)
    await c.message.answer(
        "🔑 <b>Добавить ключевые слова</b>\n\n"
        "Отправь список — каждое слово/фраза с новой строки:\n\n"
        "<code>нужен трафик\nищу рассылку\nкупить базу\nнужны инвайты</code>\n\n"
        "<i>Можно вставить сколько угодно строк за раз.</i>",
        parse_mode="HTML",
    )
    await c.answer()


@router.message(KwFSM.values)
async def fsm_kw(m: Message, state: FSMContext) -> None:
    aid = _sel.get(m.from_user.id)
    if not aid:
        await state.clear()
        await m.answer("Аккаунт не выбран. Начни заново.")
        return
    lines = (m.text or "").splitlines()
    n = await _kw.add_bulk(aid, lines)
    manager.invalidate(aid)
    await state.clear()
    total = await _kw.list_for(aid)
    await m.answer(
        f"✅ Добавлено <b>{n}</b> ключей для аккаунта #{aid}\n"
        f"Итого ключей: <b>{len(total)}</b>",
        reply_markup=_btn(("🏠 Главное меню", "m:home"), ("🔑 К ключам", "m:keywords")),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "kw:list")
async def cb_kw_list(c: CallbackQuery) -> None:
    _kw_pg[c.from_user.id] = 0
    await _render_kw(c)


async def _render_kw(c: CallbackQuery) -> None:
    aid = _sel.get(c.from_user.id)
    if not aid:
        await c.answer("Сначала выбери аккаунт", show_alert=True)
        return
    rows = await _kw.list_for(aid)
    b    = InlineKeyboardBuilder()
    if not rows:
        await _edit(
            c.message,
            f"🔑 <b>Ключи аккаунта #{aid}</b>\n\nСписок пуст.",
            _btn(("➕ Добавить ключи", "kw:add"), ("⬅️ Назад", "m:keywords")),
        )
        await c.answer()
        return

    pg    = max(0, min(_kw_pg.get(c.from_user.id, 0), (len(rows) - 1) // PAGE))
    _kw_pg[c.from_user.id] = pg
    total = (len(rows) + PAGE - 1) // PAGE
    part  = rows[pg * PAGE:(pg + 1) * PAGE]

    lines = [f"🔑 <b>Ключи #{aid}</b>  стр. {pg+1}/{total}  (всего: {len(rows)})\n"]
    for r in part:
        hits = f"<i>×{r.triggers_count}</i>" if r.triggers_count else ""
        lines.append(f"<code>{r.value}</code> {hits}")
        b.button(text=f"🗑 #{r.id}", callback_data=f"kw:del:{r.id}")

    b.adjust(3)  # кнопки удаления по 3 в ряд

    nav = []
    if pg > 0:
        nav.append(("◀️ Назад", "kw:pg:prev"))
    if pg < total - 1:
        nav.append(("Вперёд ▶️", "kw:pg:next"))
    for t, d in nav:
        b.button(text=t, callback_data=d)
    if nav:
        b.adjust(3, *(2 for _ in range(100)))

    b.button(text="🧹 Очистить всё", callback_data="kw:clear")
    b.button(text="⬅️ Назад",        callback_data="m:keywords")
    b.adjust(3, *(2 for _ in range(100)), 1, 1)

    await _edit(c.message, "\n".join(lines)[:3800], b.as_markup())
    await c.answer()


@router.callback_query(F.data.in_({"kw:pg:prev", "kw:pg:next"}))
async def cb_kw_pg(c: CallbackQuery) -> None:
    _kw_pg[c.from_user.id] = _kw_pg.get(c.from_user.id, 0) + (-1 if c.data.endswith("prev") else 1)
    await _render_kw(c)


@router.callback_query(F.data.startswith("kw:del:"))
async def cb_kw_del(c: CallbackQuery) -> None:
    kid = int(c.data.split(":")[-1])
    await _kw.delete_one(kid)
    manager.invalidate(_sel.get(c.from_user.id))
    await c.answer("🗑 Удалено")
    await _render_kw(c)


@router.callback_query(F.data == "kw:clear")
async def cb_kw_clear(c: CallbackQuery) -> None:
    aid = _sel.get(c.from_user.id)
    if not aid:
        await c.answer("Сначала выбери аккаунт", show_alert=True)
        return
    n = await _kw.clear_for(aid)
    manager.invalidate(aid)
    await c.answer(f"🧹 Удалено {n} ключей", show_alert=True)
    await cb_kw_menu(c)


# ── Раздел: Промпты ───────────────────────────────────────────────────────────
@router.callback_query(F.data == "m:prompts")
async def cb_prompts(c: CallbackQuery) -> None:
    aid = _sel.get(c.from_user.id)
    if not aid:
        await c.answer("Сначала выбери аккаунт", show_alert=True)
        return
    existing = {p.scope for p in await _pr.list_for(aid)}
    lines = [f"🧠 <b>Промпты</b>  —  аккаунт #{aid}\n{_sep()}\n"]
    for scope, (label, _) in PROMPT_INFO.items():
        mark = "✏️" if scope in existing else "📝"
        lines.append(f"{mark} {label}")
    b = InlineKeyboardBuilder()
    for scope, (label, _) in PROMPT_INFO.items():
        b.button(text=label, callback_data=f"pr:{scope}")
    b.button(text="⬅️ Назад", callback_data="m:home")
    b.adjust(1)
    await _edit(c.message, "\n".join(lines), b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("pr:"))
async def cb_pr(c: CallbackQuery, state: FSMContext) -> None:
    scope = c.data.split(":", 1)[1]
    if scope not in PROMPT_INFO:
        await c.answer("Неизвестный тип", show_alert=True)
        return
    aid = _sel.get(c.from_user.id)
    if not aid:
        await c.answer("Сначала выбери аккаунт", show_alert=True)
        return
    label, default = PROMPT_INFO[scope]
    current = await _pr.get(aid, scope, default)
    await state.update_data(scope=scope)
    await state.set_state(PromptFSM.body)
    await c.message.answer(
        f"🧠 <b>{label}</b>\n\n"
        f"<b>Текущий промпт:</b>\n"
        f"<code>{current[:800]}</code>\n\n"
        "Отправь новый текст промпта (или /cancel для отмены):",
        parse_mode="HTML",
    )
    await c.answer()


@router.message(PromptFSM.body)
async def fsm_prompt(m: Message, state: FSMContext) -> None:
    if m.text and m.text.strip() == "/cancel":
        await state.clear()
        await m.answer("Отменено.", reply_markup=_btn(("🏠 Главное меню", "m:home")))
        return
    aid = _sel.get(m.from_user.id)
    data = await state.get_data()
    scope = data.get("scope", "")
    if not aid or scope not in PROMPT_INFO:
        await state.clear()
        await m.answer("Ошибка состояния. Начни заново.")
        return
    await _pr.upsert(aid, scope, (m.text or "").strip())
    await state.clear()
    label, _ = PROMPT_INFO[scope]
    await m.answer(
        f"✅ <b>{label}</b> сохранён для аккаунта #{aid}",
        reply_markup=_btn(("🏠 Главное меню", "m:home"), ("🧠 Промпты", "m:prompts")),
        parse_mode="HTML",
    )


# ── Раздел: Логи ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == "m:logs")
async def cb_logs(c: CallbackQuery) -> None:
    _log_pg[c.from_user.id] = 0
    await _render_logs(c)


async def _render_logs(c: CallbackQuery) -> None:
    aid  = _sel.get(c.from_user.id)
    rows = await _log.list_for(aid) if aid else await _log.list_all()
    b    = InlineKeyboardBuilder()

    filter_tag = f"аккаунт #{aid}" if aid else "все аккаунты"

    if not rows:
        b.button(text="⬅️ Назад", callback_data="m:home")
        b.adjust(1)
        await _edit(
            c.message,
            f"📜 <b>Логи</b>  ({filter_tag})\n\nЛогов пока нет.",
            b.as_markup(),
        )
        await c.answer()
        return

    pg    = max(0, min(_log_pg.get(c.from_user.id, 0), (len(rows) - 1) // PAGE))
    _log_pg[c.from_user.id] = pg
    total = (len(rows) + PAGE - 1) // PAGE
    part  = rows[pg * PAGE:(pg + 1) * PAGE]

    blocks = [f"📜 <b>Логи</b>  ({filter_tag})  стр. {pg+1}/{total}\n{_sep()}\n"]
    for r in part:
        ts  = r.created_at.strftime("%d.%m %H:%M") if r.created_at else ""
        kw  = f"🔑 <code>{r.keyword}</code>" if r.keyword else ""
        usr = f"@{r.username}" if r.username else f"id:{r.user_id}"
        wrk = f" via ♻️#{r.worker_id}" if r.worker_id else ""
        inc = (r.incoming or "")[:90].replace("\n", " ")
        out = (r.outgoing or "")[:90].replace("\n", " ")
        err = f"\n⚠️ <i>{(r.error or '')[:100]}</i>" if r.error else ""

        blocks.append(
            f"<b>#{r.id}</b> {ts}  acc#{r.account_id}{wrk}\n"
            f"👤 {usr}  {kw}\n"
            f"📥 <i>{inc}</i>\n"
            f"📤 <i>{out}</i>{err}"
        )
        blocks.append(_sep(16))

    if pg > 0:
        b.button(text="◀️ Назад", callback_data="log:pg:prev")
    if pg < total - 1:
        b.button(text="Вперёд ▶️", callback_data="log:pg:next")
    if pg > 0 or pg < total - 1:
        b.adjust(2)
    if aid:
        b.button(text="🧹 Очистить логи", callback_data="log:clear")
    b.button(text="⬅️ Назад", callback_data="m:home")
    b.adjust(*(2 for _ in range(100)), 1, 1)

    await _edit(c.message, "\n".join(blocks)[:3900], b.as_markup())
    await c.answer()


@router.callback_query(F.data.in_({"log:pg:prev", "log:pg:next"}))
async def cb_log_pg(c: CallbackQuery) -> None:
    _log_pg[c.from_user.id] = _log_pg.get(c.from_user.id, 0) + (-1 if c.data.endswith("prev") else 1)
    await _render_logs(c)


@router.callback_query(F.data == "log:clear")
async def cb_log_clear(c: CallbackQuery) -> None:
    aid = _sel.get(c.from_user.id)
    if not aid:
        await c.answer("Сначала выбери аккаунт", show_alert=True)
        return
    n = await _log.clear_for(aid)
    await c.answer(f"🧹 Удалено {n} логов", show_alert=True)
    await _render_logs(c)
