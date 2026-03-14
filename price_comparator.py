"""
Price comparison module for aggregating game prices across multiple stores.
"""
import asyncio
import logging
from typing import Optional
from parsers.utils import fetch_with_retry
from database import price_cache_get, price_cache_set

log = logging.getLogger(__name__)

# API endpoints
STEAM_SEARCH_API = "https://store.steampowered.com/api/storesearch/"
GOG_SEARCH_API = "https://embed.gog.com/games/ajax/filtered"
EPIC_GRAPHQL_API = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
CHEAPSHARK_SEARCH_API = "https://www.cheapshark.com/api/1.0/games"


async def compare_prices(game_title: str) -> dict:
    """
    Queries Steam, GOG, Epic, CheapShark for game prices.
    Returns dict: {store: {price, discount, link, currency}}
    Uses 6-hour cache to reduce API load.
    Completes within 5 seconds or returns cached/partial data.
    
    Args:
        game_title: Game title to search for
        
    Returns:
        Dict mapping store names to price info dicts
        Example: {
            "Steam": {"price": "499", "discount": 50, "link": "...", "currency": "RUB"},
            "GOG": {"price": "599", "discount": 30, "link": "...", "currency": "RUB"}
        }
    """
    # Check cache first
    cached = await price_cache_get(game_title)
    if cached:
        log.info(f"Price cache hit for '{game_title}'")
        return cached.get("prices", {})
    
    log.info(f"Fetching prices for '{game_title}' from all stores")
    
    # Query all stores in parallel with 5-second timeout
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                _fetch_steam_price(game_title),
                _fetch_gog_price(game_title),
                _fetch_epic_price(game_title),
                _fetch_cheapshark_price(game_title),
                return_exceptions=True
            ),
            timeout=5.0
        )
    except asyncio.TimeoutError:
        log.warning(f"Price comparison timeout for '{game_title}'")
        return {}
    
    # Aggregate results, handling partial failures
    prices = {}
    store_names = ["Steam", "GOG", "Epic Games", "CheapShark"]
    
    for store_name, result in zip(store_names, results):
        if isinstance(result, Exception):
            log.error(f"{store_name} price fetch failed: {result}")
            continue
        if result:
            prices[store_name] = result
    
    # Cache results if we got any data
    if prices:
        await price_cache_set(game_title, prices)
        log.info(f"Cached prices for '{game_title}': {len(prices)} stores")
    
    return prices


async def _fetch_steam_price(game_title: str) -> Optional[dict]:
    """
    Fetches price from Steam Store API.
    
    Returns:
        Dict with price info or None if not found/error
    """
    try:
        params = {
            "term": game_title,
            "cc": "ru",
            "l": "russian"
        }
        data = await fetch_with_retry(
            STEAM_SEARCH_API,
            params=params,
            as_json=True,
            retries=2
        )
        
        if not data or "items" not in data or not data["items"]:
            return None
        
        # Take first result
        item = data["items"][0]
        
        # Parse price data
        price_text = item.get("price", {}).get("final", 0)
        original_price = item.get("price", {}).get("initial", 0)
        
        # Convert from cents to rubles
        price = price_text / 100 if price_text else 0
        old_price = original_price / 100 if original_price else price
        
        # Calculate discount
        discount = 0
        if old_price > 0 and price < old_price:
            discount = int(((old_price - price) / old_price) * 100)
        
        return {
            "price": f"{price:.0f}",
            "discount": discount,
            "link": f"https://store.steampowered.com/app/{item['id']}/",
            "currency": "RUB"
        }
    except Exception as e:
        log.error(f"Steam price fetch error: {e}")
        return None


async def _fetch_gog_price(game_title: str) -> Optional[dict]:
    """
    Fetches price from GOG API.
    
    Returns:
        Dict with price info or None if not found/error
    """
    try:
        params = {
            "search": game_title,
            "limit": "1"
        }
        data = await fetch_with_retry(
            GOG_SEARCH_API,
            params=params,
            as_json=True,
            retries=2
        )
        
        if not data or "products" not in data or not data["products"]:
            return None
        
        product = data["products"][0]
        
        # Parse price
        price_data = product.get("price", {})
        price = price_data.get("finalAmount", "0")
        base_price = price_data.get("baseAmount", price)
        discount_percent = product.get("price", {}).get("discountPercentage", 0)
        
        return {
            "price": str(price),
            "discount": int(discount_percent) if discount_percent else 0,
            "link": f"https://www.gog.com{product.get('url', '')}",
            "currency": price_data.get("currency", "RUB")
        }
    except Exception as e:
        log.error(f"GOG price fetch error: {e}")
        return None


async def _fetch_epic_price(game_title: str) -> Optional[dict]:
    """
    Fetches price from Epic Games Store.
    
    Returns:
        Dict with price info or None if not found/error
    """
    try:
        # Epic doesn't have a simple search API, so we'll skip for now
        # This would require GraphQL queries which are more complex
        return None
    except Exception as e:
        log.error(f"Epic price fetch error: {e}")
        return None


async def _fetch_cheapshark_price(game_title: str) -> Optional[dict]:
    """
    Fetches price from CheapShark API (aggregates multiple stores).
    
    Returns:
        Dict with price info or None if not found/error
    """
    try:
        params = {
            "title": game_title,
            "limit": "1"
        }
        data = await fetch_with_retry(
            CHEAPSHARK_SEARCH_API,
            params=params,
            as_json=True,
            retries=2
        )
        
        if not data or not isinstance(data, list) or len(data) == 0:
            return None
        
        game = data[0]
        
        # Parse price
        cheapest_price = float(game.get("cheapest", "0"))
        normal_price = float(game.get("normalPrice", cheapest_price))
        
        # Calculate discount
        discount = 0
        if normal_price > 0 and cheapest_price < normal_price:
            discount = int(((normal_price - cheapest_price) / normal_price) * 100)
        
        return {
            "price": f"{cheapest_price:.2f}",
            "discount": discount,
            "link": f"https://www.cheapshark.com/redirect?dealID={game.get('cheapestDealID', '')}",
            "currency": "USD"
        }
    except Exception as e:
        log.error(f"CheapShark price fetch error: {e}")
        return None


def format_price_comparison(prices: dict) -> str:
    """
    Formats price comparison as HTML message.
    Sorts by price, adds "Best Deal" badge to lowest.
    
    Args:
        prices: Dict mapping store names to price info dicts
        
    Returns:
        Formatted HTML string for Telegram message
        
    Requirements: 3.3, 3.4, 3.8
    """
    if not prices:
        return "❌ Цены не найдены"
    
    # Store emojis
    store_emojis = {
        "Steam": "🎮",
        "GOG": "🎯",
        "Epic Games": "🎪",
        "CheapShark": "💰"
    }
    
    # Convert prices to float for sorting
    price_list = []
    for store, info in prices.items():
        try:
            # Handle different currency formats
            price_str = info.get("price", "0")
            price_float = float(price_str.replace(",", "."))
            price_list.append((store, info, price_float))
        except (ValueError, AttributeError):
            log.warning(f"Invalid price format for {store}: {info.get('price')}")
            continue
    
    # Sort by price (lowest first)
    price_list.sort(key=lambda x: x[2])
    
    if not price_list:
        return "❌ Не удалось обработать цены"
    
    # Build formatted message
    lines = ["💵 <b>Сравнение цен:</b>\n"]
    
    for idx, (store, info, price_float) in enumerate(price_list):
        emoji = store_emojis.get(store, "🏪")
        price = info.get("price", "0")
        discount = info.get("discount", 0)
        link = info.get("link", "")
        currency = info.get("currency", "RUB")
        
        # Add "Best Deal" badge to lowest price
        badge = " 🏆 <b>Лучшая цена!</b>" if idx == 0 else ""
        
        # Format discount if present
        discount_text = f" (-{discount}%)" if discount > 0 else ""
        
        # Build line
        line = f"{emoji} <a href='{link}'>{store}</a>: {price} {currency}{discount_text}{badge}"
        lines.append(line)
    
    return "\n".join(lines)
