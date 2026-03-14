"""
Мини-игры и челленджи для увеличения вовлечённости.
"""
import asyncio
import random
import logging
from datetime import datetime, timedelta
from typing import Optional

import pytz
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import get_pool

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")


# ============================================================================
# Система баллов
# ============================================================================

async def init_scores_table():
    """Создать таблицу для хранения баллов пользователей."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_scores (
                user_id BIGINT PRIMARY KEY,
                total_score INT DEFAULT 0,
                games_played INT DEFAULT 0,
                correct_answers INT DEFAULT 0,
                last_played TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        
        # Таблица истории баллов для отслеживания прогресса по челленджам
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_score_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                points INT NOT NULL,
                reason TEXT,
                earned_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_score_history_user_date 
            ON user_score_history(user_id, earned_at)
        """)


async def add_score(user_id: int, points: int, correct: bool = True, reason: str = "game"):
    """Добавить баллы пользователю."""
    if points < 0:
        return []

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Rate limit: не более 1 начисления в секунду с одной причиной
        if reason in ("game", "screenshot", "price_game"):
            last = await conn.fetchval("""
                SELECT earned_at FROM user_score_history
                WHERE user_id = $1 AND reason = $2
                ORDER BY earned_at DESC LIMIT 1
            """, user_id, reason)
            if last:
                from datetime import timezone
                now_utc = datetime.now(timezone.utc)
                last_utc = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
                if (now_utc - last_utc).total_seconds() < 1:
                    log.warning(f"Rate limit: user {user_id} добавление баллов слишком быстро")
                    return []

        # games_played инкрементируем только для реальных игровых действий
        is_game_action = reason in ("game", "screenshot", "price_game")
        await conn.execute("""
            INSERT INTO user_scores (user_id, total_score, games_played, correct_answers, last_played)
            VALUES ($1, $2, $3::int, $4::int, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                total_score = user_scores.total_score + $2,
                games_played = user_scores.games_played + $3::int,
                correct_answers = user_scores.correct_answers + $4::int,
                last_played = NOW()
        """, user_id, points, 1 if is_game_action else 0, 1 if (correct and is_game_action) else 0)

        # Записываем в историю
        await conn.execute("""
            INSERT INTO user_score_history (user_id, points, reason)
            VALUES ($1, $2, $3)
        """, user_id, points, reason)

    # Обновляем серии и проверяем достижения
    from achievements import update_streak, update_daily_streak, check_and_unlock_achievements
    await update_streak(user_id, correct)
    await update_daily_streak(user_id)

    return await check_and_unlock_achievements(user_id)


async def get_user_score(user_id: int) -> dict:
    """Получить статистику пользователя."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT total_score, games_played, correct_answers
            FROM user_scores
            WHERE user_id = $1
        """, user_id)
        if not row:
            return {"total_score": 0, "games_played": 0, "correct_answers": 0}
        return dict(row)


async def get_leaderboard(limit: int = 10) -> list:
    """Получить топ игроков."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, total_score, games_played, correct_answers
            FROM user_scores
            ORDER BY total_score DESC
            LIMIT $1
        """, limit)
        return [dict(r) for r in rows]


# ============================================================================
# Игра 1: Угадай игру по скриншоту
# ============================================================================

async def init_screenshot_game_table():
    """Создать таблицу для игры с скриншотами."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS screenshot_games (
                game_id TEXT PRIMARY KEY,
                correct_title TEXT NOT NULL,
                screenshot_url TEXT NOT NULL,
                options TEXT[] NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS screenshot_answers (
                user_id BIGINT,
                game_id TEXT,
                answer TEXT,
                is_correct BOOLEAN,
                answered_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (user_id, game_id)
            )
        """)


async def create_screenshot_game(deal) -> Optional[dict]:
    """
    Создать игру "Угадай игру по скриншоту".
    Использует IGDB данные для получения скриншота.
    """
    from igdb import get_game_info
    
    igdb_info = await get_game_info(deal.title)
    if not igdb_info or not igdb_info.get("screenshots"):
        return None
    
    screenshot = random.choice(igdb_info["screenshots"])
    correct_title = deal.title
    
    # Генерируем варианты ответов (нужны похожие игры)
    similar = igdb_info.get("similar_games", [])
    options = [correct_title]
    
    # Добавляем похожие игры как варианты
    for game in similar[:3]:
        if game != correct_title:
            options.append(game)
    
    # Если не хватает вариантов, добавляем случайные
    if len(options) < 4:
        fallback_games = [
            "Dark Souls", "Skyrim", "Witcher 3", "Cyberpunk 2077",
            "GTA V", "Red Dead Redemption 2", "Elden Ring", "Bloodborne"
        ]
        for game in fallback_games:
            if game not in options and game != correct_title:
                options.append(game)
                if len(options) >= 4:
                    break
    
    random.shuffle(options)
    
    game_id = f"screenshot_{deal.deal_id}"
    
    # Сохраняем в БД
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO screenshot_games (game_id, correct_title, screenshot_url, options)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (game_id) DO UPDATE SET
                screenshot_url = $3,
                options = $4
        """, game_id, correct_title, screenshot, options)
    
    return {
        "game_id": game_id,
        "correct_title": correct_title,
        "screenshot_url": screenshot,
        "options": options
    }


async def check_screenshot_answer(user_id: int, game_id: str, answer: str) -> dict:
    """Проверить ответ пользователя."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Получаем правильный ответ
        row = await conn.fetchrow("""
            SELECT correct_title FROM screenshot_games WHERE game_id = $1
        """, game_id)
        
        if not row:
            return {"error": "Игра не найдена"}
        
        correct_title = row["correct_title"]
        is_correct = answer == correct_title
        
        # Атомарная вставка ответа — защита от двойного нажатия
        insert_result = await conn.execute("""
            INSERT INTO screenshot_answers (user_id, game_id, answer, is_correct)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, game_id) DO NOTHING
        """, user_id, game_id, answer, is_correct)

        if insert_result == "INSERT 0 0":
            return {"error": "Ты уже отвечал на этот вопрос!"}
    
    # Начисляем баллы и проверяем достижения
    points = 10 if is_correct else 0
    new_achievements = await add_score(user_id, points, is_correct, reason="screenshot")
    
    # Увеличиваем счётчик правильных ответов для достижений
    if is_correct:
        from achievements import increment_screenshot_correct
        await increment_screenshot_correct(user_id)
        # Проверяем достижения ещё раз после обновления счётчика
        more_achievements = await check_and_unlock_achievements(user_id)
        if more_achievements:
            new_achievements.extend(more_achievements)
    
    return {
        "is_correct": is_correct,
        "correct_title": correct_title,
        "points": points,
        "new_achievements": new_achievements if new_achievements else []
    }


# ============================================================================
# Игра 2: Ежедневный челлендж
# ============================================================================

async def init_daily_challenge_table():
    """Создать таблицу для ежедневных челленджей."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_challenges (
                challenge_date DATE PRIMARY KEY,
                challenge_type TEXT NOT NULL,
                challenge_data JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_challenge_completions (
                user_id BIGINT,
                challenge_date DATE,
                completed_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (user_id, challenge_date)
            )
        """)


async def get_daily_challenge() -> Optional[dict]:
    """Получить челлендж дня."""
    import json
    today = datetime.now(MSK).date()
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT challenge_type, challenge_data
            FROM daily_challenges
            WHERE challenge_date = $1
        """, today)
        
        if row:
            data = row["challenge_data"]
            # Парсим JSON если это строка
            if isinstance(data, str):
                data = json.loads(data)
            
            return {
                "type": row["challenge_type"],
                "data": data
            }
    
    return None


async def create_daily_challenge(challenge_type: str, data: dict):
    """Создать челлендж дня."""
    import json
    today = datetime.now(MSK).date()
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO daily_challenges (challenge_date, challenge_type, challenge_data)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (challenge_date) DO UPDATE SET
                challenge_type = $2,
                challenge_data = $3::jsonb
        """, today, challenge_type, json.dumps(data))


async def complete_daily_challenge(user_id: int) -> dict:
    """Отметить челлендж как выполненный."""
    today = datetime.now(MSK).date()

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Атомарная вставка — если уже выполнен, INSERT вернёт 0 строк
        result = await conn.execute("""
            INSERT INTO daily_challenge_completions (user_id, challenge_date)
            VALUES ($1, $2)
            ON CONFLICT (user_id, challenge_date) DO NOTHING
        """, user_id, today)

        if result == "INSERT 0 0":
            return {"error": "Ты уже выполнил челлендж сегодня!"}
    
    # Начисляем бонусные баллы и проверяем достижения
    new_achievements = await add_score(user_id, 50, True, reason="challenge")
    
    # Увеличиваем счётчик выполненных челленджей
    from achievements import increment_challenges_completed, check_and_unlock_achievements
    await increment_challenges_completed(user_id)
    more_achievements = await check_and_unlock_achievements(user_id)
    if more_achievements:
        new_achievements.extend(more_achievements)
    
    return {
        "success": True,
        "points": 50,
        "new_achievements": new_achievements if new_achievements else []
    }


# ============================================================================
# Инициализация всех таблиц
# ============================================================================

async def init_minigames_db():
    """Инициализировать все таблицы для мини-игр."""
    await init_scores_table()
    await init_screenshot_game_table()
    await init_daily_challenge_table()
    
    # Инициализируем таблицу достижений
    from achievements import init_achievements_table
    await init_achievements_table()
    
    # Инициализируем таблицу призов
    from rewards import init_rewards_table
    await init_rewards_table()
    
    # Инициализируем реферальную систему
    from referral import init_referral_table
    await init_referral_table()
    
    log.info("Таблицы мини-игр, достижений, призов и рефералов инициализированы")
