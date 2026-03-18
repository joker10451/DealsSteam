"""
GamerPower API — бесплатные раздачи игр с PC/Steam/Epic и других платформ.
Документация: https://www.gamerpower.com/api-read
Без авторизации. Атрибуция: GamerPower.com
"""
import re
import logging
from parsers.steam import Deal
from parsers.utils import fetch_with_retry

log = logging.getLogger(__name__)

GAMERPOWER_URL = "https://www.gamerpower.com/api/giveaways?platform=pc.steam.epic-games-store&type=game&sort-by=date"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


async def get_gamerpower_deals() -> list[Deal]:
    """Возвращает активные бесплатные раздачи игр с GamerPower."""
    try:
        data = await fetch_with_retry(GAMERPOWER_URL, headers=HEADERS)
    except Exception:
        return []

    if not data or not isinstance(data, list):
        return []

    deals = []
    for item in data:
        try:
            title = item.get("title", "").strip()
            if not title:
                continue

            giveaway_id = item.get("id")
            if not giveaway_id:
                continue

            # Определяем площадку
            platforms = item.get("platforms", "").lower()
            if "steam" in platforms:
                store = "Steam"
            elif "epic" in platforms:
                store = "Epic Games"
            else:
                store = item.get("platforms", "PC")

            link = item.get("open_giveaway_url") or item.get("giveaway_url") or ""
            image_url = item.get("image") or item.get("thumbnail") or None
            worth = item.get("worth", "")  # например "$14.99"

            # Конвертируем worth в формат ~X.XX USD для автоконвертации в рубли
            old_price = "—"
            if worth and worth not in ("N/A", "", "0.00"):
                import re
                m = re.match(r"\$?([\d.]+)", worth.strip())
                if m:
                    old_price = f"~{m.group(1)} USD"

            deals.append(Deal(
                deal_id=f"gp_{giveaway_id}",
                title=title,
                store=store,
                old_price=old_price,
                new_price="Бесплатно",
                discount=100,
                link=link,
                image_url=image_url,
                is_free=True,
                genres=[],
            ))
        except Exception as e:
            log.debug(f"GamerPower: ошибка парсинга элемента: {e}")
            continue

    log.info(f"GamerPower: найдено {len(deals)} раздач")
    return deals
