"""
Steam Web API integration module.
Provides functions for resolving Steam IDs, fetching wishlists, and fetching libraries.
"""
import re
import logging
from typing import Optional
from parsers.utils import fetch_with_retry
from config import STEAM_API_KEY

log = logging.getLogger(__name__)

# Steam API endpoints
STEAM_WISHLIST_URL = "https://store.steampowered.com/wishlist/profiles/{steamid}/wishlistdata/"
STEAM_LIBRARY_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
STEAM_RESOLVE_URL = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"

# Regex pattern to extract Steam ID64 or vanity name from profile URLs
# Matches: steamcommunity.com/profiles/76561198012345678 or steamcommunity.com/id/username
STEAM_ID_REGEX = re.compile(r"steamcommunity\.com/(?:profiles/(\d+)|id/([a-zA-Z0-9_-]+))")


async def resolve_steam_id(profile_input: str) -> Optional[str]:
    """
    Extracts Steam ID64 from profile URL or vanity name.
    
    Handles three input formats:
    1. Direct Steam ID64: "76561198012345678"
    2. Profile URL with ID64: "https://steamcommunity.com/profiles/76561198012345678"
    3. Vanity URL: "https://steamcommunity.com/id/username"
    
    For vanity URLs, calls the ResolveVanityURL API endpoint to get the Steam ID64.
    
    Args:
        profile_input: Steam profile URL, vanity URL, or direct Steam ID64
        
    Returns:
        Steam ID64 string (17 digits starting with 7656119) or None if invalid/private
        
    Requirements: 5.4, 5.6, 5.7
    """
    if not profile_input:
        log.warning("Empty profile input provided")
        return None
    
    profile_input = profile_input.strip()
    
    # Check if input is already a valid Steam ID64 (17 digits starting with 7656119)
    if re.match(r"^7656119\d{10}$", profile_input):
        log.info(f"Direct Steam ID64 provided: {profile_input}")
        return profile_input
    
    # Try to extract Steam ID64 or vanity name from URL
    match = STEAM_ID_REGEX.search(profile_input)
    
    if not match:
        log.warning(f"Invalid Steam profile format: {profile_input}")
        return None
    
    steam_id64, vanity_name = match.groups()
    
    # If we extracted a Steam ID64 directly from the URL
    if steam_id64:
        log.info(f"Extracted Steam ID64 from URL: {steam_id64}")
        return steam_id64
    
    # If we extracted a vanity name, resolve it via API
    if vanity_name:
        log.info(f"Resolving vanity URL: {vanity_name}")
        resolved_id = await _resolve_vanity_url(vanity_name)
        if resolved_id:
            log.info(f"Resolved vanity URL '{vanity_name}' to Steam ID64: {resolved_id}")
        else:
            log.warning(f"Failed to resolve vanity URL: {vanity_name}")
        return resolved_id
    
    return None


async def _resolve_vanity_url(vanity_name: str) -> Optional[str]:
    """
    Resolves a Steam vanity URL to a Steam ID64 using the Steam Web API.
    
    Args:
        vanity_name: The vanity name from the URL (e.g., "username" from steamcommunity.com/id/username)
        
    Returns:
        Steam ID64 string or None if resolution fails
        
    Requirements: 5.7
    """
    if not STEAM_API_KEY:
        log.error("STEAM_API_KEY not configured, cannot resolve vanity URL")
        return None
    
    params = {
        "key": STEAM_API_KEY,
        "vanityurl": vanity_name
    }
    
    try:
        # Use fetch_with_retry for exponential backoff and retry logic
        response = await fetch_with_retry(
            STEAM_RESOLVE_URL,
            retries=3,
            delay=2.0,
            params=params,
            as_json=True
        )
        
        if not response:
            log.error(f"No response from ResolveVanityURL API for: {vanity_name}")
            return None
        
        # Check if the API call was successful
        # Response format: {"response": {"steamid": "76561198012345678", "success": 1}}
        # success=1 means found, success=42 means not found
        if response.get("response", {}).get("success") == 1:
            steam_id = response["response"].get("steamid")
            if steam_id:
                return steam_id
        
        log.warning(f"Vanity URL not found or private: {vanity_name}")
        return None
        
    except Exception as e:
        log.error(f"Error resolving vanity URL '{vanity_name}': {e}")
        return None


async def fetch_wishlist(steam_id: str) -> list[dict]:
    """
    Fetches public wishlist for given Steam ID64.
    
    Args:
        steam_id: Steam ID64 string (17 digits starting with 7656119)
        
    Returns:
        List of dicts with {appid, name} or empty list if private/error.
        Max 100 games to prevent database overload.
        
    Requirements: 1.1, 1.4, 5.2, 5.8
    """
    if not steam_id:
        log.warning("Empty Steam ID provided to fetch_wishlist")
        return []
    
    # Validate Steam ID64 format
    if not re.match(r"^7656119\d{10}$", steam_id):
        log.warning(f"Invalid Steam ID64 format: {steam_id}")
        return []
    
    url = STEAM_WISHLIST_URL.format(steamid=steam_id)
    log.info(f"Fetching wishlist for Steam ID: {steam_id}")
    
    try:
        # Use fetch_with_retry for exponential backoff and 15-second timeout
        response = await fetch_with_retry(
            url,
            retries=3,
            delay=2.0,
            as_json=True
        )
        
        if not response:
            log.warning(f"Empty or no response from wishlist endpoint for Steam ID: {steam_id}")
            return []
        
        # Steam wishlist API returns a dict where keys are appids
        # Format: {"12345": {"name": "Game Name", ...}, "67890": {...}}
        if not isinstance(response, dict):
            log.warning(f"Unexpected response format from wishlist endpoint: {type(response)}")
            return []
        
        # Extract games and limit to 100
        games = []
        for appid_str, game_data in response.items():
            try:
                appid = int(appid_str)
                name = game_data.get("name", "Unknown Game")
                games.append({"appid": appid, "name": name})
                
                # Limit to 100 games
                if len(games) >= 100:
                    log.info(f"Wishlist limited to 100 games for Steam ID: {steam_id}")
                    break
            except (ValueError, AttributeError) as e:
                log.warning(f"Skipping invalid game entry in wishlist: {e}")
                continue
        
        log.info(f"Successfully fetched {len(games)} games from wishlist for Steam ID: {steam_id}")
        return games
        
    except Exception as e:
        log.error(f"Error fetching wishlist for Steam ID '{steam_id}': {e}")
        return []


async def fetch_library(steam_id: str) -> list[int]:
    """
    Fetches owned games for given Steam ID64.
    
    Args:
        steam_id: Steam ID64 string (17 digits starting with 7656119)
        
    Returns:
        List of appids (integers) or empty list if private/error.
        Uses IPlayerService/GetOwnedGames endpoint.
        
    Requirements: 2.1, 2.2, 5.2, 5.8
    """
    if not steam_id:
        log.warning("Empty Steam ID provided to fetch_library")
        return []
    
    # Validate Steam ID64 format
    if not re.match(r"^7656119\d{10}$", steam_id):
        log.warning(f"Invalid Steam ID64 format: {steam_id}")
        return []
    
    if not STEAM_API_KEY:
        log.error("STEAM_API_KEY not configured, cannot fetch library")
        return []
    
    log.info(f"Fetching library for Steam ID: {steam_id}")
    
    params = {
        "key": STEAM_API_KEY,
        "steamid": steam_id,
        "include_appinfo": "0",  # We only need appids, not full game info
        "format": "json"
    }
    
    try:
        # Use fetch_with_retry for exponential backoff and 15-second timeout
        response = await fetch_with_retry(
            STEAM_LIBRARY_URL,
            retries=3,
            delay=2.0,
            params=params,
            as_json=True
        )
        
        if not response:
            log.warning(f"Empty or no response from library endpoint for Steam ID: {steam_id}")
            return []
        
        # Steam library API returns: {"response": {"game_count": N, "games": [{"appid": 123}, ...]}}
        # If profile is private, response might be {"response": {}} with no games key
        games_data = response.get("response", {}).get("games", [])
        
        if not games_data:
            log.warning(f"No games found in library for Steam ID: {steam_id} (profile may be private)")
            return []
        
        # Extract appids
        appids = []
        for game in games_data:
            try:
                appid = game.get("appid")
                if appid is not None:
                    appids.append(int(appid))
            except (ValueError, TypeError) as e:
                log.warning(f"Skipping invalid game entry in library: {e}")
                continue
        
        log.info(f"Successfully fetched {len(appids)} games from library for Steam ID: {steam_id}")
        return appids
        
    except Exception as e:
        log.error(f"Error fetching library for Steam ID '{steam_id}': {e}")
        return []
