"""Бот Б — «Власна справа»: довідник, калькулятор, три виходи (замовлення, сайт, канал)."""
import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from . import db, texts as T, ui
from .config import cfg
from .content import CARDS, MENU_ORDER, find_faq
from .grant import calc_kalk, money

log = logging.getLogger("info")
r = Router()
PRIVATE = F.chat.type == "private"
SOURCE_OK = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


async def menu_kb():
    rows = [[ui.btn(T.BTN_CALC, app="kalk")]]
    rows += [[ui.btn(CARDS[k]["btn"], cb=f"card:{k}")] for k in MENU_ORDER]
    rows.append([ui.btn("❓ Часті запитання", cb="faq")])
    if await db.get_setting("video_url", cfg.video_url):
        rows.append([ui.btn(T.BTN_VIDEO, cb="video")])
    rows.append([ui.btn(T.BTN_ORDER, url=ui.order_link("botB"))])
    rows.append([ui.btn(T.BTN_SITE, url=cfg.site_url), ui.btn(T.BTN_CHANNEL, url=cfg.channel_url)])
    return ui.kb(*rows)


async def show_menu(bot: Bot, chat_id: int, edit: Message | None = None):
    await ui.show(bot, chat_id, "menu", T.B1, await menu_kb(), edit)


def calc_text(state: dict, price: str) -> str:
    g = calc_kalk(state)
    bon = "\n".join(f"  +{b['p']} % — {b['l']}" for b in g["bonuses"]) or "  бонусів не позначено"
    cap = f" → стеля {money(g['cap'])} грн" if g["raw"] > g["cap"] else ""
    jobs = "" if g["start"] else "\nДля масштабування обов’язкове щонайменше 1 нове робоче місце."
    return T.B3.format(grant=money(g["grant"]), path="Старт" if g["start"] else "Масштабування",
                       base=money(g["base"]), pct=f"+{g['pct']}", cap=cap, bonuses=bon, jobs=jobs,
                       date=cfg.terms_date) + "\n\n" + T.B4.format(price=price)


async def send_calc(bot: Bot, tg_id: int, calc: dict):
    await ui.show(bot, tg_id, "calc", calc_text(calc["state"], await ui.price()), ui.kb(
        [ui.btn(T.BTN_ORDER_SUM, url=ui.order_link(f"c{calc['id']}"))],
        [ui.btn(T.BTN_RECALC, app="kalk"), ui.btn(T.BTN_MENU, cb="menu")],
        [ui.btn(T.BTN_CHANNEL, url=cfg.channel_url)]))


@r.message(CommandStart(), PRIVATE)
async def cmd_start(m: Message, command: CommandObject, bot: Bot):
    arg = (command.args or "").strip()
    source = arg if arg and SOURCE_OK.match(arg) else None
    await db.touch_user(m.from_user.id, m.from_user.username, ui.user_name(m.from_user), source=source, bot="info")
    await db.log("info_start", m.from_user.id, source)
    await show_menu(bot, m.chat.id)


@r.message(Command("calc"), PRIVATE)
async def cmd_calc(m: Message):
    await m.answer("Калькулятор суми гранту:", reply_markup=ui.kb([ui.btn(T.BTN_CALC, app="kalk")]))


@r.message(Command("order"), PRIVATE)
async def cmd_order(m: Message):
    await m.answer(T.B4.format(price=await ui.price()), reply_markup=ui.kb([ui.btn(T.BTN_ORDER, url=ui.order_link("botB"))]))


@r.message(Command("help"), PRIVATE)
async def cmd_help(m: Message):
    await m.answer(T.B_HELP)


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


@r.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery, bot: Bot):
    await c.answer()
    await show_menu(bot, c.message.chat.id, edit=c.message)


@r.callback_query(F.data.startswith("card:"))
async def cb_card(c: CallbackQuery, bot: Bot):
    key = c.data.split(":", 1)[1]
    card = CARDS.get(key)
    await c.answer()
    if not card:
        return
    await db.log("card_" + key, c.from_user.id)
    text = card["text"].replace("{price}", await ui.price())
    last = [ui.btn(T.BTN_ORDER, url=ui.order_link("botB"))] if key == "service" else [ui.btn(T.BTN_CALC, app="kalk")]
    await ui.show(bot, c.message.chat.id, card["banner"], text, ui.kb(
        [ui.btn(T.BTN_MORE, url=card["url"])], last, [ui.btn(T.BTN_BACK, cb="menu")]), edit=c.message)


@r.callback_query(F.data == "faq")
async def cb_faq(c: CallbackQuery, bot: Bot):
    from .content import FAQ
    await c.answer()
    rows = [[ui.btn(f["q"], cb=f"faq:{i}")] for i, f in enumerate(FAQ)]
    rows.append([ui.btn(T.BTN_BACK, cb="menu")])
    await ui.show(bot, c.message.chat.id, "about", "<b>Часті запитання про грант «Власна справа 2.0»</b>\n\nОберіть питання або напишіть своє повідомленням.",
                  ui.kb(*rows), edit=c.message)


@r.callback_query(F.data.startswith("faq:"))
async def cb_faq_item(c: CallbackQuery, bot: Bot):
    from .content import FAQ
    await c.answer()
    i = int(c.data.split(":")[1])
    if not 0 <= i < len(FAQ):
        return
    f = FAQ[i]
    text = f"<b>{f['q']}</b>\n\n{f['a'].replace('{price}', await ui.price())}"
    await ui.show(bot, c.message.chat.id, "about", text, ui.kb(
        [ui.btn(T.BTN_CALC, app="kalk")], [ui.btn("← До питань", cb="faq"), ui.btn(T.BTN_MENU, cb="menu")]), edit=c.message)


@r.callback_query(F.data == "video")
async def cb_video(c: CallbackQuery, bot: Bot):
    await c.answer()
    url = await db.get_setting("video_url", cfg.video_url)
    if not url:
        return
    try:
        await bot.send_video(c.message.chat.id, url, caption="Що потрібно знати про грант «Власна справа 2.0»",
                             reply_markup=ui.kb([ui.btn(T.BTN_CALC, app="kalk")], [ui.btn(T.BTN_ORDER, url=ui.order_link("botB"))]))
    except TelegramAPIError:
        await c.message.answer(f"Відео: {url}")


@r.callback_query(F.data == "noremind")
async def cb_noremind(c: CallbackQuery):
    await db.set_user(c.from_user.id, no_remind=True)
    await c.answer()
    await c.message.answer(T.NO_REMIND_OK)


@r.message(PRIVATE, F.text)
async def any_text(m: Message):
    if m.text.startswith("/"):
        return
    await db.touch_user(m.from_user.id, m.from_user.username, ui.user_name(m.from_user), bot="info")
    f = find_faq(m.text)
    if f:
        await m.answer(f"<b>{f['q']}</b>\n\n{f['a'].replace('{price}', await ui.price())}", reply_markup=ui.kb(
            [ui.btn(T.BTN_CALC, app="kalk")], [ui.btn(T.BTN_ORDER, url=ui.order_link("botB"))]),
            disable_web_page_preview=True)
    else:
        await m.answer(T.B8, reply_markup=ui.kb([ui.btn(T.BTN_OWNER, url=cfg.owner_url)], [ui.btn(T.BTN_MENU, cb="menu")]))
