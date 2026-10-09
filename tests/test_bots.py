"""Обробники ботів через справжній диспетчер aiogram із підміненим транспортом."""
import datetime as dt

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, TelegramMethod
from aiogram.types import Message, Update

from app import bot_info, bot_order, db, notify
from test_core import FULL

pytestmark = pytest.mark.asyncio
ADMIN = {"id": 500, "is_bot": False, "first_name": "Uncle", "username": "diadyaevan"}
CLIENT = {"id": 600, "is_bot": False, "first_name": "Іван", "username": "ivan_test"}
GROUP = {"id": -100500, "type": "supergroup", "title": "Заявки"}


class Session(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list[TelegramMethod] = []
        self._id = 1000

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        self._id += 1
        d = method.model_dump(exclude_none=True, exclude={"media", "photo", "document", "video", "reply_markup"})
        chat_id = d.get("chat_id", 0)
        base = {"message_id": d.get("message_id") or self._id, "date": dt.datetime.now(),
                "chat": {"id": chat_id, "type": "private" if chat_id > 0 else "supergroup"}}
        name = type(method).__name__
        if name in ("SendPhoto", "EditMessageMedia"):
            base["photo"] = [{"file_id": f"f{self._id}", "file_unique_id": "u", "width": 1280, "height": 640}]
            base["caption"] = getattr(method, "caption", None) or getattr(getattr(method, "media", None), "caption", None)
        else:
            base["text"] = d.get("text", "")
        return Message.model_validate(base).as_(bot)

    async def stream_content(self, *a, **kw):
        yield b""

    async def close(self):
        pass

    def names(self):
        return [type(m).__name__ for m in self.sent]

    def last(self, name):
        return [m for m in self.sent if type(m).__name__ == name][-1]


def make(router, token):
    s = Session()
    bot = Bot(token, session=s, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    # роутер модуля один на процес: відчіпляємо від попереднього диспетчера
    router._parent_router = None
    dp.include_router(router)
    return bot, dp, s


_uid = 0


def msg(text, user=CLIENT, chat=None):
    global _uid
    _uid += 1
    chat = chat or {"id": user["id"], "type": "private"}
    ent = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else None
    return Update.model_validate({"update_id": _uid, "message": {
        "message_id": _uid, "date": 1, "chat": chat, "from": user, "text": text, **({"entities": ent} if ent else {})}})


def cb(data, user, message):
    global _uid
    _uid += 1
    return Update.model_validate({"update_id": _uid, "callback_query": {
        "id": str(_uid), "from": user, "chat_instance": "x", "data": data, "message": message}})


def texts_of(m):
    return getattr(m, "caption", None) or getattr(m, "text", None) or getattr(getattr(m, "media", None), "caption", "")


def buttons(m):
    return [b for row in m.reply_markup.inline_keyboard for b in row]


async def test_order_bot_start_resume_restart(env):
    bot, dp, s = make(bot_order.r, "111:AAA-order-test")
    await dp.feed_update(bot, msg("/start tiktok"))
    m = s.last("SendPhoto")
    t = texts_of(m)
    assert "7 000 грн" in t and "без передоплати" in t and "не є сервісом Дії" in t and "Натискаючи «Почати анкету»" in t
    b = buttons(m)
    assert b[0].web_app.url == "https://example.test/app/anketa" and "Почати анкету" in b[0].text
    u = await db.get_user(600)
    assert u["source"] == "tiktok" and u["order_bot"]
    # є збережений прогрес → екран повернення
    await db.save_progress(600, {"step": 7, "path": "scale"})
    await dp.feed_update(bot, msg("/start"))
    m = s.last("SendPhoto")
    assert "кроці 7 із 17" in texts_of(m) and "Продовжити" in buttons(m)[0].text
    # «Почати заново» редагує той самий екран
    photo_msg = {"message_id": 55, "date": 1, "chat": {"id": 600, "type": "private"},
                 "photo": [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]}
    await dp.feed_update(bot, cb("restart", CLIENT, photo_msg))
    m = s.last("EditMessageMedia")
    assert m.message_id == 55 and "Почати анкету" in buttons(m)[0].text
    assert (await db.get_progress(600))["state"].get("_reset") is True


async def test_order_bot_prefill_from_calc(env):
    bot, dp, s = make(bot_order.r, "111:AAA-order-test")
    cid = await db.save_calc(600, {"path": "start", "on": ["vet"]}, 360_000, 20)
    other = await db.save_calc(601, {"path": "start", "on": []}, 300_000, 0)
    await dp.feed_update(bot, msg(f"/start c{cid}"))
    assert "360 000 грн) уже підставлено" in texts_of(s.last("SendPhoto"))
    assert (await db.get_user(600))["source"] == "botB"
    await dp.feed_update(bot, msg(f"/start c{other}"))
    assert "уже підставлено" not in texts_of(s.last("SendPhoto")), "чужий розрахунок не підставляється"


async def test_group_statuses_and_admin(env):
    bot, dp, s = make(bot_order.r, "111:AAA-order-test")
    notify.BOTS["order"] = bot
    # клієнт не може призначити групу
    await dp.feed_update(bot, msg("/setgroup", CLIENT, GROUP))
    assert await db.get_setting("orders_chat_id") == ""
    await dp.feed_update(bot, msg("/setgroup", ADMIN, GROUP))
    assert await db.get_setting("orders_chat_id") == "-100500"
    await db.touch_user(600, "ivan_test", "Іван", bot="order")
    app_id = await db.add_app(600, dict(FULL), "АНКЕТА", 500_000, "tiktok")
    await notify.deliver(app_id)
    card = [m for m in s.sent if type(m).__name__ == "SendMessage" and m.chat_id == -100500][-1]
    assert "Заявка №1" in card.text and buttons(card)[0].url == "https://t.me/ivan_test"
    group_msg = {"message_id": (await db.get_app(app_id))["group_msg"], "date": 1, "chat": GROUP, "text": "x"}
    # не адмін
    n = len(s.sent)
    await dp.feed_update(bot, cb(f"st:{app_id}:work", CLIENT, group_msg))
    assert (await db.get_app(app_id))["status"] == "new" and s.last("AnswerCallbackQuery").show_alert
    # адмін: «В роботі» → клієнт отримує сповіщення з датою
    await dp.feed_update(bot, cb(f"st:{app_id}:work", ADMIN, group_msg))
    assert (await db.get_app(app_id))["status"] == "work"
    to_client = [m for m in s.sent[n:] if type(m).__name__ == "SendMessage" and m.chat_id == 600]
    assert len(to_client) == 1 and "Узяв Ваш бізнес-план у роботу" in to_client[0].text
    assert "В роботі" in s.last("EditMessageText").text
    # «Звіт надіслано», «Оплачено» — клієнту бот не пише
    n = len(s.sent)
    for st in ("report", "paid", "done"):
        await dp.feed_update(bot, cb(f"st:{app_id}:{st}", ADMIN, group_msg))
    assert not [m for m in s.sent[n:] if type(m).__name__ in ("SendMessage", "SendPhoto") and m.chat_id == 600]
    assert (await db.get_app(app_id))["status"] == "done"
    # адмін-команди
    await dp.feed_update(bot, msg("/price 8000", ADMIN))
    assert await db.get_setting("price") == "8000"
    await dp.feed_update(bot, msg("/price 9000", CLIENT))
    assert await db.get_setting("price") == "8000"
    await dp.feed_update(bot, msg("/stats", ADMIN))
    assert "Оплачено: 1 · 8 000 грн" in s.last("SendMessage").text
    await dp.feed_update(bot, msg("/find Іваненко", ADMIN))
    assert "№1" in s.last("SendMessage").text
    await dp.feed_update(bot, msg("/pause", ADMIN))
    assert await db.get_setting("paused") == "1"


async def test_client_free_text_goes_to_owner(env):
    bot, dp, s = make(bot_order.r, "111:AAA-order-test")
    await db.set_setting("orders_chat_id", "-100500")
    await dp.feed_update(bot, msg("А можна швидше, ніж за 2 дні?"))
    names = s.names()
    assert "CopyMessage" in names
    assert "передаю Uncle Evan" in [m for m in s.sent if type(m).__name__ == "SendMessage" and m.chat_id == 600][0].text
    await dp.feed_update(bot, msg("Потрібен бізнес-план під кредит 5-7-9"))
    assert "лише з грантом «Власна справа 2.0»" in [m for m in s.sent if type(m).__name__ == "SendMessage" and m.chat_id == 600][-1].text


async def test_info_bot_menu_cards_faq(env):
    bot, dp, s = make(bot_info.r, "222:BBB-info-test")
    await dp.feed_update(bot, msg("/start site"))
    m = s.last("SendPhoto")
    b = buttons(m)
    assert "не офіційний сервіс Дії" in texts_of(m)
    assert b[0].web_app.url == "https://example.test/app/kalk"
    urls = [x.url for x in b if x.url]
    assert "https://t.me/UncleEvanBot?start=botB" in urls and "https://t.me/granrtydia" in urls \
        and "https://uncleevan.watt-coin.org/" in urls, "три виходи: замовлення, сайт, канал"
    assert (await db.get_user(600))["source"] == "site"
    photo_msg = {"message_id": 77, "date": 1, "chat": {"id": 600, "type": "private"},
                 "photo": [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]}
    for key in ("about", "tracks", "bonus", "spend", "duties", "apply", "refuse", "service"):
        await dp.feed_update(bot, cb(f"card:{key}", CLIENT, photo_msg))
        m = s.last("EditMessageMedia")
        assert m.message_id == 77 and len(m.media.caption) <= 1024 and "{price}" not in m.media.caption
    assert "7 000 грн" in m.media.caption
    await dp.feed_update(bot, cb("faq", CLIENT, photo_msg))
    assert len(buttons(s.last("EditMessageMedia"))) >= 10
    await dp.feed_update(bot, cb("faq:0", CLIENT, photo_msg))
    assert "безповоротний" in s.last("EditMessageMedia").media.caption
    await dp.feed_update(bot, msg("скільки коштує бізнес-план?"))
    assert "7 000 грн" in s.last("SendMessage").text
    await dp.feed_update(bot, msg("абракадабра"))
    assert "Напишіть мені особисто" in s.last("SendMessage").text
    await dp.feed_update(bot, cb("menu", CLIENT, photo_msg))
    assert "Із чого почнемо" in s.last("EditMessageMedia").media.caption


async def test_group_set_automatically_when_admin_adds_bot(env):
    bot, dp, s = make(bot_order.r, "111:AAA-order-test")
    me = {"id": 1, "is_bot": True, "first_name": "Bot"}

    def added(user, status="member"):
        global _uid
        _uid += 1
        return Update.model_validate({"update_id": _uid, "my_chat_member": {
            "chat": GROUP, "from": user, "date": 1,
            "old_chat_member": {"user": me, "status": "left"},
            "new_chat_member": {"user": me, "status": status}}})

    await dp.feed_update(bot, added(CLIENT))
    assert await db.get_setting("orders_chat_id") == "", "сторонній не може призначити групу"
    await dp.feed_update(bot, added(ADMIN, "administrator"))
    assert await db.get_setting("orders_chat_id") == "-100500"
    assert "Заявки приходитимуть" in s.last("SendMessage").text
    assert "my_chat_member" in dp.resolve_used_update_types()
