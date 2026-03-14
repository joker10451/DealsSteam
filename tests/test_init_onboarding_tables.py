"""
Test that onboarding tables are initialized correctly during bot startup.
"""
import pytest
from database import init_db, get_pool


async def test_init_db_creates_onboarding_tables():
    """Test that init_db() creates onboarding tables."""
    # Call init_db which should create all tables including onboarding tables
    await init_db()
    
    pool = await get_pool()
    
    # Verify onboarding_progress table exists
    result1 = await pool.fetchval("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'onboarding_progress'
        )
    """)
    assert result1 is True, "onboarding_progress table should exist"
    
    # Verify onboarding_hints table exists
    result2 = await pool.fetchval("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'onboarding_hints'
        )
    """)
    assert result2 is True, "onboarding_hints table should exist"
    
    # Verify index exists
    result3 = await pool.fetchval("""
        SELECT EXISTS (
            SELECT FROM pg_indexes 
            WHERE tablename = 'onboarding_hints' 
            AND indexname = 'idx_onboarding_hints_user'
        )
    """)
    assert result3 is True, "idx_onboarding_hints_user index should exist"


async def test_onboarding_progress_table_structure():
    """Test that onboarding_progress table has correct columns."""
    await init_db()
    
    pool = await get_pool()
    
    # Get column names
    columns = await pool.fetch("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'onboarding_progress'
        ORDER BY ordinal_position
    """)
    
    column_names = [col['column_name'] for col in columns]
    
    # Verify all required columns exist
    assert 'user_id' in column_names
    assert 'current_step' in column_names
    assert 'status' in column_names
    assert 'completed_at' in column_names
    assert 'skipped_at' in column_names
    assert 'created_at' in column_names
    assert 'updated_at' in column_names


async def test_onboarding_hints_table_structure():
    """Test that onboarding_hints table has correct columns."""
    await init_db()
    
    pool = await get_pool()
    
    # Get column names
    columns = await pool.fetch("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'onboarding_hints'
        ORDER BY ordinal_position
    """)
    
    column_names = [col['column_name'] for col in columns]
    
    # Verify all required columns exist
    assert 'id' in column_names
    assert 'user_id' in column_names
    assert 'hint_type' in column_names
    assert 'shown_at' in column_names


async def test_onboarding_tables_idempotent():
    """Test that calling init_db multiple times doesn't cause errors."""
    # Call init_db twice - should not raise any errors
    await init_db()
    await init_db()
    
    pool = await get_pool()
    
    # Tables should still exist
    result = await pool.fetchval("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'onboarding_progress'
        )
    """)
    assert result is True
