"""
Tests for onboarding tutorial management functions.
"""
import pytest
from onboarding import (
    is_new_user,
    start_tutorial,
    get_tutorial_step,
    next_tutorial_step,
    prev_tutorial_step,
    TUTORIAL_STEPS,
)
from database import (
    get_onboarding_progress,
    create_onboarding_progress,
    update_onboarding_step,
)


@pytest.fixture
async def test_user_id():
    """Fixture providing a test user ID for onboarding tests."""
    return 9_000_000_002


@pytest.fixture
async def db_cleanup_onboarding(test_user_id):
    """Cleanup onboarding_progress and onboarding_hints records after test."""
    yield
    from database import get_pool
    pool = await get_pool()
    await pool.execute("DELETE FROM onboarding_hints WHERE user_id = $1", test_user_id)
    await pool.execute("DELETE FROM onboarding_progress WHERE user_id = $1", test_user_id)


# Tests for is_new_user

async def test_is_new_user_true(test_user_id, db_cleanup_onboarding):
    """Test is_new_user returns True for user without progress."""
    result = await is_new_user(test_user_id)
    assert result is True


async def test_is_new_user_false(test_user_id, db_cleanup_onboarding):
    """Test is_new_user returns False for user with progress."""
    await create_onboarding_progress(test_user_id)
    result = await is_new_user(test_user_id)
    assert result is False


# Tests for start_tutorial

async def test_start_tutorial_creates_progress(test_user_id, db_cleanup_onboarding):
    """Test start_tutorial creates onboarding progress record."""
    result = await start_tutorial(test_user_id, has_referral=False)
    
    assert result is not None
    assert isinstance(result, dict)
    assert result['step'] == 0
    assert result['total_steps'] == TUTORIAL_STEPS
    
    # Verify progress was created
    progress = await get_onboarding_progress(test_user_id)
    assert progress is not None
    assert progress['current_step'] == 0


async def test_start_tutorial_with_referral(test_user_id, db_cleanup_onboarding):
    """Test start_tutorial with referral includes extra step."""
    result = await start_tutorial(test_user_id, has_referral=True)
    
    assert result is not None
    assert result['step'] == 0
    assert result['total_steps'] == TUTORIAL_STEPS + 1
    assert result['has_referral'] is True


# Tests for get_tutorial_step

async def test_get_tutorial_step_welcome(test_user_id):
    """Test getting welcome step (step 0)."""
    result = await get_tutorial_step(test_user_id, 0, has_referral=False)
    
    assert result['step'] == 0
    assert result['total_steps'] == TUTORIAL_STEPS
    assert '🎮' in result['title']
    assert 'Привет' in result['message']
    assert '+20 баллов' in result['message']


async def test_get_tutorial_step_channel(test_user_id):
    """Test getting channel step (step 1)."""
    result = await get_tutorial_step(test_user_id, 1, has_referral=False)
    
    assert result['step'] == 1
    assert '📢' in result['title']
    assert 'Канал со скидками' in result['title']
    assert 'Шаг 1 из' in result['message']


async def test_get_tutorial_step_wishlist(test_user_id):
    """Test getting wishlist step (step 2)."""
    result = await get_tutorial_step(test_user_id, 2, has_referral=False)
    
    assert result['step'] == 2
    assert '💝' in result['title']
    assert 'вишлист' in result['message']
    assert '/wishlist' in result['message']


async def test_get_tutorial_step_minigames(test_user_id):
    """Test getting minigames step (step 3)."""
    result = await get_tutorial_step(test_user_id, 3, has_referral=False)
    
    assert result['step'] == 3
    assert '🎮' in result['title']
    assert 'Мини-игры' in result['title']
    assert '/games' in result['message']


async def test_get_tutorial_step_shop(test_user_id):
    """Test getting shop step (step 4)."""
    result = await get_tutorial_step(test_user_id, 4, has_referral=False)
    
    assert result['step'] == 4
    assert '🏪' in result['title']
    assert 'Магазин призов' in result['title']
    assert '/shop' in result['message']


async def test_get_tutorial_step_referral(test_user_id):
    """Test getting referral step (step 5) with has_referral=True."""
    result = await get_tutorial_step(test_user_id, 5, has_referral=True)
    
    assert result['step'] == 5
    assert '👥' in result['title']
    assert 'Реферальная программа' in result['title']
    assert '+50 баллов' in result['message']
    assert '/invite' in result['message']


async def test_get_tutorial_step_boundary_negative(test_user_id):
    """Test get_tutorial_step with negative step (should clamp to 0)."""
    result = await get_tutorial_step(test_user_id, -5, has_referral=False)
    
    assert result['step'] == 0
    assert '🎮' in result['title']


async def test_get_tutorial_step_boundary_high(test_user_id):
    """Test get_tutorial_step with step beyond max (should clamp to max)."""
    result = await get_tutorial_step(test_user_id, 100, has_referral=False)
    
    # Step should be clamped to total_steps
    assert result['step'] == TUTORIAL_STEPS
    assert result['total_steps'] == TUTORIAL_STEPS


# Tests for next_tutorial_step

async def test_next_tutorial_step_increment(test_user_id, db_cleanup_onboarding):
    """Test next_tutorial_step increments step."""
    await create_onboarding_progress(test_user_id)
    await update_onboarding_step(test_user_id, 2)
    
    new_step = await next_tutorial_step(test_user_id)
    assert new_step == 3
    
    progress = await get_onboarding_progress(test_user_id)
    assert progress['current_step'] == 3


async def test_next_tutorial_step_at_boundary(test_user_id, db_cleanup_onboarding):
    """Test next_tutorial_step at max boundary (should not exceed)."""
    await create_onboarding_progress(test_user_id)
    max_step = TUTORIAL_STEPS + 1
    await update_onboarding_step(test_user_id, max_step)
    
    new_step = await next_tutorial_step(test_user_id)
    assert new_step == max_step  # Should stay at max
    
    progress = await get_onboarding_progress(test_user_id)
    assert progress['current_step'] == max_step


async def test_next_tutorial_step_no_progress(test_user_id, db_cleanup_onboarding):
    """Test next_tutorial_step with no progress record (should return 0)."""
    new_step = await next_tutorial_step(test_user_id)
    assert new_step == 0


# Tests for prev_tutorial_step

async def test_prev_tutorial_step_decrement(test_user_id, db_cleanup_onboarding):
    """Test prev_tutorial_step decrements step."""
    await create_onboarding_progress(test_user_id)
    await update_onboarding_step(test_user_id, 3)
    
    new_step = await prev_tutorial_step(test_user_id)
    assert new_step == 2
    
    progress = await get_onboarding_progress(test_user_id)
    assert progress['current_step'] == 2


async def test_prev_tutorial_step_at_zero(test_user_id, db_cleanup_onboarding):
    """Test prev_tutorial_step at step 0 (should stay at 0)."""
    await create_onboarding_progress(test_user_id)
    await update_onboarding_step(test_user_id, 0)
    
    new_step = await prev_tutorial_step(test_user_id)
    assert new_step == 0
    
    progress = await get_onboarding_progress(test_user_id)
    assert progress['current_step'] == 0


async def test_prev_tutorial_step_no_progress(test_user_id, db_cleanup_onboarding):
    """Test prev_tutorial_step with no progress record (should return 0)."""
    new_step = await prev_tutorial_step(test_user_id)
    assert new_step == 0


# Integration tests

async def test_tutorial_flow_forward(test_user_id, db_cleanup_onboarding):
    """Test complete forward flow through tutorial."""
    # Start tutorial
    result = await start_tutorial(test_user_id, has_referral=False)
    assert result['step'] == 0
    
    # Move through steps
    for expected_step in range(1, TUTORIAL_STEPS + 1):
        new_step = await next_tutorial_step(test_user_id)
        assert new_step == expected_step


async def test_tutorial_flow_backward(test_user_id, db_cleanup_onboarding):
    """Test backward navigation through tutorial."""
    await create_onboarding_progress(test_user_id)
    await update_onboarding_step(test_user_id, 4)
    
    # Move backward through steps
    for expected_step in [3, 2, 1, 0]:
        new_step = await prev_tutorial_step(test_user_id)
        assert new_step == expected_step
    
    # Try to go below 0
    new_step = await prev_tutorial_step(test_user_id)
    assert new_step == 0


async def test_tutorial_step_validation(test_user_id, db_cleanup_onboarding):
    """Test step boundary validation in get_tutorial_step."""
    # Test all valid steps
    for step in range(0, TUTORIAL_STEPS + 1):
        result = await get_tutorial_step(test_user_id, step, has_referral=False)
        assert result is not None
        assert 'message' in result
        assert 'title' in result
