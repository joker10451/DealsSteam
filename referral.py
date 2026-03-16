"""
Реферальная система для привлечения новых пользователей.
"""
import logging
import hashlib
from typing import Optional, List
from datetime import datetime

import pytz
from database import get_pool

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")

# Награды
REFERRER_BONUS = 100  # Баллы за приглашение друга
REFEREE_BONUS = 50    # Баллы новому пользователю


async def init_referral_table():
    """Создать таблицы для реферальной системы."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Таблица рефералов
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referee_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                bonus_paid BOOLEAN DEFAULT FALSE,
                UNIQUE(referee_id)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_referrals_referee ON referrals(referee_id)
        """)


def generate_referral_code(user_id: int) -> str:
    """Генерирует уникальный реферальный код для пользователя."""
    # Используем хеш для создания короткого кода
    hash_input = f"{user_id}_gamedeals_bot"
    hash_obj = hashlib.md5(hash_input.encode())
    return hash_obj.hexdigest()[:8].upper()


def get_referral_link(user_id: int, bot_username: str) -> str:
    """Создаёт реферальную ссылку для пользователя."""
    code = generate_referral_code(user_id)
    return f"https://t.me/{bot_username}?start=ref_{code}"


async def decode_referral_code(code: str) -> Optional[int]:
    """Декодирует реферальный код в user_id через прямой поиск по хешу."""
    if not code or len(code) != 8:
        return None
    # Ищем только среди пользователей у которых есть запись в user_scores
    # Ограничиваем выборку последними 10000 активными пользователями
    pool = await get_pool()
    async with pool.acquire() as conn:
        users = await conn.fetch("""
            SELECT user_id FROM user_scores
            ORDER BY created_at DESC LIMIT 10000
        """)
        for user in users:
            user_id = user["user_id"]
            if generate_referral_code(user_id) == code.upper():
                return user_id
    return None


async def register_referral(referrer_id: int, referee_id: int) -> dict:
    """Регистрирует нового реферала и начисляет бонусы."""
    if referrer_id == referee_id:
        return {"error": "Нельзя пригласить самого себя!"}
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем, не был ли пользователь уже приглашён
        existing = await conn.fetchval("""
            SELECT referrer_id FROM referrals WHERE referee_id = $1
        """, referee_id)
        
        if existing:
            return {"error": "Ты уже был приглашён другим пользователем"}

        # Лимит: один реферер не может пригласить более 50 человек
        referrer_count = await conn.fetchval("""
            SELECT COUNT(*) FROM referrals WHERE referrer_id = $1
        """, referrer_id)
        if referrer_count and referrer_count >= 50:
            return {"error": "Достигнут лимит приглашений"}

        # Проверяем, не новый ли это пользователь (нет записи онбординга)
        from database import get_onboarding_progress
        onboarding = await get_onboarding_progress(referee_id)
        if onboarding is not None:
            return {"error": "Реферальная ссылка работает только для новых пользователей"}
        
        # Регистрируем реферала и сразу помечаем бонус как выплаченный
        await conn.execute("""
            INSERT INTO referrals (referrer_id, referee_id, bonus_paid)
            VALUES ($1, $2, TRUE)
        """, referrer_id, referee_id)
        
        # Начисляем бонусы рефереру
        await conn.execute("""
            INSERT INTO user_scores (user_id, total_score)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET total_score = user_scores.total_score + $2
        """, referrer_id, REFERRER_BONUS)
        
        # Начисляем бонусы новому пользователю
        await conn.execute("""
            INSERT INTO user_scores (user_id, total_score)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET total_score = user_scores.total_score + $2
        """, referee_id, REFEREE_BONUS)
        
        log.info(f"Реферал зарегистрирован: {referrer_id} пригласил {referee_id}")

        # Читаем новый баланс реферера в том же соединении
        new_balance = await conn.fetchval(
            "SELECT total_score FROM user_scores WHERE user_id = $1", referrer_id
        ) or REFERRER_BONUS

    # Отправляем уведомление рефереру с актуальным балансом
    try:
        from publisher import get_bot
        bot = get_bot()
        if bot:
            await bot.send_message(
                referrer_id,
                f"✅ <b>Твой друг подписался!</b>\n\n"
                f"Тебе начислено <b>+{REFERRER_BONUS}</b> баллов.\n"
                f"Твой баланс: <b>{new_balance}</b> ⭐\n\n"
                f"Продолжай приглашать друзей: /invite",
                parse_mode="HTML",
            )
    except Exception as e:
        log.warning(f"Не удалось отправить уведомление рефереру {referrer_id}: {e}")
    
    return {
        "success": True,
        "referrer_bonus": REFERRER_BONUS,
        "referee_bonus": REFEREE_BONUS,
    }


async def get_referral_stats(user_id: int) -> dict:
    """Получить статистику рефералов пользователя."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Количество приглашённых
        total_referrals = await conn.fetchval("""
            SELECT COUNT(*) FROM referrals WHERE referrer_id = $1
        """, user_id)
        
        # Последние 5 рефералов
        recent = await conn.fetch("""
            SELECT referee_id, created_at
            FROM referrals
            WHERE referrer_id = $1
            ORDER BY created_at DESC
            LIMIT 5
        """, user_id)
        
        # Общий заработок с рефералов
        total_earned = total_referrals * REFERRER_BONUS if total_referrals else 0
        
        return {
            "total_referrals": total_referrals or 0,
            "total_earned": total_earned,
            "recent": [dict(r) for r in recent],
        }


async def get_top_referrers(limit: int = 10) -> List[dict]:
    """Получить топ пользователей по количеству рефералов."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                referrer_id,
                COUNT(*) as referral_count,
                COUNT(*) * $1 as total_earned
            FROM referrals
            GROUP BY referrer_id
            ORDER BY referral_count DESC
            LIMIT $2
        """, REFERRER_BONUS, limit)
        
        return [dict(r) for r in rows]


def format_referral_message(user_id: int, bot_username: str, stats: dict) -> str:
    """Форматирует сообщение с реферальной информацией."""
    from html import escape as esc
    
    link = get_referral_link(user_id, bot_username)
    total = stats["total_referrals"]
    earned = stats["total_earned"]
    
    lines = [
        "👥 <b>Реферальная программа</b>\n",
        f"Приглашай друзей и зарабатывай баллы!\n",
        f"<b>Твоя статистика:</b>",
        f"👤 Приглашено друзей: <b>{total}</b>",
        f"💰 Заработано баллов: <b>{earned}</b>\n",
        f"<b>Как это работает:</b>",
        f"1. Отправь другу свою ссылку",
        f"2. Друг переходит и начинает играть",
        f"3. Ты получаешь <b>+{REFERRER_BONUS}</b> баллов",
        f"4. Друг получает <b>+{REFEREE_BONUS}</b> баллов\n",
        f"<b>Твоя реферальная ссылка:</b>",
        f'<a href="{link}">👉 Нажми чтобы скопировать</a>\n',
        f"Или скопируй вручную:",
        f"<code>{link}</code>",
    ]
    
    if stats["recent"]:
        lines.append("\n<b>Последние приглашения:</b>")
        for ref in stats["recent"]:
            date = ref["created_at"].strftime("%d.%m.%Y")
            lines.append(f"• {date}")
    
    return "\n".join(lines)


async def check_and_apply_referral(user_id: int, start_param: Optional[str]) -> Optional[dict]:
    """
    Проверяет и применяет реферальный код при старте бота.
    Вызывается из /start команды.
    """
    if not start_param or not start_param.startswith("ref_"):
        return None
    
    code = start_param[4:]  # Убираем "ref_"
    referrer_id = await decode_referral_code(code)
    
    if not referrer_id:
        return {"error": "Неверный реферальный код"}
    
    result = await register_referral(referrer_id, user_id)
    return result


async def broadcast_referral_announcement(bot_username: str) -> dict:
    """
    Рассылает анонс реферальной программы всем активным пользователям.
    Возвращает статистику: sent, failed.
    """
    import asyncio
    from publisher import get_bot
    from database import get_pool

    bot = get_bot()
    if not bot:
        return {"error": "Бот не инициализирован"}

    pool = await get_pool()
    users = await pool.fetch("""
        SELECT user_id FROM user_scores ORDER BY total_score DESC
    """)

    sent = 0
    failed = 0

    for row in users:
        user_id = row["user_id"]
        link = get_referral_link(user_id, bot_username)
        text = (
            "🔥 <b>Новая фича: Зарабатывай баллы за друзей!</b>\n\n"
            "У тебя есть личная реферальная ссылка.\n\n"
            "<b>Как это работает:</b>\n"
            "1. Скопируй свою ссылку ниже\n"
            "2. Отправь другу\n"
            "3. Друг получает <b>+50 баллов</b> на старт\n"
            "4. Ты получаешь <b>+100 баллов</b> за каждого!\n\n"
            "🎲 <b>Бонус для розыгрышей:</b> каждый приглашённый друг даёт тебе "
            "+1 дополнительный шанс на победу в конкурсах!\n\n"
            "Накопил баллы? Меняй на Steam-ключи в /shop\n\n"
            f"<b>Твоя ссылка:</b>\n<code>{link}</code>"
        )
        try:
            await bot.send_message(user_id, text, parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            log.warning(f"Не удалось отправить анонс пользователю {user_id}: {e}")
            failed += 1

    log.info(f"Рассылка реферального анонса: отправлено {sent}, ошибок {failed}")
    return {"sent": sent, "failed": failed}
