"""
AI-генерация текстов постов через Groq API (Llama 3.3 70B).
Бесплатно: 14 400 запросов/день, без карты — console.groq.com

Если GROQ_API_KEY не задан — возвращает None, publisher использует fallback.
"""
import logging
import random
from typing import Optional

from config import GROQ_API_KEY, TG_CHANNEL_LINK

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# Стили постов — выбираются случайно чтобы канал не палился как бот
_STYLES = [
    "дерзкий и прямой — как будто советуешь другу не упустить халяву",
    "как инсайдер который знает цену вещам — спокойно и уверенно",
    "с лёгким юмором — не смешно, но с характером",
    "как человек который сам только что купил и доволен",
    "срочность — скидка не вечная, надо брать сейчас",
]

_SYSTEM_PROMPT = """Ты пишешь посты для Telegram-канала со скидками на игры.
Пиши ЖИВО и ЦЕПЛЯЮЩЕ. Пиши на русском языке.

ПРАВИЛА:
- Коротко и по делу — максимум 5-6 строк основного текста
- Никаких шаблонных фраз: "хорошая сделка", "стоит купить", "затягивает с первых минут", "отличный вариант"
- Пиши как человек, не как бот
- Используй HTML-теги Telegram: <b>жирный</b>, <i>курсив</i> — и больше ничего
- Не используй markdown, звёздочки, решётки

СТРУКТУРА:
1. Первая строка — сильный крючок с названием и скидкой (жирным)
2. 2-3 короткие причины почему игра крутая (без воды)
3. Цена: было → стало
4. Одна строка усиления (насколько это дёшево / редко бывает)
5. Последняя строка — автодожим (см. ниже)

АВТОДОЖИМ — последняя строка поста, одна из вариантов (выбери случайно):
- 🔥 Больше таких скидок — в канале
- Подписывайся, чтобы не пропускать такие цены
- Таких цен давно не было — следи за каналом"""


async def generate_post_text(
    title: str,
    old_price: str,
    new_price: str,
    discount: int,
    is_free: bool,
    rating_score: Optional[int],
    genres: list[str],
    igdb_description: Optional[str],
) -> Optional[str]:
    """
    Генерирует текст поста через Groq.
    Возвращает готовый HTML-текст или None если API недоступен.
    """
    if not GROQ_API_KEY:
        return None

    style = random.choice(_STYLES)

    genre_str = ", ".join(genres[:3]) if genres else "не указан"
    rating_str = f"{rating_score}%" if rating_score else "нет данных"
    desc_str = f"\nОписание: {igdb_description}" if igdb_description else ""

    if is_free:
        price_info = f"Цена: БЕСПЛАТНО (было {old_price})"
    else:
        price_info = f"Старая цена: {old_price}\nНовая цена: {new_price}\nСкидка: -{discount}%"

    user_prompt = f"""Напиши пост в стиле: {style}

Данные об игре:
Название: {title}
{price_info}
Жанр: {genre_str}
Рейтинг: {rating_str}{desc_str}

Ссылка на канал для автодожима: {TG_CHANNEL_LINK}

Сгенерируй готовый пост."""

    try:
        from parsers.utils import get_session
        import aiohttp

        session = get_session()
        _own = False
        if session is None or session.closed:
            session = aiohttp.ClientSession()
            _own = True

        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 400,
            "temperature": 0.85,
        }
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            async with session.post(
                GROQ_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data["choices"][0]["message"]["content"].strip()
                    log.info(f"AI пост сгенерирован для '{title}' (стиль: {style})")
                    return text
                elif resp.status == 429:
                    log.warning("Groq: rate limit, используем fallback")
                    return None
                else:
                    body = await resp.text()
                    log.warning(f"Groq: HTTP {resp.status} — {body[:200]}")
                    return None
        finally:
            if _own:
                await session.close()

    except Exception as e:
        log.warning(f"Groq AI генерация не удалась: {e}")
        return None
