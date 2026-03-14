"""
RAWG API — получение описания, скриншотов и метаданных игры.
Документация: https://rawg.io/apidocs
Бесплатно: 20 000 запросов/месяц
"""
import aiohttp
import asyncio
from typing import Optional
from config import RAWG_API_KEY

RAWG_SEARCH = "https://api.rawg.io/api/games?key={key}&search={query}&page_size=1"
RAWG_DETAIL = "https://api.rawg.io/api/games/{slug}?key={key}"


async def _get(url: str) -> Optional[dict]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.json()
    except Exception:
        pass
    return None


async def get_metacritic(title: str) -> Optional[int]:
    """Возвращает оценку Metacritic для игры или None."""
    if not RAWG_API_KEY:
        return None
    from urllib.parse import quote
    search_url = RAWG_SEARCH.format(key=RAWG_API_KEY, query=quote(title))
    data = await _get(search_url)
    if not data or not data.get("results"):
        return None
    game = data["results"][0]
    return game.get("metacritic")


async def get_game_info(title: str) -> Optional[dict]:
    """
    Возвращает словарь с данными об игре:
    {
        'description': str,       # короткое описание (1-2 предложения)
        'metacritic': int | None, # оценка Metacritic
        'playtime': int,          # среднее время прохождения в часах
        'screenshots': [str],     # список URL скриншотов (до 3)
        'background': str | None, # фоновое изображение
        'genres': [str],          # жанры на русском
        'tags': [str],            # теги
    }
    """
    if not RAWG_API_KEY:
        return None

    # Поиск игры
    search_url = RAWG_SEARCH.format(key=RAWG_API_KEY, query=aiohttp.helpers.quote(title))
    data = await _get(search_url)
    if not data or not data.get("results"):
        return None

    game = data["results"][0]
    slug = game.get("slug", "")
    if not slug:
        return None

    # Детальная информация
    detail = await _get(RAWG_DETAIL.format(slug=slug, key=RAWG_API_KEY))
    if not detail:
        return None

    # Описание — берём первые 2 предложения из description_raw
    raw_desc = detail.get("description_raw", "")
    sentences = [s.strip() for s in raw_desc.replace("\n", " ").split(".") if len(s.strip()) > 20]
    description = ". ".join(sentences[:2]) + "." if sentences else ""

    # Жанры
    genres = [g["name"] for g in detail.get("genres", [])[:3]]

    # Скриншоты из основного объекта
    screenshots = []
    for img in detail.get("short_screenshots", [])[:3]:
        url = img.get("image")
        if url:
            screenshots.append(url)

    return {
        "description": description[:300] if description else None,
        "metacritic": detail.get("metacritic"),
        "playtime": detail.get("playtime", 0),
        "screenshots": screenshots,
        "background": detail.get("background_image"),
        "genres": genres,
        "slug": slug,
    }
