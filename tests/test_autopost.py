"""Автопост: тексти, JPEG для Viber, базова точка, ідемпотентність по каналах."""
import io
import json

import pytest
from PIL import Image

from app import autopost as ap, db

pytestmark = pytest.mark.asyncio
POST = {"id": 7, "date": 2000.0, "modified": 2000.0, "link": "https://s.test/a/", "title": "ПДВ & ФОП",
        "excerpt": "Перше речення. " * 120, "media": 0}


def test_texts_fit_limits():
    t = ap.tg_text(POST, ap.TG_CAPTION)
    assert len(t) <= ap.TG_CAPTION and t.startswith("<b>ПДВ &amp; ФОП</b>") and 'href="https://s.test/a/"' in t
    v = ap.viber_text(POST, ap.VB_PICTURE_TEXT)
    assert len(v) <= ap.VB_PICTURE_TEXT and v.endswith("https://s.test/a/") and v.count("Перше речення.") > 3


def test_clean_strips_wp_markup():
    assert ap.clean("<p>Текст&nbsp;тут [&hellip;]</p>\n") == "Текст тут…"


def test_to_jpeg_under_limit():
    im = Image.effect_noise((2400, 1260), 120).convert("RGBA")
    buf = io.BytesIO(); im.save(buf, "PNG")
    j = ap.to_jpeg(buf.getvalue())
    assert j[:3] == b"\xff\xd8\xff" and len(j) <= 1_000_000


async def test_baseline_then_publish_once(env, monkeypatch):
    posts = [dict(POST, id=1, date=1000.0, modified=1000.0)]

    async def fake_fetch(s, n=8, extra=""):
        return posts
    monkeypatch.setattr(ap, "fetch_posts", fake_fetch)
    monkeypatch.setattr(ap.cfg, "viber_token", "")
    await ap.tick()                                   # базова точка — нічого не шлемо
    assert not env["order"].sent(chat="@news4uncleevan")
    posts.insert(0, dict(POST, id=2, date=1500.0, modified=1500.0, excerpt="Коротко."))
    await ap.tick()                                   # нова стаття без картинки, ще < 30 хв? date давня → текстом
    await ap.tick()                                   # повтор — не дублюємо
    sent = env["order"].sent(chat="@news4uncleevan")
    assert len(sent) == 1 and "Коротко." in sent[0][1][1]
    assert json.loads(await db.get_setting("ap:2:tg"))["ok"]


async def test_radar_feed_has_own_baseline(env, monkeypatch):
    """Категорія 19 запитується окремо; перший запуск — лише базова точка; нова стаття Радару йде в канал один раз."""
    feeds = {"": [dict(POST, id=1, date=1000.0, modified=1000.0)],
             "&categories=19": [dict(POST, id=50, date=900.0, modified=900.0)]}
    seen = []

    async def fake_fetch(s, n=8, extra=""):
        seen.append(extra)
        return list(feeds[extra])
    monkeypatch.setattr(ap, "fetch_posts", fake_fetch)
    monkeypatch.setattr(ap.cfg, "viber_token", "")
    await ap.tick()
    assert set(seen) == {"", "&categories=19"} and not env["order"].sent(chat="@news4uncleevan")
    feeds["&categories=19"].insert(0, dict(POST, id=51, date=1200.0, modified=1200.0, excerpt="Новий грант."))
    await ap.tick(); await ap.tick()
    sent = env["order"].sent(chat="@news4uncleevan")
    assert len(sent) == 1 and "Новий грант." in sent[0][1][1]
    assert json.loads(await db.get_setting("ap:51:tg"))["ok"]
