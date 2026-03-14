"""
Автоматическая генерация ежедневных челленджей.
"""
import logging
import random
from datetime import datetime
from typing import Optional

import pytz
from database import get_pool
from minigames import create_daily_challenge

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")

# Типы челленджей
CHALLENGE_TYPES = [
    "find_cheapest",      # Найди самую дешёвую игру
    "guess_streak",       # Угадай 3 игры подряд
    "daily_score",        # Набери N баллов за день
    "vote_games",         # Проголосуй за 5 игр
    "find_genre",         # Найди игру определённого жанра
    "find_discount",      # Найди игру со скидкой больше N%
]


async def generate_daily_challenge() -> dict:
    """Генерирует случайный челлендж дня."""
    challenge_type = random.choice(CHALLENGE_TYPES)
    
    if challenge_type == "find_cheapest":
        # Найти самую дешёвую игру дня
        hint = random.choice([50, 100, 150, 200])
        return {
            "type": "find_cheapest",
            "data": {
                "hint": hint,
                "description": f"Найди самую дешёвую игру дня (подсказка: меньше {hint}₽)"
            }
        }
    
    elif challenge_type == "guess_streak":
        # Угадать N игр подряд
        streak = random.choice([3, 5])
        return {
            "type": "guess_streak",
            "data": {
                "required_streak": streak,
                "description": f"Угадай {streak} игры подряд в мини-играх"
            }
        }
    
    elif challenge_type == "daily_score":
        # Набрать N баллов за день
        target = random.choice([30, 50, 75, 100])
        return {
            "type": "daily_score",
            "data": {
                "target_score": target,
                "description": f"Набери {target} баллов за сегодня"
            }
        }
    
    elif challenge_type == "vote_games":
        # Проголосовать за N игр
        votes = random.choice([3, 5, 10])
        return {
            "type": "vote_games",
            "data": {
                "required_votes": votes,
                "description": f"Проголосуй за {votes} игр в канале"
            }
        }
    
    elif challenge_type == "find_genre":
        # Найти игру определённого жанра
        genres = ["RPG", "Action", "Strategy", "Horror", "Indie", "Roguelike", "Shooter"]
        genre = random.choice(genres)
        return {
            "type": "find_genre",
            "data": {
                "genre": genre,
                "description": f"Найди игру жанра {genre} со скидкой"
            }
        }
    
    elif challenge_type == "find_discount":
        # Найти игру со скидкой больше N%
        discount = random.choice([70, 80, 90])
        return {
            "type": "find_discount",
            "data": {
                "min_discount": discount,
                "description": f"Найди игру со скидкой больше {discount}%"
            }
        }
    
    return {
        "type": "daily_score",
        "data": {
            "target_score": 50,
            "description": "Набери 50 баллов за сегодня"
        }
    }


async def create_todays_challenge():
    """Создаёт челлендж на сегодня."""
    today = datetime.now(MSK).date()
    
    # Проверяем, есть ли уже челлендж на сегодня
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("""
            SELECT 1 FROM daily_challenges WHERE challenge_date = $1
        """, today)
        
        if existing:
            log.info(f"Челлендж на {today} уже существует")
            return
    
    # Генерируем новый челлендж
    challenge = await generate_daily_challenge()
    await create_daily_challenge(challenge["type"], challenge["data"])
    
    log.info(f"Создан челлендж на {today}: {challenge['type']}")
    return challenge


async def publish_daily_challenge():
    """Публикует челлендж дня в канал."""
    from publisher import get_bot, send_with_retry
    from config import CHANNEL_ID
    
    bot = get_bot()
    if not bot:
        log.warning("Бот не инициализирован")
        return
    
    # Создаём челлендж если его нет
    challenge = await create_todays_challenge()
    
    # Получаем челлендж дня
    from minigames import get_daily_challenge
    challenge = await get_daily_challenge()
    
    if not challenge:
        log.warning("Не удалось получить челлендж дня")
        return
    
    # Форматируем сообщение
    text = format_challenge_message(challenge)
    
    # Публикуем в канал
    try:
        await send_with_retry(
            bot.send_message,
            chat_id=CHANNEL_ID,
            text=text,
        )
        log.info("Челлендж дня опубликован в канал")
    except Exception as e:
        log.error(f"Ошибка публикации челленджа: {e}")


def format_challenge_message(challenge: dict) -> str:
    """Форматирует сообщение с челленджем."""
    from html import escape as esc
    
    challenge_type = challenge["type"]
    data = challenge["data"]
    description = data.get("description", "")
    
    emoji_map = {
        "find_cheapest": "💰",
        "guess_streak": "🎯",
        "daily_score": "⚡️",
        "vote_games": "👍",
        "find_genre": "🎮",
        "find_discount": "🔥",
    }
    
    emoji = emoji_map.get(challenge_type, "🎯")
    
    lines = [
        f"{emoji} <b>ЧЕЛЛЕНДЖ ДНЯ</b>\n",
        f"<b>Задание:</b> {esc(description)}\n",
        f"<b>Награда:</b> +50 баллов 🏆\n",
    ]
    
    # Добавляем инструкции в зависимости от типа
    if challenge_type == "find_cheapest":
        lines.append("📋 <b>Как выполнить:</b>")
        lines.append("1. Посмотри все скидки в канале")
        lines.append("2. Найди самую дешёвую игру")
        lines.append("3. Напиши боту её название")
    
    elif challenge_type == "guess_streak":
        streak = data.get("required_streak", 3)
        lines.append("📋 <b>Как выполнить:</b>")
        lines.append(f"1. Угадай {streak} игры подряд")
        lines.append("2. В играх 'Угадай цену' или 'Угадай игру'")
        lines.append("3. Без ошибок!")
    
    elif challenge_type == "daily_score":
        target = data.get("target_score", 50)
        lines.append("📋 <b>Как выполнить:</b>")
        lines.append(f"1. Набери {target} баллов за сегодня")
        lines.append("2. Играй в мини-игры")
        lines.append("3. Голосуй за скидки")
    
    elif challenge_type == "vote_games":
        votes = data.get("required_votes", 5)
        lines.append("📋 <b>Как выполнить:</b>")
        lines.append(f"1. Проголосуй за {votes} игр")
        lines.append("2. Нажимай 🔥 или 👎 под постами")
        lines.append("3. В течение дня")
    
    elif challenge_type == "find_genre":
        genre = data.get("genre", "RPG")
        lines.append("📋 <b>Как выполнить:</b>")
        lines.append(f"1. Найди игру жанра {genre}")
        lines.append("2. Со скидкой в канале")
        lines.append("3. Напиши боту её название")
    
    elif challenge_type == "find_discount":
        discount = data.get("min_discount", 70)
        lines.append("📋 <b>Как выполнить:</b>")
        lines.append(f"1. Найди игру со скидкой >{discount}%")
        lines.append("2. В постах канала")
        lines.append("3. Напиши боту её название")
    
    lines.append("\n💡 Проверить прогресс: /challenge в боте")
    
    return "\n".join(lines)


async def check_challenge_progress(user_id: int) -> Optional[dict]:
    """Проверяет прогресс пользователя по челленджу дня."""
    from minigames import get_daily_challenge
    
    challenge = await get_daily_challenge()
    if not challenge:
        return None
    
    challenge_type = challenge["type"]
    data = challenge["data"]
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем, не выполнен ли уже
        completed = await conn.fetchval("""
            SELECT 1 FROM daily_challenge_completions
            WHERE user_id = $1 AND challenge_date = CURRENT_DATE
        """, user_id)
        
        if completed:
            return {
                "completed": True,
                "progress": 100,
                "message": "✅ Челлендж выполнен!"
            }
        
        # Проверяем прогресс в зависимости от типа
        if challenge_type == "daily_score":
            target = data.get("target_score", 50)
            
            # Получаем баллы за сегодня
            today_score = await conn.fetchval("""
                SELECT COALESCE(SUM(points), 0)
                FROM user_score_history
                WHERE user_id = $1 
                AND earned_at >= CURRENT_DATE
            """, user_id)
            
            progress = min(100, int((today_score / target) * 100))
            
            return {
                "completed": today_score >= target,
                "progress": progress,
                "current": today_score,
                "target": target,
                "message": f"Набрано {today_score}/{target} баллов"
            }
        
        elif challenge_type == "vote_games":
            required = data.get("required_votes", 5)
            
            # Считаем голоса за сегодня
            votes_today = await conn.fetchval("""
                SELECT COUNT(*)
                FROM votes
                WHERE user_id = $1
                AND voted_at >= CURRENT_DATE
            """, user_id)
            
            progress = min(100, int((votes_today / required) * 100))
            
            return {
                "completed": votes_today >= required,
                "progress": progress,
                "current": votes_today,
                "target": required,
                "message": f"Проголосовано {votes_today}/{required} раз"
            }
        
        # Для остальных типов пока возвращаем базовую информацию
        return {
            "completed": False,
            "progress": 0,
            "message": "Челлендж в процессе"
        }
