"""Доставка заявки: у групу «Заявки», клієнту, у Google-таблицю."""
import asyncio
import json
import logging
import time
import urllib.request
from html import escape as e

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from . import db, texts as T, ui
from .config import cfg
from .grant import calc_anketa, money

log = logging.getLogger("notify")
BOTS: dict[str, Bot] = {}


def status_kb(app: dict, user: dict | None, with_user_link: bool = True) -> InlineKeyboardMarkup:
    cur = app["status"]
    b = lambda s: ui.btn(("• " if s == cur else "") + T.STATUSES[s], cb=f"st:{app['id']}:{s}")
    rows = [[b("clarify"), b("work")], [b("report"), b("paid")], [b("done"), b("reject")]]
    if user and user.get("username"):
        rows.insert(0, [ui.btn("✍️ Написати клієнту", url=f"https://t.me/{user['username']}")])
    elif with_user_link:
        rows.insert(0, [ui.btn("✍️ Написати клієнту", url=f"tg://user?id={app['tg_id']}")])
    return ui.kb(*rows)


def app_card(app: dict, user: dict | None) -> str:
    s = app["state"] or {}
    g = calc_anketa(s)
    path = "Старт" if g["start"] else "Масштабування"
    tg = s.get("tg") or (("@" + user["username"]) if user and user.get("username") else "—")
    lines = [
        f"<b>{T.STATUSES[app['status']]} · Заявка №{app['id']} · {path}</b>",
        "",
        f"👤 {e(str(s.get('pib') or '—'))}",
        f"📞 {e(str(s.get('phone') or '—'))}{' ✓ Telegram' if s.get('phone_verified') else ''} · {e(str(tg))}",
    ]
    if s.get("email"):
        lines.append(f"✉️ {e(str(s['email']))}")
    lines += [
        f"🏷 {e(str(s.get('bizname') or '—'))}: {e(str(s.get('what') or '—'))}",
        f"📍 {e(str(s.get('region') or '—'))}, {e(str(s.get('city') or '—'))}",
        f"🏭 {e(str(s.get('sector') or '—'))}",
        f"💰 Грант: <b>{money(g['grant'])} грн</b> (бонуси +{g['pct']} %) · кошторис {money(g['items_total'])} грн",
        f"👥 Нових робочих місць: {g['jobs']}",
        f"🔗 Джерело: {e(app.get('source') or 'пряме')}",
    ]
    if not g["start"] and g["jobs"] < 1:
        lines.append("⚠️ Масштабування без нового робочого місця")
    return "\n".join(lines)


def _doc(app: dict) -> BufferedInputFile:
    return BufferedInputFile(app["text"].encode("utf-8"), filename=f"anketa-{app['id']}.txt")


async def target_chat() -> int | None:
    v = await db.get_setting("orders_chat_id") or await db.get_setting("admin_id")
    return int(v) if v else None


async def to_group(text: str, **kw):
    bot = BOTS.get("order") or BOTS.get("info")
    chat = await target_chat()
    if not bot or not chat:
        log.warning("немає групи заявок: %s", text[:200])
        return None
    try:
        return await bot.send_message(chat, text, **kw)
    except TelegramAPIError as ex:
        log.error("group send failed: %s", ex)
        return None


async def deliver(app_id: int) -> None:
    app = await db.get_app(app_id)
    user = await db.get_user(app["tg_id"])
    bot = BOTS.get("order")
    if not bot:
        return
    price = await ui.price()
    # клієнту
    try:
        await ui.show(bot, app["tg_id"], "done", T.A6.format(grant=money(app["grant"]), price=price), ui.kb(
            [ui.btn(T.BTN_OWNER, url=cfg.owner_url)],
            [ui.btn(T.BTN_CHANNEL, url=cfg.channel_url), ui.btn(T.BTN_REVIEWS, url=cfg.reviews_url)]))
        await bot.send_document(app["tg_id"], _doc(app), caption=f"Ваша анкета №{app['id']}")
    except TelegramAPIError as ex:
        log.error("client copy failed: %s", ex)
    # у групу
    chat = await target_chat()
    if chat:
        card = app_card(app, user)
        m = None
        try:
            m = await bot.send_message(chat, card, reply_markup=status_kb(app, user))
        except TelegramBadRequest:
            try:
                m = await bot.send_message(chat, card, reply_markup=status_kb(app, user, with_user_link=False))
            except TelegramAPIError as ex:
                log.error("group card failed: %s", ex)
        except TelegramAPIError as ex:
            log.error("group card failed: %s", ex)
        if m:
            await db.set_app(app_id, group_chat=chat, group_msg=m.message_id)
            try:
                await bot.send_document(chat, _doc(app), reply_to_message_id=m.message_id)
            except TelegramAPIError as ex:
                log.error("group doc failed: %s", ex)
    else:
        log.warning("заявка №%s збережена, але групу не налаштовано (/setgroup)", app_id)
    asyncio.create_task(to_sheet(app, user))


async def to_sheet(app: dict, user: dict | None) -> None:
    """Рядок у Google-таблицю через вебхук Apps Script (необов'язково)."""
    if not cfg.sheet_webhook:
        return
    s = app["state"] or {}
    g = calc_anketa(s)
    row = {
        "id": app["id"], "date": time.strftime("%Y-%m-%d %H:%M", time.gmtime(app["created_at"] + 3 * 3600)),
        "status": T.STATUSES[app["status"]], "path": "Старт" if g["start"] else "Масштабування",
        "pib": s.get("pib"), "phone": s.get("phone"), "email": s.get("email"),
        "telegram": s.get("tg") or (user or {}).get("username"), "bizname": s.get("bizname"),
        "what": s.get("what"), "region": s.get("region"), "city": s.get("city"), "sector": s.get("sector"),
        "grant": g["grant"], "bonus_pct": g["pct"], "items_total": g["items_total"], "jobs": g["jobs"],
        "source": app.get("source"), "text": app["text"],
    }

    def post():
        req = urllib.request.Request(cfg.sheet_webhook, data=json.dumps(row, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()

    try:
        await asyncio.to_thread(post)
    except Exception as ex:  # таблиця не має ламати заявку
        log.error("sheet webhook failed: %s", ex)
