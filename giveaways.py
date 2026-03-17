"""
Система конкурсов с розыгрышем игровых ключей.
Поддерживает автоматическое отслеживание участников, выбор победителей и выдачу призов.
"""
import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape as esc
from typing import Optional

import pytz

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")


@dataclass
class Giveaway:
    """Конкурс с розыгрышем."""
    giveaway_id: str
    title: str
    description: str
    prize_type: str  # 'steam_key', 'points', 'subscription'
    prize_value: str  # ключ игры, количество баллов, или reward_id
    start_time: datetime
    end_time: datetime
    channel_post_id: Optional[int] = None
    winner_user_id: Optional[int] = None
    status: str = "active"  # active, ended, cancelled
    require_channel_sub: bool = True
    min_account_age_days: int = 7


async def init_giveaways_db():
    """Инициализация таблиц для конкурсов."""
    from database import get_pool
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Таблица конкурсов
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaways (
                giveaway_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                prize_type TEXT NOT NULL,
                prize_value TEXT NOT NULL,
                start_time TIMESTAMPTZ NOT NULL,
                end_time TIMESTAMPTZ NOT NULL,
                channel_post_id INTEGER,
                winner_user_id BIGINT,
                status TEXT DEFAULT 'active',
                require_channel_sub BOOLEAN DEFAULT TRUE,
                min_account_age_days INTEGER DEFAULT 7,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        
        # Таблица участников
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_participants (
                id SERIAL PRIMARY KEY,
                giveaway_id TEXT NOT NULL REFERENCES giveaways(giveaway_id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                joined_at TIMESTAMPTZ DEFAULT NOW(),
                is_eligible BOOLEAN DEFAULT TRUE,
                UNIQUE(giveaway_id, user_id)
            )
        """)
        
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_giveaway_participants_giveaway ON giveaway_participants(giveaway_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_giveaway_participants_user ON giveaway_participants(user_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_giveaways_status ON giveaways(status)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_giveaways_end_time ON giveaways(end_time)"
        )
        # Миграция: добавляем reminder_sent если нет
        await conn.execute(
            "ALTER TABLE giveaways ADD COLUMN IF NOT EXISTS reminder_sent BOOLEAN DEFAULT FALSE"
        )
        # Миграция: статус доставки приза
        await conn.execute(
            "ALTER TABLE giveaways ADD COLUMN IF NOT EXISTS prize_delivery_status TEXT DEFAULT 'pending'"
        )
    
    log.info("Таблицы конкурсов инициализированы")


async def create_giveaway(
    title: str,
    description: str,
    prize_type: str,
    prize_value: str,
    duration_hours: int = 72,
    require_channel_sub: bool = True,
    min_account_age_days: int = 7,
    _test: bool = False,
) -> str:
    """
    Создать новый конкурс.
    
    Args:
        title: Название игры/приза
        description: Описание конкурса
        prize_type: Тип приза (steam_key, points, subscription)
        prize_value: Значение приза
        duration_hours: Длительность в часах (по умолчанию 72 = 3 дня)
        require_channel_sub: Требовать подписку на канал
        min_account_age_days: Минимальный возраст аккаунта в днях
        _test: Если True — ID получает префикс giveaway_test_ (не попадёт в планировщик)
        
    Returns:
        giveaway_id созданного конкурса
    """
    from database import get_pool
    
    now = datetime.now(MSK)
    end_time = now + timedelta(hours=duration_hours)
    import uuid as _uuid
    prefix = "giveaway_test" if _test else "giveaway"
    giveaway_id = f"{prefix}_{int(now.timestamp())}_{_uuid.uuid4().hex[:6]}"
    
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO giveaways (
            giveaway_id, title, description, prize_type, prize_value,
            start_time, end_time, require_channel_sub, min_account_age_days
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    """, giveaway_id, title, description, prize_type, prize_value,
        now, end_time, require_channel_sub, min_account_age_days)
    
    log.info(f"Создан конкурс {giveaway_id}: {title}")
    return giveaway_id


async def join_giveaway(giveaway_id: str, user_id: int) -> tuple[bool, str]:
    """
    Добавить участника в конкурс.
    
    Returns:
        (success, message) - успех и сообщение для пользователя
    """
    from database import get_pool, get_user_registration_date
    
    pool = await get_pool()
    
    # Проверяем существование и статус конкурса
    giveaway = await pool.fetchrow(
        "SELECT * FROM giveaways WHERE giveaway_id = $1", giveaway_id
    )
    
    if not giveaway:
        return False, "Конкурс не найден"
    
    if giveaway["status"] != "active":
        return False, "Конкурс уже завершён"
    
    # Сравниваем aware datetimes напрямую (asyncpg возвращает TIMESTAMPTZ как UTC-aware)
    end_time = giveaway["end_time"]
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=MSK)
    if datetime.now(MSK) > end_time:
        return False, "Конкурс уже завершён"
    
    # Проверяем возраст аккаунта
    if giveaway["min_account_age_days"] > 0:
        reg_date = await get_user_registration_date(user_id)
        if reg_date:
            reg_aware = reg_date if reg_date.tzinfo else reg_date.replace(tzinfo=MSK)
            account_age = (datetime.now(MSK) - reg_aware).days
            if account_age < giveaway["min_account_age_days"]:
                return False, f"Аккаунт должен быть старше {giveaway['min_account_age_days']} дней"
    
    # Проверяем подписку на канал (если требуется)
    if giveaway["require_channel_sub"]:
        from config import CHANNEL_ID
        from publisher import get_bot
        
        bot = get_bot()
        try:
            member = await bot.get_chat_member(CHANNEL_ID, user_id)
            if member.status in ["left", "kicked", "banned"]:
                return False, "Нужно подписаться на канал для участия"
        except Exception as e:
            log.warning(f"Не удалось проверить подписку user {user_id}: {e} — пропускаем проверку")
    
    # Добавляем участника
    try:
        existing = await pool.fetchval(
            "SELECT 1 FROM giveaway_participants WHERE giveaway_id = $1 AND user_id = $2",
            giveaway_id, user_id
        )
        if existing:
            return False, "Ты уже участвуешь в этом розыгрыше! 🎲"

        await pool.execute("""
            INSERT INTO giveaway_participants (giveaway_id, user_id)
            VALUES ($1, $2)
        """, giveaway_id, user_id)
        
        return True, "Ты участвуешь в розыгрыше! 🎉"
    except Exception as e:
        log.error(f"Ошибка добавления участника: {e}")
        return False, "Ошибка регистрации"


async def get_active_giveaways() -> list[dict]:
    """Получить список активных конкурсов."""
    from database import get_pool
    
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT g.*, COUNT(p.user_id) as participants_count
        FROM giveaways g
        LEFT JOIN giveaway_participants p ON g.giveaway_id = p.giveaway_id
        WHERE g.status = 'active' AND g.end_time > NOW()
        GROUP BY g.giveaway_id
        ORDER BY g.end_time ASC
    """)
    
    return [dict(r) for r in rows]


async def get_giveaway_participants(giveaway_id: str) -> list[int]:
    """Получить список user_id участников конкурса."""
    from database import get_pool
    
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT user_id FROM giveaway_participants
        WHERE giveaway_id = $1 AND is_eligible = TRUE
    """, giveaway_id)
    
    return [r["user_id"] for r in rows]


async def select_winner(giveaway_id: str) -> Optional[int]:
    """
    Выбрать случайного победителя из участников.
    Участники, пригласившие друзей, получают дополнительные шансы:
    1 базовый шанс + 1 за каждого приглашённого реферала.

    Returns:
        user_id победителя или None если нет участников
    """
    from database import get_pool

    pool = await get_pool()

    # Один запрос: участники + количество их рефералов через LEFT JOIN
    rows = await pool.fetch("""
        SELECT
            gp.user_id,
            1 + COALESCE(r.referral_count, 0) AS slots
        FROM giveaway_participants gp
        LEFT JOIN (
            SELECT referrer_id, COUNT(*) AS referral_count
            FROM referrals
            GROUP BY referrer_id
        ) r ON r.referrer_id = gp.user_id
        WHERE gp.giveaway_id = $1 AND gp.is_eligible = TRUE
    """, giveaway_id)

    if not rows:
        log.warning(f"Нет участников в конкурсе {giveaway_id}")
        return None

    # Строим взвешенный список
    weighted: list[int] = []
    for row in rows:
        weighted.extend([row["user_id"]] * int(row["slots"]))

    winner_id = random.choice(weighted)

    log.info(f"Победитель конкурса {giveaway_id}: user {winner_id} (пул: {len(weighted)} слотов)")
    return winner_id


async def award_prize(giveaway_id: str, winner_id: int) -> tuple[bool, str]:
    """
    Выдать приз победителю.
    
    Returns:
        (success, message) - успех и сообщение
    """
    from database import get_pool
    
    pool = await get_pool()
    giveaway = await pool.fetchrow(
        "SELECT * FROM giveaways WHERE giveaway_id = $1", giveaway_id
    )
    
    if not giveaway:
        return False, "Конкурс не найден"
    
    prize_type = giveaway["prize_type"]
    prize_value = giveaway["prize_value"]
    
    try:
        if prize_type == "steam_key":
            from publisher import get_bot, send_with_retry
            from config import ADMIN_ID
            bot = get_bot()

            try:
                await send_with_retry(lambda: bot.send_message(
                    winner_id,
                    f"🎮 <b>Поздравляем! Ты выиграл {esc(giveaway['title'])}!</b>\n\n"
                    f"🔑 Твой ключ: <code>{esc(prize_value)}</code>\n\n"
                    f"Активируй его в Steam:\n"
                    f"1. Открой Steam\n"
                    f"2. Игры → Активировать продукт\n"
                    f"3. Введи ключ\n\n"
                    f"Приятной игры! 🎉",
                    parse_mode="HTML",
                ))
                return True, "Ключ отправлен победителю"
            except Exception as send_err:
                log.error(f"Не удалось отправить ключ победителю {winner_id}: {send_err}")
                if ADMIN_ID:
                    try:
                        await send_with_retry(lambda: bot.send_message(
                            ADMIN_ID,
                            f"🚨 <b>Проблема с розыгрышем!</b>\n\n"
                            f"🎮 Конкурс: <b>{esc(giveaway['title'])}</b>\n"
                            f"👤 Победитель: <code>{winner_id}</code>\n"
                            f"❌ Причина: победитель не запускал бота\n\n"
                            f"🔑 Ключ: <code>{esc(prize_value)}</code>\n\n"
                            f"Выдай ключ вручную или перевыбери победителя командой "
                            f"/reroll_{giveaway_id}",
                            parse_mode="HTML",
                        ))
                    except Exception as admin_err:
                        log.error(f"Не удалось уведомить админа: {admin_err}")
                await pool.execute(
                    "UPDATE giveaways SET prize_delivery_status = 'failed' WHERE giveaway_id = $1",
                    giveaway_id,
                )
                return False, f"Ключ не доставлен: {send_err}"

        elif prize_type == "points":
            points = int(prize_value)
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO user_scores (user_id, total_score)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id) DO UPDATE
                    SET total_score = user_scores.total_score + $2
                """, winner_id, points)

            from publisher import get_bot, send_with_retry
            bot = get_bot()
            await send_with_retry(lambda: bot.send_message(
                winner_id,
                f"🎉 <b>Поздравляем!</b>\n\n"
                f"Ты выиграл <b>{points} баллов</b> в конкурсе!\n"
                f"Потрать их в /shop",
                parse_mode="HTML",
            ))
            return True, f"Начислено {points} баллов"

        elif prize_type == "subscription":
            from database import get_pool as _get_pool
            _pool = await _get_pool()
            async with _pool.acquire() as _conn:
                await _conn.execute("""
                    INSERT INTO user_rewards (user_id, reward_id, is_active)
                    VALUES ($1, $2, TRUE)
                    ON CONFLICT DO NOTHING
                """, winner_id, prize_value)

            from publisher import get_bot, send_with_retry
            bot = get_bot()
            await send_with_retry(lambda: bot.send_message(
                winner_id,
                f"🎉 <b>Поздравляем!</b>\n\n"
                f"Ты выиграл приз в конкурсе!\n"
                f"Проверь /myrewards",
                parse_mode="HTML",
            ))
            return True, "Приз выдан"

        else:
            return False, f"Неизвестный тип приза: {prize_type}"
            
    except Exception as e:
        log.error(f"Ошибка выдачи приза: {e}")
        return False, str(e)


async def publish_giveaway(giveaway_id: str) -> Optional[int]:
    """
    Опубликовать конкурс в канале.

    Returns:
        message_id опубликованного сообщения
    """
    from database import get_pool
    from publisher import get_bot, send_with_retry
    from config import CHANNEL_ID
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    pool = await get_pool()
    giveaway = await pool.fetchrow(
        "SELECT * FROM giveaways WHERE giveaway_id = $1", giveaway_id
    )
    
    if not giveaway:
        log.error(f"Конкурс {giveaway_id} не найден")
        return None
    
    # Форматируем сообщение
    end_time = giveaway["end_time"]
    if end_time.tzinfo is not None:
        end_time = end_time.astimezone(MSK)
    else:
        end_time = end_time.replace(tzinfo=MSK)
    end_str = end_time.strftime("%d.%m.%Y %H:%M МСК")
    
    prize_emoji = {
        "steam_key": "🎮",
        "points": "⭐",
        "subscription": "👑"
    }.get(giveaway["prize_type"], "🎁")
    
    text = (
        f"🎁 <b>РОЗЫГРЫШ!</b>\n\n"
        f"{prize_emoji} <b>{esc(giveaway['title'])}</b>\n\n"
        f"{esc(giveaway['description'])}\n\n"
        f"📅 Розыгрыш: <b>{end_str}</b>\n"
        f"👥 Участников: <b>0</b>\n\n"
        f"🎲 <b>Больше друзей = больше шансов!</b>\n"
        f"За каждого приглашённого друга ты получаешь +1 дополнительный шанс на победу.\n"
        f"Своя ссылка — в боте: /invite\n\n"
        f"<i>Нажми кнопку ниже для участия!</i>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎲 Участвовать",
            callback_data=f"giveaway_join:{giveaway_id}"
        )],
        [InlineKeyboardButton(
            text="📢 Поделиться",
            switch_inline_query=f"🎁 Розыгрыш {giveaway['title']}! Участвуй → t.me/GameDealsRadarRu"
        )]
    ])
    
    from publisher import get_bot, send_with_retry
    from config import CHANNEL_ID

    bot = get_bot()
    try:
        msg = await send_with_retry(lambda: bot.send_message(
            CHANNEL_ID,
            text,
            reply_markup=keyboard,
        ))
        
        # Сохраняем message_id
        await pool.execute("""
            UPDATE giveaways SET channel_post_id = $2
            WHERE giveaway_id = $1
        """, giveaway_id, msg.message_id)
        
        log.info(f"Конкурс {giveaway_id} опубликован: message_id={msg.message_id}")
        return msg.message_id
        
    except Exception as e:
        log.error(f"Ошибка публикации конкурса: {e}")
        return None


async def end_giveaway(giveaway_id: str) -> bool:
    """
    Завершить конкурс, выбрать победителя и выдать приз.

    Returns:
        True если успешно
    """
    from database import get_pool
    from publisher import get_bot, send_with_retry
    from config import CHANNEL_ID

    pool = await get_pool()

    # Атомарно переводим статус active → ending, чтобы исключить двойной запуск
    updated = await pool.fetchval("""
        UPDATE giveaways SET status = 'ending'
        WHERE giveaway_id = $1 AND status = 'active'
        RETURNING giveaway_id
    """, giveaway_id)

    if not updated:
        log.warning(f"Конкурс {giveaway_id} уже завершается или завершён — пропускаем")
        return False

    giveaway = await pool.fetchrow(
        "SELECT * FROM giveaways WHERE giveaway_id = $1", giveaway_id
    )

    if not giveaway:
        log.error(f"Конкурс {giveaway_id} не найден")
        return False

    # Выбираем победителя
    winner_id = await select_winner(giveaway_id)

    if not winner_id:
        await pool.execute(
            "UPDATE giveaways SET status = 'ended' WHERE giveaway_id = $1",
            giveaway_id,
        )
        bot = get_bot()
        if bot and giveaway["channel_post_id"]:
            try:
                await send_with_retry(lambda: bot.edit_message_text(
                    "🎁 <b>Розыгрыш завершён</b>\n\n❌ Нет участников",
                    CHANNEL_ID,
                    giveaway["channel_post_id"],
                    parse_mode="HTML",
                ))
            except Exception:
                pass
        return False

    # Проверяем что winner_id — реальный пользователь, а не канал/группа
    # Telegram user_id всегда положительный и < 10^10 для обычных пользователей
    if winner_id <= 0:
        log.error(f"Некорректный winner_id={winner_id} для конкурса {giveaway_id}")
        await pool.execute(
            "UPDATE giveaways SET status = 'ended' WHERE giveaway_id = $1", giveaway_id
        )
        return False

    # Сохраняем победителя и ставим статус ended
    await pool.execute(
        "UPDATE giveaways SET winner_user_id = $2, status = 'ended' WHERE giveaway_id = $1",
        giveaway_id, winner_id,
    )

    # Выдаём приз
    success, prize_msg = await award_prize(giveaway_id, winner_id)

    # Объявляем победителя в канале (без ключа — только имя)
    bot = get_bot()
    try:
        winner = await bot.get_chat(winner_id)
        winner_name = winner.first_name or str(winner_id)
        if winner.username:
            winner_mention = f"@{winner.username}"
        else:
            winner_mention = f'<a href="tg://user?id={winner_id}">{esc(winner_name)}</a>'

        participants_count = len(await get_giveaway_participants(giveaway_id))

        # В канале — только поздравление БЕЗ ключа
        announcement = (
            f"🎉 <b>Розыгрыш завершён!</b>\n\n"
            f"🎮 <b>{esc(giveaway['title'])}</b>\n\n"
            f"🏆 Победитель: {winner_mention}\n"
            f"👥 Участников: {participants_count}\n\n"
            f"Поздравляем! 🎊"
        )

        if giveaway["channel_post_id"]:
            await send_with_retry(lambda: bot.edit_message_text(
                announcement, CHANNEL_ID, giveaway["channel_post_id"],
                parse_mode="HTML",
            ))
        else:
            await send_with_retry(lambda: bot.send_message(
                CHANNEL_ID, announcement, parse_mode="HTML"
            ))

        log.info(f"Конкурс {giveaway_id} завершён, победитель: {winner_id}")

        try:
            from config import ADMIN_ID
            if ADMIN_ID:
                await send_with_retry(lambda: bot.send_message(
                    ADMIN_ID,
                    f"✅ <b>Конкурс завершён!</b>\n\n"
                    f"🎮 {esc(giveaway['title'])}\n"
                    f"🏆 Победитель: {winner_mention} (<code>{winner_id}</code>)\n"
                    f"👥 Участников: {participants_count}\n"
                    f"📦 Приз: {'✅ отправлен' if success else '❌ ' + prize_msg}",
                    parse_mode="HTML",
                ))
        except Exception as e:
            log.warning(f"Не удалось отправить алерт админу: {e}")

        return True

    except Exception as e:
        log.error(f"Ошибка объявления победителя: {e}")
        return False


async def check_giveaway_reminders():
    """Отправить напоминание в канал за 1 час до конца конкурса."""
    from database import get_pool
    from publisher import get_bot
    from config import CHANNEL_ID
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT * FROM giveaways
        WHERE status = 'active'
          AND end_time BETWEEN NOW() + INTERVAL '50 minutes' AND NOW() + INTERVAL '70 minutes'
          AND reminder_sent = FALSE
          AND giveaway_id NOT LIKE 'giveaway_test_%'
    """)

    from publisher import get_bot, send_with_retry
    bot = get_bot()
    for giveaway in rows:
        try:
            participants = await get_giveaway_participants(giveaway["giveaway_id"])
            count = len(participants)
            prize_emoji = {"steam_key": "🎮", "points": "⭐", "subscription": "👑"}.get(giveaway["prize_type"], "🎁")
            text = (
                f"⏰ <b>Последний час!</b>\n\n"
                f"{prize_emoji} Розыгрыш <b>{esc(giveaway['title'])}</b> заканчивается через 1 час!\n\n"
                f"👥 Участников: <b>{count}</b>\n\n"
                f"Ещё не участвуешь? Жми кнопку! 👇"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🎲 Участвовать",
                    callback_data=f"giveaway_join:{giveaway['giveaway_id']}"
                )]
            ])
            await send_with_retry(lambda t=text, kb=keyboard: bot.send_message(
                CHANNEL_ID, t, reply_markup=kb, parse_mode="HTML"
            ))
            await pool.execute(
                "UPDATE giveaways SET reminder_sent = TRUE WHERE giveaway_id = $1",
                giveaway["giveaway_id"]
            )
            log.info(f"Напоминание отправлено для конкурса {giveaway['giveaway_id']}")
        except Exception as e:
            log.error(f"Ошибка напоминания для {giveaway['giveaway_id']}: {e}")


async def delete_giveaway(giveaway_id: str) -> tuple[bool, str]:
    """Удалить конкурс из БД и пост из канала."""
    from database import get_pool
    from publisher import get_bot
    from config import CHANNEL_ID

    pool = await get_pool()
    giveaway = await pool.fetchrow("SELECT * FROM giveaways WHERE giveaway_id = $1", giveaway_id)

    if not giveaway:
        return False, "Конкурс не найден"

    if giveaway["channel_post_id"]:
        try:
            bot = get_bot()
            await bot.delete_message(CHANNEL_ID, giveaway["channel_post_id"])
        except Exception as e:
            log.warning(f"Не удалось удалить пост конкурса: {e}")

    await pool.execute("DELETE FROM giveaway_participants WHERE giveaway_id = $1", giveaway_id)
    await pool.execute("DELETE FROM giveaways WHERE giveaway_id = $1", giveaway_id)
    log.info(f"Конкурс {giveaway_id} удалён")
    return True, "Конкурс удалён"


async def check_ended_giveaways():
    """Проверить и завершить истёкшие конкурсы (вызывается по расписанию)."""
    from database import get_pool
    
    pool = await get_pool()
    ended = await pool.fetch("""
        SELECT giveaway_id FROM giveaways
        WHERE status = 'active' AND end_time <= NOW()
    """)
    
    for row in ended:
        giveaway_id = row["giveaway_id"]
        log.info(f"Завершаем конкурс {giveaway_id}")
        await end_giveaway(giveaway_id)
