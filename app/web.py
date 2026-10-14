"""Веб-частина: сторінки Mini App (анкета, калькулятор) і API для них."""
import hashlib
import json
import logging
import pathlib
import re

import sqlalchemy as sa
from aiohttp import web

from . import autopost, bot_info, db, notify, tgauth, ui
from .config import cfg
from .grant import calc_anketa, calc_kalk, money

log = logging.getLogger("web")
ROOT = pathlib.Path(__file__).resolve().parent.parent
WEBAPP = ROOT / "webapp"
MAX_BODY = 200_000
_page_cache: dict[str, str] = {}


def _bridge_ver() -> str:
    return hashlib.sha1((WEBAPP / "tg-bridge.js").read_bytes()).hexdigest()[:10]


async def _page(kind: str) -> str:
    price = await ui.price()
    key = f"{kind}:{price}:{ui.BOT_NAMES.get('order')}"
    if key not in _page_cache:
        html = (WEBAPP / f"{kind}.html").read_text("utf-8")
        cfg_js = json.dumps({"orderBot": ui.BOT_NAMES.get("order", "UncleEvanBot"), "price": price}, ensure_ascii=False)
        html = html.replace("</head>", '<script src="https://telegram.org/js/telegram-web-app.js"></script></head>', 1)
        tail = (f'<script>window.UE_KIND="{kind}";window.UE_CFG={cfg_js};</script>'
                f'<script src="/app/tg-bridge.js?v={_bridge_ver()}"></script></body>')
        i = html.rindex("</body>")
        _page_cache[key] = html[:i] + tail + html[i + len("</body>"):]
    return _page_cache[key]


async def page_anketa(req: web.Request) -> web.Response:
    return web.Response(text=await _page("anketa"), content_type="text/html", headers={"Cache-Control": "no-cache"})


async def page_kalk(req: web.Request) -> web.Response:
    return web.Response(text=await _page("kalk"), content_type="text/html", headers={"Cache-Control": "no-cache"})


async def bridge_js(req: web.Request) -> web.Response:
    return web.FileResponse(WEBAPP / "tg-bridge.js", headers={"Cache-Control": "public, max-age=31536000"})


async def raw_form(req: web.Request) -> web.Response:
    """Чиста форма без моста — той самий файл, що стоїть на сайті."""
    name = req.match_info["name"]
    if name not in ("anketa", "kalk"):
        raise web.HTTPNotFound()
    return web.FileResponse(WEBAPP / f"{name}.html", headers={
        "Access-Control-Allow-Origin": cfg.site_url.rstrip("/"), "Cache-Control": "no-cache"})


async def health(req: web.Request) -> web.Response:
    try:
        await db.scalar(sa.select(sa.func.count()).select_from(db.settings))
        return web.json_response({"ok": True, "bots": sorted(notify.BOTS)})
    except Exception as ex:
        return web.json_response({"ok": False, "error": str(ex)}, status=500)


def _auth(req: web.Request) -> tuple[dict, str] | None:
    init = req.headers.get("X-TG-Init", "")
    for which, token in (("order", cfg.order_token), ("info", cfg.info_token)):
        u = tgauth.verify(init, token)
        if u:
            return u, which
    return None


async def _json(req: web.Request) -> dict:
    raw = await req.read()
    if len(raw) > MAX_BODY:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=len(raw))
    try:
        d = json.loads(raw or b"{}")
    except ValueError:
        raise web.HTTPBadRequest(text="bad json")
    if not isinstance(d, dict):
        raise web.HTTPBadRequest(text="bad json")
    return d


def api(handler):
    async def wrap(req: web.Request) -> web.Response:
        a = _auth(req)
        if not a:
            return web.json_response({"ok": False, "error": "auth"}, status=401)
        user, which = a
        body = await _json(req)
        name = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x)
        await db.touch_user(int(user["id"]), user.get("username"), name)
        return web.json_response(await handler(int(user["id"]), which, body))
    return wrap


def _clean_state(s) -> dict:
    if not isinstance(s, dict):
        raise web.HTTPBadRequest(text="state")
    return s


@api
async def api_state(tg_id: int, which: str, body: dict) -> dict:
    kind = body.get("kind")
    calc = await db.last_calc(tg_id)
    if kind == "kalk":
        await db.log("calc_open", tg_id)
        return {"ok": True, "calc": calc["state"] if calc else None}
    u = await db.get_user(tg_id)
    if u and not u["consent_at"]:
        await db.set_user(tg_id, consent_at=db.now())
    await db.log("anketa_open", tg_id)
    p = await db.get_progress(tg_id)
    fresh = calc and not calc["ordered"] and db.now() - calc["updated_at"] < 30 * 86400
    return {"ok": True, "progress": p["state"] if p else None, "calc": calc["state"] if fresh else None}


@api
async def api_progress(tg_id: int, which: str, body: dict) -> dict:
    s = _clean_state(body.get("state"))
    s.pop("_reset", None)
    s.pop("_sent", None)
    await db.save_progress(tg_id, s)
    return {"ok": True}


@api
async def api_calc(tg_id: int, which: str, body: dict) -> dict:
    s = _clean_state(body.get("state"))
    state = {"path": "scale" if s.get("path") == "scale" else "start", "region": str(s.get("region") or "")[:80],
             "sector": str(s.get("sector") or "")[:80], "on": [str(x)[:10] for x in (s.get("on") or [])][:10]}
    g = calc_kalk(state)
    cid = await db.save_calc(tg_id, state, g["grant"], g["pct"])
    if body.get("order"):
        await db.log("calc_order", tg_id)
    if body.get("send") and notify.BOTS.get("info"):
        try:
            await bot_info.send_calc(notify.BOTS["info"], tg_id, {"id": cid, "state": state})
        except Exception as ex:
            log.error("send calc failed: %s", ex)
    return {"ok": True, "id": cid, "grant": g["grant"]}


@api
async def api_submit(tg_id: int, which: str, body: dict) -> dict:
    s = _clean_state(body.get("state"))
    text = str(body.get("text") or "")
    if s.get("path") not in ("start", "scale") or len(str(s.get("pib") or "").strip()) < 3:
        return {"ok": False, "error": "incomplete"}
    if len(re.sub(r"\D", "", str(s.get("phone") or ""))) != 12:
        return {"ok": False, "error": "phone"}
    if not text or len(text) > 60_000:
        return {"ok": False, "error": "text"}
    for k in ("_sent", "_reset", "step"):
        s.pop(k, None)
    last = await db.one(sa.select(db.apps).where(db.apps.c.tg_id == tg_id).order_by(db.apps.c.id.desc()))
    if last and db.now() - last["created_at"] < 60 and last["text"] == text:
        return {"ok": True, "id": last["id"], "dup": True}
    g = calc_anketa(s)
    u = await db.get_user(tg_id)
    app_id = await db.add_app(tg_id, s, text, g["grant"], (u or {}).get("source"))
    await db.log("application", tg_id, (u or {}).get("source"))
    try:
        await notify.deliver(app_id)
    except Exception as ex:  # заявка вже в базі — доставку не втрачаємо мовчки
        log.exception("deliver failed: %s", ex)
        await notify.to_group(f"⚠️ Заявка №{app_id} збережена, але картка не надіслалась. /find {app_id}")
    return {"ok": True, "id": app_id, "grant": g["grant"]}


def contacts_key() -> str:
    """Ключ доступу до CSV контактів — похідна від токена бота кнопок; не зберігається окремо."""
    return hashlib.sha256(("contacts:" + cfg.keys_token).encode()).hexdigest()[:24] if cfg.keys_token else ""


async def contacts_csv(req: web.Request) -> web.Response:
    """База контактів для Google-таблиці (формула IMPORTDATA оновлює її сама)."""
    import csv
    import datetime as dt
    import hmac
    import io
    from . import bot_keys
    key = contacts_key()
    if not key or not hmac.compare_digest(req.query.get("key", ""), key):
        raise web.HTTPForbidden()
    rows = await db.all_(sa.select(bot_keys.contacts).order_by(bot_keys.contacts.c.last_seen.desc()))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Telegram ID", "Username", "Ім'я", "Телефон", "Email", "Перший контакт", "Останній контакт", "Остання кнопка"])
    f = lambda t: dt.datetime.fromtimestamp(t, cfg.tz).strftime("%d.%m.%Y %H:%M") if t else ""

    def ph(p):  # 0671234567 → 380671234567 (таблиця читає як число; формат колонки додає «+»)
        d = re.sub(r"\D", "", p or "")
        return "38" + d if len(d) == 10 and d.startswith("0") else d

    for x in rows:
        w.writerow([x["tg_id"], ("@" + x["username"]) if x["username"] else "", x["name"] or "", ph(x["phone"]),
                    x["email"] or "", f(x["first_seen"]), f(x["last_seen"]), x["last_button"] or ""])
    return web.Response(text=buf.getvalue(), content_type="text/csv", charset="utf-8",
                        headers={"Cache-Control": "no-store"})


async def viber_hook(req: web.Request) -> web.Response:
    """Колбек Viber Channels API: потрібна лише відповідь 200."""
    return web.json_response({"status": 0})


async def media_jpeg(req: web.Request) -> web.Response:
    try:
        data = await autopost.jpeg_for(int(req.match_info["post_id"]))
    except Exception as ex:
        log.warning("media jpeg: %s", ex)
        data = None
    if not data:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


def make_app() -> web.Application:
    app = web.Application(client_max_size=MAX_BODY * 2)
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/app/anketa", page_anketa)
    app.router.add_get("/app/kalk", page_kalk)
    app.router.add_get("/app/tg-bridge.js", bridge_js)
    app.router.add_get("/forms/{name}.html", raw_form)
    app.router.add_static("/icons/", WEBAPP / "icons", show_index=False)
    app.router.add_post("/api/state", api_state)
    app.router.add_post("/api/progress", api_progress)
    app.router.add_post("/api/calc", api_calc)
    app.router.add_post("/api/submit", api_submit)
    app.router.add_post("/viber", viber_hook)
    app.router.add_get("/api/contacts.csv", contacts_csv)
    app.router.add_get("/media/{post_id:\\d+}.jpeg", media_jpeg)
    return app
