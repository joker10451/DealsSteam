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


async def add_score(user_id: int, points: int, correct: bool = True):
    """Добавить баллы пользователю."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO user_scores (user_id, total_score, games_played, correct_answers, last_played)
            VALUES ($1, $2, 1, $3, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                total_score = user_scores.total_score + $2,
                games_played = user_scores.games_played + 1,
                correct_answers = user_scores.correct_answers + CASE WHEN $3 THEN 1 ELSE 0 END,
                last_played = NOW()
        """, user_id, points, correct)
    
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
        
        # Проверяем, не отвечал ли уже
        existing = await conn.fetchval("""
            SELECT 1 FROM screenshot_answers
            WHERE user_id = $1 AND game_id = $2
        """, user_id, game_id)
        
        if existing:
            return {"error": "Ты уже отвечал на этот вопрос!"}
        
        # Сохраняем ответ
        await conn.execute("""
            INSERT INTO screenshot_answers (user_id, game_id, answer, is_correct)
            VALUES ($1, $2, $3, $4)
        """, user_id, game_id, answer, is_correct)
    
    # Начисляем баллы и проверяем достижения
    points = 10 if is_correct else 0
    new_achievements = await add_score(user_id, points, is_correct)
    
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
    today = datetime.now(MSK).date()
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT challenge_type, challenge_data
            FROM daily_challenges
            WHERE challenge_date = $1
        """, today)
        
        if row:
            return {
                "type": row["challenge_type"],
                "data": row["challenge_data"]
            }
    
    return None


async def create_daily_challenge(challenge_type: str, data: dict):
    """Создать челлендж дня."""
    today = datetime.now(MSK).date()
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO daily_challenges (challenge_date, challenge_type, challenge_data)
            VALUES ($1, $2, $3)
            ON CONFLICT (challenge_date) DO UPDATE SET
                challenge_type = $2,
                challenge_data = $3
        """, today, challenge_type, data)


async def complete_daily_challenge(user_id: int) -> dict:
    """Отметить челлендж как выполненный."""
    today = datetime.now(MSK).date()
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем, не выполнен ли уже
        existing = await conn.fetchval("""
            SELECT 1 FROM daily_challenge_completions
            WHERE user_id = $1 AND challenge_date = $2
        """, user_id, today)
        
        if existing:
            return {"error": "Ты уже выполнил челлендж сегодня!"}
        
        # Отмечаем как выполненный
        await conn.execute("""
            INSERT INTO daily_challenge_completions (user_id, challenge_date)
            VALUES ($1, $2)
        """, user_id, today)
    
    # Начисляем бонусные баллы и проверяем достижения
    new_achievements = await add_score(user_id, 50, True)
    
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
    
    log.info("Таблицы мини-игр, достижений и призов инициализированы")
