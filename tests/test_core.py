import datetime as dt
import time

import pytest
import sqlalchemy as sa
from aiohttp.test_utils import TestClient, TestServer

from app import db, scheduler, tgauth, texts as T
from app.bot_order import segment_ids, stats_text
from app.config import cfg
from app.content import CARDS, FAQ, find_faq
from app.grant import calc_anketa, calc_kalk
from app.web import make_app

pytestmark = pytest.mark.asyncio
ORDER, INFO = "111:AAA-order-test", "222:BBB-info-test"
USER = {"id": 777, "first_name": "Іван", "username": "ivan_test"}

FULL = {
    "path": "start", "org": "Ще не зареєстрований — планую ФОП", "pib": "Іваненко Іван Іванович",
    "phone": "+380 95 722 19 78", "age": "18–25 років", "status": ["Ветеран або учасник бойових дій"],
    "bizname": "СТО Тест", "region": "Харківська область", "city": "Харків",
    "sector": "Ремонт техніки, СТО", "what": "СТО з ремонту ходової", "jobs": "2 робочі місця",
    "bonus": ["Нічого з цього"], "items": [{"name": "Підйомник", "qty": "2", "price": "150000", "cat": "Обладнання (до 100%)"}],
    "monthly": {}, "step": 18,
}


def hdr(user=USER, token=ORDER):
    return {"X-TG-Init": tgauth.sign(user, token)}


async def client():
    c = TestClient(TestServer(make_app()))
    await c.start_server()
    return c


# ---------- розрахунок ----------
async def test_site_examples():
    # приклади зі сторінки сайту
    assert calc_kalk({"path": "start", "on": ["vet"], "sector": "Переробна промисловість, виробництво"})["grant"] == 500_000
    g = calc_kalk({"path": "scale", "on": ["pdv"], "region": "Харківська область"})
    assert (g["pct"], g["raw"], g["grant"]) == (50, 2_250_000, 2_250_000)
    assert calc_kalk({"path": "scale", "on": ["pdv"], "sector": "IT та кібербезпека"})["grant"] == 2_500_000
    assert calc_kalk({"path": "start", "on": []})["grant"] == 300_000


async def test_anketa_calc():
    g = calc_anketa(FULL)
    # вік 5 + ветеран 20 + 2 місця 20 + прифронтовий 30 = 75 %
    assert g["pct"] == 75 and g["raw"] == 525_000 and g["grant"] == 500_000
    assert g["items_total"] == 300_000 and g["jobs"] == 2
    assert calc_anketa({"path": "scale", "tax": "ФОП 3 група — 3% з ПДВ", "jobs": "0 — працюватиму сам"})["pct"] == 20


# ---------- підпис Telegram ----------
async def test_initdata():
    good = tgauth.sign(USER, ORDER)
    assert tgauth.verify(good, ORDER)["id"] == 777
    assert tgauth.verify(good, INFO) is None
    assert tgauth.verify(good.replace("777", "778"), ORDER) is None
    assert tgauth.verify(tgauth.sign(USER, ORDER, int(time.time()) - 5 * 86400), ORDER) is None
    assert tgauth.verify("", ORDER) is None and tgauth.verify("garbage", ORDER) is None


# ---------- API ----------
async def test_api_requires_auth(env):
    c = await client()
    try:
        assert (await c.post("/api/state", json={})).status == 401
        assert (await c.post("/api/submit", json={}, headers={"X-TG-Init": "hash=1"})).status == 401
        assert (await c.get("/health")).status == 200
    finally:
        await c.close()


async def test_pages_inject_bridge(env):
    c = await client()
    try:
        for kind in ("anketa", "kalk"):
            html = await (await c.get(f"/app/{kind}")).text()
            assert "telegram-web-app.js" in html and f'UE_KIND="{kind}"' in html and "/app/tg-bridge.js?v=" in html
            assert '"orderBot": "UncleEvanBot"' in html
        assert (await c.get("/app/tg-bridge.js")).status == 200
    finally:
        await c.close()


async def test_full_funnel(env):
    """Калькулятор у боті Б → анкета в боті А → заявка в групу й клієнту."""
    await db.set_setting("orders_chat_id", "-100500")
    c = await client()
    try:
        # бот Б: рахує
        r = await (await c.post("/api/calc", headers=hdr(token=INFO), json={
            "state": {"path": "start", "region": "Харківська область", "sector": "IT та кібербезпека", "on": ["vet"]},
            "send": True})).json()
        assert r["grant"] == 500_000
        assert env["info"].sent("send_photo", 777), "розрахунок має прийти в чат"
        cap = env["info"].sent("send_photo", 777)[0][2]["caption"]
        assert "500 000" in cap and "7 000" in cap and "без передоплати" in cap
        # бот А: відкриває анкету — розрахунок підставляється
        st = await (await c.post("/api/state", headers=hdr(), json={"kind": "anketa"})).json()
        assert st["progress"] is None and st["calc"]["region"] == "Харківська область"
        assert (await db.get_user(777))["consent_at"]
        # прогрес зберігається і повертається
        await c.post("/api/progress", headers=hdr(), json={"state": {"step": 5, "path": "start", "pib": "Іван"}})
        st = await (await c.post("/api/state", headers=hdr(), json={"kind": "anketa"})).json()
        assert st["progress"]["step"] == 5
        # неповна анкета не приймається
        bad = await (await c.post("/api/submit", headers=hdr(), json={"state": {"path": "start", "pib": "Ів"}, "text": "x"})).json()
        assert bad["ok"] is False
        bad = await (await c.post("/api/submit", headers=hdr(), json={"state": {**FULL, "phone": "+380 95"}, "text": "x"})).json()
        assert bad == {"ok": False, "error": "phone"}
        # заявка
        r = await (await c.post("/api/submit", headers=hdr(), json={"state": dict(FULL), "text": "АНКЕТА…"})).json()
        assert r["ok"] and r["grant"] == 500_000
        app = await db.get_app(r["id"])
        assert app["status"] == "new" and app["group_msg"] and app["group_chat"] == -100500
        assert await db.get_progress(777) is None
        o = env["order"]
        assert o.sent("send_photo", 777) and o.sent("send_document", 777), "клієнт отримує підтвердження і копію"
        card = o.sent("send_message", -100500)[0][1][1]
        assert "Заявка №1" in card and "Іваненко" in card and "500 000" in card and "+380 95 722 19 78" in card
        assert o.sent("send_document", -100500), "файл анкети в групі"
        # подвійне натискання не створює другу заявку
        r2 = await (await c.post("/api/submit", headers=hdr(), json={"state": dict(FULL), "text": "АНКЕТА…"})).json()
        assert r2.get("dup") and r2["id"] == r["id"]
        assert (await db.scalar(sa.select(sa.func.count()).select_from(db.apps))) == 1
        # розрахунок позначено як замовлений → дозагріву не буде
        assert (await db.last_calc(777))["ordered"] is True
    finally:
        await c.close()


async def test_submit_without_group_still_saved(env):
    c = await client()
    try:
        r = await (await c.post("/api/submit", headers=hdr(), json={"state": dict(FULL), "text": "А"})).json()
        assert r["ok"] and (await db.get_app(r["id"]))["group_msg"] is None
    finally:
        await c.close()


# ---------- нагадування ----------
async def _user(tg_id, **kw):
    await db.touch_user(tg_id, f"u{tg_id}", "Тест", bot=kw.pop("bot", "order"))
    if kw:
        await db.set_user(tg_id, **kw)


async def test_anketa_reminders(env):
    await _user(1)
    await _user(2, no_remind=True)
    await _user(3)
    for uid in (1, 2):
        await db.save_progress(uid, {"step": 6, "path": "start"})
    await db.save_progress(3, {"step": 0, "path": None, "_reset": True})
    t0 = db.now()
    assert (await scheduler.tick(t0 + 600, force=True))["anketa"] == 0, "раніше години не нагадуємо"
    assert (await scheduler.tick(t0 + 3700, force=True))["anketa"] == 1
    m = env["order"].sent("send_message", 1)[0]
    assert "кроці 6 із 17" in m[1][1]
    assert (await scheduler.tick(t0 + 4000, force=True))["anketa"] == 0, "друге — лише через добу"
    assert (await scheduler.tick(t0 + 25 * 3600, force=True))["anketa"] == 1
    assert (await scheduler.tick(t0 + 73 * 3600, force=True))["anketa"] == 1
    assert (await scheduler.tick(t0 + 500 * 3600, force=True))["anketa"] == 0, "максимум три"
    assert not env["order"].sent("send_message", 2) and not env["order"].sent("send_message", 3)


async def test_calc_reminders_and_block(env):
    await _user(10, bot="info")
    await _user(11, bot="info")
    await db.save_calc(10, {"path": "start", "on": []}, 300_000, 0)
    await db.save_calc(11, {"path": "start", "on": []}, 300_000, 0)
    env["info"].fail_for.add(11)
    t0 = db.now()
    assert (await scheduler.tick(t0 + 86400, force=True))["calc"] == 0
    assert (await scheduler.tick(t0 + 2 * 86400 + 60, force=True))["calc"] == 1
    assert (await db.get_user(11))["blocked"] is True
    assert "300 000" in env["info"].sent("send_message", 10)[0][1][1]
    assert (await scheduler.tick(t0 + 7 * 86400 + 60, force=True))["calc"] == 1
    assert (await scheduler.tick(t0 + 30 * 86400, force=True))["calc"] == 0


async def test_review_and_unpaid(env):
    await db.set_setting("orders_chat_id", "-100500")
    await _user(20)
    a = await db.add_app(20, dict(FULL), "t", 500_000, "tiktok")
    b = await db.add_app(20, dict(FULL), "t", 500_000, "tiktok")
    await db.set_app(a, status="done", status_at=db.now())
    await db.set_app(b, status="report", status_at=db.now())
    t0 = db.now()
    n = await scheduler.tick(t0 + 25 * 3600, force=True)
    assert n["unpaid"] == 1 and n["review"] == 0
    assert "оплати ще немає" in env["order"].sent("send_message", -100500)[0][1][1]
    n = await scheduler.tick(t0 + 15 * 86400, force=True)
    assert n["review"] == 1 and n["unpaid"] == 0


async def test_quiet_hours():
    tz = cfg.tz
    assert scheduler.quiet(dt.datetime(2026, 10, 9, 23, 0, tzinfo=tz))
    assert scheduler.quiet(dt.datetime(2026, 10, 9, 7, 59, tzinfo=tz))
    assert not scheduler.quiet(dt.datetime(2026, 10, 9, 8, 0, tzinfo=tz))
    assert not scheduler.quiet(dt.datetime(2026, 10, 9, 21, 59, tzinfo=tz))


# ---------- адмін ----------
async def test_stats_segments_delete(env):
    await db.touch_user(30, "a", "A", source="tiktok", bot="info")
    await db.touch_user(31, "b", "B", source="site", bot="order")
    await db.touch_user(30, "a", "A", source="other", bot="info")
    assert (await db.get_user(30))["source"] == "tiktok", "джерело фіксується перше"
    await db.save_calc(30, {"path": "start", "on": []}, 300_000, 0)
    app = await db.add_app(31, dict(FULL), "t", 500_000, "site")
    await db.set_app(app, status="paid")
    s = await stats_text(30)
    assert "Заявок: 1" in s and "Оплачено: 1 · 7 000 грн" in s and "tiktok: 1 → 0" in s and "site: 1 → 1" in s
    assert await segment_ids("calc") == [(30, "info")]
    assert await segment_ids("clients") == [(31, "order")]
    assert len(await segment_ids("all")) == 2
    await db.delete_user_data(31)
    assert await db.get_user(31) is None and await db.get_app(app) is None


# ---------- тексти ----------
async def test_texts_rules():
    import re
    blob = " ".join(v for k, v in vars(T).items() if isinstance(v, str) and not k.startswith("_"))
    blob += " ".join(c["text"] + c["btn"] for c in CARDS.values()) + " ".join(f["q"] + f["a"] for f in FAQ)
    assert not re.search(r"(?<![А-Яа-яЇїІіЄєҐґ’])(ти|тебе|тобі|твій|твоя|твої|твого)(?![А-Яа-яЇїІіЄєҐґ’])", blob, re.I), "лише на «Ви»"
    assert not re.search(r"оплат\w*\s*(—\s*)?після (результату|отримання гранту)", blob, re.I)
    for c in CARDS.values():
        assert len(re.sub(r"<[^>]+>", "", c["text"])) <= 1000
    assert find_faq("а грант треба повертати?")["q"].startswith("Чи потрібно повертати")
    assert find_faq("привіт") is None
