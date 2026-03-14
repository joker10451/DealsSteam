"""
Проверка региональных цен Steam через Steam Store API.
"""
import asyncio
import aiohttp
import re
from typing import Optional
from currency import to_rubles, format_rub

REGIONS = [
    ("🇹🇷", "tr", "TRY"),
    ("🇦🇷", "ar", "ARS"),
    ("🇰🇿", "kz", "KZT"),
    ("🇺🇸", "us", "USD"),
]

# Курсы к USD (приблизительные, для сортировки)
# Используем Steam API — он сам возвращает цену в local currency cents
# Для честного сравнения конвертируем через курс ЦБ/fixer
_USD_RATES: dict[str, float] = {}
_USD_RATES_TIME: float = 0
_RATES_TTL = 6 * 3600


def extract_appid(text: str) -> Optional[str]:
    """Извлекает appid из ссылки Steam или возвращает None."""
    match = re.search(r"store\.steampowered\.com/app/(\d+)", text)
    return match.group(1) if match else None


async def _get_usd_rates() -> dict[str, float]:
    """Получает курсы валют к USD через открытый API."""
    import time
    global _USD_RATES, _USD_RATES_TIME
    now = time.time()
    if _USD_RATES and now - _USD_RATES_TIME < _RATES_TTL:
        return _USD_RATES
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    return _USD_RATES
                data = await r.json()
        rates = data.get("rates", {})
        _USD_RATES = rates
        _USD_RATES_TIME = now
        return rates
    except Exception:
        return _USD_RATES


async def cents_to_usd(cents: int, currency: str) -> Optional[float]:
    """Конвертирует центы локальной валюты в USD."""
    if currency == "USD":
        return cents / 100
    rates = await _get_usd_rates()
    rate = rates.get(currency)
    if not rate:
        return None
    # cents / 100 = local amount; local / rate = USD
    return round((cents / 100) / rate, 2)


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
        return price
    except Exception:
        return None


async def get_regional_prices(appid: str) -> list[dict]:
    """Возвращает список цен по регионам (запросы параллельные)."""
    tasks = [get_price_in_region(appid, cc) for _, cc, _ in REGIONS]
    prices = await asyncio.gather(*tasks)

    results = []
    for (flag, cc, currency), price in zip(REGIONS, prices):
        if price:
            final_cents = price.get("final", 0)
            usd_equiv = await cents_to_usd(final_cents, currency)
            results.append({
                "flag": flag,
                "country": cc.upper(),
                "currency": currency,
                "formatted": price.get("final_formatted", "—"),
                "discount": price.get("discount_percent", 0),
                "final_cents": final_cents,
                "usd_equiv": usd_equiv,
            })

    # Расчётный рублёвый эквивалент на основе US цены
    us = next((r for r in results if r["country"] == "US"), None)
    if us and us["usd_equiv"] is not None:
        rub = await to_rubles(us["usd_equiv"], "USD")
        if rub:
            results.insert(0, {
                "flag": "🇷🇺",
                "country": "RU",
                "currency": "RUB",
                "formatted": format_rub(rub),
                "discount": us["discount"],
                "final_cents": None,
                "usd_equiv": us["usd_equiv"],
                "estimated": True,
            })

    return results


def format_regional_prices(title: str, results: list[dict]) -> str:
    if not results:
        return f"Не удалось получить цены для <b>{title}</b>."

    lines = [f"🌍 <b>Региональные цены: {title}</b>\n"]
    for r in results:
        discount_str = f"  <code>-{r['discount']}%</code>" if r["discount"] > 0 else ""
        if r["currency"] != "USD" and r["usd_equiv"] is not None:
            usd_str = f"  <i>(≈ ${r['usd_equiv']:.2f})</i>"
        else:
            usd_str = ""
        estimated_str = "  <i>~расчётно</i>" if r.get("estimated") else ""
        lines.append(f"{r['flag']} {r['country']}: <b>{r['formatted']}</b>{usd_str}{discount_str}{estimated_str}")

    # Самый дешёвый регион по USD эквиваленту
    with_usd = [r for r in results if r["usd_equiv"] is not None]
    if with_usd:
        cheapest = min(with_usd, key=lambda x: x["usd_equiv"])
        lines.append(f"\n💡 Дешевле всего: {cheapest['flag']} {cheapest['country']} — <b>{cheapest['formatted']}</b>")
        if cheapest["currency"] != "USD":
            lines[-1] += f"  <i>(≈ ${cheapest['usd_equiv']:.2f})</i>"

    return "\n".join(lines)
