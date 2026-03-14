import asyncio
import logging
import os

import pytz
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from config import BOT_TOKEN, CHANNEL_ID, DATABASE_URL, POST_TIMES, ADMIN_ID
from database import init_db
from parsers.utils import init_session, close_session
from publisher import set_bot
from scheduler import (
    check_and_post, post_weekly_digest, post_hidden_gems, run_parser_tests,
    sync_all_steam_wishlists, sync_all_steam_libraries
)
from publisher import flush_notification_queue
from free_game_monitor import check_epic_free_games, check_gog_free_games
from database import price_cache_cleanup
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
    
    # Инициализация реферальной системы
    from referral import init_referral_table
    await init_referral_table()
    
    await init_session()
    set_bot(bot)

    register_all(dp)

    # Команды для обычных пользователей
    user_commands = [
        BotCommand(command="start", description="Начать"),
        BotCommand(command="wishlist", description="Мой вишлист"),
        BotCommand(command="remove", description="Удалить из вишлиста"),
        BotCommand(command="cancel", description="Удалить кнопками"),
        BotCommand(command="genre", description="Подписка на жанр"),
        BotCommand(command="genres", description="Мои подписки на жанры"),
        BotCommand(command="top", description="Топ скидок сейчас"),
        BotCommand(command="price", description="Цены по регионам Steam"),
        BotCommand(command="find", description="Найти скидки по тегу"),
        BotCommand(command="steam", description="Привязать Steam аккаунт"),
        BotCommand(command="steamsync", description="Синхронизировать Steam"),
        BotCommand(command="steamstatus", description="Статус Steam интеграции"),
        BotCommand(command="steamdisconnect", description="Отключить Steam"),
        BotCommand(command="freenotify", description="Уведомления о бесплатных играх"),
        BotCommand(command="notify_settings", description="Настройки уведомлений вишлиста"),
        BotCommand(command="min_discount", description="Мин. скидка для уведомлений"),
        BotCommand(command="quiet_hours", description="Тихие часы (не беспокоить)"),
        BotCommand(command="group_notify", description="Группировать уведомления"),
        BotCommand(command="games", description="Мини-игры"),
        BotCommand(command="score", description="Мои баллы"),
        BotCommand(command="leaderboard", description="Таблица лидеров"),
        BotCommand(command="profile", description="Мой профиль"),
        BotCommand(command="achievements", description="Мои достижения"),
        BotCommand(command="challenge", description="Челлендж дня"),
        BotCommand(command="shop", description="Магазин призов"),
        BotCommand(command="myrewards", description="Мои призы"),
        BotCommand(command="buy", description="Купить приз"),
        BotCommand(command="invite", description="Пригласить друга"),
    ]
    # Команды для админа (включают всё)
    admin_commands = user_commands + [
        BotCommand(command="post", description="Опубликовать скидки"),
        BotCommand(command="gems", description="Опубликовать жемчужины"),
        BotCommand(command="digest", description="Опубликовать дайджест"),
        BotCommand(command="stats", description="Метрики за 7 дней"),
        BotCommand(command="givekey", description="Выдать Steam ключ"),
        BotCommand(command="addpoints", description="Начислить баллы"),
        BotCommand(command="rewardstats", description="Статистика призов"),
    ]

    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    if ADMIN_ID:
        await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    log.info("Команды бота установлены")

    await start_web_server()

    scheduler = AsyncIOScheduler(timezone=MSK)

    scheduler.add_job(run_parser_tests, CronTrigger(hour=8, minute=0, timezone=MSK), name="parser_tests")
    log.info("Авто-тест парсеров: каждый день в 08:00 МСК")

    scheduler.add_job(post_hidden_gems, CronTrigger(hour=14, minute=0, timezone=MSK), name="hidden_gems")
    log.info("Скрытые жемчужины: каждый день в 14:00 МСК")

    # Генерация челленджа в 00:00 и публикация в 09:00
    from daily_challenges import create_todays_challenge, publish_daily_challenge
    scheduler.add_job(create_todays_challenge, CronTrigger(hour=0, minute=0, timezone=MSK), name="generate_challenge")
    log.info("Генерация челленджа: каждый день в 00:00 МСК")
    
    scheduler.add_job(publish_daily_challenge, CronTrigger(hour=9, minute=0, timezone=MSK), name="publish_challenge")
    log.info("Публикация челленджа: каждый день в 09:00 МСК")

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

    # Steam Integration Jobs
    scheduler.add_job(
        sync_all_steam_wishlists,
        CronTrigger(hour=6, minute=0, timezone=MSK),
        name="steam_wishlist_sync"
    )
    log.info("Синхронизация Steam вишлистов: каждый день в 06:00 МСК")

    scheduler.add_job(
        sync_all_steam_libraries,
        CronTrigger(day_of_week="mon", hour=3, minute=0, timezone=MSK),
        name="steam_library_sync"
    )
    log.info("Синхронизация Steam библиотек: каждый понедельник в 03:00 МСК")

    # Free Game Monitoring Jobs
    scheduler.add_job(
        check_epic_free_games,
        "interval",
        hours=2,
        name="epic_free_monitor"
    )
    log.info("Мониторинг бесплатных игр Epic: каждые 2 часа")

    scheduler.add_job(
        check_gog_free_games,
        "interval",
        hours=6,
        name="gog_free_monitor"
    )
    log.info("Мониторинг бесплатных игр GOG: каждые 6 часов")

    # Price Cache Cleanup Job
    scheduler.add_job(
        price_cache_cleanup,
        CronTrigger(hour=4, minute=0, timezone=MSK),
        name="price_cache_cleanup"
    )
    log.info("Очистка кеша цен: каждый день в 04:00 МСК")

    scheduler.add_job(
        flush_notification_queue,
        CronTrigger(minute=0, timezone=MSK),  # каждый час в :00
        name="flush_notif_queue"
    )
    log.info("Flush очереди уведомлений: каждый час")

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
