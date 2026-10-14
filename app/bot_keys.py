"""Бот В — @uncleevan77bot «кнопки»: власник у будь-якому чаті набирає @бот → меню → клік → серія
повідомлень клієнту від імені власника (через Telegram Business connection).

Потік:
  1. inline-меню (тільки для адмінів) → вибір кнопки → у чат іде ПЕРШЕ повідомлення серії з невидимим маркером;
  2. бот як Business-бот бачить це повідомлення (business_message), читає маркер, досилає решту серії
     через business_connection_id — вже без «via @бот»;
  3. контакт клієнта (chat) записується в таблицю contacts.
Усі business-апдейти логуються — це і є тест, чи бачить бот власні повідомлення власника.
"""
import asyncio
import hashlib
import json
import logging
import pathlib
import re

import sqlalchemy as sa
import yaml
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BusinessConnection, BusinessMessagesDeleted, CallbackQuery, ChosenInlineResult,
                           CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, InlineQuery,
                           InlineQueryResultArticle, InputTextMessageContent, LinkPreviewOptions, Message)

from . import db, notify, ui
from .config import cfg

log = logging.getLogger("keys")
r = Router()
TPL = pathlib.Path(__file__).resolve().parent.parent / "templates" / "buttons.yaml"
MARK = "⁣"  # invisible separator: маркер серії у кінці першого повідомлення
_tpl: dict = {}


# ---------- шаблони ----------
def load() -> dict:
    global _tpl
    _tpl = yaml.safe_load(TPL.read_text("utf-8"))
    return _tpl


def buttons() -> list[dict]:
    t = _tpl or load()
    out = []
    for kind in ("me", "client"):
        for b in t.get(kind) or []:
            out.append({**b, "kind": kind})
    return out


def by_id(bid: str) -> dict | None:
    return next((b for b in buttons() if b["id"] == bid), None)


def marker(bid: str, idx: int) -> str:
    """Невидимий хвіст: U+2063 + id + крок, закодовано символами нульової ширини."""
    code = f"{bid}:{idx}"
    bits = "".join(f"{ord(c):08b}" for c in code)
    return MARK + bits.translate(str.maketrans("01", "​‌")) + MARK


def read_marker(text: str) -> tuple[str, int] | None:
    m = re.search(f"{MARK}([​‌]+){MARK}", text or "")
    if not m:
        return None
    bits = m.group(1).translate(str.maketrans("​‌", "01"))
    try:
        code = "".join(chr(int(bits[i:i + 8], 2)) for i in range(0, len(bits), 8))
        bid, idx = code.rsplit(":", 1)
        return bid, int(idx)
    except Exception:
        return None


async def render(text: str, name: str = "") -> str:
    return text.replace("{price}", await ui.price()).replace("{name}", name).rstrip()


def kb(rows: list | None) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    out = []
    for row in rows:
        line = []
        for b in row:
            if "url" in b:
                line.append(InlineKeyboardButton(text=b["text"], url=b["url"]))
            elif "copy" in b:
                line.append(InlineKeyboardButton(text=b["text"], copy_text=CopyTextButton(text=b["copy"])))
        out.append(line)
    return InlineKeyboardMarkup(inline_keyboard=out)


# ---------- inline-меню ----------
@r.inline_query()
async def inline(q: InlineQuery):
    if not ui.is_admin(q.from_user):
        await q.answer([], cache_time=5, is_personal=True)
        return
    needle = (q.query or "").strip().lower()
    results = []
    for b in buttons():
        if needle and needle not in b["title"].lower() and needle not in b.get("desc", "").lower():
            continue
        first = b["messages"][0]
        text = await render(first["text"])
        if len(b["messages"]) > 1:
            text += marker(b["id"], 1)
        results.append(InlineQueryResultArticle(
            id=hashlib.md5(b["id"].encode()).hexdigest(),
            title=("👤 " if b["kind"] == "client" else "") + b["title"],
            description=(await render(b.get("desc", ""))) + (f" · {len(b['messages'])} повід." if len(b["messages"]) > 1 else ""),
            input_message_content=InputTextMessageContent(
                message_text=text, parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True)),
            reply_markup=kb(first.get("buttons")),
            thumbnail_url=f"{cfg.public_url}/icons/{b['id']}.png" if cfg.public_url else None,
            thumbnail_width=160, thumbnail_height=160,
        ))
    await q.answer(results[:50], cache_time=1, is_personal=True)


@r.chosen_inline_result()
async def chosen(c: ChosenInlineResult):
    log.info("keys: chosen %s by %s", c.result_id, c.from_user.username)


# ---------- Business connection ----------
@r.business_connection()
async def on_connect(bc: BusinessConnection):
    await db.set_setting("keys_bc", json.dumps({
        "id": bc.id, "user": bc.user.id, "username": bc.user.username,
        "enabled": bc.is_enabled, "can_reply": bool(getattr(bc, "can_reply", None) or
                                                 getattr(getattr(bc, "rights", None), "can_reply", None))}))
    log.info("keys: business connection %s enabled=%s can_reply=%s", bc.id, bc.is_enabled, getattr(bc, "rights", None))
    await notify.to_group(f"🔌 Бот-кнопки: підключення {'активне' if bc.is_enabled else 'вимкнене'}, "
                          f"права: {getattr(bc, 'rights', None) or getattr(bc, 'can_reply', None)}")


@r.business_message()
async def on_business_message(m: Message, bot: Bot):
    """Головний тест: чи приходять сюди повідомлення самого власника."""
    me_owner = m.from_user and m.from_user.id == m.chat.id  # вхідне від клієнта: from == chat
    who = "клієнт" if me_owner else "ВЛАСНИК"
    kind = m.content_type
    log.info("keys: business_message chat=%s from=%s(%s) type=%s text=%r", m.chat.id,
             m.from_user and m.from_user.username, who, kind, (m.text or m.caption or "")[:60])
    await db.set_setting("keys_last_bm", json.dumps({"chat": m.chat.id, "who": who, "type": kind,
                                                      "from": m.from_user and m.from_user.username}))
    # контакт
    await upsert_contact(m, from_client=bool(me_owner))
    # тригер кнопки
    mk = read_marker(m.text or "")
    if who == "ВЛАСНИК" and mk:
        bid, idx = mk
        b = by_id(bid)
        if not b:
            return
        name = m.chat.first_name or ""
        for step in b["messages"][idx:]:
            try:
                await bot.send_message(m.chat.id, await render(step["text"], name), parse_mode="HTML",
                                       business_connection_id=m.business_connection_id,
                                       reply_markup=kb(step.get("buttons")),
                                       link_preview_options=LinkPreviewOptions(is_disabled=True))
            except TelegramAPIError as ex:
                log.warning("keys: кнопку %s не надіслано: %s", bid, ex)
                await notify.to_group(f"⚠️ Кнопка «{b['title']}»: не надіслано — {ex}")
                break
            await asyncio.sleep(0.5)
        await db.log("keys_button", m.chat.id, bid)


@r.deleted_business_messages()
async def on_deleted(d: BusinessMessagesDeleted):
    log.info("keys: deleted in %s: %s", d.chat.id, d.message_ids)


# ---------- контакти ----------
contacts = sa.Table(
    "contacts", db.md,
    sa.Column("tg_id", sa.BigInteger, primary_key=True),
    sa.Column("username", sa.String(64)),
    sa.Column("name", sa.String(200)),
    sa.Column("phone", sa.String(40)),
    sa.Column("email", sa.String(120)),
    sa.Column("first_seen", sa.BigInteger),
    sa.Column("last_seen", sa.BigInteger),
    sa.Column("last_button", sa.String(64)),
    extend_existing=True,
)
PHONE = re.compile(r"(?:\+?38)?0\d{9}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


async def upsert_contact(m: Message, from_client: bool = True) -> None:
    """Телефон/email беремо лише з повідомлень клієнта — у шаблонах власника свої контакти."""
    if m.chat.type != "private":
        return
    txt = (m.text or m.caption or "") if from_client else ""
    phone = (m.contact.phone_number if (m.contact and from_client) else None) or (PHONE.search(txt) or [None])[0]
    email = (EMAIL.search(txt) or [None])[0]
    now = db.now()
    name = " ".join(x for x in (m.chat.first_name, m.chat.last_name) if x)
    async with db.engine.begin() as c:
        row = (await c.execute(sa.select(contacts).where(contacts.c.tg_id == m.chat.id))).first()
        vals = {"username": m.chat.username, "name": name, "last_seen": now}
        if phone: vals["phone"] = phone
        if email: vals["email"] = email
        mk = read_marker(m.text or "")
        if mk: vals["last_button"] = mk[0]
        if row:
            await c.execute(sa.update(contacts).where(contacts.c.tg_id == m.chat.id).values(**vals))
        else:
            await c.execute(sa.insert(contacts).values(tg_id=m.chat.id, first_seen=now, **vals))


async def send_series(bot: Bot, chat_id: int, bc_id: str, b: dict, start: int = 0, name: str = "") -> str | None:
    """Надсилає повідомлення кнопки від імені власника. Повертає текст помилки або None."""
    for step in b["messages"][start:]:
        try:
            await bot.send_message(chat_id, await render(step["text"], name), parse_mode="HTML",
                                   business_connection_id=bc_id, reply_markup=kb(step.get("buttons")),
                                   link_preview_options=LinkPreviewOptions(is_disabled=True))
        except TelegramAPIError as ex:
            log.warning("keys: %s → %s: %s", b["id"], chat_id, ex)
            return str(ex)
        await asyncio.sleep(0.4)
    await db.log("keys_button", chat_id, b["id"])
    return None


def menu_kb(target: int) -> InlineKeyboardMarkup:
    rows, line = [], []
    for b in buttons():
        line.append(InlineKeyboardButton(text=("👤 " if b["kind"] == "client" else "") + b["title"],
                                         callback_data=f"ks:{target}:{b['id']}"))
        if len(line) == 2:
            rows.append(line); line = []
    if line:
        rows.append(line)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def target_label(chat_id: int) -> str:
    row = await db.one(sa.select(contacts).where(contacts.c.tg_id == chat_id))
    if row:
        return f"{row['name'] or ''} {'@' + row['username'] if row['username'] else ''}".strip() or str(chat_id)
    return str(chat_id)


@r.message(CommandStart(deep_link=True, magic=F.args.regexp(r"^bizChat-?\d+$")), F.chat.type == "private")
async def start_bizchat(m: Message, command: CommandObject):
    """Власник натиснув «Керувати ботом» у чаті з клієнтом → меню кнопок для цього чату."""
    if not ui.is_admin(m.from_user):
        return
    target = int(command.args[len("bizChat"):])
    await db.set_setting("keys_target", str(target))
    await m.answer(f"Клієнт: <b>{await target_label(target)}</b>\nОберіть кнопку — повідомлення піде від Вашого імені:",
                   reply_markup=menu_kb(target))


@r.callback_query(F.data.startswith("ks:"))
async def on_menu(c: CallbackQuery, bot: Bot):
    if not ui.is_admin(c.from_user):
        await c.answer(); return
    _, target, bid = c.data.split(":", 2)
    b = by_id(bid)
    bc = json.loads(await db.get_setting("keys_bc", "{}"))
    if not b or not bc.get("id"):
        await c.answer("Бот не підключений до акаунта (Telegram Business → Чатботи).", show_alert=True); return
    row = await db.one(sa.select(contacts).where(contacts.c.tg_id == int(target)))
    name = (row["name"] or "").split(" ")[0] if row else ""
    err = await send_series(bot, int(target), bc["id"], b, 0, name)
    await c.answer("Надіслано ✅" if not err else f"Не вийшло: {err[:150]}", show_alert=bool(err))


# ---------- приватний чат з ботом ----------
@r.message(CommandStart(), F.chat.type == "private")
async def start(m: Message):
    if ui.is_admin(m.from_user):
        t = await db.get_setting("keys_target")
        await m.answer("Кнопки Uncle Evan.\n• У чаті з клієнтом натисніть угорі «Manage Bot» → тут з'явиться меню для цього чату.\n"
                       "• Або наберіть у чаті <b>@" + (ui.BOT_NAMES.get("keys") or "uncleevan77bot") + "</b>.\n"
                       "/status — стан, /contacts — контакти.",
                       reply_markup=menu_kb(int(t)) if t else None)
    else:
        await m.answer("Це службовий бот Uncle Evan. Для бізнес-плану — @UncleEvanBot, умови гранту — @VlasnaSpravaBot.")


@r.message(Command("status"), F.chat.type == "private")
async def status(m: Message):
    if not ui.is_admin(m.from_user):
        return
    bc = await db.get_setting("keys_bc", "—")
    last = await db.get_setting("keys_last_bm", "—")
    n = await db.scalar(sa.select(sa.func.count()).select_from(contacts))
    await m.answer(f"Підключення: <code>{bc}</code>\nОстаннє business-повідомлення: <code>{last}</code>\n"
                   f"Контактів: {n}\nКнопок: {len(buttons())}")


@r.message(Command("sheet"), F.chat.type == "private")
async def sheet_formula(m: Message):
    """Формула для Google-таблиці: аркуш сам підтягує базу контактів із сервера."""
    if not ui.is_admin(m.from_user):
        return
    from .web import contacts_key
    url = f"{cfg.public_url}/api/contacts.csv?key={contacts_key()}"
    await m.answer("Формула для комірки A1 аркуша «Telegram — усі, хто писав»:\n"
                   f"<code>=IMPORTDATA(\"{url}\")</code>")


@r.message(Command("contact_del"), F.chat.type == "private")
async def contact_del(m: Message, command: CommandObject):
    """/contact_del <tg_id> — прибрати тестовий/зайвий рядок із бази контактів."""
    if not ui.is_admin(m.from_user):
        return
    if not (command.args or "").strip().lstrip("-").isdigit():
        await m.answer("Вкажи Telegram ID: /contact_del 123456789")
        return
    async with db.engine.begin() as c:
        res = await c.execute(sa.delete(contacts).where(contacts.c.tg_id == int(command.args.strip())))
    await m.answer("Видалено." if res.rowcount else "Такого контакту немає.")


@r.message(Command("contacts"), F.chat.type == "private")
async def export_contacts(m: Message):
    if not ui.is_admin(m.from_user):
        return
    rows = await db.all_(sa.select(contacts).order_by(contacts.c.last_seen.desc()))
    if not rows:
        await m.answer("Контактів ще немає.")
        return
    lines = ["tg_id;username;name;phone;email;last_button"]
    for x in rows:
        lines.append(";".join(str(x[k] or "") for k in ("tg_id", "username", "name", "phone", "email", "last_button")))
    from aiogram.types import BufferedInputFile
    await m.answer_document(BufferedInputFile("\n".join(lines).encode("utf-8-sig"), "contacts.csv"),
                            caption=f"{len(rows)} контактів")
