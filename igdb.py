"""
IGDB API (Twitch) — описания, обложки, рейтинги игр.
Документация: https://api-docs.igdb.com
Бесплатно: 4 запроса/сек, без лимита в месяц.
"""
import aiohttp
import asyncio
import time
from typing import Optional
from config import IGDB_CLIENT_ID, IGDB_CLIENT_SECRET

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_URL = "https://api.igdb.com/v4/games"

# Кэш токена — живёт ~60 дней
_token: Optional[str] = None
_token_expires: float = 0

# Кэш результатов запросов: {title_lower: (result, expires_at)}
_game_cache: dict[str, tuple] = {}
_GAME_CACHE_TTL = 24 * 3600  # 24 часа


async def _get_token() -> Optional[str]:
    global _token, _token_expires
    if _token and time.time() < _token_expires - 60:
        return _token
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(TOKEN_URL, params={
                "client_id": IGDB_CLIENT_ID,
                "client_secret": IGDB_CLIENT_SECRET,
                "grant_type": "client_credentials",
            }) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                _token = data.get("access_token")
                _token_expires = time.time() + data.get("expires_in", 3600)
                return _token
    except Exception:
        return None


async def get_game_info(title: str) -> Optional[dict]:
    """
    Возвращает:
    {
        'description': str,       # краткое описание
        'rating': float | None,   # оценка IGDB (0-100)
        'cover_url': str | None,  # URL обложки
        'screenshots': [str],     # до 3 скриншотов
        'genres': [str],          # жанры
        'playtime': int,          # среднее время прохождения (часы)
    }
    """
    if not IGDB_CLIENT_ID or not IGDB_CLIENT_SECRET:
        return None

    cache_key = title.lower().strip()
    cached = _game_cache.get(cache_key)
    if cached and time.time() < cached[1]:
        return cached[0]

    token = await _get_token()
    if not token:
        return None

    headers = {
        "Client-ID": IGDB_CLIENT_ID,
        "Authorization": f"Bearer {token}",
    }

    # Запрос к IGDB
    body = (
        f'search "{title}"; '
        f'fields name, summary, rating, cover.url, '
        f'screenshots.url, genres.name, hypes, '
        f'aggregated_rating, total_rating; '
        f'where version_parent = null; '
        f'limit 1;'
    )

    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.post(IGDB_URL, data=body, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None
                results = await r.json()
    except Exception:
        return None

    if not results:
        return None

    game = results[0]

    # Описание — первые 2 предложения
    summary = game.get("summary", "")
    sentences = [s.strip() for s in summary.split(".") if len(s.strip()) > 15]
    description = ". ".join(sentences[:2]) + "." if sentences else None

    # Обложка — меняем размер на большой
    cover_url = None
    if game.get("cover"):
        cover_url = game["cover"]["url"].replace("t_thumb", "t_cover_big").lstrip("/")
        if not cover_url.startswith("http"):
            cover_url = "https://" + cover_url

    # Скриншоты
    screenshots = []
    for s in game.get("screenshots", [])[:3]:
        url = s["url"].replace("t_thumb", "t_screenshot_big").lstrip("/")
        if not url.startswith("http"):
            url = "https://" + url
        screenshots.append(url)

    # Рейтинг (0-100)
    rating = game.get("total_rating") or game.get("rating") or game.get("aggregated_rating")
    rating = round(rating) if rating else None

    # Жанры
    genres = [g["name"] for g in game.get("genres", [])[:3]]

    result = {
        "description": description,
        "rating": rating,
        "cover_url": cover_url,
        "screenshots": screenshots,
        "genres": genres,
    }
    _game_cache[cache_key] = (result, time.time() + _GAME_CACHE_TTL)
    return result
