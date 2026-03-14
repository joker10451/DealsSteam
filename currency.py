"""
Конвертация валют в рубли через API Центробанка РФ.
Курс кешируется на 6 часов.
"""
import aiohttp
import time
from typing import Optional

_cache: dict[str, float] = {}
_cache_time: float = 0
_CACHE_TTL = 6 * 3600  # 6 часов


async def _fetch_rates() -> dict[str, float]:
    """Загружает курсы с ЦБ РФ."""
    url = "https://www.cbr-xml-daily.ru/daily_json.js"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    return {}
                data = await r.json(content_type=None)
        rates = {}
        for code, info in data.get("Valute", {}).items():
            # nominal может быть не 1 (например USD=1, JPY=100)
            nominal = info.get("Nominal", 1)
            value = info.get("Value", 0)
            rates[code.upper()] = value / nominal
        return rates
    except Exception:
        return {}


async def get_rate(currency: str) -> Optional[float]:
    """Возвращает курс валюты к рублю. Например get_rate('USD') -> 92.5"""
    global _cache, _cache_time

    currency = currency.upper()
    if currency == "RUB":
        return 1.0

    now = time.time()
    if not _cache or now - _cache_time > _CACHE_TTL:
        _cache = await _fetch_rates()
        _cache_time = now

    return _cache.get(currency)


async def to_rubles(amount: float, currency: str) -> Optional[float]:
    """Конвертирует сумму в рубли. Возвращает None если курс недоступен."""
    rate = await get_rate(currency)
    if rate is None:
        return None
    return round(amount * rate)


def format_rub(amount: float) -> str:
    """Форматирует сумму в рублях: 1 234 ₽"""
    return f"{int(amount):,} ₽".replace(",", " ")
