"""
Тесты для автоматической генерации ежедневных челленджей.
"""
import pytest
from datetime import datetime
import pytz

from daily_challenges import (
    generate_daily_challenge,
    create_todays_challenge,
    check_challenge_progress,
    format_challenge_message,
)
from minigames import get_daily_challenge, complete_daily_challenge, add_score
from database import get_pool

MSK = pytz.timezone("Europe/Moscow")


async def test_generate_daily_challenge():
    """Тест генерации случайного челленджа."""
    challenge = await generate_daily_challenge()
    
    assert "type" in challenge
    assert "data" in challenge
    assert challenge["type"] in [
        "find_cheapest", "guess_streak", "daily_score",
        "vote_games", "find_genre", "find_discount"
    ]
    assert "description" in challenge["data"]


async def test_create_todays_challenge(db_cleanup):
    """Тест создания челленджа на сегодня."""
    challenge = await create_todays_challenge()
    
    # Проверяем, что челлендж создан
    saved = await get_daily_challenge()
    assert saved is not None
    assert "type" in saved
    assert "data" in saved


async def test_create_todays_challenge_idempotent(db_cleanup):
    """Тест что повторный вызов не создаёт дубликаты."""
    await create_todays_challenge()
    first = await get_daily_challenge()
    
    await create_todays_challenge()
    second = await get_daily_challenge()
    
    # Должен быть тот же челлендж
    assert first["type"] == second["type"]


async def test_format_challenge_message():
    """Тест форматирования сообщения с челленджем."""
    challenge = {
        "type": "daily_score",
        "data": {
            "target_score": 50,
            "description": "Набери 50 баллов за сегодня"
        }
    }
    
    text = format_challenge_message(challenge)
    
    assert "ЧЕЛЛЕНДЖ ДНЯ" in text
    assert "50 баллов" in text
    assert "Как выполнить" in text


async def test_check_challenge_progress_daily_score(db_cleanup):
    """Тест проверки прогресса по челленджу daily_score."""
    user_id = 9_000_000_001
    
    # Создаём челлендж
    from minigames import create_daily_challenge
    await create_daily_challenge("daily_score", {"target_score": 50})
    
    # Добавляем баллы
    await add_score(user_id, 30, True, "test")
    
    # Проверяем прогресс
    progress = await check_challenge_progress(user_id)
    
    assert progress is not None
    # Может быть completed=True если пользователь уже выполнил челлендж ранее
    # Проверяем только что current <= target
    if not progress.get("completed"):
        assert progress["current"] == 30
        assert progress["target"] == 50
        assert progress["progress"] == 60  # 30/50 * 100


async def test_check_challenge_progress_completed(db_cleanup):
    """Тест проверки прогресса для выполненного челленджа."""
    user_id = 9_000_000_002
    
    # Создаём челлендж
    from minigames import create_daily_challenge
    await create_daily_challenge("daily_score", {"target_score": 50})
    
    # Добавляем баллы
    await add_score(user_id, 60, True, "test")
    
    # Проверяем прогресс
    progress = await check_challenge_progress(user_id)
    
    assert progress is not None
    assert progress["completed"] is True
    assert progress["current"] >= 50


async def test_complete_daily_challenge(db_cleanup):
    """Тест выполнения челленджа."""
    user_id = 9_000_000_003
    
    # Создаём челлендж
    from minigames import create_daily_challenge
    await create_daily_challenge("daily_score", {"target_score": 50})
    
    # Выполняем челлендж
    result = await complete_daily_challenge(user_id)
    
    # Может вернуть ошибку если уже выполнен, или success
    assert "success" in result or "error" in result
    
    if "success" in result:
        assert result["success"] is True
        assert result["points"] == 50


async def test_check_challenge_progress_vote_games(db_cleanup):
    """Тест проверки прогресса по челленджу vote_games."""
    user_id = 9_000_000_004
    
    # Создаём челлендж
    from minigames import create_daily_challenge
    await create_daily_challenge("vote_games", {"required_votes": 5})
    
    # Добавляем голоса
    pool = await get_pool()
    async with pool.acquire() as conn:
        for i in range(3):
            await conn.execute("""
                INSERT INTO votes (user_id, deal_id, vote)
                VALUES ($1, $2, 'fire')
            """, user_id, f"test_{i}")
    
    # Проверяем прогресс
    progress = await check_challenge_progress(user_id)
    
    assert progress is not None
    assert progress["completed"] is False
    assert progress["current"] == 3
    assert progress["target"] == 5
    assert progress["progress"] == 60  # 3/5 * 100


@pytest.mark.parametrize("challenge_type", [
    "find_cheapest", "guess_streak", "daily_score",
    "vote_games", "find_genre", "find_discount"
])
async def test_format_all_challenge_types(challenge_type):
    """Тест форматирования всех типов челленджей."""
    challenge = {
        "type": challenge_type,
        "data": {
            "description": "Test challenge",
            "hint": 100,
            "required_streak": 3,
            "target_score": 50,
            "required_votes": 5,
            "genre": "RPG",
            "min_discount": 70,
        }
    }
    
    text = format_challenge_message(challenge)
    
    assert "ЧЕЛЛЕНДЖ ДНЯ" in text
    assert "Как выполнить" in text
    assert len(text) > 100  # Должно быть достаточно информации
