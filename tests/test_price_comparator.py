"""
Tests for price_comparator module.
"""
import pytest
from unittest.mock import AsyncMock, patch
from price_comparator import compare_prices, _fetch_steam_price, _fetch_gog_price, _fetch_cheapshark_price


@pytest.mark.asyncio
async def test_compare_prices_uses_cache():
    """WHEN cached data exists and is fresh, compare_prices should return cached data without API calls.
    
    Validates: Requirements 3.6
    """
    game_title = "Cyberpunk 2077"
    cached_prices = {
        "Steam": {"price": "999", "discount": 50, "link": "https://steam.com", "currency": "RUB"}
    }
    
    with patch("price_comparator.price_cache_get", new_callable=AsyncMock) as mock_cache_get:
        mock_cache_get.return_value = {"prices": cached_prices, "cached_at": "2024-01-01"}
        
        result = await compare_prices(game_title)
        
        assert result == cached_prices
        mock_cache_get.assert_called_once_with(game_title)


@pytest.mark.asyncio
async def test_compare_prices_parallel_fetch():
    """WHEN cache miss occurs, compare_prices should query all stores in parallel.
    
    Validates: Requirements 3.1
    """
    game_title = "Witcher 3"
    
    with patch("price_comparator.price_cache_get", new_callable=AsyncMock) as mock_cache_get, \
         patch("price_comparator.price_cache_set", new_callable=AsyncMock) as mock_cache_set, \
         patch("price_comparator._fetch_steam_price", new_callable=AsyncMock) as mock_steam, \
         patch("price_comparator._fetch_gog_price", new_callable=AsyncMock) as mock_gog, \
         patch("price_comparator._fetch_epic_price", new_callable=AsyncMock) as mock_epic, \
         patch("price_comparator._fetch_cheapshark_price", new_callable=AsyncMock) as mock_cheapshark:
        
        mock_cache_get.return_value = None
        mock_steam.return_value = {"price": "500", "discount": 50, "link": "https://steam.com", "currency": "RUB"}
        mock_gog.return_value = {"price": "600", "discount": 40, "link": "https://gog.com", "currency": "RUB"}
        mock_epic.return_value = None
        mock_cheapshark.return_value = {"price": "5.99", "discount": 60, "link": "https://cheapshark.com", "currency": "USD"}
        
        result = await compare_prices(game_title)
        
        assert "Steam" in result
        assert "GOG" in result
        assert "CheapShark" in result
        assert "Epic Games" not in result
        
        mock_steam.assert_called_once()
        mock_gog.assert_called_once()
        mock_epic.assert_called_once()
        mock_cheapshark.assert_called_once()
        
        # Should cache the results
        mock_cache_set.assert_called_once()


@pytest.mark.asyncio
async def test_compare_prices_partial_failure():
    """WHEN some stores fail, compare_prices should continue with available stores.
    
    Validates: Requirements 3.7
    """
    game_title = "Elden Ring"
    
    with patch("price_comparator.price_cache_get", new_callable=AsyncMock) as mock_cache_get, \
         patch("price_comparator.price_cache_set", new_callable=AsyncMock) as mock_cache_set, \
         patch("price_comparator._fetch_steam_price", new_callable=AsyncMock) as mock_steam, \
         patch("price_comparator._fetch_gog_price", new_callable=AsyncMock) as mock_gog, \
         patch("price_comparator._fetch_epic_price", new_callable=AsyncMock) as mock_epic, \
         patch("price_comparator._fetch_cheapshark_price", new_callable=AsyncMock) as mock_cheapshark:
        
        mock_cache_get.return_value = None
        mock_steam.return_value = {"price": "1999", "discount": 0, "link": "https://steam.com", "currency": "RUB"}
        mock_gog.side_effect = Exception("Network error")
        mock_epic.return_value = None
        mock_cheapshark.side_effect = Exception("API error")
        
        result = await compare_prices(game_title)
        
        # Should have Steam result despite other failures
        assert "Steam" in result
        assert len(result) == 1
        
        # Should still cache partial results
        mock_cache_set.assert_called_once()


@pytest.mark.asyncio
async def test_compare_prices_timeout():
    """WHEN API calls exceed 5 seconds, compare_prices should timeout and return empty dict.
    
    Validates: Requirements 3.2
    """
    game_title = "Slow Game"
    
    async def slow_fetch(*args, **kwargs):
        import asyncio
        await asyncio.sleep(10)
        return None
    
    with patch("price_comparator.price_cache_get", new_callable=AsyncMock) as mock_cache_get, \
         patch("price_comparator._fetch_steam_price", new_callable=AsyncMock) as mock_steam:
        
        mock_cache_get.return_value = None
        mock_steam.side_effect = slow_fetch
        
        result = await compare_prices(game_title)
        
        assert result == {}


@pytest.mark.asyncio
async def test_fetch_steam_price_success():
    """WHEN Steam API returns valid data, _fetch_steam_price should parse it correctly.
    
    Validates: Requirements 3.1
    """
    game_title = "Portal 2"
    
    mock_response = {
        "items": [
            {
                "id": "620",
                "price": {
                    "final": 19900,
                    "initial": 39900
                }
            }
        ]
    }
    
    with patch("price_comparator.fetch_with_retry", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_response
        
        result = await _fetch_steam_price(game_title)
        
        assert result is not None
        assert result["price"] == "199"
        assert result["discount"] == 50
        assert "steampowered.com" in result["link"]
        assert result["currency"] == "RUB"


@pytest.mark.asyncio
async def test_fetch_steam_price_no_results():
    """WHEN Steam API returns no results, _fetch_steam_price should return None.
    
    Validates: Requirements 3.7
    """
    game_title = "Nonexistent Game"
    
    with patch("price_comparator.fetch_with_retry", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"items": []}
        
        result = await _fetch_steam_price(game_title)
        
        assert result is None


@pytest.mark.asyncio
async def test_fetch_gog_price_success():
    """WHEN GOG API returns valid data, _fetch_gog_price should parse it correctly.
    
    Validates: Requirements 3.1
    """
    game_title = "Witcher 3"
    
    mock_response = {
        "products": [
            {
                "url": "/game/the_witcher_3",
                "price": {
                    "finalAmount": "299",
                    "baseAmount": "999",
                    "discountPercentage": 70,
                    "currency": "RUB"
                }
            }
        ]
    }
    
    with patch("price_comparator.fetch_with_retry", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_response
        
        result = await _fetch_gog_price(game_title)
        
        assert result is not None
        assert result["price"] == "299"
        assert result["discount"] == 70
        assert "gog.com" in result["link"]
        assert result["currency"] == "RUB"


@pytest.mark.asyncio
async def test_fetch_cheapshark_price_success():
    """WHEN CheapShark API returns valid data, _fetch_cheapshark_price should parse it correctly.
    
    Validates: Requirements 3.1
    """
    game_title = "Half-Life"
    
    mock_response = [
        {
            "cheapest": "2.49",
            "normalPrice": "9.99",
            "cheapestDealID": "abc123"
        }
    ]
    
    with patch("price_comparator.fetch_with_retry", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_response
        
        result = await _fetch_cheapshark_price(game_title)
        
        assert result is not None
        assert result["price"] == "2.49"
        assert result["discount"] == 75
        assert "cheapshark.com" in result["link"]
        assert result["currency"] == "USD"


@pytest.mark.asyncio
async def test_compare_prices_caches_results():
    """WHEN compare_prices fetches new data, it should cache the results.
    
    Validates: Requirements 3.6
    """
    game_title = "Dark Souls"
    
    with patch("price_comparator.price_cache_get", new_callable=AsyncMock) as mock_cache_get, \
         patch("price_comparator.price_cache_set", new_callable=AsyncMock) as mock_cache_set, \
         patch("price_comparator._fetch_steam_price", new_callable=AsyncMock) as mock_steam, \
         patch("price_comparator._fetch_gog_price", new_callable=AsyncMock) as mock_gog, \
         patch("price_comparator._fetch_epic_price", new_callable=AsyncMock) as mock_epic, \
         patch("price_comparator._fetch_cheapshark_price", new_callable=AsyncMock) as mock_cheapshark:
        
        mock_cache_get.return_value = None
        mock_steam.return_value = {"price": "799", "discount": 20, "link": "https://steam.com", "currency": "RUB"}
        mock_gog.return_value = None
        mock_epic.return_value = None
        mock_cheapshark.return_value = None
        
        result = await compare_prices(game_title)
        
        # Should cache the results
        mock_cache_set.assert_called_once_with(game_title, result)
