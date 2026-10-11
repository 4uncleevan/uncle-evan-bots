"""Автопост: нова стаття на сайті → Telegram-канал і Viber-канал.

Сервер сам опитує публічний REST сайту (жодних секретів на боці WordPress).
Текст поста = заголовок + поле ue_channel (якщо зареєстроване на сайті й заповнене) або «Уривок» + посилання;
картинка = «Зображення запису».
Кожен канал відмічається окремо, тож повтор не дублює пост, а збій одного каналу не блокує інший.
"""
import asyncio
import datetime as dt
import html
import io
import json
import logging
import re

import aiohttp
from aiogram.types import BufferedInputFile, LinkPreviewOptions

from . import db, notify
from .config import cfg

log = logging.getLogger("autopost")
POLL = 120                # с між перевірками сайту
SETTLE = 180              # с після останньої правки статті, перш ніж поширювати
NO_IMAGE_WAIT = 1800      # без картинки чекаємо до 30 хв, потім — текстом
MAX_TRIES = 3
TG_CAPTION, VB_PICTURE_TEXT, VB_TEXT = 1024, 768, 7000
VIBER_API = "https://chatapi.viber.com/pa"
_jpeg_cache: dict[int, bytes] = {}


# ---------- текст ----------
def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s*\[(?:…|\.\.\.)\]\s*$", "…", s.strip())
    return re.sub(r"[ \t ]+", " ", re.sub(r"\s*\n\s*", "\n", s)).strip()


def cut(s: str, limit: int) -> str:
    """Обрізає по межі речення/слова, а не посеред слова."""
    if len(s) <= limit:
        return s
    part = s[: limit - 1]
    for sep in (". ", "! ", "? ", "\n"):
        i = part.rfind(sep)
        if i > limit * 0.5:
            return part[: i + 1].rstrip()
    return part.rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"


def tg_text(p: dict, limit: int) -> str:
    title, link = html.escape(p["title"]), html.escape(p["link"], quote=True)
    head, tail = f"<b>{title}</b>\n\n", f'\n\n👉 <a href="{link}">Читати на сайті</a>'
    body = html.escape(cut(p["excerpt"], max(0, limit - len(head) - len(tail) - 40)))
    return head + body + tail if body else head.strip() + tail


def viber_text(p: dict, limit: int) -> str:
    tail = f"\n\n👉 {p['link']}"
    head = p["title"] + "\n\n"
    body = cut(p["excerpt"], max(0, limit - len(head) - len(tail)))
    return (head + body + tail) if body else (p["title"] + tail)


# ---------- сайт ----------
def _api(path: str) -> str:
    return cfg.site_url.rstrip("/") + "/wp-json/wp/v2/" + path


async def _json(s: aiohttp.ClientSession, url: str):
    async with s.get(url, headers={"Cache-Control": "no-cache"}) as r:
        r.raise_for_status()
        return await r.json(content_type=None)


def _ts(v: str) -> float:
    return dt.datetime.fromisoformat(v).replace(tzinfo=dt.timezone.utc).timestamp()


async def fetch_posts(s: aiohttp.ClientSession, n: int = 8) -> list[dict]:
    rows = await _json(s, _api(f"posts?per_page={n}&orderby=date&order=desc"
                               "&_fields=id,date_gmt,modified_gmt,link,title,excerpt,featured_media,meta"))
    return [{"id": r["id"], "date": _ts(r["date_gmt"]), "modified": _ts(r["modified_gmt"]),
             "link": r["link"], "title": clean(r["title"]["rendered"]),
             "excerpt": clean(((r.get("meta") or {}).get("ue_channel") if isinstance(r.get("meta"), dict) else "")
                              or r["excerpt"]["rendered"]), "media": r.get("featured_media") or 0} for r in rows]


async def fetch_post(s: aiohttp.ClientSession, post_id: int) -> dict | None:
    for p in await fetch_posts(s, 20):
        if p["id"] == post_id:
            return p
    return None


async def image_url(s: aiohttp.ClientSession, media_id: int) -> str:
    if not media_id:
        return ""
    try:
        return (await _json(s, _api(f"media/{media_id}?_fields=source_url"))).get("source_url", "")
    except Exception as ex:
        log.warning("media %s: %s", media_id, ex)
        return ""


async def download(s: aiohttp.ClientSession, url: str) -> bytes:
    async with s.get(url) as r:
        r.raise_for_status()
        return await r.read()


def to_jpeg(raw: bytes, max_bytes: int = 1_000_000) -> bytes:
    """Viber приймає лише JPEG ≤ 1 МБ."""
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    if im.width > 1600:
        im = im.resize((1600, round(im.height * 1600 / im.width)), Image.LANCZOS)
    for q in (88, 80, 72, 64, 55):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= max_bytes:
            return buf.getvalue()
        if q == 64:
            im = im.resize((im.width * 3 // 4, im.height * 3 // 4), Image.LANCZOS)
    return buf.getvalue()


async def jpeg_for(post_id: int) -> bytes | None:
    """Для /media/{id}.jpeg: з кешу або заново з сайту (після перезапуску сервера)."""
    if post_id in _jpeg_cache:
        return _jpeg_cache[post_id]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        p = await fetch_post(s, post_id)
        url = p and await image_url(s, p["media"])
        if not url:
            return None
        _jpeg_cache[post_id] = await asyncio.to_thread(to_jpeg, await download(s, url))
    return _jpeg_cache[post_id]


# ---------- Telegram ----------
async def post_telegram(p: dict, img: bytes | None, img_url: str) -> str:
    bot = notify.BOTS.get("order") or notify.BOTS.get("info")
    if not bot:
        raise RuntimeError("бот не запущено")
    chat = cfg.tg_channel
    if img and len(tg_text(p, 10_000)) <= TG_CAPTION:
        ext = img_url.rsplit(".", 1)[-1].split("?")[0][:4] or "jpg"
        m = await bot.send_photo(chat, BufferedInputFile(img, f"cover.{ext}"), caption=tg_text(p, TG_CAPTION))
    elif img_url:  # довгий текст: картинка великим прев'ю над текстом
        m = await bot.send_message(chat, tg_text(p, 4096), link_preview_options=LinkPreviewOptions(
            url=img_url, prefer_large_media=True, show_above_text=True))
    else:
        m = await bot.send_message(chat, tg_text(p, 4096))
    return f"{cfg.channel_url.rstrip('/')}/{m.message_id}"


# ---------- Viber ----------
async def viber(s: aiohttp.ClientSession, method: str, payload: dict) -> dict:
    payload = {"auth_token": cfg.viber_token, **payload}
    async with s.post(f"{VIBER_API}/{method}", json=payload) as r:
        data = await r.json(content_type=None)
    if data.get("status") != 0:
        raise RuntimeError(f"Viber {method}: {data.get('status')} {data.get('status_message')}")
    return data


async def viber_setup(s: aiohttp.ClientSession) -> str:
    """Вебхук (обов'язковий для Channels API) + id суперадміна, від імені якого постимо."""
    hook = f"{cfg.public_url}/viber"
    if cfg.public_url and await db.get_setting("viber_hook") != hook:
        await viber(s, "set_webhook", {"url": hook})
        await db.set_setting("viber_hook", hook)
    sender = cfg.viber_from or await db.get_setting("viber_from")
    if not sender:
        info = await viber(s, "get_account_info", {})
        admins = [m for m in info.get("members", []) if m.get("role") == "superadmin"]
        if not admins:
            raise RuntimeError("Viber: у каналі не знайдено суперадміна")
        sender = admins[0]["id"]
        await db.set_setting("viber_from", sender)
    return sender


async def post_viber(s: aiohttp.ClientSession, p: dict, has_img: bool) -> str:
    sender = await viber_setup(s)
    full = viber_text(p, VB_TEXT)
    if has_img and cfg.public_url:
        short = viber_text(p, VB_PICTURE_TEXT)
        await viber(s, "post", {"from": sender, "type": "picture", "text": short,
                                "media": f"{cfg.public_url}/media/{p['id']}.jpeg"})
        if short != full:  # повний текст не вліз у підпис — окремим повідомленням
            await viber(s, "post", {"from": sender, "type": "text", "text": full})
    else:
        await viber(s, "post", {"from": sender, "type": "text", "text": full})
    return "ok"


# ---------- оркестрація ----------
def _channels() -> list[str]:
    return ["tg"] + (["viber"] if cfg.viber_token else [])


async def publish(p: dict, force: bool = False) -> dict[str, str]:
    """Поширює одну статтю; повертає {канал: результат}. Уже відмічені канали пропускає."""
    res: dict[str, str] = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
        url = await image_url(s, p["media"])
        img = None
        if url:
            try:
                img = await download(s, url)
                _jpeg_cache[p["id"]] = await asyncio.to_thread(to_jpeg, img)
            except Exception as ex:
                log.warning("картинка %s: %s", url, ex)
                img, url = None, ""
        for ch in _channels():
            key = f"ap:{p['id']}:{ch}"
            state = json.loads(await db.get_setting(key, "{}"))
            if state.get("ok") and not force:
                continue
            if state.get("tries", 0) >= MAX_TRIES and not force:
                continue
            try:
                out = await (post_telegram(p, img, url) if ch == "tg" else post_viber(s, p, bool(img)))
                await db.set_setting(key, json.dumps({"ok": out}))
                res[ch] = "✅"
            except Exception as ex:
                log.exception("%s post %s: %s", ch, p["id"], ex)
                await db.set_setting(key, json.dumps({"tries": state.get("tries", 0) + 1, "err": str(ex)[:300]}))
                res[ch] = f"❌ {str(ex)[:200]}"
    return res


def ready(p: dict, now: float) -> bool:
    if now - p["modified"] < SETTLE:
        return False
    return bool(p["media"]) or now - p["date"] > NO_IMAGE_WAIT


async def tick() -> None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        posts = await fetch_posts(s)
    if not posts:
        return
    since = await db.get_setting("ap_since")
    if not since:  # перший запуск: старі статті не розсилаємо
        await db.set_setting("ap_since", str(max(p["date"] for p in posts)))
        log.info("автопост: базова точка %s", posts[0]["title"])
        return
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    for p in sorted(posts, key=lambda x: x["date"]):
        if p["date"] <= float(since) or not ready(p, now):
            continue
        res = await publish(p)
        if res:
            names = {"tg": "Telegram", "viber": "Viber"}
            line = " · ".join(f"{names[k]} {v}" for k, v in res.items())
            await notify.to_group(f"📣 <b>{html.escape(p['title'])}</b>\n{line}\n"
                                  f"WhatsApp і Facebook — окремо.", disable_web_page_preview=True)


async def loop() -> None:
    if not cfg.autopost:
        log.info("автопост вимкнено (AUTOPOST=0)")
        return
    await asyncio.sleep(20)
    if cfg.viber_token:
        try:  # перевірка токена одразу після запуску: вебхук + суперадмін
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                sender = await viber_setup(s)
            if await db.get_setting("viber_ok") != sender:
                await db.set_setting("viber_ok", sender)
                await notify.to_group("✅ Viber-канал підключено до автопосту.")
            log.info("Viber готовий")
        except Exception as ex:
            log.warning("Viber: %s", ex)
            await notify.to_group(f"⚠️ Viber-канал не підключився: {html.escape(str(ex)[:300])}")
    while True:
        try:
            await tick()
        except Exception as ex:
            log.warning("автопост: %s", ex)
        await asyncio.sleep(POLL)
