"""
Скрытые жемчужины — инди-игры с высоким рейтингом, малым числом отзывов и большой скидкой.
Использует Steam Search API.
"""
import aiohttp
from dataclasses import dataclass
from typing import Optional


@dataclass
class GemDeal:
    appid: str
    title: str
    old_price: str
    new_price: str
    discount: int
    score: int        # % позитивных отзывов
    reviews: int      # кол-во отзывов
    image_url: str
    link: str


async def find_hidden_gems(
    min_discount: int = 70,
    min_score: int = 85,
    max_reviews: int = 500,
    limit: int = 2,
) -> list[GemDeal]:
    """
    Ищет инди-игры через Steam Search:
    - скидка >= min_discount%
    - рейтинг >= min_score%
    - отзывов <= max_reviews (малоизвестные)
    """
    url = "https://store.steampowered.com/search/results/"
    params = {
        "json": 1,
        "tags": "492",          # тег Indie
        "specials": 1,          # только со скидкой
        "sort_by": "Reviews_DESC",
        "count": 50,
    }

    gems = []
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return []
                data = await r.json()
    except Exception:
        return []

    items = data.get("items", [])
    for item in items:
        if len(gems) >= limit:
            break

        # Парсим скидку из HTML-фрагмента
        discount = _parse_discount(item.get("discount_block", ""))
        if discount < min_discount:
            continue

        appid = str(item.get("id", ""))
        if not appid:
            continue

        # Получаем детали (рейтинг, цены)
        details = await _get_app_details(appid)
        if not details:
            continue

        score = details.get("score", 0)
        reviews = details.get("reviews", 0)

        if score < min_score or reviews > max_reviews or reviews < 10:
            continue

        gems.append(GemDeal(
            appid=appid,
            title=item.get("name", "Unknown"),
            old_price=details.get("old_price", ""),
            new_price=details.get("new_price", ""),
            discount=discount,
            score=score,
            reviews=reviews,
            image_url=f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg",
            link=f"https://store.steampowered.com/app/{appid}/",
        ))

    return gems


def _parse_discount(block: str) -> int:
    import re
    match = re.search(r"-(\d+)%", block)
    return int(match.group(1)) if match else 0


async def _get_app_details(appid: str) -> Optional[dict]:
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=price_overview,ratings&cc=ru"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    return None
                data = await r.json()
        app = data.get(str(appid), {})
        if not app.get("success"):
            return None
        d = app["data"]
        price = d.get("price_overview", {})
        ratings = d.get("ratings", {}).get("steam", {})

        score = int(ratings.get("percent_positive", 0)) if ratings else 0
        reviews = int(ratings.get("total", 0)) if ratings else 0

        return {
            "old_price": price.get("initial_formatted", ""),
            "new_price": price.get("final_formatted", ""),
            "score": score,
            "reviews": reviews,
        }
    except Exception:
        return None
