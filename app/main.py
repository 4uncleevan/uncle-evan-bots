"""Запуск: база, обидва боти (polling), веб-сервер Mini App, нагадування — один процес."""
import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from aiohttp import web

from . import bot_info, bot_order, db, notify, scheduler, ui
from .config import cfg
from .web import make_app

log = logging.getLogger("main")


async def setup_bot(bot: Bot, which: str) -> None:
    me = await bot.get_me()
    ui.BOT_NAMES[which] = me.username
    notify.BOTS[which] = bot
    if which == "order":
        cmds = [BotCommand(command="start", description="Анкета для бізнес-плану"),
                BotCommand(command="help", description="Довідка"),
                BotCommand(command="delete", description="Видалити мої дані")]
        page, label = "anketa", "Анкета"
    else:
        cmds = [BotCommand(command="start", description="Меню"),
                BotCommand(command="calc", description="Калькулятор гранту"),
                BotCommand(command="order", description="Замовити бізнес-план"),
                BotCommand(command="help", description="Довідка"),
                BotCommand(command="delete", description="Видалити мої дані")]
        page, label = "kalk", "Калькулятор"
    await bot.set_my_commands(cmds)
    if cfg.public_url:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
            text=label, web_app=WebAppInfo(url=f"{cfg.public_url}/app/{page}")))
    wh = await bot.get_webhook_info()
    log.info("bot %s = @%s, очікує оновлень: %s, вебхук: %s", which, me.username, wh.pending_update_count, bool(wh.url))
    if wh.url:  # вебхук блокує polling — знімаємо, чергу повідомлень зберігаємо
        await bot.delete_webhook(drop_pending_updates=False)
        log.warning("bot %s: знято вебхук", which)


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await db.init(cfg.db_url)
    if cfg.db_url.startswith("sqlite"):
        log.warning("DATABASE_URL не задано: працюю на тимчасовому файлі SQLite — на хостингу дані зникнуть при перезапуску")
    if not cfg.public_url:
        log.warning("PUBLIC_URL не задано: кнопки анкети й калькулятора ведуть на сайт, а не в Mini App")

    runner = web.AppRunner(make_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", cfg.port).start()
    log.info("web on :%s, public %s", cfg.port, cfg.public_url or "—")

    tasks: list[asyncio.Task] = []
    props = DefaultBotProperties(parse_mode="HTML")
    for which, token, router in (("order", cfg.order_token, bot_order.r), ("info", cfg.info_token, bot_info.r)):
        if not token:
            log.warning("токен бота %s не задано — бот не запущено", which)
            continue
        bot = Bot(token, default=props)
        await setup_bot(bot, which)
        dp = Dispatcher()
        dp.include_router(router)
        tasks.append(asyncio.create_task(dp.start_polling(bot, handle_signals=False), name=f"poll-{which}"))
    tasks.append(asyncio.create_task(scheduler.loop(), name="scheduler"))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    waiter = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait([waiter, *tasks], return_when=asyncio.FIRST_COMPLETED)
    for t in done:
        if t is not waiter and t.exception():
            log.error("task %s crashed: %s", t.get_name(), t.exception())
            await notify.to_group(f"⚠️ Збій бота: {t.get_name()} — {t.exception()}")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for b in notify.BOTS.values():
        await b.session.close()
    await runner.cleanup()
    await db.close()


if __name__ == "__main__":
    asyncio.run(run())
