"""
IGDB API (Twitch) — описания, обложки, рейтинги игр.
Документация: https://api-docs.igdb.com
Бесплатно: 4 запроса/сек, без лимита в месяц.
"""
import aiohttp
import asyncio
import logging
import time
from typing import Optional
from config import IGDB_CLIENT_ID, IGDB_CLIENT_SECRET

log = logging.getLogger(__name__)

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_URL = "https://api.igdb.com/v4/games"
MYMEMORY_URL = "https://api.mymemory.translated.net/get"

# Кэш токена — живёт ~60 дней
_token: Optional[str] = None
_token_expires: float = 0

# Кэш результатов запросов: {title_lower: (result, expires_at)}
_game_cache: dict[str, tuple] = {}
_GAME_CACHE_TTL = 24 * 3600  # 24 часа


async def _translate_to_ru(text: str) -> str:
    """Переводит текст на русский через MyMemory API (бесплатно, без ключа)."""
    if not text:
        return text
    try:
        from parsers.utils import fetch_with_retry
        data = await fetch_with_retry(
            MYMEMORY_URL,
            params={"q": text, "langpair": "en|ru"},
        )
        if not data:
            return text
        translated = data.get("responseData", {}).get("translatedText", "")
        # MyMemory возвращает "MYMEMORY WARNING" при исчерпании лимита
        if translated and "MYMEMORY WARNING" not in translated:
            return translated
        log.warning(f"MyMemory перевод не удался: {data.get('responseStatus')} — {translated[:80]}")
        return text
    except Exception as e:
        log.warning(f"Ошибка перевода: {e}")
        return text


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
        f'aggregated_rating, total_rating, age_ratings.rating, id, '
        f'similar_games.name; '
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
    
    # Проверяем схожесть названий (простая проверка)
    game_name = game.get("name", "").lower()
    search_title = title.lower()
    # Если названия слишком разные, возвращаем None
    if game_name and search_title not in game_name and game_name not in search_title:
        # Проверяем хотя бы частичное совпадение слов
        search_words = set(search_title.split())
        game_words = set(game_name.split())
        common_words = search_words & game_words
        if len(common_words) < 2:  # Меньше 2 общих слов - скорее всего не та игра
            return None

    # Описание — первые 2 предложения, переводим на русский
    summary = game.get("summary", "")
    sentences = [s.strip() for s in summary.split(".") if len(s.strip()) > 15]
    description_en = ". ".join(sentences[:2]) + "." if sentences else None
    description = await _translate_to_ru(description_en) if description_en else None

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

    # Возрастной рейтинг: PEGI 18 = 4, ESRB AO = 4 (по IGDB enum)
    age_ratings = game.get("age_ratings", [])
    is_adult = any(r.get("rating") in (4, 6) for r in age_ratings)

    # Похожие игры
    similar = [g["name"] for g in game.get("similar_games", [])[:3]]

    result = {
        "description": description,
        "rating": rating,
        "cover_url": cover_url,
        "screenshots": screenshots,
        "genres": genres,
        "igdb_id": game.get("id"),
        "is_adult": is_adult,
        "similar_games": similar,
    }
    _game_cache[cache_key] = (result, time.time() + _GAME_CACHE_TTL)
    return result
