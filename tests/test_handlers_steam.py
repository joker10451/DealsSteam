"""
Tests for Steam integration command handlers.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from aiogram.types import Message, User, Chat
from handlers.steam import cmd_steam_sync
from config import STEAM_SYNC_COOLDOWN_HOURS


class TestSteamSyncCommand:
    """Tests for /steamsync command handler."""
    
    async def test_sync_without_linked_account(self, db_cleanup):
        """Test sync attempt without linked Steam account."""
        # Create mock message
        message = MagicMock(spec=Message)
        message.from_user = MagicMock(spec=User)
        message.from_user.id = 9_000_000_001
        message.answer = AsyncMock()
        
        # Call command
        await cmd_steam_sync(message)
        
        # Verify error message sent
        message.answer.assert_called_once()
        call_args = message.answer.call_args[0][0]
        assert "не привязан" in call_args.lower()
    
    @patch('handlers.steam.steam_get_user')
    @patch('handlers.steam.fetch_wishlist')
    @patch('handlers.steam.fetch_library')
    @patch('handlers.steam.wishlist_add')
    @patch('handlers.steam.steam_library_replace')
    @patch('handlers.steam.steam_update_sync_time')
    async def test_successful_first_sync(
        self, 
        mock_update_sync, 
        mock_library_replace, 
        mock_wishlist_add,
        mock_fetch_library,
        mock_fetch_wishlist,
        mock_get_user,
        db_cleanup
    ):
        """Test successful first sync (no cooldown)."""
        # Mock user with no previous sync
        mock_get_user.return_value = {
            "user_id": 9_000_000_001,
            "steam_id": "76561198012345678",
            "wishlist_sync_enabled": True,
            "library_sync_enabled": True,
            "last_wishlist_sync": None,
            "last_library_sync": None
        }
        
        # Mock API responses
        mock_fetch_wishlist.return_value = [
            {"appid": 12345, "name": "Game One"},
            {"appid": 67890, "name": "Game Two"}
        ]
        mock_fetch_library.return_value = [12345, 67890, 11111]
        mock_wishlist_add.return_value = True
        
        # Create mock message
        message = MagicMock(spec=Message)
        message.from_user = MagicMock(spec=User)
        message.from_user.id = 9_000_000_001
        message.answer = AsyncMock()
        
        # Call command
        await cmd_steam_sync(message)
        
        # Verify API calls
        mock_fetch_wishlist.assert_called_once_with("76561198012345678")
        mock_fetch_library.assert_called_once_with("76561198012345678")
        
        # Verify database updates
        assert mock_wishlist_add.call_count == 2
        mock_library_replace.assert_called_once_with(9_000_000_001, [12345, 67890, 11111])
        assert mock_update_sync.call_count == 2
        
        # Verify success message
        assert message.answer.call_count >= 2  # Progress + success message
        final_call = message.answer.call_args_list[-1][0][0]
        assert "завершена" in final_call.lower()
        assert "2 игр" in final_call.lower()  # Wishlist count
        assert "3 игр" in final_call.lower()  # Library count
    
    @patch('handlers.steam.steam_get_user')
    async def test_sync_cooldown_enforcement(self, mock_get_user, db_cleanup):
        """Test that cooldown prevents sync within 1 hour."""
        # Mock user with recent sync (30 minutes ago)
        recent_sync = datetime.now().astimezone() - timedelta(minutes=30)
        mock_get_user.return_value = {
            "user_id": 9_000_000_001,
            "steam_id": "76561198012345678",
            "wishlist_sync_enabled": True,
            "library_sync_enabled": True,
            "last_wishlist_sync": recent_sync,
            "last_library_sync": None
        }
        
        # Create mock message
        message = MagicMock(spec=Message)
        message.from_user = MagicMock(spec=User)
        message.from_user.id = 9_000_000_001
        message.answer = AsyncMock()
        
        # Call command
        await cmd_steam_sync(message)
        
        # Verify cooldown message sent
        message.answer.assert_called_once()
        call_args = message.answer.call_args[0][0]
        assert "доступна через" in call_args.lower()
        assert "мин" in call_args.lower()
    
    @patch('handlers.steam.steam_get_user')
    @patch('handlers.steam.fetch_wishlist')
    @patch('handlers.steam.fetch_library')
    @patch('handlers.steam.wishlist_add')
    @patch('handlers.steam.steam_library_replace')
    @patch('handlers.steam.steam_update_sync_time')
    async def test_sync_after_cooldown_expired(
        self,
        mock_update_sync,
        mock_library_replace,
        mock_wishlist_add,
        mock_fetch_library,
        mock_fetch_wishlist,
        mock_get_user,
        db_cleanup
    ):
        """Test that sync works after cooldown period expires."""
        # Mock user with old sync (2 hours ago, beyond 1-hour cooldown)
        old_sync = datetime.now().astimezone() - timedelta(hours=2)
        mock_get_user.return_value = {
            "user_id": 9_000_000_001,
            "steam_id": "76561198012345678",
            "wishlist_sync_enabled": True,
            "library_sync_enabled": True,
            "last_wishlist_sync": old_sync,
            "last_library_sync": old_sync
        }
        
        # Mock API responses
        mock_fetch_wishlist.return_value = [{"appid": 12345, "name": "Game One"}]
        mock_fetch_library.return_value = [12345]
        mock_wishlist_add.return_value = True
        
        # Create mock message
        message = MagicMock(spec=Message)
        message.from_user = MagicMock(spec=User)
        message.from_user.id = 9_000_000_001
        message.answer = AsyncMock()
        
        # Call command
        await cmd_steam_sync(message)
        
        # Verify sync proceeded (not blocked by cooldown)
        mock_fetch_wishlist.assert_called_once()
        mock_fetch_library.assert_called_once()
        
        # Verify success message (not cooldown message)
        final_call = message.answer.call_args_list[-1][0][0]
        assert "завершена" in final_call.lower()
        assert "доступна через" not in final_call.lower()
    
    @patch('handlers.steam.steam_get_user')
    @patch('handlers.steam.fetch_wishlist')
    @patch('handlers.steam.fetch_library')
    async def test_sync_with_private_profile(
        self,
        mock_fetch_library,
        mock_fetch_wishlist,
        mock_get_user,
        db_cleanup
    ):
        """Test sync with private Steam profile (no data returned)."""
        # Mock user
        mock_get_user.return_value = {
            "user_id": 9_000_000_001,
            "steam_id": "76561198012345678",
            "wishlist_sync_enabled": True,
            "library_sync_enabled": True,
            "last_wishlist_sync": None,
            "last_library_sync": None
        }
        
        # Mock private profile (empty responses)
        mock_fetch_wishlist.return_value = []
        mock_fetch_library.return_value = []
        
        # Create mock message
        message = MagicMock(spec=Message)
        message.from_user = MagicMock(spec=User)
        message.from_user.id = 9_000_000_001
        message.answer = AsyncMock()
        
        # Call command
        await cmd_steam_sync(message)
        
        # Verify error message about private profile
        final_call = message.answer.call_args_list[-1][0][0]
        assert "не удалось" in final_call.lower()
        assert "приватный" in final_call.lower()
    
    @patch('handlers.steam.steam_get_user')
    @patch('handlers.steam.fetch_wishlist')
    @patch('handlers.steam.fetch_library')
    @patch('handlers.steam.wishlist_add')
    @patch('handlers.steam.steam_library_replace')
    @patch('handlers.steam.steam_update_sync_time')
    async def test_sync_with_partial_data(
        self,
        mock_update_sync,
        mock_library_replace,
        mock_wishlist_add,
        mock_fetch_library,
        mock_fetch_wishlist,
        mock_get_user,
        db_cleanup
    ):
        """Test sync when only wishlist or library is available."""
        # Mock user
        mock_get_user.return_value = {
            "user_id": 9_000_000_001,
            "steam_id": "76561198012345678",
            "wishlist_sync_enabled": True,
            "library_sync_enabled": True,
            "last_wishlist_sync": None,
            "last_library_sync": None
        }
        
        # Mock wishlist available but library private
        mock_fetch_wishlist.return_value = [{"appid": 12345, "name": "Game One"}]
        mock_fetch_library.return_value = []
        mock_wishlist_add.return_value = True
        
        # Create mock message
        message = MagicMock(spec=Message)
        message.from_user = MagicMock(spec=User)
        message.from_user.id = 9_000_000_001
        message.answer = AsyncMock()
        
        # Call command
        await cmd_steam_sync(message)
        
        # Verify partial success message
        final_call = message.answer.call_args_list[-1][0][0]
        assert "завершена" in final_call.lower()
        assert "1 игр" in final_call.lower()  # Only wishlist count shown
    
    @patch('handlers.steam.steam_get_user')
    async def test_sync_uses_most_recent_sync_time(self, mock_get_user, db_cleanup):
        """Test that cooldown uses the most recent of wishlist or library sync."""
        # Mock user with different sync times
        recent_wishlist = datetime.now().astimezone() - timedelta(minutes=20)
        older_library = datetime.now().astimezone() - timedelta(hours=2)
        
        mock_get_user.return_value = {
            "user_id": 9_000_000_001,
            "steam_id": "76561198012345678",
            "wishlist_sync_enabled": True,
            "library_sync_enabled": True,
            "last_wishlist_sync": recent_wishlist,  # More recent
            "last_library_sync": older_library
        }
        
        # Create mock message
        message = MagicMock(spec=Message)
        message.from_user = MagicMock(spec=User)
        message.from_user.id = 9_000_000_001
        message.answer = AsyncMock()
        
        # Call command
        await cmd_steam_sync(message)
        
        # Verify cooldown is based on most recent sync (wishlist)
        call_args = message.answer.call_args[0][0]
        assert "доступна через" in call_args.lower()
