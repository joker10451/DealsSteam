"""
Тесты для системы конкурсов.
"""
import pytest
from datetime import datetime, timedelta
import pytz

MSK = pytz.timezone("Europe/Moscow")


async def test_init_giveaways_db():
    """Тест инициализации таблиц конкурсов."""
    from giveaways import init_giveaways_db
    from database import get_pool

    await init_giveaways_db()

    pool = await get_pool()
    tables = await pool.fetch("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        AND table_name IN ('giveaways', 'giveaway_participants')
    """)
    table_names = {t["table_name"] for t in tables}
    assert "giveaways" in table_names
    assert "giveaway_participants" in table_names


async def test_create_giveaway():
    """Тест создания конкурса."""
    from giveaways import create_giveaway
    from database import get_pool

    giveaway_id = await create_giveaway(
        title="Test Game",
        description="Test description",
        prize_type="steam_key",
        prize_value="TEST-KEY-12345",
        duration_hours=72,
        _test=True,
    )

    assert giveaway_id.startswith("giveaway_test_")

    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM giveaways WHERE giveaway_id = $1", giveaway_id
    )
    assert row is not None
    assert row["title"] == "Test Game"
    assert row["prize_type"] == "steam_key"
    assert row["status"] == "active"

    # Cleanup
    await pool.execute("DELETE FROM giveaways WHERE giveaway_id = $1", giveaway_id)


async def test_join_giveaway():
    """Тест участия в конкурсе."""
    from giveaways import create_giveaway, join_giveaway
    from database import get_pool, create_onboarding_progress

    giveaway_id = await create_giveaway(
        title="Test Game",
        description="Test",
        prize_type="points",
        prize_value="100",
        duration_hours=1,
        require_channel_sub=False,
        min_account_age_days=0,
        _test=True,
    )

    test_user_id = 9_000_000_001
    await create_onboarding_progress(test_user_id)

    success, msg = await join_giveaway(giveaway_id, test_user_id)
    assert success is True
    assert "участвуешь" in msg.lower()

    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM giveaway_participants WHERE giveaway_id = $1 AND user_id = $2",
        giveaway_id, test_user_id
    )
    assert row is not None

    # Cleanup
    await pool.execute("DELETE FROM giveaway_participants WHERE giveaway_id = $1", giveaway_id)
    await pool.execute("DELETE FROM giveaways WHERE giveaway_id = $1", giveaway_id)
    await pool.execute("DELETE FROM onboarding_progress WHERE user_id = $1", test_user_id)


async def test_get_active_giveaways():
    """Тест получения активных конкурсов."""
    from giveaways import create_giveaway, get_active_giveaways
    from database import get_pool

    giveaway_ids = []
    for i in range(3):
        gid = await create_giveaway(
            title=f"Test Game {i}",
            description="Test",
            prize_type="points",
            prize_value="100",
            duration_hours=1,
            _test=True,
        )
        giveaway_ids.append(gid)

    active = await get_active_giveaways()
    active_ids = {g["giveaway_id"] for g in active}
    for gid in giveaway_ids:
        assert gid in active_ids

    pool = await get_pool()
    for gid in giveaway_ids:
        await pool.execute("DELETE FROM giveaways WHERE giveaway_id = $1", gid)


async def test_select_winner():
    """Тест выбора победителя."""
    from giveaways import create_giveaway, join_giveaway, select_winner
    from database import get_pool, create_onboarding_progress

    giveaway_id = await create_giveaway(
        title="Test Game",
        description="Test",
        prize_type="points",
        prize_value="100",
        duration_hours=1,
        require_channel_sub=False,
        min_account_age_days=0,
        _test=True,
    )

    test_users = [9_000_000_001, 9_000_000_002, 9_000_000_003]
    for user_id in test_users:
        await create_onboarding_progress(user_id)
        await join_giveaway(giveaway_id, user_id)

    winner_id = await select_winner(giveaway_id)
    assert winner_id is not None
    assert winner_id[0] in test_users

    pool = await get_pool()
    # Cleanup
    await pool.execute("DELETE FROM giveaway_participants WHERE giveaway_id = $1", giveaway_id)
    await pool.execute("DELETE FROM giveaways WHERE giveaway_id = $1", giveaway_id)
    for user_id in test_users:
        await pool.execute("DELETE FROM onboarding_progress WHERE user_id = $1", user_id)


async def test_get_giveaway_participants():
    """Тест получения списка участников."""
    from giveaways import create_giveaway, join_giveaway, get_giveaway_participants
    from database import get_pool, create_onboarding_progress

    giveaway_id = await create_giveaway(
        title="Test",
        description="Test",
        prize_type="points",
        prize_value="100",
        duration_hours=1,
        require_channel_sub=False,
        min_account_age_days=0,
        _test=True,
    )

    test_users = [9_000_000_001, 9_000_000_002]
    for user_id in test_users:
        await create_onboarding_progress(user_id)
        await join_giveaway(giveaway_id, user_id)

    participants = await get_giveaway_participants(giveaway_id)
    assert len(participants) == 2
    assert set(participants) == set(test_users)

    pool = await get_pool()
    await pool.execute("DELETE FROM giveaway_participants WHERE giveaway_id = $1", giveaway_id)
    await pool.execute("DELETE FROM giveaways WHERE giveaway_id = $1", giveaway_id)
    for user_id in test_users:
        await pool.execute("DELETE FROM onboarding_progress WHERE user_id = $1", user_id)


async def test_check_ended_giveaways():
    """Тест автоматической проверки завершённых конкурсов."""
    from giveaways import check_ended_giveaways
    from database import get_pool

    now = datetime.now(MSK)
    pool = await get_pool()
    giveaway_id = f"giveaway_test_{int(now.timestamp())}"

    await pool.execute("""
        INSERT INTO giveaways (
            giveaway_id, title, description, prize_type, prize_value,
            start_time, end_time, status
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """, giveaway_id, "Test", "Test", "points", "100",
        now - timedelta(hours=2), now - timedelta(hours=1), "active")

    await check_ended_giveaways()

    row = await pool.fetchrow(
        "SELECT status FROM giveaways WHERE giveaway_id = $1", giveaway_id
    )
    assert row["status"] == "ended"

    # Cleanup
    await pool.execute("DELETE FROM giveaways WHERE giveaway_id = $1", giveaway_id)


async def test_remove_participant_from_all_giveaways():
    """Тест удаления участника из всех активных розыгрышей при выходе из канала."""
    from giveaways import create_giveaway, join_giveaway, remove_participant_from_all_giveaways
    from database import get_pool, create_onboarding_progress

    # Создаём два активных конкурса
    gid1 = await create_giveaway(
        title="Test Game 1", description="Test", prize_type="points", prize_value="100",
        duration_hours=1, require_channel_sub=False, min_account_age_days=0, _test=True,
    )
    gid2 = await create_giveaway(
        title="Test Game 2", description="Test", prize_type="points", prize_value="200",
        duration_hours=1, require_channel_sub=False, min_account_age_days=0, _test=True,
    )

    test_user = 9_000_000_010
    other_user = 9_000_000_011
    await create_onboarding_progress(test_user)
    await create_onboarding_progress(other_user)

    await join_giveaway(gid1, test_user)
    await join_giveaway(gid2, test_user)
    await join_giveaway(gid1, other_user)  # другой участник — не должен пострадать

    removed = await remove_participant_from_all_giveaways(test_user)
    assert removed == 2

    pool = await get_pool()
    # test_user удалён из обоих
    row1 = await pool.fetchrow(
        "SELECT 1 FROM giveaway_participants WHERE giveaway_id = $1 AND user_id = $2",
        gid1, test_user,
    )
    row2 = await pool.fetchrow(
        "SELECT 1 FROM giveaway_participants WHERE giveaway_id = $1 AND user_id = $2",
        gid2, test_user,
    )
    assert row1 is None
    assert row2 is None

    # other_user остался
    row3 = await pool.fetchrow(
        "SELECT 1 FROM giveaway_participants WHERE giveaway_id = $1 AND user_id = $2",
        gid1, other_user,
    )
    assert row3 is not None

    # Cleanup
    await pool.execute("DELETE FROM giveaway_participants WHERE giveaway_id = ANY($1::text[])", [gid1, gid2])
    await pool.execute("DELETE FROM giveaways WHERE giveaway_id = ANY($1::text[])", [gid1, gid2])
    for uid in [test_user, other_user]:
        await pool.execute("DELETE FROM onboarding_progress WHERE user_id = $1", uid)


async def test_remove_participant_not_in_ended_giveaway():
    """Завершённые розыгрыши не должны затрагиваться при удалении участника."""
    from giveaways import create_giveaway, join_giveaway, remove_participant_from_all_giveaways
    from database import get_pool, create_onboarding_progress

    gid = await create_giveaway(
        title="Ended Game", description="Test", prize_type="points", prize_value="50",
        duration_hours=1, require_channel_sub=False, min_account_age_days=0, _test=True,
    )

    test_user = 9_000_000_012
    await create_onboarding_progress(test_user)
    await join_giveaway(gid, test_user)

    pool = await get_pool()
    # Вручную завершаем конкурс
    await pool.execute("UPDATE giveaways SET status = 'ended' WHERE giveaway_id = $1", gid)

    removed = await remove_participant_from_all_giveaways(test_user)
    assert removed == 0  # ничего не удалено — конкурс уже ended

    # Запись в participants осталась (для истории)
    row = await pool.fetchrow(
        "SELECT 1 FROM giveaway_participants WHERE giveaway_id = $1 AND user_id = $2",
        gid, test_user,
    )
    assert row is not None

    # Cleanup
    await pool.execute("DELETE FROM giveaway_participants WHERE giveaway_id = $1", gid)
    await pool.execute("DELETE FROM giveaways WHERE giveaway_id = $1", gid)
    await pool.execute("DELETE FROM onboarding_progress WHERE user_id = $1", test_user)


async def test_select_winner_marks_ineligible_via_mock(monkeypatch):
    """
    Если участник не в канале (мок get_chat_member возвращает 'left'),
    select_winner должен пометить его is_eligible=FALSE и не выбрать победителем.
    """
    from giveaways import create_giveaway, join_giveaway, select_winner
    from database import get_pool, create_onboarding_progress

    gid = await create_giveaway(
        title="Mock Channel Test", description="Test", prize_type="points", prize_value="100",
        duration_hours=1, require_channel_sub=True, min_account_age_days=0, _test=True,
    )

    eligible_user = 9_000_000_013
    left_user = 9_000_000_014
    for uid in [eligible_user, left_user]:
        await create_onboarding_progress(uid)

    pool = await get_pool()
    # Вставляем участников напрямую (минуя проверку подписки в join_giveaway)
    for uid in [eligible_user, left_user]:
        await pool.execute(
            "INSERT INTO giveaway_participants (giveaway_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            gid, uid,
        )

    # Мокаем get_bot и get_chat_member
    class FakeMember:
        def __init__(self, status):
            self.status = status

    class FakeBot:
        async def get_chat_member(self, chat_id, user_id):
            if user_id == left_user:
                return FakeMember("left")
            return FakeMember("member")

    import giveaways as giveaways_module
    import publisher

    original_get_bot = publisher.get_bot
    publisher.get_bot = lambda: FakeBot()

    try:
        result = await select_winner(gid)
    finally:
        publisher.get_bot = original_get_bot

    assert result is not None
    winner_id = result[0]
    assert winner_id == eligible_user  # left_user не должен выиграть

    # left_user помечен как ineligible
    row = await pool.fetchrow(
        "SELECT is_eligible FROM giveaway_participants WHERE giveaway_id = $1 AND user_id = $2",
        gid, left_user,
    )
    assert row["is_eligible"] is False

    # Cleanup
    await pool.execute("DELETE FROM giveaway_participants WHERE giveaway_id = $1", gid)
    await pool.execute("DELETE FROM giveaways WHERE giveaway_id = $1", gid)
    for uid in [eligible_user, left_user]:
        await pool.execute("DELETE FROM onboarding_progress WHERE user_id = $1", uid)
