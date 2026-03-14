"""
CheapShark API — агрегатор скидок из 20+ магазинов (Steam, GOG, Humble, Fanatical и др.)
Документация: https://www.cheapshark.com/api
Цены в USD, поэтому показываем их как есть.
"""
import asyncio
import aiohttp
from parsers.steam import Deal
from parsers.utils import fetch_with_retry

CHEAPSHARK_URL = (
    "https://www.cheapshark.com/api/1.0/deals"
    "?sortBy=recent&desc=1&pageSize=60&onSale=1&lowerPrice=1"
)
DEAL_LINK = "https://www.cheapshark.com/redirect?dealID={deal_id}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Магазины которые уже покрыты другими парсерами — пропускаем чтобы не дублировать
SKIP_STORE_IDS = {"1", "7"}  # 1=Steam, 7=GOG


async def get_cheapshark_deals(min_discount: int = 50) -> list[Deal]:
    data = await fetch_with_retry(CHEAPSHARK_URL, headers=HEADERS)
    if not data:
        return []

    deals = []
    for item in data:
        # Пропускаем если не на распродаже
        if str(item.get("isOnSale", "0")) != "1":
            continue

        store_id = str(item.get("storeID", ""))
        if store_id in SKIP_STORE_IDS:
            continue

        try:
            normal = float(item.get("normalPrice", 0))
            sale = float(item.get("salePrice", 0))
            if normal <= 0:
                continue
            discount_pct = int(round((1 - sale / normal) * 100))
        except (ValueError, TypeError, ZeroDivisionError):
            continue

        if discount_pct < min_discount:
            continue

        title = item.get("title", "Неизвестная игра")
        normal_price = item.get("normalPrice", "—")
        sale_price = item.get("salePrice", "—")
        deal_id = item.get("dealID", "")
        game_id = item.get("gameID", deal_id)
        thumb = item.get("thumb", None)

        old_price = f"${float(normal_price):.2f}"
        new_price_val = float(sale_price)
        is_free = new_price_val == 0
        new_price = "Бесплатно" if is_free else f"${new_price_val:.2f}"

        deals.append(Deal(
            deal_id=f"cs_{game_id}",
            title=title,
            store="CheapShark",
            old_price=old_price,
            new_price=new_price,
            discount=discount_pct,
            link=DEAL_LINK.format(deal_id=deal_id),
            image_url=thumb,
            is_free=is_free,
            genres=[],
        ))

    return deals
