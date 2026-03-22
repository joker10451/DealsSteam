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


async def pick_best_deal(candidates: list, rating_cache: dict | None = None) -> int:
    """
    AI выбирает лучшую сделку из списка кандидатов для публикации.

    Args:
        candidates: список Deal объектов (уже отфильтрованных)
        rating_cache: dict {deal_id: rating_dict} — предзагруженные рейтинги

    Returns:
        индекс лучшей сделки в списке (0 если AI недоступен или ошибка)
    """
    if not GROQ_API_KEY or not candidates:
        return 0

    if len(candidates) == 1:
        return 0

    rating_cache = rating_cache or {}

    # Формируем компактный список для AI
    lines = []
    for i, deal in enumerate(candidates):
        rating = rating_cache.get(deal.deal_id)
        rating_str = f"{rating['score']}%" if rating else "?"
        price_str = "БЕСПЛАТНО" if deal.is_free else f"{deal.new_price} (было {deal.old_price})"
        genres_str = ", ".join((deal.genres or [])[:2]) or "?"
        lines.append(
            f"{i+1}. {deal.title} | -{deal.discount}% | {price_str} | "
            f"рейтинг: {rating_str} | жанр: {genres_str} | магазин: {deal.store}"
        )

    deals_text = "\n".join(lines)

    prompt = f"""Ты редактор Telegram-канала со скидками на игры. Выбери ОДНУ игру из списка, которая даст максимальный отклик у аудитории.

Критерии (по важности):
1. Известность игры — популярные игры дают больше реакций
2. Размер скидки — чем больше, тем лучше
3. Рейтинг — высокий рейтинг = доверие аудитории
4. Цена — дешевле = больше покупок
5. Жанр — action/rpg/indie популярнее нишевых

Список игр:
{deals_text}

Ответь ТОЛЬКО цифрой — номером лучшей игры. Никакого текста, только цифра."""

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
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 5,
            "temperature": 0.2,
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
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    answer = data["choices"][0]["message"]["content"].strip()
                    idx = int(answer) - 1
                    if 0 <= idx < len(candidates):
                        log.info(f"AI выбрал сделку #{idx+1}: {candidates[idx].title}")
                        return idx
                elif resp.status == 429:
                    log.warning("Groq pick_best_deal: rate limit, используем fallback")
                else:
                    log.warning(f"Groq pick_best_deal: HTTP {resp.status}")
        finally:
            if _own:
                await session.close()

    except (ValueError, KeyError, IndexError) as e:
        log.warning(f"AI выбор сделки: не удалось распарсить ответ: {e}")
    except Exception as e:
        log.warning(f"AI выбор сделки не удался: {e}")

    return 0


async def generate_digest_header(top_deals: list[dict]) -> str | None:
    """
    Генерирует цепляющий заголовок для еженедельного дайджеста.

    Args:
        top_deals: список dict с ключами title, discount, store (топ скидок недели)

    Returns:
        Готовая первая строка дайджеста (HTML) или None если AI недоступен.
    """
    if not GROQ_API_KEY or not top_deals:
        return None

    # Компактный список топ-3 для контекста
    highlights = []
    for d in top_deals[:3]:
        highlights.append(f"{d['title']} −{d['discount']}% ({d['store']})")
    highlights_str = "\n".join(highlights)

    prompt = f"""Напиши ОДНУ строку — цепляющий заголовок для еженедельного дайджеста скидок на игры в Telegram-канале.

Топ скидок этой недели:
{highlights_str}

Требования:
- Одна строка, максимум 80 символов
- Без даты — дату добавим сами
- Эмодзи в начале (одно)
- Пиши на русском, живо и с характером
- Не используй: "лучшие скидки", "топ недели", "дайджест"
- Примеры стиля: "🔥 Эта неделя была жирной — смотри сам", "💥 Пока ты спал — Steam раздавал", "🎮 Неделя закончилась, скидки — нет"

Ответь ТОЛЬКО одной строкой заголовка, без кавычек."""

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
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 60,
            "temperature": 0.9,
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
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    header = data["choices"][0]["message"]["content"].strip().strip('"').strip("'")
                    log.info(f"AI заголовок дайджеста: {header}")
                    return header
                elif resp.status == 429:
                    log.warning("Groq digest header: rate limit")
                else:
                    log.warning(f"Groq digest header: HTTP {resp.status}")
        finally:
            if _own:
                await session.close()

    except Exception as e:
        log.warning(f"AI заголовок дайджеста не удался: {e}")

    return None
