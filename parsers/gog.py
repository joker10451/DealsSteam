import asyncio
import aiohttp
import re
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from currency import to_rubles, format_rub
from parsers.steam import Deal
from parsers.utils import fetch_with_retry

GOG_API_URL = (
    "https://catalog.gog.com/v1/catalog"
    "?limit=48&order=desc:trending&discounted=eq:true&productType=in:game,pack"
    "&page=1&countryCode=RU&locale=ru-RU&currencyCode=RUB"
)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Символы валют → коды для конвертации
_CURRENCY_SYMBOLS = {
    "$": "USD", "€": "EUR", "£": "GBP", "zł": "PLN",
    "kr": "SEK", "₴": "UAH", "₸": "KZT",
}


def _normalize_price(price_str: str) -> str:
    """Если цена уже в рублях — возвращает как есть. Иначе помечает для конвертации."""
    if not price_str or price_str == "—":
        return price_str
    # Уже рубли
    if "₽" in price_str or "руб" in price_str.lower():
        return price_str
    # Пробуем распознать валюту и сумму
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in price_str:
            num_match = re.search(r"[\d\s,.]+", price_str)
            if num_match:
                try:
                    amount = float(num_match.group().replace(" ", "").replace(",", "."))
                    return f"~{amount:.0f} {code}"  # помечаем для конвертации в боте
                except ValueError:
                    pass
    return price_str


async def get_gog_deals(min_discount: int = 50) -> list[Deal]:
    try:
        data = await fetch_with_retry(GOG_API_URL, headers=HEADERS)
    except Exception:
        return []
    if not data:
        return []

    deals = []
    for product in data.get("products", []):
        price_info = product.get("price", {})
        discount_str = price_info.get("discount", "0%")
        try:
            discount_pct = abs(int(discount_str.replace("%", "").replace("-", "").strip()))
        except (ValueError, AttributeError):
            continue

        if discount_pct < min_discount:
            continue

        slug = product.get("slug", "")
        title = product.get("title", "Неизвестная игра")
        base = price_info.get("base", "—")
        final = price_info.get("final", "—")
        is_free = discount_pct == 100

        # Нормализуем цены в рубли
        base = _normalize_price(base)
        final = _normalize_price(final)

        # Жанры из API
        genres = [g["name"] for g in product.get("genres", [])[:3]]

        cover = product.get("coverHorizontal", "")
        if cover and "{formatter}" in cover:
            cover = cover.replace("{formatter}", "product_card_v2_mobile_slider_639")

        deals.append(Deal(
            deal_id=f"gog_{slug}",
            title=title,
            store="GOG",
            old_price=base,
            new_price="Бесплатно" if is_free else final,
            discount=discount_pct,
            link=product.get("storeLink") or f"https://www.gog.com/ru/game/{slug}",
            image_url=cover or None,
            is_free=is_free,
            genres=genres,
        ))

    return deals
