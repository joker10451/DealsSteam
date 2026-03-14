import asyncio
import aiohttp
from parsers.steam import Deal

# country=RU гарантирует цены в рублях
EPIC_FREE_URL = (
    "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
    "?locale=ru&country=RU&allowCountries=RU"
)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


async def _fetch_with_retry(url: str, retries: int = 3, delay: float = 2.0):
    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
        except Exception:
            pass
        if attempt < retries - 1:
            await asyncio.sleep(delay * (attempt + 1))
    return None


def _fmt(kopecks: int) -> str:
    """Epic возвращает цены в копейках (целое число). Конвертируем в рубли."""
    rubles = kopecks // 100
    return f"{rubles:,} ₽".replace(",", " ")


async def get_epic_deals(min_discount: int = 50) -> list[Deal]:
    try:
        data = await _fetch_with_retry(EPIC_FREE_URL)
    except Exception:
        return []
    if not data:
        return []

    elements = (
        data.get("data", {})
            .get("Catalog", {})
            .get("searchStore", {})
            .get("elements", [])
    )
    deals = []

    for item in elements:
        promotions = item.get("promotions") or {}
        promo_offers = promotions.get("promotionalOffers", [])
        active_offers = []
        for group in promo_offers:
            active_offers.extend(group.get("promotionalOffers", []))

        if not active_offers:
            continue

        offer = active_offers[0]
        discount_pct = 100 - int(offer.get("discountSetting", {}).get("discountPercentage", 100))
        is_free = discount_pct == 100

        if not is_free and discount_pct < min_discount:
            continue

        title = item.get("title", "Неизвестная игра")
        price_info = item.get("price", {}).get("totalPrice", {})
        original = price_info.get("originalPrice", 0)   # в копейках
        final = price_info.get("discountPrice", 0)       # в копейках

        old_price = _fmt(original) if original else "—"
        new_price = "Бесплатно" if is_free else _fmt(final)

        slug = (item.get("productSlug") or item.get("urlSlug") or "").replace("/home", "")

        genres = [
            t["name"] for t in item.get("tags", [])
            if t.get("groupName") == "genre"
        ][:3]

        image_url = next(
            (img["url"] for img in item.get("keyImages", []) if img.get("type") == "Thumbnail"),
            None,
        )

        deals.append(Deal(
            deal_id=f"epic_{item.get('id', slug)}",
            title=title,
            store="Epic Games",
            old_price=old_price,
            new_price=new_price,
            discount=discount_pct,
            link=f"https://store.epicgames.com/ru/p/{slug}" if slug else "https://store.epicgames.com/ru/free-games",
            image_url=image_url,
            is_free=is_free,
            genres=genres,
        ))

    return deals
