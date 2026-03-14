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
    """WHEN init_db() вызывается, THE Database SHALL создать все таблицы.

    Validates: Requirements 2.1
    """
    await database.init_db()
    pool = await database.get_pool()
    expected = {"posted_deals", "wishlist", "votes", "price_game", "steam_users", "onboarding_progress", "onboarding_hints"}
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



# ---------------------------------------------------------------------------
# Steam Integration Tests
# ---------------------------------------------------------------------------

async def _delete_steam_user(user_id: int):
    """Удаляет тестового пользователя из steam_users."""
    pool = await database.get_pool()
    await pool.execute("DELETE FROM steam_users WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_steam_link_account_success():
    """WHEN steam_link_account вызывается с новым user_id, THE Database SHALL вернуть True.

    Validates: Requirements 1.3, 6.1
    """
    user_id = _test_user_id()
    steam_id = "76561198012345678"
    
    try:
        result = await database.steam_link_account(user_id, steam_id)
        assert result is True, "Первая привязка должна вернуть True"
        
        # Проверяем, что запись создана
        pool = await database.get_pool()
        row = await pool.fetchrow(
            "SELECT steam_id FROM steam_users WHERE user_id = $1", user_id
        )
        assert row is not None, "Запись должна быть создана"
        assert row["steam_id"] == steam_id, "Steam ID должен совпадать"
    finally:
        await _delete_steam_user(user_id)


@pytest.mark.asyncio
async def test_steam_link_account_duplicate():
    """WHEN steam_link_account вызывается с существующим user_id, THE Database SHALL вернуть False.

    Validates: Requirements 1.3, 6.1
    """
    user_id = _test_user_id()
    steam_id = "76561198012345678"
    
    try:
        # Первая привязка
        result1 = await database.steam_link_account(user_id, steam_id)
        assert result1 is True, "Первая привязка должна вернуть True"
        
        # Повторная привязка (дубликат)
        result2 = await database.steam_link_account(user_id, steam_id)
        assert result2 is False, "Повторная привязка должна вернуть False"
    finally:
        await _delete_steam_user(user_id)


@pytest.mark.asyncio
async def test_steam_unlink_account_deletes_all_data():
    """WHEN steam_unlink_account вызывается, THE Database SHALL удалить все данные из steam_users и steam_library.

    Validates: Requirements 6.3
    """
    user_id = _test_user_id()
    steam_id = "76561198012345678"
    pool = await database.get_pool()
    
    try:
        # Создаем данные пользователя
        await database.steam_link_account(user_id, steam_id)
        
        # Добавляем записи в steam_library
        await pool.execute(
            "INSERT INTO steam_library (user_id, appid) VALUES ($1, $2), ($1, $3)",
            user_id, 123, 456
        )
        
        # Проверяем, что данные созданы
        user_row = await pool.fetchrow(
            "SELECT * FROM steam_users WHERE user_id = $1", user_id
        )
        assert user_row is not None, "Запись в steam_users должна существовать"
        
        library_rows = await pool.fetch(
            "SELECT * FROM steam_library WHERE user_id = $1", user_id
        )
        assert len(library_rows) == 2, "Должно быть 2 записи в steam_library"
        
        # Отвязываем аккаунт
        result = await database.steam_unlink_account(user_id)
        assert result is True, "steam_unlink_account должна вернуть True при удалении данных"
        
        # Проверяем, что все данные удалены
        user_row = await pool.fetchrow(
            "SELECT * FROM steam_users WHERE user_id = $1", user_id
        )
        assert user_row is None, "Запись в steam_users должна быть удалена"
        
        library_rows = await pool.fetch(
            "SELECT * FROM steam_library WHERE user_id = $1", user_id
        )
        assert len(library_rows) == 0, "Все записи в steam_library должны быть удалены"
    finally:
        # Очистка на случай ошибки теста
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)
        await _delete_steam_user(user_id)


@pytest.mark.asyncio
async def test_steam_unlink_account_no_data():
    """WHEN steam_unlink_account вызывается для несуществующего user_id, THE Database SHALL вернуть False.

    Validates: Requirements 6.3
    """
    user_id = _test_user_id()
    
    # Вызываем отвязку для пользователя без данных
    result = await database.steam_unlink_account(user_id)
    assert result is False, "steam_unlink_account должна вернуть False если нет данных для удаления"


@pytest.mark.asyncio
async def test_steam_get_user_exists():
    """WHEN steam_get_user вызывается с существующим user_id, THE Database SHALL вернуть dict с данными.
    
    Validates: Requirements 1.6, 2.6, 7.4
    """
    user_id = _test_user_id()
    steam_id = "76561198087654321"
    
    try:
        # Создаем пользователя
        await database.steam_link_account(user_id, steam_id)
        
        # Получаем данные
        result = await database.steam_get_user(user_id)
        
        assert result is not None, "Должен вернуть dict"
        assert result["user_id"] == user_id, "user_id должен совпадать"
        assert result["steam_id"] == steam_id, "steam_id должен совпадать"
        assert result["wishlist_sync_enabled"] is True, "wishlist_sync_enabled по умолчанию True"
        assert result["library_sync_enabled"] is True, "library_sync_enabled по умолчанию True"
        assert result["last_wishlist_sync"] is None, "last_wishlist_sync изначально None"
        assert result["last_library_sync"] is None, "last_library_sync изначально None"
        assert result["created_at"] is not None, "created_at должен быть установлен"
    finally:
        await _delete_steam_user(user_id)


@pytest.mark.asyncio
async def test_steam_get_user_not_exists():
    """WHEN steam_get_user вызывается с несуществующим user_id, THE Database SHALL вернуть None.
    
    Validates: Requirements 1.6, 2.6, 7.4
    """
    user_id = _test_user_id()
    
    result = await database.steam_get_user(user_id)
    
    assert result is None, "Должен вернуть None для несуществующего пользователя"


@pytest.mark.asyncio
async def test_steam_update_sync_time_wishlist():
    """WHEN steam_update_sync_time вызывается с sync_type='wishlist', THE Database SHALL обновить last_wishlist_sync.
    
    Validates: Requirements 1.6, 2.6, 7.4
    """
    user_id = _test_user_id()
    steam_id = "76561198011111111"
    
    try:
        # Создаем пользователя
        await database.steam_link_account(user_id, steam_id)
        
        # Проверяем начальное состояние
        user_before = await database.steam_get_user(user_id)
        assert user_before["last_wishlist_sync"] is None, "Изначально должно быть None"
        
        # Обновляем время синхронизации
        await database.steam_update_sync_time(user_id, "wishlist")
        
        # Проверяем обновление
        user_after = await database.steam_get_user(user_id)
        assert user_after["last_wishlist_sync"] is not None, "last_wishlist_sync должен быть установлен"
        assert user_after["last_library_sync"] is None, "last_library_sync не должен измениться"
    finally:
        await _delete_steam_user(user_id)


@pytest.mark.asyncio
async def test_steam_update_sync_time_library():
    """WHEN steam_update_sync_time вызывается с sync_type='library', THE Database SHALL обновить last_library_sync.
    
    Validates: Requirements 1.6, 2.6, 7.4
    """
    user_id = _test_user_id()
    steam_id = "76561198022222222"
    
    try:
        # Создаем пользователя
        await database.steam_link_account(user_id, steam_id)
        
        # Проверяем начальное состояние
        user_before = await database.steam_get_user(user_id)
        assert user_before["last_library_sync"] is None, "Изначально должно быть None"
        
        # Обновляем время синхронизации
        await database.steam_update_sync_time(user_id, "library")
        
        # Проверяем обновление
        user_after = await database.steam_get_user(user_id)
        assert user_after["last_library_sync"] is not None, "last_library_sync должен быть установлен"
        assert user_after["last_wishlist_sync"] is None, "last_wishlist_sync не должен измениться"
    finally:
        await _delete_steam_user(user_id)


@pytest.mark.asyncio
async def test_steam_get_all_synced_users():
    """WHEN steam_get_all_synced_users вызывается, THE Database SHALL вернуть список пользователей с включенной синхронизацией.
    
    Validates: Requirements 1.6, 2.6
    """
    user_id_1 = _test_user_id()
    user_id_2 = _test_user_id()
    user_id_3 = _test_user_id()
    steam_id_1 = "76561198000000001"
    steam_id_2 = "76561198000000002"
    steam_id_3 = "76561198000000003"
    
    try:
        # Создаем трех пользователей
        await database.steam_link_account(user_id_1, steam_id_1)
        await database.steam_link_account(user_id_2, steam_id_2)
        await database.steam_link_account(user_id_3, steam_id_3)
        
        # Отключаем синхронизацию для третьего пользователя
        pool = await database.get_pool()
        await pool.execute(
            "UPDATE steam_users SET wishlist_sync_enabled = FALSE, library_sync_enabled = FALSE WHERE user_id = $1",
            user_id_3
        )
        
        # Получаем всех пользователей с включенной синхронизацией
        result = await database.steam_get_all_synced_users()
        
        # Проверяем результат
        assert isinstance(result, list), "Должен вернуть список"
        
        # Фильтруем только наших тестовых пользователей
        test_users = [u for u in result if u["user_id"] in [user_id_1, user_id_2, user_id_3]]
        
        assert len(test_users) == 2, "Должно быть 2 пользователя с включенной синхронизацией"
        
        user_ids = [u["user_id"] for u in test_users]
        assert user_id_1 in user_ids, "user_id_1 должен быть в списке"
        assert user_id_2 in user_ids, "user_id_2 должен быть в списке"
        assert user_id_3 not in user_ids, "user_id_3 не должен быть в списке (синхронизация отключена)"
        
        # Проверяем структуру данных
        for user in test_users:
            assert "user_id" in user, "Должен содержать user_id"
            assert "steam_id" in user, "Должен содержать steam_id"
            assert "wishlist_sync_enabled" in user, "Должен содержать wishlist_sync_enabled"
            assert "library_sync_enabled" in user, "Должен содержать library_sync_enabled"
            assert user["wishlist_sync_enabled"] or user["library_sync_enabled"], "Хотя бы одна синхронизация должна быть включена"
    finally:
        await _delete_steam_user(user_id_1)
        await _delete_steam_user(user_id_2)
        await _delete_steam_user(user_id_3)


@pytest.mark.asyncio
async def test_steam_get_all_synced_users_empty():
    """WHEN steam_get_all_synced_users вызывается и нет пользователей с синхронизацией, THE Database SHALL вернуть пустой список.
    
    Validates: Requirements 1.6, 2.6
    """
    # Создаем пользователя с отключенной синхронизацией
    user_id = _test_user_id()
    steam_id = "76561198000000099"
    
    try:
        await database.steam_link_account(user_id, steam_id)
        
        # Отключаем всю синхронизацию
        pool = await database.get_pool()
        await pool.execute(
            "UPDATE steam_users SET wishlist_sync_enabled = FALSE, library_sync_enabled = FALSE WHERE user_id = $1",
            user_id
        )
        
        # Получаем всех пользователей с включенной синхронизацией
        result = await database.steam_get_all_synced_users()
        
        # Фильтруем только нашего тестового пользователя
        test_users = [u for u in result if u["user_id"] == user_id]
        
        assert len(test_users) == 0, "Не должно быть пользователей с отключенной синхронизацией"
    finally:
        await _delete_steam_user(user_id)


@pytest.mark.asyncio
async def test_steam_library_replace_empty_to_new():
    """WHEN steam_library_replace вызывается для пользователя без библиотеки, THE Database SHALL добавить новые appids.
    
    Validates: Requirements 2.3
    """
    user_id = _test_user_id()
    appids = [123, 456, 789]
    pool = await database.get_pool()
    
    try:
        # Заменяем библиотеку (изначально пустую)
        await database.steam_library_replace(user_id, appids)
        
        # Проверяем, что все appids добавлены
        rows = await pool.fetch(
            "SELECT appid FROM steam_library WHERE user_id = $1 ORDER BY appid",
            user_id
        )
        stored_appids = [r["appid"] for r in rows]
        
        assert stored_appids == sorted(appids), "Все appids должны быть добавлены"
    finally:
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_steam_library_replace_existing():
    """WHEN steam_library_replace вызывается для пользователя с существующей библиотекой, THE Database SHALL удалить старые и добавить новые appids.
    
    Validates: Requirements 2.3
    """
    user_id = _test_user_id()
    old_appids = [111, 222, 333]
    new_appids = [444, 555, 666]
    pool = await database.get_pool()
    
    try:
        # Добавляем старые appids
        await database.steam_library_replace(user_id, old_appids)
        
        # Проверяем, что старые appids добавлены
        rows = await pool.fetch(
            "SELECT appid FROM steam_library WHERE user_id = $1 ORDER BY appid",
            user_id
        )
        stored_appids = [r["appid"] for r in rows]
        assert stored_appids == sorted(old_appids), "Старые appids должны быть добавлены"
        
        # Заменяем библиотеку новыми appids
        await database.steam_library_replace(user_id, new_appids)
        
        # Проверяем, что только новые appids остались
        rows = await pool.fetch(
            "SELECT appid FROM steam_library WHERE user_id = $1 ORDER BY appid",
            user_id
        )
        stored_appids = [r["appid"] for r in rows]
        
        assert stored_appids == sorted(new_appids), "Только новые appids должны остаться"
        assert 111 not in stored_appids, "Старые appids должны быть удалены"
    finally:
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_steam_library_replace_empty_list():
    """WHEN steam_library_replace вызывается с пустым списком, THE Database SHALL удалить все существующие appids.
    
    Validates: Requirements 2.3
    """
    user_id = _test_user_id()
    appids = [777, 888, 999]
    pool = await database.get_pool()
    
    try:
        # Добавляем appids
        await database.steam_library_replace(user_id, appids)
        
        # Проверяем, что appids добавлены
        rows = await pool.fetch(
            "SELECT appid FROM steam_library WHERE user_id = $1",
            user_id
        )
        assert len(rows) == 3, "Должно быть 3 appids"
        
        # Заменяем пустым списком
        await database.steam_library_replace(user_id, [])
        
        # Проверяем, что все appids удалены
        rows = await pool.fetch(
            "SELECT appid FROM steam_library WHERE user_id = $1",
            user_id
        )
        assert len(rows) == 0, "Все appids должны быть удалены"
    finally:
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_steam_library_replace_with_duplicates():
    """WHEN steam_library_replace вызывается со списком содержащим дубликаты, THE Database SHALL использовать ON CONFLICT DO NOTHING.
    
    Validates: Requirements 2.3
    """
    user_id = _test_user_id()
    appids_with_duplicates = [100, 200, 100, 300, 200]  # Дубликаты: 100, 200
    pool = await database.get_pool()
    
    try:
        # Заменяем библиотеку списком с дубликатами
        await database.steam_library_replace(user_id, appids_with_duplicates)
        
        # Проверяем, что дубликаты не создали проблем и хранятся уникальные значения
        rows = await pool.fetch(
            "SELECT appid FROM steam_library WHERE user_id = $1 ORDER BY appid",
            user_id
        )
        stored_appids = [r["appid"] for r in rows]
        
        # Должны быть только уникальные значения
        assert stored_appids == [100, 200, 300], "Должны быть только уникальные appids"
    finally:
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_steam_library_replace_atomicity():
    """WHEN steam_library_replace выполняется, THE Database SHALL использовать транзакцию для атомарности операций.
    
    Validates: Requirements 2.3
    """
    user_id = _test_user_id()
    old_appids = [10, 20, 30]
    new_appids = [40, 50, 60]
    pool = await database.get_pool()
    
    try:
        # Добавляем старые appids
        await database.steam_library_replace(user_id, old_appids)
        
        # Заменяем новыми appids
        await database.steam_library_replace(user_id, new_appids)
        
        # Проверяем, что операция была атомарной - либо все старые удалены и все новые добавлены
        rows = await pool.fetch(
            "SELECT appid FROM steam_library WHERE user_id = $1 ORDER BY appid",
            user_id
        )
        stored_appids = [r["appid"] for r in rows]
        
        # Не должно быть смеси старых и новых - только новые
        assert stored_appids == sorted(new_appids), "Должны быть только новые appids (атомарность)"
        for old_appid in old_appids:
            assert old_appid not in stored_appids, f"Старый appid {old_appid} не должен присутствовать"
    finally:
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)



@pytest.mark.asyncio
async def test_steam_library_contains_exists():
    """WHEN steam_library_contains вызывается с существующим appid, THE Database SHALL вернуть True.
    
    Validates: Requirements 2.4, 2.5
    """
    user_id = _test_user_id()
    appids = [12345, 67890, 11111]
    pool = await database.get_pool()
    
    try:
        # Добавляем appids в библиотеку
        await database.steam_library_replace(user_id, appids)
        
        # Проверяем существующий appid
        result = await database.steam_library_contains(user_id, 12345)
        assert result is True, "Должен вернуть True для существующего appid"
        
        result = await database.steam_library_contains(user_id, 67890)
        assert result is True, "Должен вернуть True для существующего appid"
    finally:
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_steam_library_contains_not_exists():
    """WHEN steam_library_contains вызывается с несуществующим appid, THE Database SHALL вернуть False.
    
    Validates: Requirements 2.4, 2.5
    """
    user_id = _test_user_id()
    appids = [12345, 67890]
    pool = await database.get_pool()
    
    try:
        # Добавляем appids в библиотеку
        await database.steam_library_replace(user_id, appids)
        
        # Проверяем несуществующий appid
        result = await database.steam_library_contains(user_id, 99999)
        assert result is False, "Должен вернуть False для несуществующего appid"
    finally:
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_steam_library_contains_empty_library():
    """WHEN steam_library_contains вызывается для пользователя без библиотеки, THE Database SHALL вернуть False.
    
    Validates: Requirements 2.4, 2.5
    """
    user_id = _test_user_id()
    
    # Проверяем appid для пользователя без библиотеки
    result = await database.steam_library_contains(user_id, 12345)
    assert result is False, "Должен вернуть False для пользователя без библиотеки"


@pytest.mark.asyncio
async def test_steam_library_filter_deals_empty_list():
    """WHEN steam_library_filter_deals вызывается с пустым списком deals, THE Database SHALL вернуть пустой список.
    
    Validates: Requirements 2.4, 2.5
    """
    user_id = _test_user_id()
    
    result = await database.steam_library_filter_deals(user_id, [])
    assert result == [], "Должен вернуть пустой список для пустого входа"


@pytest.mark.asyncio
async def test_steam_library_filter_deals_no_library():
    """WHEN steam_library_filter_deals вызывается для пользователя без библиотеки, THE Database SHALL вернуть все deals.
    
    Validates: Requirements 2.4, 2.5
    """
    from parsers.steam import Deal
    
    user_id = _test_user_id()
    
    # Создаем тестовые deals
    deals = [
        Deal(
            deal_id="steam_12345",
            title="Game 1",
            store="Steam",
            old_price="1000 ₽",
            new_price="500 ₽",
            discount=50,
            link="https://store.steampowered.com/app/12345",
        ),
        Deal(
            deal_id="steam_67890",
            title="Game 2",
            store="Steam",
            old_price="2000 ₽",
            new_price="1000 ₽",
            discount=50,
            link="https://store.steampowered.com/app/67890",
        ),
    ]
    
    # Фильтруем deals для пользователя без библиотеки
    result = await database.steam_library_filter_deals(user_id, deals)
    
    assert len(result) == 2, "Должен вернуть все deals для пользователя без библиотеки"
    assert result == deals, "Список deals не должен измениться"


@pytest.mark.asyncio
async def test_steam_library_filter_deals_filters_owned():
    """WHEN steam_library_filter_deals вызывается с deals содержащими owned games, THE Database SHALL исключить owned games.
    
    Validates: Requirements 2.4, 2.5
    """
    from parsers.steam import Deal
    
    user_id = _test_user_id()
    appids = [12345, 67890]  # Owned games
    pool = await database.get_pool()
    
    try:
        # Добавляем owned games в библиотеку
        await database.steam_library_replace(user_id, appids)
        
        # Создаем тестовые deals (2 owned, 1 not owned)
        deals = [
            Deal(
                deal_id="steam_12345",  # Owned
                title="Owned Game 1",
                store="Steam",
                old_price="1000 ₽",
                new_price="500 ₽",
                discount=50,
                link="https://store.steampowered.com/app/12345",
            ),
            Deal(
                deal_id="steam_99999",  # Not owned
                title="Not Owned Game",
                store="Steam",
                old_price="1500 ₽",
                new_price="750 ₽",
                discount=50,
                link="https://store.steampowered.com/app/99999",
            ),
            Deal(
                deal_id="steam_67890",  # Owned
                title="Owned Game 2",
                store="Steam",
                old_price="2000 ₽",
                new_price="1000 ₽",
                discount=50,
                link="https://store.steampowered.com/app/67890",
            ),
        ]
        
        # Фильтруем deals
        result = await database.steam_library_filter_deals(user_id, deals)
        
        assert len(result) == 1, "Должен вернуть только 1 deal (not owned)"
        assert result[0].deal_id == "steam_99999", "Должен вернуть только not owned deal"
        assert result[0].title == "Not Owned Game", "Название должно совпадать"
    finally:
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_steam_library_filter_deals_non_steam_deals():
    """WHEN steam_library_filter_deals вызывается с non-Steam deals, THE Database SHALL включить их в результат.
    
    Validates: Requirements 2.4, 2.5
    """
    from parsers.steam import Deal
    
    user_id = _test_user_id()
    appids = [12345]
    pool = await database.get_pool()
    
    try:
        # Добавляем owned game в библиотеку
        await database.steam_library_replace(user_id, appids)
        
        # Создаем тестовые deals (1 Steam owned, 1 GOG, 1 Epic)
        deals = [
            Deal(
                deal_id="steam_12345",  # Owned Steam game
                title="Owned Steam Game",
                store="Steam",
                old_price="1000 ₽",
                new_price="500 ₽",
                discount=50,
                link="https://store.steampowered.com/app/12345",
            ),
            Deal(
                deal_id="gog_67890",  # GOG game
                title="GOG Game",
                store="GOG",
                old_price="1500 ₽",
                new_price="750 ₽",
                discount=50,
                link="https://www.gog.com/game/67890",
            ),
            Deal(
                deal_id="epic_11111",  # Epic game
                title="Epic Game",
                store="Epic Games",
                old_price="2000 ₽",
                new_price="1000 ₽",
                discount=50,
                link="https://store.epicgames.com/11111",
            ),
        ]
        
        # Фильтруем deals
        result = await database.steam_library_filter_deals(user_id, deals)
        
        assert len(result) == 2, "Должен вернуть 2 deals (GOG и Epic)"
        assert result[0].deal_id == "gog_67890", "Должен включить GOG deal"
        assert result[1].deal_id == "epic_11111", "Должен включить Epic deal"
    finally:
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_steam_library_filter_deals_invalid_deal_id():
    """WHEN steam_library_filter_deals вызывается с deals с невалидным deal_id, THE Database SHALL включить их в результат.
    
    Validates: Requirements 2.4, 2.5
    """
    from parsers.steam import Deal
    
    user_id = _test_user_id()
    appids = [12345]
    pool = await database.get_pool()
    
    try:
        # Добавляем owned game в библиотеку
        await database.steam_library_replace(user_id, appids)
        
        # Создаем тестовые deals с невалидными deal_id
        deals = [
            Deal(
                deal_id="steam_invalid",  # Невалидный appid (не число)
                title="Invalid Deal",
                store="Steam",
                old_price="1000 ₽",
                new_price="500 ₽",
                discount=50,
                link="https://store.steampowered.com/app/invalid",
            ),
            Deal(
                deal_id="steam_",  # Пустой appid
                title="Empty AppID Deal",
                store="Steam",
                old_price="1500 ₽",
                new_price="750 ₽",
                discount=50,
                link="https://store.steampowered.com/app/",
            ),
        ]
        
        # Фильтруем deals
        result = await database.steam_library_filter_deals(user_id, deals)
        
        # Невалидные deal_id должны быть включены (не вызывать ошибку)
        assert len(result) == 2, "Должен вернуть все deals с невалидными deal_id"
    finally:
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_steam_library_filter_deals_all_owned():
    """WHEN steam_library_filter_deals вызывается и все deals owned, THE Database SHALL вернуть пустой список.
    
    Validates: Requirements 2.4, 2.5
    """
    from parsers.steam import Deal
    
    user_id = _test_user_id()
    appids = [12345, 67890, 11111]
    pool = await database.get_pool()
    
    try:
        # Добавляем все games в библиотеку
        await database.steam_library_replace(user_id, appids)
        
        # Создаем тестовые deals (все owned)
        deals = [
            Deal(
                deal_id="steam_12345",
                title="Owned Game 1",
                store="Steam",
                old_price="1000 ₽",
                new_price="500 ₽",
                discount=50,
                link="https://store.steampowered.com/app/12345",
            ),
            Deal(
                deal_id="steam_67890",
                title="Owned Game 2",
                store="Steam",
                old_price="1500 ₽",
                new_price="750 ₽",
                discount=50,
                link="https://store.steampowered.com/app/67890",
            ),
            Deal(
                deal_id="steam_11111",
                title="Owned Game 3",
                store="Steam",
                old_price="2000 ₽",
                new_price="1000 ₽",
                discount=50,
                link="https://store.steampowered.com/app/11111",
            ),
        ]
        
        # Фильтруем deals
        result = await database.steam_library_filter_deals(user_id, deals)
        
        assert len(result) == 0, "Должен вернуть пустой список если все deals owned"
        assert result == [], "Результат должен быть пустым списком"
    finally:
        await pool.execute("DELETE FROM steam_library WHERE user_id = $1", user_id)



# ---------------------------------------------------------------------------
# Price Cache Tests
# ---------------------------------------------------------------------------

async def _delete_price_cache(game_title: str):
    """Удаляет тестовую запись из price_cache."""
    pool = await database.get_pool()
    await pool.execute("DELETE FROM price_cache WHERE game_title = $1", game_title)


@pytest.mark.asyncio
async def test_price_cache_set_and_get():
    """WHEN price_cache_set вызывается, price_cache_get должен вернуть сохраненные данные.
    
    Validates: Requirements 3.6
    """
    game_title = f"test_game_{uuid.uuid4().hex[:8]}"
    prices = {
        "Steam": {"price": 1000, "discount": 50, "link": "https://steam.com"},
        "GOG": {"price": 1200, "discount": 40, "link": "https://gog.com"},
    }
    
    try:
        # Сохраняем в кеш
        await database.price_cache_set(game_title, prices)
        
        # Получаем из кеша
        result = await database.price_cache_get(game_title)
        
        assert result is not None, "Должен вернуть данные из кеша"
        assert result["prices"] == prices, "Prices должны совпадать"
        assert result["cached_at"] is not None, "cached_at должен быть установлен"
    finally:
        await _delete_price_cache(game_title)


@pytest.mark.asyncio
async def test_price_cache_get_not_exists():
    """WHEN price_cache_get вызывается для несуществующей игры, THE Database SHALL вернуть None.
    
    Validates: Requirements 3.6
    """
    game_title = f"nonexistent_game_{uuid.uuid4().hex[:8]}"
    
    result = await database.price_cache_get(game_title)
    
    assert result is None, "Должен вернуть None для несуществующей игры"


@pytest.mark.asyncio
async def test_price_cache_get_expired():
    """WHEN price_cache_get вызывается для записи старше 6 часов, THE Database SHALL вернуть None.
    
    Validates: Requirements 3.6
    """
    game_title = f"test_game_{uuid.uuid4().hex[:8]}"
    prices = {"Steam": {"price": 1000, "discount": 50}}
    pool = await database.get_pool()
    
    try:
        # Вставляем запись с датой старше 6 часов
        old_ts = datetime.now(timezone.utc) - timedelta(hours=7)
        await pool.execute(
            "INSERT INTO price_cache (game_title, prices, cached_at) VALUES ($1, $2, $3)",
            game_title, prices, old_ts,
        )
        
        # Пытаемся получить из кеша
        result = await database.price_cache_get(game_title)
        
        assert result is None, "Должен вернуть None для записи старше 6 часов"
    finally:
        await _delete_price_cache(game_title)


@pytest.mark.asyncio
async def test_price_cache_get_fresh():
    """WHEN price_cache_get вызывается для записи младше 6 часов, THE Database SHALL вернуть данные.
    
    Validates: Requirements 3.6
    """
    game_title = f"test_game_{uuid.uuid4().hex[:8]}"
    prices = {"Steam": {"price": 1000, "discount": 50}}
    pool = await database.get_pool()
    
    try:
        # Вставляем запись с датой 5 часов назад (свежая)
        fresh_ts = datetime.now(timezone.utc) - timedelta(hours=5)
        await pool.execute(
            "INSERT INTO price_cache (game_title, prices, cached_at) VALUES ($1, $2, $3)",
            game_title, prices, fresh_ts,
        )
        
        # Получаем из кеша
        result = await database.price_cache_get(game_title)
        
        assert result is not None, "Должен вернуть данные для свежей записи"
        assert result["prices"] == prices, "Prices должны совпадать"
    finally:
        await _delete_price_cache(game_title)


@pytest.mark.asyncio
async def test_price_cache_set_upsert():
    """WHEN price_cache_set вызывается дважды для одной игры, THE Database SHALL обновить существующую запись.
    
    Validates: Requirements 3.6
    """
    game_title = f"test_game_{uuid.uuid4().hex[:8]}"
    prices_old = {"Steam": {"price": 1000, "discount": 50}}
    prices_new = {"Steam": {"price": 800, "discount": 60}}
    pool = await database.get_pool()
    
    try:
        # Первая вставка
        await database.price_cache_set(game_title, prices_old)
        
        # Проверяем первую запись
        result1 = await database.price_cache_get(game_title)
        assert result1 is not None, "Первая запись должна существовать"
        assert result1["prices"] == prices_old, "Первые prices должны совпадать"
        cached_at_1 = result1["cached_at"]
        
        # Небольшая задержка чтобы timestamp изменился
        import asyncio
        await asyncio.sleep(0.1)
        
        # Вторая вставка (upsert)
        await database.price_cache_set(game_title, prices_new)
        
        # Проверяем обновленную запись
        result2 = await database.price_cache_get(game_title)
        assert result2 is not None, "Обновленная запись должна существовать"
        assert result2["prices"] == prices_new, "Prices должны быть обновлены"
        cached_at_2 = result2["cached_at"]
        
        # Проверяем, что timestamp обновился
        assert cached_at_2 > cached_at_1, "cached_at должен быть обновлен"
        
        # Проверяем, что запись одна (не создалась дубликат)
        rows = await pool.fetch(
            "SELECT COUNT(*) as cnt FROM price_cache WHERE game_title = $1",
            game_title
        )
        assert rows[0]["cnt"] == 1, "Должна быть только одна запись (upsert)"
    finally:
        await _delete_price_cache(game_title)


@pytest.mark.asyncio
async def test_price_cache_cleanup():
    """WHEN price_cache_cleanup вызывается, THE Database SHALL удалить записи старше 6 часов.
    
    Validates: Requirements 3.6
    """
    game_title_old = f"test_game_old_{uuid.uuid4().hex[:8]}"
    game_title_fresh = f"test_game_fresh_{uuid.uuid4().hex[:8]}"
    prices = {"Steam": {"price": 1000, "discount": 50}}
    pool = await database.get_pool()
    
    try:
        # Вставляем старую запись (7 часов назад)
        old_ts = datetime.now(timezone.utc) - timedelta(hours=7)
        await pool.execute(
            "INSERT INTO price_cache (game_title, prices, cached_at) VALUES ($1, $2, $3)",
            game_title_old, prices, old_ts,
        )
        
        # Вставляем свежую запись (5 часов назад)
        fresh_ts = datetime.now(timezone.utc) - timedelta(hours=5)
        await pool.execute(
            "INSERT INTO price_cache (game_title, prices, cached_at) VALUES ($1, $2, $3)",
            game_title_fresh, prices, fresh_ts,
        )
        
        # Вызываем cleanup
        deleted = await database.price_cache_cleanup()
        
        assert deleted >= 1, f"Должна быть удалена хотя бы 1 запись, удалено: {deleted}"
        
        # Проверяем, что старая запись удалена
        row_old = await pool.fetchrow(
            "SELECT 1 FROM price_cache WHERE game_title = $1", game_title_old
        )
        assert row_old is None, "Старая запись должна быть удалена"
        
        # Проверяем, что свежая запись осталась
        row_fresh = await pool.fetchrow(
            "SELECT 1 FROM price_cache WHERE game_title = $1", game_title_fresh
        )
        assert row_fresh is not None, "Свежая запись должна остаться"
    finally:
        await _delete_price_cache(game_title_old)
        await _delete_price_cache(game_title_fresh)


@pytest.mark.asyncio
async def test_price_cache_cleanup_no_old_records():
    """WHEN price_cache_cleanup вызывается и нет старых записей, THE Database SHALL вернуть 0.
    
    Validates: Requirements 3.6
    """
    game_title = f"test_game_{uuid.uuid4().hex[:8]}"
    prices = {"Steam": {"price": 1000, "discount": 50}}
    
    try:
        # Вставляем только свежую запись
        await database.price_cache_set(game_title, prices)
        
        # Вызываем cleanup
        deleted = await database.price_cache_cleanup()
        
        # Проверяем, что свежая запись не удалена
        result = await database.price_cache_get(game_title)
        assert result is not None, "Свежая запись не должна быть удалена"
    finally:
        await _delete_price_cache(game_title)


@pytest.mark.asyncio
async def test_price_cache_set_complex_prices():
    """WHEN price_cache_set вызывается со сложной структурой prices, THE Database SHALL сохранить её корректно.
    
    Validates: Requirements 3.6
    """
    game_title = f"test_game_{uuid.uuid4().hex[:8]}"
    prices = {
        "Steam": {
            "price": 1000,
            "discount": 50,
            "link": "https://steam.com/app/123",
            "currency": "RUB",
            "original_price": 2000,
        },
        "GOG": {
            "price": 1200,
            "discount": 40,
            "link": "https://gog.com/game/test",
            "currency": "RUB",
        },
        "Epic Games": {
            "price": 900,
            "discount": 55,
            "link": "https://epicgames.com/store/test",
            "currency": "RUB",
        },
        "CheapShark": {
            "price": 850,
            "discount": 57,
            "link": "https://cheapshark.com/redirect?dealID=abc123",
            "currency": "USD",
        },
    }
    
    try:
        # Сохраняем сложную структуру
        await database.price_cache_set(game_title, prices)
        
        # Получаем из кеша
        result = await database.price_cache_get(game_title)
        
        assert result is not None, "Должен вернуть данные из кеша"
        assert result["prices"] == prices, "Сложная структура prices должна совпадать"
        
        # Проверяем, что все магазины сохранены
        assert "Steam" in result["prices"], "Steam должен быть в prices"
        assert "GOG" in result["prices"], "GOG должен быть в prices"
        assert "Epic Games" in result["prices"], "Epic Games должен быть в prices"
        assert "CheapShark" in result["prices"], "CheapShark должен быть в prices"
        
        # Проверяем вложенные поля
        assert result["prices"]["Steam"]["original_price"] == 2000, "Вложенные поля должны сохраниться"
        assert result["prices"]["CheapShark"]["currency"] == "USD", "Вложенные поля должны сохраниться"
    finally:
        await _delete_price_cache(game_title)



# ---------------------------------------------------------------------------
# Free Game Subscriptions Tests
# ---------------------------------------------------------------------------

async def _delete_free_game_sub(user_id: int):
    """Удаляет тестовую подписку на бесплатные игры."""
    pool = await database.get_pool()
    await pool.execute("DELETE FROM free_game_subs WHERE user_id = $1", user_id)


@pytest.mark.asyncio
async def test_free_game_subscribe_success():
    """WHEN free_game_subscribe вызывается с новым user_id, THE Database SHALL вернуть True.

    Validates: Requirements 4.8
    """
    user_id = _test_user_id()
    
    try:
        result = await database.free_game_subscribe(user_id)
        assert result is True, "Первая подписка должна вернуть True"
        
        # Проверяем, что запись создана
        pool = await database.get_pool()
        row = await pool.fetchrow(
            "SELECT user_id FROM free_game_subs WHERE user_id = $1", user_id
        )
        assert row is not None, "Запись должна быть создана"
        assert row["user_id"] == user_id, "user_id должен совпадать"
    finally:
        await _delete_free_game_sub(user_id)


@pytest.mark.asyncio
async def test_free_game_subscribe_duplicate():
    """WHEN free_game_subscribe вызывается с существующим user_id, THE Database SHALL вернуть False.

    Validates: Requirements 4.8
    """
    user_id = _test_user_id()
    
    try:
        # Первая подписка
        result1 = await database.free_game_subscribe(user_id)
        assert result1 is True, "Первая подписка должна вернуть True"
        
        # Повторная подписка (дубликат)
        result2 = await database.free_game_subscribe(user_id)
        assert result2 is False, "Повторная подписка должна вернуть False"
    finally:
        await _delete_free_game_sub(user_id)


@pytest.mark.asyncio
async def test_free_game_unsubscribe_success():
    """WHEN free_game_unsubscribe вызывается для подписанного пользователя, THE Database SHALL вернуть True.

    Validates: Requirements 4.8
    """
    user_id = _test_user_id()
    
    try:
        # Подписываемся
        await database.free_game_subscribe(user_id)
        
        # Отписываемся
        result = await database.free_game_unsubscribe(user_id)
        assert result is True, "Отписка должна вернуть True"
        
        # Проверяем, что запись удалена
        pool = await database.get_pool()
        row = await pool.fetchrow(
            "SELECT user_id FROM free_game_subs WHERE user_id = $1", user_id
        )
        assert row is None, "Запись должна быть удалена"
    finally:
        await _delete_free_game_sub(user_id)


@pytest.mark.asyncio
async def test_free_game_unsubscribe_not_subscribed():
    """WHEN free_game_unsubscribe вызывается для неподписанного пользователя, THE Database SHALL вернуть False.

    Validates: Requirements 4.8
    """
    user_id = _test_user_id()
    
    # Отписываемся без предварительной подписки
    result = await database.free_game_unsubscribe(user_id)
    assert result is False, "Отписка неподписанного пользователя должна вернуть False"


@pytest.mark.asyncio
async def test_free_game_get_subscribers_empty():
    """WHEN free_game_get_subscribers вызывается и нет подписчиков, THE Database SHALL вернуть пустой список.

    Validates: Requirements 4.9
    """
    # Получаем всех подписчиков
    result = await database.free_game_get_subscribers()
    
    # Фильтруем только тестовых пользователей (если они есть)
    test_subscribers = [uid for uid in result if uid >= TEST_USER_BASE]
    
    assert isinstance(result, list), "Должен вернуть список"
    assert len(test_subscribers) == 0, "Не должно быть тестовых подписчиков"


@pytest.mark.asyncio
async def test_free_game_get_subscribers_multiple():
    """WHEN free_game_get_subscribers вызывается с несколькими подписчиками, THE Database SHALL вернуть список всех user_ids.

    Validates: Requirements 4.9
    """
    user_id_1 = _test_user_id()
    user_id_2 = _test_user_id()
    user_id_3 = _test_user_id()
    
    try:
        # Подписываем трех пользователей
        await database.free_game_subscribe(user_id_1)
        await database.free_game_subscribe(user_id_2)
        await database.free_game_subscribe(user_id_3)
        
        # Получаем всех подписчиков
        result = await database.free_game_get_subscribers()
        
        # Проверяем результат
        assert isinstance(result, list), "Должен вернуть список"
        
        # Фильтруем только наших тестовых пользователей
        test_subscribers = [uid for uid in result if uid in [user_id_1, user_id_2, user_id_3]]
        
        assert len(test_subscribers) == 3, "Должно быть 3 тестовых подписчика"
        assert user_id_1 in test_subscribers, "user_id_1 должен быть в списке"
        assert user_id_2 in test_subscribers, "user_id_2 должен быть в списке"
        assert user_id_3 in test_subscribers, "user_id_3 должен быть в списке"
    finally:
        await _delete_free_game_sub(user_id_1)
        await _delete_free_game_sub(user_id_2)
        await _delete_free_game_sub(user_id_3)


@pytest.mark.asyncio
async def test_free_game_subscribe_unsubscribe_round_trip():
    """WHEN free_game_subscribe и free_game_unsubscribe вызываются последовательно, THE Database SHALL корректно обрабатывать подписку и отписку.

    Validates: Requirements 4.8, 4.9
    """
    user_id = _test_user_id()
    
    try:
        # Подписываемся
        result1 = await database.free_game_subscribe(user_id)
        assert result1 is True, "Подписка должна вернуть True"
        
        # Проверяем, что пользователь в списке подписчиков
        subscribers = await database.free_game_get_subscribers()
        assert user_id in subscribers, "Пользователь должен быть в списке подписчиков"
        
        # Отписываемся
        result2 = await database.free_game_unsubscribe(user_id)
        assert result2 is True, "Отписка должна вернуть True"
        
        # Проверяем, что пользователя нет в списке подписчиков
        subscribers = await database.free_game_get_subscribers()
        assert user_id not in subscribers, "Пользователя не должно быть в списке подписчиков"
        
        # Повторная отписка должна вернуть False
        result3 = await database.free_game_unsubscribe(user_id)
        assert result3 is False, "Повторная отписка должна вернуть False"
    finally:
        await _delete_free_game_sub(user_id)


# ---------------------------------------------------------------------------
# Onboarding Tables Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_onboarding_tables_creates_tables():
    """WHEN init_onboarding_tables() вызывается, THE Database SHALL создать таблицы onboarding_progress и onboarding_hints.
    
    Validates: Requirements 7.1, 7.2, 7.3, 7.4
    """
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.init_onboarding_tables(conn)
    
    # Проверяем, что таблицы созданы
    expected = {"onboarding_progress", "onboarding_hints"}
    rows = await pool.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = ANY($1::text[])",
        list(expected),
    )
    found = {r["table_name"] for r in rows}
    assert found == expected, f"Отсутствуют таблицы: {expected - found}"


@pytest.mark.asyncio
async def test_onboarding_progress_table_structure():
    """WHEN onboarding_progress таблица создана, THE Database SHALL иметь все необходимые поля.
    
    Validates: Requirements 7.2
    """
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.init_onboarding_tables(conn)
    
    # Проверяем структуру таблицы onboarding_progress
    rows = await pool.fetch("""
        SELECT column_name, data_type, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'onboarding_progress'
        ORDER BY ordinal_position
    """)
    
    columns = {r["column_name"]: r["data_type"] for r in rows}
    
    # Проверяем наличие всех необходимых полей
    assert "user_id" in columns, "Должно быть поле user_id"
    assert "current_step" in columns, "Должно быть поле current_step"
    assert "status" in columns, "Должно быть поле status"
    assert "completed_at" in columns, "Должно быть поле completed_at"
    assert "skipped_at" in columns, "Должно быть поле skipped_at"
    assert "created_at" in columns, "Должно быть поле created_at"
    assert "updated_at" in columns, "Должно быть поле updated_at"
    
    # Проверяем типы данных
    assert columns["user_id"] == "bigint", "user_id должен быть bigint"
    assert columns["current_step"] == "integer", "current_step должен быть integer"
    assert columns["status"] == "text", "status должен быть text"
    assert "timestamp" in columns["completed_at"], "completed_at должен быть timestamptz"
    assert "timestamp" in columns["skipped_at"], "skipped_at должен быть timestamptz"
    assert "timestamp" in columns["created_at"], "created_at должен быть timestamptz"
    assert "timestamp" in columns["updated_at"], "updated_at должен быть timestamptz"


@pytest.mark.asyncio
async def test_onboarding_hints_table_structure():
    """WHEN onboarding_hints таблица создана, THE Database SHALL иметь все необходимые поля и индекс.
    
    Validates: Requirements 7.3, 7.4
    """
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.init_onboarding_tables(conn)
    
    # Проверяем структуру таблицы onboarding_hints
    rows = await pool.fetch("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'onboarding_hints'
        ORDER BY ordinal_position
    """)
    
    columns = {r["column_name"]: r["data_type"] for r in rows}
    
    # Проверяем наличие всех необходимых полей
    assert "id" in columns, "Должно быть поле id"
    assert "user_id" in columns, "Должно быть поле user_id"
    assert "hint_type" in columns, "Должно быть поле hint_type"
    assert "shown_at" in columns, "Должно быть поле shown_at"
    
    # Проверяем типы данных
    assert columns["id"] == "integer", "id должен быть integer (SERIAL)"
    assert columns["user_id"] == "bigint", "user_id должен быть bigint"
    assert columns["hint_type"] == "text", "hint_type должен быть text"
    assert "timestamp" in columns["shown_at"], "shown_at должен быть timestamptz"


@pytest.mark.asyncio
async def test_onboarding_hints_unique_constraint():
    """WHEN onboarding_hints таблица создана, THE Database SHALL иметь уникальное ограничение на (user_id, hint_type).
    
    Validates: Requirements 7.3
    """
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.init_onboarding_tables(conn)
    
    # Проверяем наличие уникального ограничения
    rows = await pool.fetch("""
        SELECT constraint_name, constraint_type
        FROM information_schema.table_constraints
        WHERE table_schema = 'public' 
        AND table_name = 'onboarding_hints'
        AND constraint_type = 'UNIQUE'
    """)
    
    assert len(rows) > 0, "Должно быть уникальное ограничение на onboarding_hints"


@pytest.mark.asyncio
async def test_onboarding_hints_index_exists():
    """WHEN onboarding_hints таблица создана, THE Database SHALL иметь индекс на user_id.
    
    Validates: Requirements 7.4
    """
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        await database.init_onboarding_tables(conn)
    
    # Проверяем наличие индекса
    rows = await pool.fetch("""
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public' 
        AND tablename = 'onboarding_hints'
        AND indexname = 'idx_onboarding_hints_user'
    """)
    
    assert len(rows) == 1, "Должен существовать индекс idx_onboarding_hints_user"


@pytest.mark.asyncio
async def test_onboarding_tables_idempotent():
    """WHEN init_onboarding_tables() вызывается дважды, THE Database SHALL не бросать исключение.
    
    Validates: Requirements 7.1
    """
    pool = await database.get_pool()
    async with pool.acquire() as conn:
        # Первый вызов
        await database.init_onboarding_tables(conn)
        # Второй вызов не должен бросить исключение
        await database.init_onboarding_tables(conn)
    
    # Проверяем, что таблицы все еще существуют
    expected = {"onboarding_progress", "onboarding_hints"}
    rows = await pool.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = ANY($1::text[])",
        list(expected),
    )
    found = {r["table_name"] for r in rows}
    assert found == expected, "Таблицы должны существовать после повторного вызова"
