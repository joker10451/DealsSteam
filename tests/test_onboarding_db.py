"""
Tests for onboarding database functions.
"""
import pytest
from database import (
    get_onboarding_progress,
    create_onboarding_progress,
    update_onboarding_step,
    complete_onboarding,
    skip_onboarding,
    save_hint_shown,
    get_shown_hints,
    get_user_registration_date,
)


@pytest.fixture
async def test_user_id():
    """Fixture providing a test user ID for onboarding tests."""
    return 9_000_000_001


@pytest.fixture
async def db_cleanup_onboarding(test_user_id):
    """Cleanup onboarding_progress and onboarding_hints records after test."""
    yield
    from database import get_pool
    pool = await get_pool()
    await pool.execute("DELETE FROM onboarding_hints WHERE user_id = $1", test_user_id)
    await pool.execute("DELETE FROM onboarding_progress WHERE user_id = $1", test_user_id)


async def test_create_onboarding_progress(test_user_id, db_cleanup_onboarding):
    """Test creating onboarding progress record."""
    result = await create_onboarding_progress(test_user_id)
    assert result is True
    
    progress = await get_onboarding_progress(test_user_id)
    assert progress is not None
    assert progress["user_id"] == test_user_id
    assert progress["current_step"] == 0
    assert progress["status"] == "in_progress"


async def test_get_onboarding_progress_nonexistent(test_user_id):
    """Test getting progress for user without record."""
    progress = await get_onboarding_progress(test_user_id)
    assert progress is None


async def test_update_onboarding_step(test_user_id, db_cleanup_onboarding):
    """Test updating current step."""
    await create_onboarding_progress(test_user_id)
    
    result = await update_onboarding_step(test_user_id, 3)
    assert result is True
    
    progress = await get_onboarding_progress(test_user_id)
    assert progress["current_step"] == 3


async def test_complete_onboarding(test_user_id, db_cleanup_onboarding):
    """Test completing onboarding."""
    await create_onboarding_progress(test_user_id)
    
    result = await complete_onboarding(test_user_id)
    assert result is True
    
    progress = await get_onboarding_progress(test_user_id)
    assert progress["status"] == "completed"
    assert progress["completed_at"] is not None


async def test_skip_onboarding(test_user_id, db_cleanup_onboarding):
    """Test skipping onboarding."""
    await create_onboarding_progress(test_user_id)
    
    result = await skip_onboarding(test_user_id)
    assert result is True
    
    progress = await get_onboarding_progress(test_user_id)
    assert progress["status"] == "skipped"
    assert progress["skipped_at"] is not None


async def test_create_duplicate_progress(test_user_id, db_cleanup_onboarding):
    """Test creating duplicate progress record (should be idempotent)."""
    result1 = await create_onboarding_progress(test_user_id)
    assert result1 is True
    
    result2 = await create_onboarding_progress(test_user_id)
    assert result2 is True  # ON CONFLICT DO NOTHING should succeed
    
    progress = await get_onboarding_progress(test_user_id)
    assert progress is not None
    assert progress["user_id"] == test_user_id


# Tests for hint tracking functions

async def test_save_hint_shown(test_user_id, db_cleanup_onboarding):
    """Test saving a shown hint."""
    result = await save_hint_shown(test_user_id, "wishlist_vote")
    assert result is True
    
    hints = await get_shown_hints(test_user_id)
    assert "wishlist_vote" in hints


async def test_save_hint_shown_duplicate(test_user_id, db_cleanup_onboarding):
    """Test saving duplicate hint (should be idempotent with ON CONFLICT)."""
    result1 = await save_hint_shown(test_user_id, "wishlist_vote")
    assert result1 is True
    
    result2 = await save_hint_shown(test_user_id, "wishlist_vote")
    assert result2 is True  # ON CONFLICT DO NOTHING should succeed
    
    hints = await get_shown_hints(test_user_id)
    assert hints.count("wishlist_vote") == 1  # Should only appear once


async def test_save_multiple_hints(test_user_id, db_cleanup_onboarding):
    """Test saving multiple different hints."""
    await save_hint_shown(test_user_id, "wishlist_vote")
    await save_hint_shown(test_user_id, "minigame_challenge")
    await save_hint_shown(test_user_id, "shop_earn")
    
    hints = await get_shown_hints(test_user_id)
    assert len(hints) == 3
    assert "wishlist_vote" in hints
    assert "minigame_challenge" in hints
    assert "shop_earn" in hints


async def test_get_shown_hints_empty(test_user_id):
    """Test getting hints for user with no hints shown."""
    hints = await get_shown_hints(test_user_id)
    assert hints == []


async def test_get_user_registration_date(test_user_id, db_cleanup_onboarding):
    """Test getting user registration date."""
    await create_onboarding_progress(test_user_id)
    
    reg_date = await get_user_registration_date(test_user_id)
    assert reg_date is not None
    # Check that it's a datetime-like object
    assert hasattr(reg_date, 'year')
    assert hasattr(reg_date, 'month')
    assert hasattr(reg_date, 'day')


async def test_get_user_registration_date_nonexistent(test_user_id):
    """Test getting registration date for user without record."""
    reg_date = await get_user_registration_date(test_user_id)
    assert reg_date is None
