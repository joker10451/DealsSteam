"""
Тесты для database.py.

Unit-тесты (3.1): init_db создаёт все 4 таблицы, cleanup_old_records удаляет старые записи.
Property-тесты (3.2): Properties 7–12 через hypothesis.

Стратегия изоляции:
- Все тестовые deal_id имеют префикс "test_pytest_"
- Тестовые user_id >= 9_000_000_000
- Фикстура db_cleanup удаляет все тестовые данные после каждого теста
"""
import uuid
import pytest
import asyncpg
from datetime import datetime, timedelta, timezone

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

import database
from config import DB_CLEANUP_DAYS

TEST_PREFIX = "test_pytest_"
TEST_USER_BASE = 9_000_000_000  # тестовые user_id не пересекаются с реальными


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    """Генерирует уникальный тестовый deal_id с префиксом."""
    return f"{TEST_PREFIX}{uuid.uuid4().hex[:12]}"


def _test_user_id() -> int:
    """Генерирует уникальный тестовый user_id."""
    return TEST_USER_BASE + int(uuid.uuid4().hex[:8], 16) % 1_000_000_000


async def _delete_deal(deal_id: str):
    """Удаляет тестовую запись из всех таблиц."""
    pool = await database.get_pool()
    await pool.execute("DELETE FROM price_game WHERE deal_id = $1", deal_id)
    await pool.execute("DELETE FROM votes WHERE deal_id = $1", deal_id)
    await pool.execute("DELETE FROM posted_deals WHERE deal_id = $1", deal_id)


async def _delete_user_wishlist(user_id: int):
    pool = await database.get_pool()
    await pool.execute("DELETE FROM wishlist WHERE user_id = $1", user_id)


# ---------------------------------------------------------------------------
# Task 3.1 — Unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_db_creates_all_tables():
    """WHEN init_db() вызывается, THE Database SHALL создать все 4 таблицы.

    Validates: Requirements 2.1
    """
    await database.init_db()
    pool = await database.get_pool()
    expected = {"posted_deals", "wishlist", "votes", "price_game"}
    rows = await pool.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = ANY($1::text[])",
        list(expected),
    )
    found = {r["table_name"] for r in rows}
    assert found == expected, f"Отсутствуют таблицы: {expected - found}"


@pytest.mark.asyncio
async def test_cleanup_old_records():
    """WHEN cleanup_old_records() вызывается, THE Database SHALL удалить записи старше DB_CLEANUP_DAYS.

    Validates: Requirements 2.13
    """
    deal_id = _uid()
    pool = await database.get_pool()

    # Вставляем запись с датой старше порога
    old_ts = datetime.now(timezone.utc) - timedelta(days=DB_CLEANUP_DAYS + 1)
    await pool.execute(
        "INSERT INTO posted_deals (deal_id, title, store, discount, posted_at) "
        "VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
        deal_id, "Old Test Game", "Steam", 50, old_ts,
    )

    try:
        deleted = await database.cleanup_old_records()
        assert deleted >= 1, f"Должна быть удалена хотя бы 1 запись, удалено: {deleted}"

        row = await pool.fetchrow(
            "SELECT 1 FROM posted_deals WHERE deal_id = $1", deal_id
        )
        assert row is None, "Старая запись должна быть удалена"
    finally:
        # На случай если cleanup не удалил
        await pool.execute("DELETE FROM posted_deals WHERE deal_id = $1", deal_id)


@pytest.mark.asyncio
async def test_mark_as_posted_and_is_already_posted():
    """WHEN mark_as_posted вызывается, is_already_posted должен вернуть True.

    Validates: Requirements 2.2
    """
    deal_id = _uid()
    try:
        assert not await database.is_already_posted(deal_id)
        await database.mark_as_posted(deal_id, "Test Game", "Steam", 60)
        assert await database.is_already_posted(deal_id)
    finally:
        await _delete_deal(deal_id)


@pytest.mark.asyncio
async def test_mark_as_posted_idempotent():
    """WHEN mark_as_posted вызывается дважды, не должно быть исключения.

    Validates: Requirements 2.3
    """
    deal_id = _uid()
    try:
        await database.mark_as_posted(deal_id, "Test Game", "Steam", 60)
        await database.mark_as_posted(deal_id, "Test Game", "Steam", 60)  # не должно бросить
        assert await database.is_already_posted(deal_id)
    finally:
        await _delete_deal(deal_id)


@pytest.mark.asyncio
async def test_wishlist_add_remove():
    """WHEN wishlist_add/remove вызываются, должны работать корректно.

    Validates: Requirements 2.4, 2.5, 2.6, 2.7
    """
    user_id = _test_user_id()
    query = "cyberpunk"
    try:
        assert await database.wishlist_add(user_id, query) is True
        assert await database.wishlist_add(user_id, query) is False  # дубликат
        assert await database.wishlist_remove(user_id, query) is True
        assert await database.wishlist_remove(user_id, query) is False  # уже удалено
    finally:
        await _delete_user_wishlist(user_id)


@pytest.mark.asyncio
async def test_vote_round_trip():
    """WHEN add_vote вызывается, get_votes должен вернуть корректные счётчики.

    Validates: Requirements 2.9, 2.10, 2.11
    """
    deal_id = _uid()
    user_id = _test_user_id()
    try:
        assert await database.add_vote(deal_id, user_id, "fire") is True
        assert await database.add_vote(deal_id, user_id, "fire") is False  # дубликат
        votes = await database.get_votes(deal_id)
        assert votes["fire"] == 1
        assert votes["poop"] == 0
    finally:
        await _delete_deal(deal_id)


@pytest.mark.asyncio
async def test_price_game_round_trip():
    """WHEN save_price_game/get_price_game вызываются, должен вернуться тот же price.

    Validates: Requirements 2.12
    """
    deal_id = _uid()
    price = 1499
    try:
        await database.save_price_game(deal_id, price)
        result = await database.get_price_game(deal_id)
        assert result == price
    finally:
        await _delete_deal(deal_id)


# ---------------------------------------------------------------------------
# Task 3.2 — Property-based tests (Properties 7–12)
# Hypothesis не поддерживает async напрямую — используем asyncio.run() внутри синхронных тестов.
# max_examples=5 т.к. каждый пример делает реальные сетевые запросы к Supabase.
# ---------------------------------------------------------------------------

import asyncio
import concurrent.futures


def _run(coro):
    """Запускает корутину в отдельном потоке с собственным event loop.
    Создаёт временный пул asyncpg и закрывает его после выполнения."""
    async def _wrapper():
        import database as _db
        # Создаём временный пул специально для этого потока
        from config import DATABASE_URL
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        old_pool = _db._pool
        _db._pool = pool
        try:
            return await coro
        finally:
            _db._pool = old_pool
            await pool.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, _wrapper())
        return future.result(timeout=30)


# Feature: bot-tests-and-docs, Property 7: mark_as_posted round-trip
@given(
    title=st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
    discount=st.integers(min_value=0, max_value=100),
)
@settings(max_examples=5, deadline=None)
def test_property_mark_as_posted_round_trip(title, discount):
    """Property 7: для любого deal_id mark_as_posted → is_already_posted == True."""
    async def _inner():
        deal_id = _uid()
        try:
            await database.mark_as_posted(deal_id, title, "Steam", discount)
            assert await database.is_already_posted(deal_id)
        finally:
            await _delete_deal(deal_id)
    _run(_inner())


# Feature: bot-tests-and-docs, Property 8: mark_as_posted idempotence
@given(discount=st.integers(min_value=0, max_value=100))
@settings(max_examples=5, deadline=None)
def test_property_mark_as_posted_idempotence(discount):
    """Property 8: двойной вызов mark_as_posted не бросает исключение."""
    async def _inner():
        deal_id = _uid()
        try:
            await database.mark_as_posted(deal_id, "Game", "Steam", discount)
            await database.mark_as_posted(deal_id, "Game", "Steam", discount)
            assert await database.is_already_posted(deal_id)
        finally:
            await _delete_deal(deal_id)
    _run(_inner())


# Feature: bot-tests-and-docs, Property 9: wishlist add/remove round-trip
@given(query=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Ll",))))
@settings(max_examples=5, deadline=None)
def test_property_wishlist_add_remove(query):
    """Property 9: add→add(dup)→remove→remove(dup) возвращают True/False/True/False."""
    async def _inner():
        user_id = _test_user_id()
        try:
            assert await database.wishlist_add(user_id, query) is True
            assert await database.wishlist_add(user_id, query) is False
            assert await database.wishlist_remove(user_id, query) is True
            assert await database.wishlist_remove(user_id, query) is False
        finally:
            await _delete_user_wishlist(user_id)
    _run(_inner())


# Feature: bot-tests-and-docs, Property 10: wishlist substring matching
@given(base=st.text(min_size=3, max_size=15, alphabet=st.characters(whitelist_categories=("Ll",))))
@settings(max_examples=5, deadline=None)
def test_property_wishlist_matching(base):
    """Property 10: get_wishlist_matches возвращает user_id чей query является подстрокой title."""
    async def _inner():
        user_id = _test_user_id()
        query = base
        title = f"super {base} adventure"
        try:
            await database.wishlist_add(user_id, query)
            matches = await database.get_wishlist_matches(title)
            assert user_id in matches
        finally:
            await _delete_user_wishlist(user_id)
    _run(_inner())


# Feature: bot-tests-and-docs, Property 11: vote round-trip and idempotence
@given(vote=st.sampled_from(["fire", "poop"]))
@settings(max_examples=5, deadline=None)
def test_property_vote_round_trip(vote):
    """Property 11: первый add_vote → True, повторный → False, get_votes отражает счётчик."""
    async def _inner():
        deal_id = _uid()
        user_id = _test_user_id()
        try:
            assert await database.add_vote(deal_id, user_id, vote) is True
            assert await database.add_vote(deal_id, user_id, vote) is False
            votes = await database.get_votes(deal_id)
            assert votes[vote] == 1
            other = "poop" if vote == "fire" else "fire"
            assert votes[other] == 0
        finally:
            await _delete_deal(deal_id)
    _run(_inner())


# Feature: bot-tests-and-docs, Property 12: price game round-trip
@given(price=st.integers(min_value=1, max_value=100_000))
@settings(max_examples=5, deadline=None)
def test_property_price_game_round_trip(price):
    """Property 12: save_price_game → get_price_game возвращает ту же цену."""
    async def _inner():
        deal_id = _uid()
        try:
            await database.save_price_game(deal_id, price)
            assert await database.get_price_game(deal_id) == price
        finally:
            await _delete_deal(deal_id)
    _run(_inner())
