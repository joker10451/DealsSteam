"""
Проверка региональных цен Steam через Steam Store API.
"""
import aiohttp
import re
from typing import Optional

REGIONS = [
    ("🇷🇺", "ru", "₽"),
    ("🇹🇷", "tr", "₺"),
    ("🇦🇷", "ar", "ARS"),
    ("🇰🇿", "kz", "₸"),
    ("🇺🇸", "us", "$"),
]


def extract_appid(text: str) -> Optional[str]:
    """Извлекает appid из ссылки Steam или возвращает None."""
    match = re.search(r"store\.steampowered\.com/app/(\d+)", text)
    return match.group(1) if match else None


async def get_price_in_region(appid: str, country: str) -> Optional[dict]:
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc={country}&filters=price_overview"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    return None
                data = await r.json()
        app_data = data.get(str(appid), {})
        if not app_data.get("success"):
            return None
        price = app_data["data"].get("price_overview")
        return price  # {"final": 199, "initial": 999, "discount_percent": 80, "final_formatted": "1,99₺"}
    except Exception:
        return None


async def get_regional_prices(appid: str) -> list[dict]:
    """Возвращает список цен по регионам."""
    results = []
    for flag, cc, currency in REGIONS:
        price = await get_price_in_region(appid, cc)
        if price:
            results.append({
                "flag": flag,
                "country": cc.upper(),
                "currency": currency,
                "formatted": price.get("final_formatted", "—"),
                "discount": price.get("discount_percent", 0),
                "final_cents": price.get("final", 0),
            })
    return results


def format_regional_prices(title: str, results: list[dict]) -> str:
    if not results:
        return f"Не удалось получить цены для <b>{title}</b>."

    lines = [f"🌍 <b>Региональные цены: {title}</b>\n"]
    for r in results:
        discount_str = f"  <code>-{r['discount']}%</code>" if r["discount"] > 0 else ""
        lines.append(f"{r['flag']} {r['country']}: <b>{r['formatted']}</b>{discount_str}")

    # Самый дешёвый регион
    cheapest = min(results, key=lambda x: x["final_cents"])
    lines.append(f"\n💡 Дешевле всего: {cheapest['flag']} {cheapest['country']} — <b>{cheapest['formatted']}</b>")
    return "\n".join(lines)
