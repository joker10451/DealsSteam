"""
Tests for Steam API integration module.
"""
import pytest
from unittest.mock import patch, AsyncMock
from steam_api import resolve_steam_id, _resolve_vanity_url, fetch_wishlist, fetch_library


class TestResolveSteamId:
    """Tests for resolve_steam_id function."""
    
    async def test_direct_steam_id64(self):
        """Test with direct Steam ID64 input."""
        steam_id = "76561198012345678"
        result = await resolve_steam_id(steam_id)
        assert result == steam_id
    
    async def test_profile_url_with_id64(self):
        """Test with profile URL containing Steam ID64."""
        url = "https://steamcommunity.com/profiles/76561198012345678"
        result = await resolve_steam_id(url)
        assert result == "76561198012345678"
    
    async def test_profile_url_with_id64_no_https(self):
        """Test with profile URL without https."""
        url = "steamcommunity.com/profiles/76561198012345678"
        result = await resolve_steam_id(url)
        assert result == "76561198012345678"
    
    @patch('steam_api._resolve_vanity_url')
    async def test_vanity_url(self, mock_resolve):
        """Test with vanity URL."""
        mock_resolve.return_value = "76561198012345678"
        url = "https://steamcommunity.com/id/testuser"
        result = await resolve_steam_id(url)
        assert result == "76561198012345678"
        mock_resolve.assert_called_once_with("testuser")
    
    @patch('steam_api._resolve_vanity_url')
    async def test_vanity_url_no_https(self, mock_resolve):
        """Test with vanity URL without https."""
        mock_resolve.return_value = "76561198012345678"
        url = "steamcommunity.com/id/testuser"
        result = await resolve_steam_id(url)
        assert result == "76561198012345678"
        mock_resolve.assert_called_once_with("testuser")
    
    async def test_invalid_format(self):
        """Test with invalid input format."""
        result = await resolve_steam_id("invalid_input")
        assert result is None
    
    async def test_empty_input(self):
        """Test with empty input."""
        result = await resolve_steam_id("")
        assert result is None
    
    async def test_invalid_steam_id_format(self):
        """Test with invalid Steam ID64 format (wrong prefix)."""
        result = await resolve_steam_id("12345678901234567")
        assert result is None


class TestResolveVanityUrl:
    """Tests for _resolve_vanity_url function."""
    
    @patch('steam_api.STEAM_API_KEY', 'test_api_key')
    @patch('steam_api.fetch_with_retry')
    async def test_successful_resolution(self, mock_fetch):
        """Test successful vanity URL resolution."""
        mock_fetch.return_value = {
            "response": {
                "steamid": "76561198012345678",
                "success": 1
            }
        }
        result = await _resolve_vanity_url("testuser")
        assert result == "76561198012345678"
    
    @patch('steam_api.fetch_with_retry')
    async def test_vanity_not_found(self, mock_fetch):
        """Test vanity URL not found."""
        mock_fetch.return_value = {
            "response": {
                "success": 42  # 42 means not found
            }
        }
        result = await _resolve_vanity_url("nonexistent")
        assert result is None
    
    @patch('steam_api.fetch_with_retry')
    async def test_api_failure(self, mock_fetch):
        """Test API failure."""
        mock_fetch.return_value = None
        result = await _resolve_vanity_url("testuser")
        assert result is None
    
    @patch('steam_api.STEAM_API_KEY', '')
    async def test_missing_api_key(self):
        """Test with missing API key."""
        result = await _resolve_vanity_url("testuser")
        assert result is None


class TestFetchWishlist:
    """Tests for fetch_wishlist function."""
    
    @patch('steam_api.fetch_with_retry')
    async def test_successful_wishlist_fetch(self, mock_fetch):
        """Test successful wishlist fetch with valid data."""
        mock_fetch.return_value = {
            "12345": {"name": "Game One"},
            "67890": {"name": "Game Two"},
            "11111": {"name": "Game Three"}
        }
        result = await fetch_wishlist("76561198012345678")
        assert len(result) == 3
        assert result[0] == {"appid": 12345, "name": "Game One"}
        assert result[1] == {"appid": 67890, "name": "Game Two"}
        assert result[2] == {"appid": 11111, "name": "Game Three"}
    
    @patch('steam_api.fetch_with_retry')
    async def test_empty_wishlist(self, mock_fetch):
        """Test with empty wishlist."""
        mock_fetch.return_value = {}
        result = await fetch_wishlist("76561198012345678")
        assert result == []
    
    @patch('steam_api.fetch_with_retry')
    async def test_wishlist_limit_100_games(self, mock_fetch):
        """Test that wishlist is limited to 100 games."""
        # Create a wishlist with 150 games
        mock_wishlist = {str(i): {"name": f"Game {i}"} for i in range(150)}
        mock_fetch.return_value = mock_wishlist
        
        result = await fetch_wishlist("76561198012345678")
        assert len(result) == 100
    
    @patch('steam_api.fetch_with_retry')
    async def test_private_wishlist(self, mock_fetch):
        """Test with private wishlist (no response)."""
        mock_fetch.return_value = None
        result = await fetch_wishlist("76561198012345678")
        assert result == []
    
    @patch('steam_api.fetch_with_retry')
    async def test_invalid_response_format(self, mock_fetch):
        """Test with invalid response format."""
        mock_fetch.return_value = []  # Should be dict, not list
        result = await fetch_wishlist("76561198012345678")
        assert result == []
    
    async def test_empty_steam_id(self):
        """Test with empty Steam ID."""
        result = await fetch_wishlist("")
        assert result == []
    
    async def test_invalid_steam_id_format(self):
        """Test with invalid Steam ID64 format."""
        result = await fetch_wishlist("12345678901234567")
        assert result == []
    
    @patch('steam_api.fetch_with_retry')
    async def test_malformed_game_entry(self, mock_fetch):
        """Test with malformed game entries in wishlist."""
        mock_fetch.return_value = {
            "12345": {"name": "Valid Game"},
            "invalid": {"name": "Invalid AppID"},  # Non-numeric appid
            "67890": None,  # Missing game data
            "11111": {"name": "Another Valid Game"}
        }
        result = await fetch_wishlist("76561198012345678")
        # Should skip invalid entries and return only valid ones
        assert len(result) == 2
        assert result[0] == {"appid": 12345, "name": "Valid Game"}
        assert result[1] == {"appid": 11111, "name": "Another Valid Game"}
    
    @patch('steam_api.fetch_with_retry')
    async def test_missing_game_name(self, mock_fetch):
        """Test with missing game name in response."""
        mock_fetch.return_value = {
            "12345": {},  # No name field
            "67890": {"name": "Valid Game"}
        }
        result = await fetch_wishlist("76561198012345678")
        assert len(result) == 2
        assert result[0] == {"appid": 12345, "name": "Unknown Game"}
        assert result[1] == {"appid": 67890, "name": "Valid Game"}
    
    @patch('steam_api.fetch_with_retry')
    async def test_api_exception(self, mock_fetch):
        """Test handling of API exceptions."""
        mock_fetch.side_effect = Exception("Network error")
        result = await fetch_wishlist("76561198012345678")
        assert result == []


class TestFetchLibrary:
    """Tests for fetch_library function."""
    
    @patch('steam_api.STEAM_API_KEY', 'test_api_key')
    @patch('steam_api.fetch_with_retry')
    async def test_successful_library_fetch(self, mock_fetch):
        """Test successful library fetch with valid data."""
        mock_fetch.return_value = {
            "response": {
                "game_count": 3,
                "games": [
                    {"appid": 12345},
                    {"appid": 67890},
                    {"appid": 11111}
                ]
            }
        }
        result = await fetch_library("76561198012345678")
        assert len(result) == 3
        assert result == [12345, 67890, 11111]
    
    @patch('steam_api.STEAM_API_KEY', 'test_api_key')
    @patch('steam_api.fetch_with_retry')
    async def test_empty_library(self, mock_fetch):
        """Test with empty library."""
        mock_fetch.return_value = {
            "response": {
                "game_count": 0,
                "games": []
            }
        }
        result = await fetch_library("76561198012345678")
        assert result == []
    
    @patch('steam_api.STEAM_API_KEY', 'test_api_key')
    @patch('steam_api.fetch_with_retry')
    async def test_private_library(self, mock_fetch):
        """Test with private library (no games key in response)."""
        mock_fetch.return_value = {
            "response": {}
        }
        result = await fetch_library("76561198012345678")
        assert result == []
    
    @patch('steam_api.STEAM_API_KEY', 'test_api_key')
    @patch('steam_api.fetch_with_retry')
    async def test_no_response(self, mock_fetch):
        """Test with no response from API."""
        mock_fetch.return_value = None
        result = await fetch_library("76561198012345678")
        assert result == []
    
    async def test_empty_steam_id(self):
        """Test with empty Steam ID."""
        result = await fetch_library("")
        assert result == []
    
    async def test_invalid_steam_id_format(self):
        """Test with invalid Steam ID64 format."""
        result = await fetch_library("12345678901234567")
        assert result == []
    
    @patch('steam_api.STEAM_API_KEY', '')
    async def test_missing_api_key(self):
        """Test with missing API key."""
        result = await fetch_library("76561198012345678")
        assert result == []
    
    @patch('steam_api.STEAM_API_KEY', 'test_api_key')
    @patch('steam_api.fetch_with_retry')
    async def test_malformed_game_entry(self, mock_fetch):
        """Test with malformed game entries in library."""
        mock_fetch.return_value = {
            "response": {
                "game_count": 4,
                "games": [
                    {"appid": 12345},
                    {"appid": "invalid"},  # Invalid appid type
                    {},  # Missing appid
                    {"appid": 67890}
                ]
            }
        }
        result = await fetch_library("76561198012345678")
        # Should skip invalid entries and return only valid ones
        assert len(result) == 2
        assert result == [12345, 67890]
    
    @patch('steam_api.STEAM_API_KEY', 'test_api_key')
    @patch('steam_api.fetch_with_retry')
    async def test_api_exception(self, mock_fetch):
        """Test handling of API exceptions."""
        mock_fetch.side_effect = Exception("Network error")
        result = await fetch_library("76561198012345678")
        assert result == []
    
    @patch('steam_api.STEAM_API_KEY', 'test_api_key')
    @patch('steam_api.fetch_with_retry')
    async def test_large_library(self, mock_fetch):
        """Test with large library (no artificial limit like wishlist)."""
        # Create a library with 500 games
        mock_games = [{"appid": i} for i in range(500)]
        mock_fetch.return_value = {
            "response": {
                "game_count": 500,
                "games": mock_games
            }
        }
        result = await fetch_library("76561198012345678")
        # Library should not be limited (unlike wishlist which is limited to 100)
        assert len(result) == 500
