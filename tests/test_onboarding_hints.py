"""
Tests for onboarding hint system functions.
"""
import pytest
from datetime import datetime, timedelta
import pytz

from onboarding import should_show_hints, show_hint, HINT_DURATION_DAYS, HINT_TYPES
from database import (
    create_onboarding_progress,
    save_hint_shown,
    get_shown_hints,
    get_user_registration_date,
    get_pool
)

MSK = pytz.timezone("Europe/Moscow")


@pytest.fixture
async def test_user_id():
    """Fixture providing a test user ID for hint tests."""
    # Use high ID to avoid conflicts with real users
    return 9_000_000_004


@pytest.fixture
async def db_cleanup_hints(test_user_id):
    """Cleanup fixture for hint tests."""
    yield
    # Cleanup after test
    pool = await get_pool()
    await pool.execute(
        "DELETE FROM onboarding_hints WHERE user_id = $1",
        test_user_id,
    )
    await pool.execute(
        "DELETE FROM onboarding_progress WHERE user_id = $1",
        test_user_id,
    )


# Tests for should_show_hints

async def test_should_show_hints_within_3_days(test_user_id, db_cleanup_hints):
    """Test should_show_hints returns True within 3 days of registration."""
    # Create user with recent registration
    await create_onboarding_progress(test_user_id)
    
    result = await should_show_hints(test_user_id)
    assert result is True


async def test_should_show_hints_after_3_days(test_user_id, db_cleanup_hints):
    """Test should_show_hints returns False after 3 days."""
    # Create user with old registration date
    pool = await get_pool()
    old_date = datetime.now(MSK) - timedelta(days=4)
    
    await pool.execute(
        "INSERT INTO onboarding_progress (user_id, created_at) VALUES ($1, $2)",
        test_user_id, old_date
    )
    
    result = await should_show_hints(test_user_id)
    assert result is False


async def test_should_show_hints_exactly_3_days(test_user_id, db_cleanup_hints):
    """Test should_show_hints returns False exactly at 3 days boundary."""
    # Create user registered exactly 3 days ago
    pool = await get_pool()
    boundary_date = datetime.now(MSK) - timedelta(days=3)
    
    await pool.execute(
        "INSERT INTO onboarding_progress (user_id, created_at) VALUES ($1, $2)",
        test_user_id, boundary_date
    )
    
    result = await should_show_hints(test_user_id)
    assert result is False


async def test_should_show_hints_no_registration(test_user_id):
    """Test should_show_hints returns False when no registration exists."""
    result = await should_show_hints(test_user_id)
    assert result is False


async def test_should_show_hints_day_0(test_user_id, db_cleanup_hints):
    """Test should_show_hints returns True on registration day (day 0)."""
    await create_onboarding_progress(test_user_id)
    
    result = await should_show_hints(test_user_id)
    assert result is True


async def test_should_show_hints_day_2(test_user_id, db_cleanup_hints):
    """Test should_show_hints returns True on day 2 (last day)."""
    pool = await get_pool()
    day_2_date = datetime.now(MSK) - timedelta(days=2)
    
    await pool.execute(
        "INSERT INTO onboarding_progress (user_id, created_at) VALUES ($1, $2)",
        test_user_id, day_2_date
    )
    
    result = await should_show_hints(test_user_id)
    assert result is True


# Tests for show_hint

async def test_show_hint_first_time(test_user_id, db_cleanup_hints):
    """Test show_hint returns message when showing hint for first time."""
    await create_onboarding_progress(test_user_id)
    
    result = await show_hint(test_user_id, "wishlist_vote")
    
    assert result is not None
    assert "💡" in result
    assert HINT_TYPES["wishlist_vote"] in result


async def test_show_hint_already_shown(test_user_id, db_cleanup_hints):
    """Test show_hint returns None when hint already shown."""
    await create_onboarding_progress(test_user_id)
    await save_hint_shown(test_user_id, "wishlist_vote")
    
    result = await show_hint(test_user_id, "wishlist_vote")
    
    assert result is None


async def test_show_hint_outside_duration(test_user_id, db_cleanup_hints):
    """Test show_hint returns None when outside 3-day duration."""
    pool = await get_pool()
    old_date = datetime.now(MSK) - timedelta(days=4)
    
    await pool.execute(
        "INSERT INTO onboarding_progress (user_id, created_at) VALUES ($1, $2)",
        test_user_id, old_date
    )
    
    result = await show_hint(test_user_id, "wishlist_vote")
    
    assert result is None


async def test_show_hint_invalid_type(test_user_id, db_cleanup_hints):
    """Test show_hint returns None for invalid hint type."""
    await create_onboarding_progress(test_user_id)
    
    result = await show_hint(test_user_id, "invalid_hint_type")
    
    assert result is None


async def test_show_hint_saves_to_database(test_user_id, db_cleanup_hints):
    """Test show_hint saves hint to database."""
    await create_onboarding_progress(test_user_id)
    
    await show_hint(test_user_id, "minigame_challenge")
    
    shown_hints = await get_shown_hints(test_user_id)
    assert "minigame_challenge" in shown_hints


async def test_show_hint_multiple_types(test_user_id, db_cleanup_hints):
    """Test showing multiple different hint types."""
    await create_onboarding_progress(test_user_id)
    
    result1 = await show_hint(test_user_id, "wishlist_vote")
    result2 = await show_hint(test_user_id, "minigame_challenge")
    result3 = await show_hint(test_user_id, "shop_earn")
    
    assert result1 is not None
    assert result2 is not None
    assert result3 is not None
    
    shown_hints = await get_shown_hints(test_user_id)
    assert len(shown_hints) == 3
    assert "wishlist_vote" in shown_hints
    assert "minigame_challenge" in shown_hints
    assert "shop_earn" in shown_hints


async def test_show_hint_all_types(test_user_id, db_cleanup_hints):
    """Test showing all available hint types."""
    await create_onboarding_progress(test_user_id)
    
    for hint_type in HINT_TYPES.keys():
        result = await show_hint(test_user_id, hint_type)
        assert result is not None
        assert HINT_TYPES[hint_type] in result
    
    shown_hints = await get_shown_hints(test_user_id)
    assert len(shown_hints) == len(HINT_TYPES)


async def test_show_hint_no_registration(test_user_id):
    """Test show_hint returns None when user has no registration."""
    result = await show_hint(test_user_id, "wishlist_vote")
    assert result is None


# Integration tests

async def test_hint_flow_complete(test_user_id, db_cleanup_hints):
    """Test complete flow of showing hints over time."""
    await create_onboarding_progress(test_user_id)
    
    # Day 0: Show first hint
    result1 = await show_hint(test_user_id, "wishlist_vote")
    assert result1 is not None
    
    # Try to show same hint again - should return None
    result2 = await show_hint(test_user_id, "wishlist_vote")
    assert result2 is None
    
    # Show different hint - should work
    result3 = await show_hint(test_user_id, "minigame_challenge")
    assert result3 is not None
    
    # Verify both hints are saved
    shown_hints = await get_shown_hints(test_user_id)
    assert len(shown_hints) == 2


async def test_hint_timing_boundary(test_user_id, db_cleanup_hints):
    """Test hint system respects 3-day boundary."""
    pool = await get_pool()
    
    # Create user at day 2 (last valid day)
    day_2_date = datetime.now(MSK) - timedelta(days=2, hours=23)
    await pool.execute(
        "INSERT INTO onboarding_progress (user_id, created_at) VALUES ($1, $2)",
        test_user_id, day_2_date
    )
    
    # Should still show hints
    result = await show_hint(test_user_id, "wishlist_vote")
    assert result is not None
    
    # Update to day 3 (outside boundary)
    day_3_date = datetime.now(MSK) - timedelta(days=3, hours=1)
    await pool.execute(
        "UPDATE onboarding_progress SET created_at = $2 WHERE user_id = $1",
        test_user_id, day_3_date
    )
    
    # Should not show hints anymore
    result2 = await show_hint(test_user_id, "minigame_challenge")
    assert result2 is None
