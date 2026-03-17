"""
Общие фикстуры для тестов game-deals-bot.
"""
import sys
import os

# Загружаем .env из корня проекта
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

# Добавляем корень проекта в sys.path, чтобы импорты работали без установки пакета
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import asyncpg

from parsers.steam import Deal
from hidden_gems import GemDeal

TEST_PREFIX = "test_pytest_"


@pytest.fixture(scope="session", autouse=True)
async def reset_db_pool():
    """Сбрасывает глобальный пул database._pool перед сессией и закрывает его после.
    После всех тестов удаляет все тестовые записи из продакшн БД.
    """
    import database
    database._pool = None
    yield
    # Финальная очистка всех тестовых записей
    if database._pool is not None:
        pool = database._pool
        await pool.execute("DELETE FROM posted_deals WHERE deal_id LIKE 'test_%'")
        await pool.execute("DELETE FROM votes WHERE deal_id LIKE 'test_%'")
        await pool.execute("DELETE FROM price_game WHERE deal_id LIKE 'test_%'")
        await pool.execute("DELETE FROM wishlist WHERE user_id >= 9000000000")
        await pool.execute("DELETE FROM steam_users WHERE user_id >= 9000000000")
        await pool.execute("DELETE FROM steam_library WHERE user_id >= 9000000000")
        await pool.close()
        database._pool = None


@pytest.fixture
def mock_deal() -> Deal:
    """Минимальный валидный Deal для использования в тестах."""
    return Deal(
        deal_id="steam_12345",
        title="Test Game",
        store="Steam",
        old_price="999 ₽",
        new_price="499 ₽",
        discount=50,
        link="https://store.steampowered.com/app/12345/",
        image_url="https://cdn.akamai.steamstatic.com/steam/apps/12345/header.jpg",
        is_free=False,
        genres=["Action", "Adventure"],
        sale_end=None,
    )


@pytest.fixture
def mock_gem_deal() -> GemDeal:
    """Минимальный валидный GemDeal."""
    return GemDeal(
        appid="67890",
        title="Hidden Gem Game",
        old_price="599 ₽",
        new_price="119 ₽",
        discount=80,
        score=92,
        reviews=350,
        image_url="https://cdn.akamai.steamstatic.com/steam/apps/67890/header.jpg",
        link="https://store.steampowered.com/app/67890/",
    )


@pytest.fixture
async def db_cleanup():
    """Фикстура для очистки тестовых данных с префиксом test_pytest_ после теста.

    Использует database.get_pool() для получения пула соединений.
    Удаляет все записи с тестовым префиксом из всех таблиц.
    """
    import database

    yield  # тест выполняется здесь

    pool = await database.get_pool()
    # Удаляем тестовые записи из всех таблиц
    await pool.execute(
        "DELETE FROM price_game WHERE deal_id LIKE $1", f"{TEST_PREFIX}%"
    )
    await pool.execute(
        "DELETE FROM votes WHERE deal_id LIKE $1", f"{TEST_PREFIX}%"
    )
    await pool.execute(
        "DELETE FROM votes WHERE user_id >= 9000000000"
    )
    await pool.execute(
        "DELETE FROM posted_deals WHERE deal_id LIKE $1", f"{TEST_PREFIX}%"
    )
    # Для wishlist используем user_id в диапазоне тестовых значений
    # (тестовые user_id начинаются с 9_000_000_000)
    await pool.execute(
        "DELETE FROM wishlist WHERE user_id >= 9000000000"
    )
