"""
Скрытые жемчужины — инди-игры с высоким рейтингом, малым числом отзывов и большой скидкой.
Использует Steam Search API.
"""
import re
import asyncio
from dataclasses import dataclass
from typing import Optional
from parsers.utils import fetch_with_retry


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
    min_discount: int = 50,
    min_score: int = 80,
    max_reviews: int = 1000,
    limit: int = 2,
) -> list[GemDeal]:
    """
    Ищет инди-игры через Steam Search:
    - скидка >= min_discount%
    - рейтинг >= min_score%
    - отзывов <= max_reviews (малоизвестные)
    """
    url = "https://store.steampowered.com/search/results/"
    base_params = {
        "json": 1,
        "tags": "492",           # тег Indie
        "specials": 1,           # только со скидкой
        "sort_by": "Discount_DESC",
        "count": 50,
    }

    gems = []
    seen_appids: set[str] = set()

    for start in range(0, 150, 50):  # до 150 результатов (3 страницы)
        if len(gems) >= limit:
            break

        data = await fetch_with_retry(url, params={**base_params, "start": start})
        if not data:
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            if len(gems) >= limit:
                break

            appid = str(item.get("id", ""))
            if not appid or appid in seen_appids:
                continue
            seen_appids.add(appid)

            discount = _parse_discount(item.get("discount_block", ""))
            if discount < min_discount:
                continue

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
    match = re.search(r"-(\d+)%", block)
    return int(match.group(1)) if match else 0


async def _get_app_details(appid: str) -> Optional[dict]:
    price_url = f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=price_overview&cc=ru"
    reviews_url = f"https://store.steampowered.com/appreviews/{appid}?json=1&language=all&purchase_type=all"
    try:
        price_data, reviews_data = await asyncio.gather(
            fetch_with_retry(price_url),
            fetch_with_retry(reviews_url),
        )

        old_price, new_price = "", ""
        if price_data:
            app = price_data.get(str(appid), {})
            if app.get("success"):
                price = app["data"].get("price_overview", {})
                old_price = price.get("initial_formatted", "")
                new_price = price.get("final_formatted", "")

        score, reviews = 0, 0
        if reviews_data:
            summary = reviews_data.get("query_summary", {})
            total = int(summary.get("total_reviews", 0))
            positive = int(summary.get("total_positive", 0))
            if total >= 10:
                score = int(positive / total * 100)
                reviews = total

        return {"old_price": old_price, "new_price": new_price, "score": score, "reviews": reviews}
    except Exception:
        return None
