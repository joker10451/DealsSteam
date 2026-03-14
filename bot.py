import asyncio
import logging
import os

import pytz
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import BOT_TOKEN, CHANNEL_ID, DATABASE_URL, POST_TIMES, ADMIN_ID
from database import init_db
from parsers.utils import init_session, close_session
from publisher import set_bot
from scheduler import check_and_post, post_weekly_digest, post_hidden_gems, run_parser_tests
from server import start_web_server, self_ping
import server
from handlers import register_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

MSK = pytz.timezone("Europe/Moscow")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


async def main():
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN не задан")
    if not CHANNEL_ID:
        errors.append("CHANNEL_ID не задан")
    if not DATABASE_URL:
        errors.append("DATABASE_URL не задан")
    if errors:
        for e in errors:
            log.critical(f"Конфигурация: {e}")
        raise SystemExit(1)

    await init_db()
    await init_session()
    set_bot(bot)

    register_all(dp)

    await start_web_server()

    scheduler = AsyncIOScheduler(timezone=MSK)

    scheduler.add_job(run_parser_tests, CronTrigger(hour=8, minute=0, timezone=MSK), name="parser_tests")
    log.info("Авто-тест парсеров: каждый день в 08:00 МСК")

    scheduler.add_job(post_hidden_gems, CronTrigger(hour=14, minute=0, timezone=MSK), name="hidden_gems")
    log.info("Скрытые жемчужины: каждый день в 14:00 МСК")

    async def _check_and_post_with_time():
        post_time = await check_and_post()
        if post_time:
            server.last_post_time = post_time

    for hour, minute in POST_TIMES:
        scheduler.add_job(
            _check_and_post_with_time,
            CronTrigger(hour=hour, minute=minute, timezone=MSK),
            name=f"post_{hour:02d}:{minute:02d}",
        )
        log.info(f"Запланирована публикация в {hour:02d}:{minute:02d} МСК")

    scheduler.add_job(
        post_weekly_digest,
        CronTrigger(day_of_week="sun", hour=12, minute=0, timezone=MSK),
        name="weekly_digest",
    )
    log.info("Еженедельный дайджест: каждое воскресенье в 12:00 МСК")

    if os.getenv("RENDER_EXTERNAL_URL"):
        scheduler.add_job(self_ping, "interval", minutes=10, name="self_ping")
        log.info("Self-ping включён: каждые 10 минут")

    scheduler.start()
    log.info("Бот запущен.")

    try:
        await dp.start_polling(bot)
    finally:
        await close_session()


if __name__ == "__main__":
    asyncio.run(main())
