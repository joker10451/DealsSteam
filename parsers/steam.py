import asyncio
import aiohttp
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from typing import Optional
from parsers.utils import fetch_with_retry


@dataclass
class Deal:
    deal_id: str
    title: str
    store: str
    old_price: str
    new_price: str
    discount: int
    link: str
    image_url: Optional[str] = None
    is_free: bool = False
    genres: list[str] = field(default_factory=list)
    sale_end: Optional[str] = None   # дата окончания скидки "DD.MM.YYYY" или None


STEAM_SEARCH_URL = (
    "https://store.steampowered.com/search/results/"
    "?specials=1&cc=ru&l=russian&count=50&json=0"
    # category1=998 — только полные игры (исключает DLC, саундтреки, артбуки)
    "&category1=998"
)
STEAM_APP_URL = "https://store.steampowered.com/app/{appid}/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# Ключевые слова в названии — признак DLC/саундтрека/артбука
SKIP_KEYWORDS = (
    " - soundtrack", " ost", "artbook", "art book", "dlc", "season pass",
    "expansion pack", "bonus content", "digital extras", "supporter pack",
    " pack", "upgrade", "pre-order", "preorder",
)


def _is_junk(title: str) -> bool:
    """Возвращает True если это DLC, саундтрек или артбук по названию."""
    low = title.lower()
    return any(kw in low for kw in SKIP_KEYWORDS)


async def get_steam_deals(min_discount: int = 50) -> list[Deal]:
    try:
        html = await fetch_with_retry(STEAM_SEARCH_URL, headers=HEADERS, as_json=False)
    except Exception:
        return []
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("a.search_result_row")
    deals = []

    for row in rows:
        appid = row.get("data-ds-appid", "")
        if not appid:
            continue

        # data-ds-itemtype: "1" = игра, "2" = DLC, "4" = саундтрек и т.д.
        item_type = row.get("data-ds-itemtype", "1")
        if item_type != "1":
            continue

        title_el = row.select_one(".title")
        title = title_el.text.strip() if title_el else "Неизвестно"

        if _is_junk(title):
            continue

        discount_el = row.select_one(".discount_pct")
        if not discount_el:
            continue
        try:
            discount = abs(int(discount_el.text.strip().replace("%", "").replace("-", "")))
        except ValueError:
            continue

        if discount < min_discount:
            continue

        original_el = row.select_one(".discount_original_price")
        final_el = row.select_one(".discount_final_price")
        old_price = original_el.text.strip() if original_el else "—"
        new_price_text = final_el.text.strip() if final_el else "—"
        is_free = "бесплатно" in new_price_text.lower() or new_price_text == "0 ₽"

        genre_els = row.select(".search_tag")
        genres = [g.text.strip() for g in genre_els[:3] if g.text.strip()]

        img_el = row.select_one("img")
        image_url = img_el.get("src") if img_el else None

        # Дата окончания скидки (data-discount-ends-at — unix timestamp)
        sale_end = None
        ends_at = row.get("data-discount-ends-at", "")
        if ends_at:
            try:
                from datetime import datetime as dt, timezone
                sale_end = dt.fromtimestamp(int(ends_at), tz=timezone.utc).strftime("%d.%m.%Y")
            except Exception:
                pass

        deals.append(Deal(
            deal_id=f"steam_{appid}",
            title=title,
            store="Steam",
            old_price=old_price,
            new_price="Бесплатно" if is_free else new_price_text,
            discount=discount,
            link=STEAM_APP_URL.format(appid=appid),
            image_url=image_url,
            is_free=is_free,
            genres=genres,
            sale_end=sale_end,
        ))

    return deals
