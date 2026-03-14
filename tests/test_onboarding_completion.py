"""
Tests for onboarding tutorial completion functions.
"""
import pytest
from onboarding import (
    complete_tutorial,
    skip_tutorial,
    get_onboarding_progress,
    COMPLETION_BONUS,
)
from database import (
    create_onboarding_progress,
    update_onboarding_step,
    complete_onboarding,
    skip_onboarding,
)


@pytest.fixture
async def test_user_id():
    """Fixture providing a test user ID for onboarding tests."""
    return 9_000_000_003


@pytest.fixture
async def db_cleanup_onboarding(test_user_id):
    """Cleanup onboarding_progress, onboarding_hints, and user_scores after test."""
    yield
    from database import get_pool
    pool = await get_pool()
    await pool.execute("DELETE FROM onboarding_hints WHERE user_id = $1", test_user_id)
    await pool.execute("DELETE FROM onboarding_progress WHERE user_id = $1", test_user_id)
    await pool.execute("DELETE FROM user_scores WHERE user_id = $1", test_user_id)
    await pool.execute("DELETE FROM user_score_history WHERE user_id = $1", test_user_id)
    await pool.execute("DELETE FROM user_achievements WHERE user_id = $1", test_user_id)


# Tests for complete_tutorial

async def test_complete_tutorial_success(test_user_id, db_cleanup_onboarding):
    """Test complete_tutorial successfully completes and awards bonus."""
    # Create progress record
    await create_onboarding_progress(test_user_id)
    await update_onboarding_step(test_user_id, 4)
    
    # Complete tutorial
    result = await complete_tutorial(test_user_id)
    
    assert result['success'] is True
    assert result['points'] == COMPLETION_BONUS
    assert 'Поздравляем' in result['message']
    assert f'+{COMPLETION_BONUS} баллов' in result['message']
    
    # Verify progress status updated
    progress = await get_onboarding_progress(test_user_id)
    assert progress is not None
    assert progress['status'] == 'completed'
    assert progress['completed_at'] is not None
    
    # Verify score was awarded (may include achievement bonuses)
    from minigames import get_user_score
    score = await get_user_score(test_user_id)
    assert score['total_score'] >= COMPLETION_BONUS  # May include achievement bonuses


async def test_complete_tutorial_no_progress(test_user_id, db_cleanup_onboarding):
    """Test complete_tutorial fails when no progress exists."""
    result = await complete_tutorial(test_user_id)
    
    assert result['success'] is False
    assert 'не найден' in result['message']


async def test_complete_tutorial_already_completed(test_user_id, db_cleanup_onboarding):
    """Test complete_tutorial fails when already completed."""
    # Create and complete progress
    await create_onboarding_progress(test_user_id)
    await complete_onboarding(test_user_id)
    
    # Try to complete again
    result = await complete_tutorial(test_user_id)
    
    assert result['success'] is False
    assert 'уже завершил' in result['message']


async def test_complete_tutorial_awards_achievements(test_user_id, db_cleanup_onboarding):
    """Test complete_tutorial can trigger achievement unlocks."""
    # Create progress record
    await create_onboarding_progress(test_user_id)
    
    # Complete tutorial
    result = await complete_tutorial(test_user_id)
    
    assert result['success'] is True
    assert 'new_achievements' in result
    # new_achievements may be empty list or contain achievements


async def test_complete_tutorial_logs_score_history(test_user_id, db_cleanup_onboarding):
    """Test complete_tutorial logs score in history with correct reason."""
    await create_onboarding_progress(test_user_id)
    
    result = await complete_tutorial(test_user_id)
    assert result['success'] is True
    
    # Verify score history entry
    from database import get_pool
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT points, reason FROM user_score_history WHERE user_id = $1",
        test_user_id
    )
    
    assert row is not None
    assert row['points'] == COMPLETION_BONUS
    assert row['reason'] == 'onboarding_completed'


# Tests for skip_tutorial

async def test_skip_tutorial_success(test_user_id, db_cleanup_onboarding):
    """Test skip_tutorial successfully skips without bonus."""
    # Create progress record
    await create_onboarding_progress(test_user_id)
    await update_onboarding_step(test_user_id, 2)
    
    # Skip tutorial
    result = await skip_tutorial(test_user_id)
    
    assert result['success'] is True
    assert 'пропущен' in result['message']
    assert '/tutorial' in result['message']
    
    # Verify progress status updated
    progress = await get_onboarding_progress(test_user_id)
    assert progress is not None
    assert progress['status'] == 'skipped'
    assert progress['skipped_at'] is not None
    
    # Verify NO score was awarded
    from minigames import get_user_score
    score = await get_user_score(test_user_id)
    assert score['total_score'] == 0


async def test_skip_tutorial_no_progress(test_user_id, db_cleanup_onboarding):
    """Test skip_tutorial fails when no progress exists."""
    result = await skip_tutorial(test_user_id)
    
    assert result['success'] is False
    assert 'не найден' in result['message']


async def test_skip_tutorial_already_completed(test_user_id, db_cleanup_onboarding):
    """Test skip_tutorial fails when already completed."""
    # Create and complete progress
    await create_onboarding_progress(test_user_id)
    await complete_onboarding(test_user_id)
    
    # Try to skip
    result = await skip_tutorial(test_user_id)
    
    assert result['success'] is False
    assert 'уже завершил' in result['message']


async def test_skip_tutorial_already_skipped(test_user_id, db_cleanup_onboarding):
    """Test skip_tutorial fails when already skipped."""
    # Create and skip progress
    await create_onboarding_progress(test_user_id)
    await skip_onboarding(test_user_id)
    
    # Try to skip again
    result = await skip_tutorial(test_user_id)
    
    assert result['success'] is False
    assert 'уже пропустил' in result['message']


# Tests for get_onboarding_progress

async def test_get_onboarding_progress_exists(test_user_id, db_cleanup_onboarding):
    """Test get_onboarding_progress returns progress when exists."""
    # Create progress
    await create_onboarding_progress(test_user_id)
    await update_onboarding_step(test_user_id, 3)
    
    # Get progress
    progress = await get_onboarding_progress(test_user_id)
    
    assert progress is not None
    assert progress['user_id'] == test_user_id
    assert progress['current_step'] == 3
    assert progress['status'] == 'in_progress'
    assert progress['created_at'] is not None
    assert progress['updated_at'] is not None


async def test_get_onboarding_progress_not_exists(test_user_id, db_cleanup_onboarding):
    """Test get_onboarding_progress returns None when no progress."""
    progress = await get_onboarding_progress(test_user_id)
    
    assert progress is None


async def test_get_onboarding_progress_completed(test_user_id, db_cleanup_onboarding):
    """Test get_onboarding_progress returns completed status."""
    await create_onboarding_progress(test_user_id)
    await complete_onboarding(test_user_id)
    
    progress = await get_onboarding_progress(test_user_id)
    
    assert progress is not None
    assert progress['status'] == 'completed'
    assert progress['completed_at'] is not None


async def test_get_onboarding_progress_skipped(test_user_id, db_cleanup_onboarding):
    """Test get_onboarding_progress returns skipped status."""
    await create_onboarding_progress(test_user_id)
    await skip_onboarding(test_user_id)
    
    progress = await get_onboarding_progress(test_user_id)
    
    assert progress is not None
    assert progress['status'] == 'skipped'
    assert progress['skipped_at'] is not None


# Integration tests

async def test_complete_then_skip_fails(test_user_id, db_cleanup_onboarding):
    """Test cannot skip after completing."""
    await create_onboarding_progress(test_user_id)
    
    # Complete first
    result1 = await complete_tutorial(test_user_id)
    assert result1['success'] is True
    
    # Try to skip
    result2 = await skip_tutorial(test_user_id)
    assert result2['success'] is False


async def test_skip_then_complete_fails(test_user_id, db_cleanup_onboarding):
    """Test cannot complete after skipping."""
    await create_onboarding_progress(test_user_id)
    
    # Skip first
    result1 = await skip_tutorial(test_user_id)
    assert result1['success'] is True
    
    # Try to complete (should fail because status is 'skipped')
    result2 = await complete_tutorial(test_user_id)
    assert result2['success'] is False
    assert 'пропустил' in result2['message']


async def test_completion_bonus_only_once(test_user_id, db_cleanup_onboarding):
    """Test completion bonus is only awarded once."""
    await create_onboarding_progress(test_user_id)
    
    # Complete tutorial
    result1 = await complete_tutorial(test_user_id)
    assert result1['success'] is True
    
    # Check score (may include achievement bonuses)
    from minigames import get_user_score
    score1 = await get_user_score(test_user_id)
    initial_score = score1['total_score']
    assert initial_score >= COMPLETION_BONUS
    
    # Try to complete again (should fail)
    result2 = await complete_tutorial(test_user_id)
    assert result2['success'] is False
    
    # Score should not change
    score2 = await get_user_score(test_user_id)
    assert score2['total_score'] == initial_score
