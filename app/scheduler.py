"""Нагадування: кинута анкета, калькулятор без заявки, відгук, несплачений звіт."""
import asyncio
import datetime as dt
import logging

import sqlalchemy as sa
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from . import db, notify, texts as T, ui
from .bot_order import TOTAL_STEPS, _started, _step
from .config import cfg
from .content import SITE
from .grant import money

log = logging.getLogger("scheduler")
H, D = 3600, 86400
ANKETA_AFTER = [1 * H, 24 * H, 72 * H]
CALC_AFTER = [2 * D, 7 * D]
REVIEW_AFTER = 14 * D
UNPAID_AFTER = 24 * H


def quiet(now: dt.datetime | None = None) -> bool:
    h = (now or dt.datetime.now(cfg.tz)).hour
    return h >= cfg.quiet_from or h < cfg.quiet_to


async def _send(which: str, tg_id: int, text: str, markup) -> bool:
    bot = notify.BOTS.get(which)
    if not bot:
        return False
    try:
        await bot.send_message(tg_id, text, reply_markup=markup, disable_web_page_preview=True)
        return True
    except TelegramForbiddenError:
        await db.set_user(tg_id, blocked=True)
    except TelegramAPIError as ex:
        log.info("reminder to %s failed: %s", tg_id, ex)
    return False


async def tick(now_ts: int | None = None, force: bool = False) -> dict:
    """Один прохід. Повертає лічильники — для тестів і журналу."""
    n = {"anketa": 0, "calc": 0, "review": 0, "unpaid": 0}
    now = now_ts or db.now()
    U, P, C, A = db.users, db.progress, db.calcs, db.apps

    # несплачений звіт — лише адміну, клієнту бот не пише
    for a in await db.all_(sa.select(A).where(A.c.status == "report", A.c.unpaid_alerted.is_(False),
                                             A.c.status_at <= now - UNPAID_AFTER)):
        await db.set_app(a["id"], unpaid_alerted=True)
        s = a["state"] or {}
        await notify.to_group(f"⏰ Заявка №{a['id']} ({s.get('pib') or '—'}): звіт надіслано понад добу тому, оплати ще немає.")
        n["unpaid"] += 1

    if not force and (quiet() or await db.get_setting("paused", "0") == "1"):
        return n
    ok_user = sa.and_(U.c.no_remind.is_(False), U.c.blocked.is_(False))
    price = await ui.price()

    # кинута анкета
    rows = await db.all_(sa.select(P, U.c.order_bot).join(U, U.c.tg_id == P.c.tg_id).where(
        ok_user, U.c.order_bot.is_(True), P.c.remind_stage < len(ANKETA_AFTER)))
    for p in rows:
        if not _started(p) or now - p["updated_at"] < ANKETA_AFTER[p["remind_stage"]]:
            continue
        step = _step(p)
        if p["remind_stage"] == 0:
            text = T.A8.format(step=step, total=TOTAL_STEPS, mins=max(2, round((TOTAL_STEPS - step + 1) * 1.1)))
        else:
            text = T.A9.format(step=step, total=TOTAL_STEPS)
        await db.run(sa.update(P).where(P.c.tg_id == p["tg_id"]).values(remind_stage=p["remind_stage"] + 1))
        if await _send("order", p["tg_id"], text, ui.kb(
                [ui.btn(T.BTN_CONTINUE, app="anketa")], [ui.btn(T.BTN_OWNER, url=cfg.owner_url)],
                [ui.btn(T.BTN_NO_REMIND, cb="noremind")])):
            n["anketa"] += 1

    # рахував у калькуляторі, заявки немає
    rows = await db.all_(sa.select(C).join(U, U.c.tg_id == C.c.tg_id).where(
        ok_user, U.c.info_bot.is_(True), C.c.ordered.is_(False), C.c.remind_stage < len(CALC_AFTER),
        C.c.tg_id.notin_(sa.select(A.c.tg_id)), C.c.tg_id.notin_(sa.select(P.c.tg_id))))
    for c in rows:
        if now - c["updated_at"] < CALC_AFTER[c["remind_stage"]]:
            continue
        if c["remind_stage"] == 0:
            text = T.B5.format(grant=money(c["grant"]))
            first = [ui.btn("Чому відмовляють: 10 причин", url=SITE + "/2026/09/25/vidmova-u-granti-vlasna-sprava/")]
        else:
            text, first = T.B6.format(price=price), None
        await db.run(sa.update(C).where(C.c.id == c["id"]).values(remind_stage=c["remind_stage"] + 1))
        if await _send("info", c["tg_id"], text, ui.kb(
                first or [], [ui.btn(T.BTN_ORDER_SUM, url=ui.order_link(f"c{c['id']}"))],
                [ui.btn(T.BTN_CHANNEL, url=cfg.channel_url)], [ui.btn(T.BTN_NO_REMIND, cb="noremind")])):
            n["calc"] += 1

    # відгук через 14 днів після передачі файлів
    rows = await db.all_(sa.select(A).join(U, U.c.tg_id == A.c.tg_id).where(
        ok_user, A.c.status == "done", A.c.review_asked.is_(False), A.c.status_at <= now - REVIEW_AFTER))
    for a in rows:
        await db.set_app(a["id"], review_asked=True)
        if await _send("order", a["tg_id"], T.A11, ui.kb(
                [ui.btn(T.BTN_REVIEWS, url=cfg.reviews_url)], [ui.btn(T.BTN_CHANNEL, url=cfg.channel_url)])):
            n["review"] += 1
    return n


async def loop(interval: int = 300) -> None:
    while True:
        try:
            n = await tick()
            if any(n.values()):
                log.info("reminders: %s", n)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)
