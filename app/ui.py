"""Спільні елементи інтерфейсу: екран з банером, кнопки, перевірка адміністратора."""
import logging
import pathlib

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message,
                           User, WebAppInfo)

from . import db, texts as T
from .config import cfg
from .grant import money

log = logging.getLogger("ui")
ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets"
_file_ids: dict[tuple[int, str], str] = {}
BOT_NAMES: dict[str, str] = {}  # 'order' / 'info' → username без @

ANKETA_SITE = "https://uncleevan.watt-coin.org/anketa-vlasna-sprava/"
KALK_SITE = "https://uncleevan.watt-coin.org/kalkulyator-grantu/"


def is_admin(u: User | None) -> bool:
    return bool(u and u.username and u.username.lower() in cfg.admins)


async def price() -> str:
    return money(int(await db.get_setting("price", str(cfg.price)) or cfg.price))


def btn(text: str, *, url: str | None = None, cb: str | None = None, app: str | None = None) -> InlineKeyboardButton:
    if app:
        if cfg.public_url:
            return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=f"{cfg.public_url}/app/{app}"))
        return InlineKeyboardButton(text=text, url=ANKETA_SITE if app == "anketa" else KALK_SITE)
    if url:
        return InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=cb)


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def order_link(payload: str = "botB") -> str:
    return f"https://t.me/{BOT_NAMES.get('order', 'UncleEvanBot')}?start={payload}"


def info_link(payload: str = "botA") -> str:
    return f"https://t.me/{BOT_NAMES.get('info', 'VlasnaSpravaBot')}?start={payload}"


async def show(bot: Bot, chat_id: int, banner: str, text: str, markup: InlineKeyboardMarkup | None = None,
               edit: Message | None = None) -> Message | None:
    """Показує екран: банер + текст + кнопки. Якщо edit — замінює попередній екран на місці."""
    key = (bot.id, banner)
    path = ASSETS / f"{banner}.jpg"
    media = _file_ids.get(key) or FSInputFile(path)
    if edit is not None and edit.photo:
        try:
            m = await bot.edit_message_media(
                chat_id=chat_id, message_id=edit.message_id,
                media=InputMediaPhoto(media=media, caption=text, parse_mode="HTML"), reply_markup=markup)
            if isinstance(m, Message) and m.photo:
                _file_ids[key] = m.photo[-1].file_id
            return m if isinstance(m, Message) else edit
        except TelegramBadRequest as ex:
            if "not modified" in str(ex).lower():
                return edit
            log.info("edit failed, sending new: %s", ex)
    m = await bot.send_photo(chat_id, media, caption=text, reply_markup=markup)
    if m.photo:
        _file_ids[key] = m.photo[-1].file_id
    return m


def user_name(u: User) -> str:
    return " ".join(x for x in [u.first_name, u.last_name] if x)[:200]
