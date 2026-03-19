import asyncio
import logging
import os

import pytz
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from config import BOT_TOKEN, CHANNEL_ID, DATABASE_URL, POST_TIMES, ADMIN_ID
from database import init_db
from parsers.utils import init_session, close_session
from publisher import set_bot
from scheduler import (
    check_and_post, post_weekly_digest, post_hidden_gems, run_parser_tests,
    sync_all_steam_wishlists, sync_all_steam_libraries,
    check_bot_health, run_garbage_collect, post_coop_digest,
)
from themed_collections import post_themed_collection

from publisher import flush_notification_queue
from free_game_monitor import check_epic_free_games
from database import price_cache_cleanup
from server import start_web_server, self_ping
import server
from handlers import register_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

MSK = pytz.timezone("Europe/Moscow")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


@dp.chat_member(ChatMemberUpdatedFilter(IS_MEMBER >> IS_NOT_MEMBER))
async def on_user_left_channel(event: ChatMemberUpdated):
    """Удалить пользователя из активных розыгрышей при выходе из канала."""
    # Реагируем только на события в нашем канале
    if str(event.chat.id) != str(CHANNEL_ID):
        return
    user_id = event.old_chat_member.user.id
    try:
        from giveaways import remove_participant_from_all_giveaways
        await remove_participant_from_all_giveaways(user_id)
    except Exception as e:
        log.error(f"Ошибка удаления {user_id} из розыгрышей: {e}")


@dp.errors()
async def global_error_handler(event, exception: Exception):
    log.error(f"Необработанное исключение в хендлере: {exception}", exc_info=exception)
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"⚠️ <b>Необработанная ошибка бота</b>\n\n"
                f"<code>{type(exception).__name__}: {str(exception)[:300]}</code>",
            )
        except Exception:
            pass
    return True


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
    
    # Инициализация системы конкурсов
    from giveaways import init_giveaways_db
    await init_giveaways_db()
    
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
        BotCommand(command="shop", description="Магазин призов"),
        BotCommand(command="myrewards", description="Мои призы"),
        BotCommand(command="buy", description="Купить приз"),
        BotCommand(command="invite", description="Пригласить друга"),
        BotCommand(command="giveaway", description="Активные конкурсы"),
        BotCommand(command="mygiveaways", description="Мои конкурсы"),
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
        BotCommand(command="creategiveaway", description="Создать конкурс"),
        BotCommand(command="endgiveaway", description="Завершить конкурс"),
        BotCommand(command="deletegiveaway", description="Удалить конкурс"),
        BotCommand(command="giveawaystat", description="Статистика участников конкурса"),
        BotCommand(command="giveawayhistory", description="История конкурсов"),
        BotCommand(command="announce_referral", description="Разослать анонс реферальной программы"),
        BotCommand(command="broadcast", description="Рассылка сообщения всем пользователям"),
        BotCommand(command="channelstat", description="Статистика пользователей бота"),
        BotCommand(command="tip", description="Опубликовать совет дня"),
        BotCommand(command="coop", description="Опубликовать кооп-дайджест"),
        BotCommand(command="testgame", description="Тест мини-игр в личку"),
        BotCommand(command="testvk", description="Тестовый пост в ВК"),
        BotCommand(command="vkgiveaway", description="Опубликовать розыгрыш в ВК"),
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

    scheduler.add_job(
        check_bot_health,
        CronTrigger(minute=30, timezone=MSK),  # каждый час в :30 (не совпадает с flush)
        name="bot_healthcheck"
    )
    log.info("Healthcheck бота: каждый час в :30")

    scheduler.add_job(
        run_garbage_collect,
        CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=MSK),
        name="db_garbage_collect"
    )
    log.info("Очистка БД (GC): каждое воскресенье в 03:00 МСК")

    scheduler.add_job(
        post_coop_digest,
        CronTrigger(day_of_week="fri", hour=18, minute=0, timezone=MSK),
        name="coop_digest"
    )
    log.info("Кооп-дайджест: каждую пятницу в 18:00 МСК")

    # ТОП СКИДКА ДНЯ — каждый день в 20:00 МСК (вечерний прайм-тайм)
    from scheduler import publish_top_of_day
    scheduler.add_job(
        publish_top_of_day,
        CronTrigger(hour=20, minute=0, timezone=MSK),
        name="top_of_day"
    )
    log.info("🏆 ТОП СКИДКА ДНЯ: каждый день в 20:00 МСК")

    # Weekend Themed Collections
    scheduler.add_job(
        lambda: post_themed_collection("weekend_coop"),
        CronTrigger(day_of_week="sat", hour=11, minute=0, timezone=MSK),
        name="weekend_coop_collection"
    )
    log.info("Подборка кооперативов: каждую субботу в 11:00 МСК")

    scheduler.add_job(
        lambda: post_themed_collection("budget_games"),
        CronTrigger(day_of_week="sun", hour=15, minute=0, timezone=MSK),
        name="budget_games_collection"
    )
    log.info("Подборка бюджетных игр: каждое воскресенье в 15:00 МСК")

    # Giveaway System Jobs
    from giveaways import check_ended_giveaways, check_giveaway_reminders
    scheduler.add_job(
        check_ended_giveaways,
        "interval",
        minutes=15,
        name="check_giveaways"
    )
    log.info("Проверка конкурсов: каждые 15 минут")

    scheduler.add_job(
        check_giveaway_reminders,
        "interval",
        minutes=15,
        name="giveaway_reminders"
    )
    log.info("Напоминания о конкурсах: каждые 15 минут")

    if os.getenv("RENDER_EXTERNAL_URL"):
        scheduler.add_job(self_ping, "interval", minutes=10, name="self_ping")
        log.info("Self-ping включён: каждые 10 минут")

    scheduler.start()
    log.info("Бот запущен.")

    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_member", "inline_query", "chosen_inline_result"])
    finally:
        await close_session()


if __name__ == "__main__":
    asyncio.run(main())
