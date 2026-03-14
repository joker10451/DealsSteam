"""
Обогащение данных о скидках:
- Рейтинг и количество отзывов из Steam Store API
- Исторический минимум цены через CheapShark
- Авто-комментарий бота по шаблонам
"""
import aiohttp
import asyncio
import time
from typing import Optional
from parsers.steam import Deal

# Простой TTL-кэш: {key: (value, expires_at)}
_cache: dict[str, tuple] = {}
_CACHE_TTL = 3600  # 1 час


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def _cache_set(key: str, value, ttl: int = _CACHE_TTL):
    _cache[key] = (value, time.time() + ttl)


# --- Steam рейтинг ---

STEAM_APPDETAILS_URL = "https://store.steampowered.com/appreviews/{appid}?json=1&language=all&purchase_type=all"

RATING_LABELS = {
    (95, 101): ("🏆 Крайне положительные", 95),
    (80, 95):  ("👍 Очень положительные", 80),
    (70, 80):  ("🙂 Положительные", 70),
    (40, 70):  ("😐 Смешанные", 40),
    (0,  40):  ("👎 Отрицательные", 0),
}


def rating_label(pct: int) -> str:
    for (lo, hi), (label, _) in RATING_LABELS.items():
        if lo <= pct < hi:
            return label
    return "❓ Нет данных"


async def get_steam_rating(appid: str) -> Optional[dict]:
    """Возвращает {'score': 85, 'total': 12000, 'label': '👍 Очень положительные'}"""
    cached = _cache_get(f"rating:{appid}")
    if cached is not None:
        return cached

    url = STEAM_APPDETAILS_URL.format(appid=appid)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
        summary = data.get("query_summary", {})
        total = summary.get("total_reviews", 0)
        positive = summary.get("total_positive", 0)
        if total < 10:
            return None
        score = int(positive / total * 100)
        result = {"score": score, "total": total, "label": rating_label(score)}
        _cache_set(f"rating:{appid}", result)
        return result
    except Exception:
        return None


# --- Исторический минимум через CheapShark ---

CS_LOWEST_URL = "https://www.cheapshark.com/api/1.0/games?steamAppID={appid}"


async def get_historical_low(appid: str) -> Optional[dict]:
    """Возвращает {'price': '4.99', 'is_current_low': True/False}"""
    cached = _cache_get(f"histlow:{appid}")
    if cached is not None:
        return cached

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(CS_LOWEST_URL.format(appid=appid), timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
        if not data:
            return None
        game = data[0] if isinstance(data, list) else data
        lowest = game.get("cheapestPriceEver", {})
        lowest_price = lowest.get("price")
        if not lowest_price:
            return None
        result = {"price": lowest_price}
        _cache_set(f"histlow:{appid}", result, ttl=6 * 3600)
        return result
    except Exception:
        return None


# --- Авто-комментарий бота ---

def generate_comment(deal: Deal, rating: Optional[dict]) -> str:
    """Генерирует короткий комментарий на основе данных об игре."""
    import random
    score = rating["score"] if rating else 0
    genres = deal.genres
    discount = deal.discount

    if deal.is_free:
        return random.choice([
            "Бесплатно — просто берём, не думаем.",
            "Цена вопроса — ноль. Качаем.",
            "Халява. Долго не думай.",
            "Бесплатно сегодня — платно завтра. Успевай.",
        ])
    
    # Огромная скидка (90%+)
    if discount >= 90:
        return random.choice([
            f"Скидка {discount}% — почти даром. Брать не раздумывая.",
            f"Такие скидки бывают раз в год. {discount}% — это почти подарок.",
            f"Цена смешная — всего {discount}% от полной. Берём.",
        ])
    
    # Очень большая скидка (80-89%)
    if discount >= 80:
        if score >= 85:
            return f"Огонь-скидка {discount}% на игру с высоким рейтингом. Однозначно брать."
        return f"Скидка {discount}% — отличная цена. Стоит взять."
    
    # Высокий рейтинг
    if score >= 95:
        if "Инди" in genres or "Indie" in genres:
            return "Инди с культовым рейтингом. Такое бывает редко — брать."
        if "RPG" in genres or "Ролевые" in genres:
            return "Один из лучших RPG по мнению сообщества. Не проходи мимо."
        return "Один из лучших в жанре. Брать не раздумывая."
    
    if score >= 85:
        if "Инди" in genres or "Indie" in genres:
            return "Инди с высоким рейтингом — редкость. Брать."
        if "Хоррор" in genres or "Horror" in genres:
            return "Высокий рейтинг для хоррора — значит реально пугает. Бери."
        if "Стратегия" in genres or "Strategy" in genres:
            return "Сообщество стратегов довольно. Хороший выбор на вечер."
        if discount >= 70:
            return f"Высокий рейтинг + скидка {discount}%. Хороший выбор."
        return "Высокий рейтинг — сообщество довольно. Хороший выбор."
    
    if score >= 70:
        if "Экшен" in genres or "Action" in genres:
            return "Крепкий экшен. Фанатам жанра зайдёт."
        if discount >= 75:
            return f"Крепкий середняк со скидкой {discount}%. Фанатам жанра зайдёт."
        return "Крепкий середняк. Фанатам жанра зайдёт."
    
    # Без рейтинга, но большая скидка
    if discount >= 75:
        return f"Скидка {discount}% — цена привлекательная. Стоит глянуть."
    
    if "RPG" in genres or "Ролевые" in genres:
        return "Для любителей RPG — стоит посмотреть."
    if "Экшен" in genres or "Action" in genres:
        return "Динамичный экшен. Подойдёт для разгрузки."
    if "Стратегия" in genres or "Strategy" in genres:
        return "Стратегия на вечер (или на неделю)."
    if "Инди" in genres or "Indie" in genres:
        return "Инди с душой. Таких мало."
    return "Хорошая скидка — самое время попробовать."


# --- Хэштеги из жанров ---

GENRE_HASHTAGS = {
    "RPG": "#RPG", "Ролевые": "#RPG",
    "Экшен": "#Экшен", "Action": "#Экшен",
    "Стратегия": "#Стратегия", "Strategy": "#Стратегия",
    "Инди": "#Инди", "Indie": "#Инди",
    "Приключение": "#Приключение", "Adventure": "#Приключение",
    "Симулятор": "#Симулятор", "Simulation": "#Симулятор",
    "Хоррор": "#Хоррор", "Horror": "#Хоррор",
    "Головоломка": "#Головоломка", "Puzzle": "#Головоломка",
    "Спорт": "#Спорт", "Sports": "#Спорт",
    "Гонки": "#Гонки", "Racing": "#Гонки",
    "Шутер": "#Шутер", "Shooter": "#Шутер",
}


def genres_to_hashtags(genres: list[str]) -> str:
    tags = []
    seen = set()
    for g in genres:
        tag = GENRE_HASHTAGS.get(g)
        if tag and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return " ".join(tags[:3])
