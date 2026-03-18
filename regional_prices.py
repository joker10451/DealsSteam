"""
Проверка региональных цен Steam через Steam Store API.
"""
import asyncio
import re
import time
import logging
from typing import Optional
from currency import to_rubles, format_rub
from parsers.utils import fetch_with_retry

log = logging.getLogger(__name__)

REGIONS = [
    ("🇷🇺", "ru", "RUB"),
    ("🇹🇷", "tr", "TRY"),
    ("🇦🇷", "ar", "ARS"),
    ("🇰🇿", "kz", "KZT"),
    ("🇺🇸", "us", "USD"),
]

_USD_RATES: dict[str, float] = {}
_USD_RATES_TIME: float = 0
_RATES_TTL = 6 * 3600


def extract_appid(text: str) -> Optional[str]:
    """Извлекает appid из ссылки Steam или возвращает None."""
    match = re.search(r"store\.steampowered\.com/app/(\d+)", text)
    return match.group(1) if match else None


async def _get_usd_rates() -> dict[str, float]:
    """Получает курсы валют к USD через открытый API."""
    global _USD_RATES, _USD_RATES_TIME
    now = time.time()
    if _USD_RATES and now - _USD_RATES_TIME < _RATES_TTL:
        return _USD_RATES
    try:
        data = await fetch_with_retry("https://open.er-api.com/v6/latest/USD")
        if data:
            _USD_RATES = data.get("rates", {})
            _USD_RATES_TIME = now
    except Exception as e:
        log.warning(f"Не удалось получить курсы валют: {e}")
    return _USD_RATES


async def cents_to_usd(cents: int, currency: str) -> Optional[float]:
    """Конвертирует центы локальной валюты в USD."""
    if currency == "USD":
        return cents / 100
    rates = await _get_usd_rates()
    rate = rates.get(currency)
    if not rate:
        return None
    return round((cents / 100) / rate, 2)


async def get_price_in_region(appid: str, country: str) -> Optional[dict]:
    url = (
        f"https://store.steampowered.com/api/appdetails"
        f"?appids={appid}&cc={country}&filters=price_overview"
    )
    try:
        data = await fetch_with_retry(url)
        if not data:
            return None
        app_data = data.get(str(appid), {})
        if not app_data.get("success"):
            return None
        return app_data["data"].get("price_overview")
    except Exception:
        return None


async def get_regional_prices(appid: str) -> list[dict]:
    """Возвращает список цен по регионам (запросы параллельные)."""
    tasks = [get_price_in_region(appid, cc) for _, cc, _ in REGIONS]
    prices = await asyncio.gather(*tasks)

    results = []
    for (flag, cc, currency), price in zip(REGIONS, prices):
        if not price:
            continue
        final_cents = price.get("final", 0)
        usd_equiv = await cents_to_usd(final_cents, currency)
        results.append({
            "flag": flag,
            "country": cc.upper(),
            "currency": currency,
            "formatted": price.get("final_formatted", "—"),
            "discount": price.get("discount_percent", 0),
            "usd_equiv": usd_equiv,
        })

    return results


def format_regional_prices(title: str, results: list[dict]) -> str:
    if not results:
        return f"Не удалось получить цены для <b>{title}</b>."

    lines = [f"🌍 <b>Региональные цены: {title}</b>\n"]
    for r in results:
        discount_str = f"  <code>-{r['discount']}%</code>" if r["discount"] > 0 else ""
        usd_str = ""
        if r["currency"] not in ("USD", "RUB") and r["usd_equiv"] is not None:
            usd_str = f"  <i>(≈ ${r['usd_equiv']:.2f})</i>"
        lines.append(
            f"{r['flag']} {r['country']}: <b>{r['formatted']}</b>{usd_str}{discount_str}"
        )

    with_usd = [r for r in results if r["usd_equiv"] is not None]
    if with_usd:
        cheapest = min(with_usd, key=lambda x: x["usd_equiv"])
        lines.append(
            f"\n💡 Дешевле всего: {cheapest['flag']} {cheapest['country']} — "
            f"<b>{cheapest['formatted']}</b>"
        )

    return "\n".join(lines)
