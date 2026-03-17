"""
Обогащение данных о скидках:
- Рейтинг и количество отзывов из Steam Store API
- Исторический минимум цены через CheapShark
- Авто-комментарий бота по шаблонам
"""
import asyncio
import time
from typing import Optional
from parsers.steam import Deal

# Простой TTL-кэш с ограничением размера: {key: (value, expires_at)}
_cache: dict[str, tuple] = {}
_CACHE_TTL = 3600  # 1 час
_CACHE_MAX_SIZE = 1000  # максимум записей


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def _cache_set(key: str, value, ttl: int = _CACHE_TTL):
    # Если кэш переполнен — удаляем 20% самых старых записей
    if len(_cache) >= _CACHE_MAX_SIZE:
        now = time.time()
        expired = [k for k, (_, exp) in _cache.items() if exp < now]
        for k in expired:
            del _cache[k]
        # Если всё ещё переполнен — удаляем по времени истечения
        if len(_cache) >= _CACHE_MAX_SIZE:
            oldest = sorted(_cache.items(), key=lambda x: x[1][1])
            for k, _ in oldest[:_CACHE_MAX_SIZE // 5]:
                del _cache[k]
    _cache[key] = (value, time.time() + ttl)


# --- Steam рейтинг ---

STEAM_APPDETAILS_URL = "https://store.steampowered.com/appreviews/{appid}?json=1&language=all&purchase_type=all"
STEAM_APPDETAILS_RU_URL = "https://store.steampowered.com/api/appdetails?appids={appid}&cc=ru&l=russian&filters=short_description"

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
        from parsers.utils import fetch_with_retry
        data = await fetch_with_retry(url, as_json=True)
        if not data:
            return None
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


# --- Steam описание на русском ---

async def get_steam_description(appid: str) -> Optional[str]:
    """Возвращает короткое описание игры на русском из Steam Store API."""
    cached = _cache_get(f"desc:{appid}")
    if cached is not None:
        return cached

    url = STEAM_APPDETAILS_RU_URL.format(appid=appid)
    try:
        from parsers.utils import fetch_with_retry
        data = await fetch_with_retry(url)
        if not data:
            return None
        app_data = data.get(str(appid), {})
        if not app_data.get("success"):
            return None
        desc = app_data.get("data", {}).get("short_description", "")
        if not desc:
            return None
        # Обрезаем до разумной длины
        if len(desc) > 300:
            desc = desc[:297] + "..."
        _cache_set(f"desc:{appid}", desc, ttl=24 * 3600)
        return desc
    except Exception:
        return None

ITAD_LOOKUP_URL = "https://api.isthereanydeal.com/games/lookup/v1"
ITAD_PRICES_URL = "https://api.isthereanydeal.com/games/prices/v3"


async def _itad_get_game_id(appid: str) -> Optional[str]:
    """Получить ITAD game ID по Steam appid."""
    from config import ITAD_API_KEY
    if not ITAD_API_KEY:
        return None
    cached = _cache_get(f"itad_id:{appid}")
    if cached is not None:
        return cached
    try:
        from parsers.utils import fetch_with_retry
        data = await fetch_with_retry(
            f"{ITAD_LOOKUP_URL}?key={ITAD_API_KEY}&appid={appid}"
        )
        if not data:
            return None
        game_id = data.get("game", {}).get("id")
        if game_id:
            _cache_set(f"itad_id:{appid}", game_id, ttl=24 * 3600)
        return game_id
    except Exception:
        return None


async def get_historical_low(appid: str) -> Optional[dict]:
    """Возвращает {'price': '4.99', 'is_current_low': True/False} через ITAD."""
    from config import ITAD_API_KEY

    cached = _cache_get(f"histlow:{appid}")
    if cached is not None:
        return cached

    # Пробуем ITAD если есть ключ
    if ITAD_API_KEY:
        try:
            game_id = await _itad_get_game_id(appid)
            if game_id:
                from parsers.utils import fetch_with_retry
                data = await fetch_with_retry(
                    f"{ITAD_PRICES_URL}?key={ITAD_API_KEY}&id={game_id}&country=US"
                )
                if data and isinstance(data, list) and data:
                    game_data = data[0]
                    deals = game_data.get("deals", [])
                    if deals:
                        # Ищем исторический минимум среди всех магазинов
                        hist_low = None
                        is_current_low = False
                        for deal in deals:
                            hl = deal.get("historyLow", {})
                            if hl and hl.get("amount") is not None:
                                if hist_low is None or hl["amount"] < hist_low:
                                    hist_low = hl["amount"]
                            # Флаг H = текущая цена = исторический минимум
                            if deal.get("flag") == "H":
                                is_current_low = True
                        if hist_low is not None:
                            result = {
                                "price": str(round(hist_low, 2)),
                                "is_current_low": is_current_low,
                            }
                            _cache_set(f"histlow:{appid}", result, ttl=6 * 3600)
                            return result
        except Exception:
            pass

    # Fallback: CheapShark
    try:
        from parsers.utils import fetch_with_retry
        data = await fetch_with_retry(
            f"https://www.cheapshark.com/api/1.0/games?steamAppID={appid}"
        )
        if not data:
            return None
        game = data[0] if isinstance(data, list) else data
        lowest = game.get("cheapestPriceEver", {})
        lowest_price = lowest.get("price")
        if not lowest_price:
            return None
        result = {"price": lowest_price, "is_current_low": False}
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
