"""Бот А — замовлення бізнес-плану: анкета → заявка. Також група «Заявки» й адмін-команди."""
import asyncio
import functools
import logging
import re
import time
from html import escape as e

import sqlalchemy as sa
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from . import db, notify, texts as T, ui
from .config import cfg
from .grant import money

log = logging.getLogger("order")
r = Router()
TOTAL_STEPS = 17
OFFTOPIC = re.compile(r"кредит|5[\s\-–]*7[\s\-–]*9|інвест|диплом|дисертац|курсов|helvetas|варто", re.I)
SOURCE_OK = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
PRIVATE = F.chat.type == "private"


def _started(p: dict | None) -> bool:
    s = (p or {}).get("state") or {}
    return bool(s.get("path") or (s.get("step") or 0) > 0) and not s.get("_reset")


def _step(p: dict) -> int:
    return max(1, min(TOTAL_STEPS, int((p.get("state") or {}).get("step") or 1)))


async def start_screen(bot: Bot, chat_id: int, tg_id: int, edit: Message | None = None, prefill: int | None = None):
    price = await ui.price()
    p = await db.get_progress(tg_id)
    contact = [ui.btn(T.BTN_OWNER, url=cfg.owner_url)]
    if _started(p):
        await ui.show(bot, chat_id, "resume", T.A7.format(step=_step(p), total=TOTAL_STEPS), ui.kb(
            [ui.btn(T.BTN_CONTINUE, app="anketa")], [ui.btn(T.BTN_RESTART, cb="restart")], contact), edit)
        return
    has_app = await db.scalar(sa.select(sa.func.count()).select_from(db.apps).where(db.apps.c.tg_id == tg_id))
    policy = f' <a href="{cfg.policy_url}">Політика обробки даних</a>.' if cfg.policy_url else ""
    text = T.A1.format(price=price)
    if prefill:
        text += T.A1_PREFILL.format(grant=money(prefill))
    text += "\n\n" + T.A2.format(policy=policy)
    await ui.show(bot, chat_id, "order", text, ui.kb(
        [ui.btn(T.BTN_NEW if has_app else T.BTN_START, app="anketa")],
        [ui.btn(T.BTN_INFO_BOT, url=ui.info_link()), ui.btn(T.BTN_SITE, url=cfg.site_url)],
        contact), edit)


# ---------------- клієнт ----------------
@r.message(CommandStart(), PRIVATE)
async def cmd_start(m: Message, command: CommandObject, bot: Bot):
    arg = (command.args or "").strip()
    source, prefill = None, None
    if re.fullmatch(r"c\d{1,12}", arg):
        source = "botB"
        c = await db.one(sa.select(db.calcs).where(db.calcs.c.id == int(arg[1:]), db.calcs.c.tg_id == m.from_user.id))
        prefill = c["grant"] if c else None
    elif arg and SOURCE_OK.match(arg):
        source = arg
    await db.touch_user(m.from_user.id, m.from_user.username, ui.user_name(m.from_user), source=source, bot="order")
    await db.log("order_start", m.from_user.id, source)
    if ui.is_admin(m.from_user):
        await db.set_setting("admin_id", str(m.from_user.id))
    await start_screen(bot, m.chat.id, m.from_user.id, prefill=prefill)


@r.callback_query(F.data == "restart")
async def cb_restart(c: CallbackQuery, bot: Bot):
    await db.save_progress(c.from_user.id, {"step": 0, "path": None, "items": [], "monthly": {}, "_reset": True})
    await c.answer("Анкету очищено")
    await start_screen(bot, c.message.chat.id, c.from_user.id, edit=c.message)


@r.callback_query(F.data == "noremind")
async def cb_noremind(c: CallbackQuery):
    await db.set_user(c.from_user.id, no_remind=True)
    await c.answer()
    await c.message.answer(T.NO_REMIND_OK)


@r.message(Command("help"), PRIVATE)
async def cmd_help(m: Message):
    await m.answer(T.A_HELP + (ADMIN_HELP if ui.is_admin(m.from_user) else ""))


@r.message(Command("delete"), PRIVATE)
async def cmd_delete(m: Message):
    await m.answer(T.DELETE_ASK, reply_markup=ui.kb([ui.btn("Так, видалити", cb="del:yes"), ui.btn("Ні", cb="del:no")]))


@r.callback_query(F.data.startswith("del:"))
async def cb_delete(c: CallbackQuery):
    await c.answer()
    if c.data == "del:yes":
        await db.delete_user_data(c.from_user.id)
        await c.message.edit_text(T.DELETE_OK)
    else:
        await c.message.edit_text(T.DELETE_NO)


# ---------------- група «Заявки» ----------------
@r.message(Command("setgroup"))
async def cmd_setgroup(m: Message):
    if not ui.is_admin(m.from_user):
        return
    if m.chat.type == "private":
        await m.answer("Цю команду треба написати в групі «Заявки».")
        return
    await db.set_setting("orders_chat_id", str(m.chat.id))
    await m.answer("Готово. Заявки приходитимуть у цю групу.")


@r.my_chat_member()
async def on_added(ev: ChatMemberUpdated, bot: Bot):
    """Адміністратор додав бота в групу або зробив його адміном — група стає групою заявок."""
    if ev.chat.type not in ("group", "supergroup") or not ui.is_admin(ev.from_user):
        return
    if ev.new_chat_member.status not in ("member", "administrator"):
        return
    if await db.get_setting("orders_chat_id") == str(ev.chat.id):
        return
    await db.set_setting("orders_chat_id", str(ev.chat.id))
    try:
        await bot.send_message(ev.chat.id, "Готово. Заявки приходитимуть у цю групу.")
    except TelegramAPIError as ex:
        log.info("group hello failed: %s", ex)


@r.callback_query(F.data.startswith("st:"))
async def cb_status(c: CallbackQuery, bot: Bot):
    if not ui.is_admin(c.from_user):
        await c.answer("Лише для адміністратора", show_alert=True)
        return
    _, app_id, status = c.data.split(":")
    app = await db.get_app(int(app_id))
    if not app or status not in T.STATUSES:
        await c.answer("Заявку не знайдено", show_alert=True)
        return
    if app["status"] == status:
        await c.answer("Статус уже такий")
        return
    await db.set_app(app["id"], status=status, status_at=db.now(), unpaid_alerted=False)
    app = await db.get_app(app["id"])
    user = await db.get_user(app["tg_id"])
    try:
        await c.message.edit_text(notify.app_card(app, user), reply_markup=notify.status_kb(
            app, user, with_user_link=bool(c.message.reply_markup and any(
                b.url and b.url.startswith(("tg://", "https://t.me/")) for row in c.message.reply_markup.inline_keyboard for b in row))))
    except TelegramAPIError as ex:
        log.info("card edit: %s", ex)
    note = ""
    if status == "work":
        date = time.strftime("%d.%m", time.gmtime(time.time() + 3 * 86400 + 3 * 3600))
        try:
            await bot.send_message(app["tg_id"], T.A10.format(date=date))
            note = " · клієнта сповіщено"
        except TelegramAPIError:
            note = " · клієнту не доставлено"
    await db.log("status_" + status, app["tg_id"], app.get("source"))
    await c.answer(T.STATUSES[status] + note)


# ---------------- адмін ----------------
ADMIN_HELP = (
    "\n\n<b>Адмін</b>\n"
    "/stats [днів] — воронка і джерела\n"
    "/find текст — пошук людини чи заявки\n"
    "/price 7000 — змінити ціну\n"
    "/video посилання | off — відео для ботів\n"
    "/broadcast сегмент — розсилка (у відповідь на повідомлення). Сегменти: calc, started, ordered, clients, all\n"
    "/pause, /resume — нагадування\n"
    "/setgroup — у групі «Заявки»\n"
    "/links — посилання з мітками джерел"
)


def admin_only(handler):
    @functools.wraps(handler)  # aiogram читає сигнатуру оригіналу й передає лише потрібні аргументи
    async def wrap(m: Message, *a, **kw):
        if ui.is_admin(m.from_user):
            return await handler(m, *a, **kw)
    return wrap


async def stats_text(days: int) -> str:
    since = db.now() - days * 86400
    cnt = lambda q: db.scalar(q)
    U, C, P, A = db.users, db.calcs, db.progress, db.apps
    new_users = await cnt(sa.select(sa.func.count()).select_from(U).where(U.c.created_at >= since))
    calc_u = await cnt(sa.select(sa.func.count(sa.distinct(C.c.tg_id))).where(C.c.updated_at >= since))
    started = await cnt(sa.select(sa.func.count()).select_from(P).where(P.c.started_at >= since))
    apps_n = await cnt(sa.select(sa.func.count()).select_from(A).where(A.c.created_at >= since))
    paid = await cnt(sa.select(sa.func.count()).select_from(A).where(
        A.c.created_at >= since, A.c.status.in_(["paid", "done"])))
    by_status = await db.all_(sa.select(A.c.status, sa.func.count().label("n")).where(A.c.created_at >= since).group_by(A.c.status))
    src_u = await db.all_(sa.select(U.c.source, sa.func.count().label("n")).where(U.c.created_at >= since).group_by(U.c.source))
    src_a = {x["source"]: x["n"] for x in await db.all_(
        sa.select(A.c.source, sa.func.count().label("n")).where(A.c.created_at >= since).group_by(A.c.source))}
    price = int(await db.get_setting("price", str(cfg.price)) or cfg.price)
    lines = [
        f"<b>Воронка за {days} дн.</b>", "",
        f"Нових людей: {new_users}",
        f"Рахували в калькуляторі: {calc_u}",
        f"Анкет у процесі: {started}",
        f"Заявок: {apps_n}",
        f"Оплачено: {paid} · {money(paid * price)} грн",
    ]
    if by_status:
        lines += ["", "<b>Статуси</b>"] + [f"{T.STATUSES.get(x['status'], x['status'])}: {x['n']}" for x in by_status]
    if src_u:
        lines += ["", "<b>Джерела</b> (люди → заявки)"]
        for x in sorted(src_u, key=lambda x: -x["n"]):
            lines.append(f"{e(x['source'] or 'пряме')}: {x['n']} → {src_a.get(x['source'], 0)}")
    paused = await db.get_setting("paused", "0") == "1"
    lines += ["", f"Нагадування: {'на паузі' if paused else 'увімкнені'}"]
    return "\n".join(lines)


@r.message(Command("stats"))
@admin_only
async def cmd_stats(m: Message, command: CommandObject):
    days = int(command.args) if (command.args or "").strip().isdigit() else 30
    await m.answer(await stats_text(max(1, min(days, 3650))))


@r.message(Command("price"))
@admin_only
async def cmd_price(m: Message, command: CommandObject):
    v = re.sub(r"\D", "", command.args or "")
    if not v or not (100 <= int(v) <= 1_000_000):
        await m.answer(f"Поточна ціна: {await ui.price()} грн. Змінити: /price 7000")
        return
    await db.set_setting("price", v)
    await m.answer(f"Ціна в ботах: {money(int(v))} грн. Банери з плашкою ціни оновлюються окремо.")


@r.message(Command("video"))
@admin_only
async def cmd_video(m: Message, command: CommandObject):
    a = (command.args or "").strip()
    if a.lower() == "off":
        await db.set_setting("video_url", "")
        await m.answer("Відео вимкнено.")
    elif a.startswith("https://"):
        await db.set_setting("video_url", a)
        await m.answer("Відео оновлено в обох ботах.")
    else:
        cur = await db.get_setting("video_url", cfg.video_url)
        await m.answer(f"Поточне відео: {e(cur) or 'немає'}\nЗмінити: /video https://…mp4 або /video off")


@r.message(Command("pause"))
@admin_only
async def cmd_pause(m: Message):
    await db.set_setting("paused", "1")
    await m.answer("Нагадування на паузі.")


@r.message(Command("resume"))
@admin_only
async def cmd_resume(m: Message):
    await db.set_setting("paused", "0")
    await m.answer("Нагадування увімкнені.")


@r.message(Command("links"))
@admin_only
async def cmd_links(m: Message):
    a, b = ui.BOT_NAMES.get("order", "?"), ui.BOT_NAMES.get("info", "?")
    tags = ["personal", "site", "tiktok", "channel"]
    await m.answer(
        "<b>Бот замовлення</b>\n" + "\n".join(f"{t}: https://t.me/{a}?start={t}" for t in tags) +
        "\n\n<b>Бот «Власна справа»</b>\n" + "\n".join(f"{t}: https://t.me/{b}?start={t}" for t in tags),
        disable_web_page_preview=True)


@r.message(Command("find"))
@admin_only
async def cmd_find(m: Message, command: CommandObject):
    q = (command.args or "").strip().lstrip("@").lower()
    if len(q) < 2:
        await m.answer("Приклад: /find Іваненко або /find 0957221978")
        return
    out = []
    digits = re.sub(r"\D", "", q)
    for a in await db.all_(sa.select(db.apps).order_by(db.apps.c.id.desc()).limit(500)):
        s = a["state"] or {}
        hay = " ".join(str(s.get(k) or "") for k in ("pib", "phone", "email", "tg", "bizname")).lower()
        if q in hay or (len(digits) >= 5 and digits in re.sub(r"\D", "", str(s.get("phone") or ""))) or q == str(a["id"]):
            out.append(f"№{a['id']} · {T.STATUSES[a['status']]} · {e(str(s.get('pib') or ''))} · "
                       f"{e(str(s.get('phone') or ''))} · {money(a['grant'])} грн")
        if len(out) >= 10:
            break
    for u in await db.all_(sa.select(db.users).where(sa.or_(
            sa.func.lower(db.users.c.username).like(f"%{q}%"), sa.func.lower(db.users.c.name).like(f"%{q}%"))).limit(10)):
        out.append(f"👤 {T.fmt_user(u)} · id {u['tg_id']} · джерело {e(u['source'] or 'пряме')}")
    await m.answer("\n".join(out) or "Нічого не знайдено.")


SEGMENTS = {
    "calc": "рахували, не замовили", "started": "почали анкету, не завершили",
    "ordered": "надіслали заявку", "clients": "оплатили", "all": "усі",
}
_pending: dict[int, tuple[str, str]] = {}


async def segment_ids(seg: str) -> list[tuple[int, str]]:
    """→ [(tg_id, bot)] — через якого бота писати людині."""
    U, C, P, A = db.users, db.calcs, db.progress, db.apps
    alive = sa.and_(U.c.blocked.is_(False))
    if seg == "calc":
        q = sa.select(U).where(alive, U.c.tg_id.in_(sa.select(C.c.tg_id)), U.c.tg_id.notin_(sa.select(A.c.tg_id)))
    elif seg == "started":
        q = sa.select(U).where(alive, U.c.tg_id.in_(sa.select(P.c.tg_id)))
    elif seg == "ordered":
        q = sa.select(U).where(alive, U.c.tg_id.in_(sa.select(A.c.tg_id)))
    elif seg == "clients":
        q = sa.select(U).where(alive, U.c.tg_id.in_(sa.select(A.c.tg_id).where(A.c.status.in_(["paid", "done"]))))
    else:
        q = sa.select(U).where(alive)
    out = []
    for u in await db.all_(q):
        prefer = ["info", "order"] if seg == "calc" else ["order", "info"]
        which = next((w for w in prefer if u[w + "_bot"]), None)
        if which:
            out.append((u["tg_id"], which))
    return out


@r.message(Command("broadcast"))
@admin_only
async def cmd_broadcast(m: Message, command: CommandObject):
    seg = (command.args or "").strip().lower()
    src = m.reply_to_message
    text = (src.html_text if src and (src.text or src.caption) else "") if src else ""
    if seg not in SEGMENTS or not text:
        await m.answer("Напишіть текст розсилки окремим повідомленням, потім у відповідь на нього: /broadcast сегмент\n\n" +
                       "\n".join(f"{k} — {v}" for k, v in SEGMENTS.items()))
        return
    ids = await segment_ids(seg)
    _pending[m.from_user.id] = (seg, text)
    await m.answer(f"Сегмент «{SEGMENTS[seg]}»: {len(ids)} отримувачів. Надіслати повідомлення вище?",
                   reply_markup=ui.kb([ui.btn("Надіслати", cb="bc:go"), ui.btn("Скасувати", cb="bc:no")]))


@r.callback_query(F.data.startswith("bc:"))
async def cb_broadcast(c: CallbackQuery):
    if not ui.is_admin(c.from_user):
        return
    job = _pending.pop(c.from_user.id, None)
    await c.answer()
    if c.data != "bc:go" or not job:
        await c.message.edit_text("Розсилку скасовано.")
        return
    seg, text = job
    await c.message.edit_text("Розсилка пішла…")
    ok = bad = 0
    markup = ui.kb([ui.btn(T.BTN_CHANNEL, url=cfg.channel_url)])
    for tg_id, which in await segment_ids(seg):
        b = notify.BOTS.get(which)
        if not b:
            continue
        try:
            await b.send_message(tg_id, text, reply_markup=markup)
            ok += 1
        except TelegramRetryAfter as ex:
            await asyncio.sleep(ex.retry_after + 1)
            bad += 1
        except TelegramForbiddenError:
            await db.set_user(tg_id, blocked=True)
            bad += 1
        except TelegramAPIError:
            bad += 1
        await asyncio.sleep(0.06)
    await c.message.edit_text(f"Розсилку завершено: доставлено {ok}, не доставлено {bad}.")


# ---------------- довільні повідомлення клієнта ----------------
@r.message(PRIVATE, F.text | F.photo | F.document | F.voice | F.video | F.contact)
async def any_message(m: Message, bot: Bot):
    if (m.text or "").startswith("/"):
        return
    await db.touch_user(m.from_user.id, m.from_user.username, ui.user_name(m.from_user), bot="order")
    if ui.is_admin(m.from_user):
        return
    if m.text and OFFTOPIC.search(m.text):
        await m.answer(T.A13, reply_markup=ui.kb([ui.btn(T.BTN_OWNER, url=cfg.owner_url)]))
    else:
        await m.answer(T.A12, reply_markup=ui.kb([ui.btn(T.BTN_OWNER, url=cfg.owner_url)]))
    chat = await notify.target_chat()
    if chat:
        u = await db.get_user(m.from_user.id)
        try:
            await bot.send_message(chat, f"💬 Повідомлення від {T.fmt_user(u)} · id {m.from_user.id}")
            await m.copy_to(chat)
        except TelegramAPIError as ex:
            log.error("forward failed: %s", ex)
