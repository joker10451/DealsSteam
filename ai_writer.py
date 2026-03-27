"""
AI-генерация текстов постов через Groq API (Llama 3.3 70B).
Бесплатно: 14 400 запросов/день, без карты — console.groq.com

Если GROQ_API_KEY не задан — возвращает None, publisher использует fallback.
"""

import logging
import random
from typing import Optional

from config import GROQ_API_KEY

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# Стили постов — выбираются случайно чтобы канал не палился как бот
_STYLES = [
    # Дерзкие и прямые
    "дерзкий и прямой — как будто советуешь другу не упустить халяву",
    "без церемоний — коротко и по делу",
    "как будто ты уже купил и не можешь молчать — восторженно",
    # Инсайдерские
    "как инсайдер который знает цену вещам — спокойно и уверенно",
    "экспертный тон — разбирающийся в играх человек",
    "от лица того кто реально поиграл и рекомендует",
    # С юмором
    "с лёгким юмором — не смешно, но с характером",
    "ироничный — шутишь но совет серьёзный",
    "с самоиронией — признаёшь что залип",
    # Эмоциональные
    "эмоционально — чувствуется что реально зашло",
    "с восторгом фаната — не можешь молчать о находке",
    "как ребёнок который нашёл конфету — радостно",
    # Срочность
    "срочность — скидка не вечная, надо брать сейчас",
    "FOMO-режим — пока не улетело",
    "прагматичный — считаешь выгоду и сообщаешь",
    # Необычные
    "от лица персонажа игры — если это узнаваемый персонаж",
    "с неожиданной аналогией — сравниваешь с чем-то из жизни",
    "минималистично — только факты без эмоций",
    "как заголовок статьи — цепляет внимание",
]

_REASONS = [
    # Геймплей
    "затягивает с первых минут",
    "один из лучших представителей жанра",
    "проведено 100+ часов и не надоело",
    "прошёл дважды — всё равно круто",
    # Атмосфера
    "атмосфера на высоте — не оторвёшься",
    "такой мир хочется изучать",
    "звук и музыка — отдельный кайф",
    # Цена/выгода
    "за эти деньги — просто подарок",
    "обычно стоит в разы дороже",
    "на стимкартах выходит почти бесплатно",
    # Отзывы
    "народ уже проголосовал — отзывы говорят сами за себя",
    "рейтинг говорит сам за себя",
    "сообщество в восторге",
    # Уникальность
    "таких игр больше не делают",
    "это классика — must have",
    "уникальный опыт который не забывается",
]

_INTENSIFIERS = [
    # Редкость цены
    "такую цену ловишь раз в году",
    "дешевле я не видел — проверено",
    "исторический минимум — не шучу",
    "цена которая не продержится долго",
    # Сравнения
    "дешевле чем обед в фастфуде",
    "за эти деньги — просто укради",
    "цена шаурмы, а игра на 100 часов",
    # Срочность
    "завтра могут поднять — проверено",
    "пока не улетело — бери",
    "лепи пока горячо",
    # Эмоции
    "чувствую себя лысым вором — так выгодно",
    "кошелёк плачет от счастья",
    "этот ценник — мем",
]

_CALLS_TO_ACTION = [
    # Классика
    "👉 Бери пока не подняли цену",
    "👉 Забирай пока не улетело",
    "👉 Скорее бери — не пожалеешь",
    # Убедительные
    "👉 Обязательно к покупке",
    "👉 Не думай — бери",
    "👉 Это не обсуждается — бери",
    # С юмором
    "👉 Или будешь жалеть — решай сам",
    "👉 Пока не поздно — вперёд",
    "👉 Я бы на твоём месте уже брал",
    # Прямые
    "👉 Кнопка ниже — жми",
    "👉 Ссылка внизу — не теряй",
    "👉 Открывай и покупай",
    # Необычные
    "👉 Твой вишлист плачет — пора",
    "👉 Время действовать",
    "👉 Решение за тобой",
]

_SYSTEM_PROMPT = """Ты пишешь посты для Telegram-канала со скидками на игры в стиле Steam Community.

ПРАВИЛА:
- РОВНО 5 строк. Не больше, не меньше. Считай строки.
- Каждая строка — одна мысль, коротко
- Только HTML-теги: <b>жирный</b>
- Никакого markdown
- Пиши ТОЛЬКО на русском

СТИЛЬ:
- Интригующий заголовок — не "купите", а "что за игра"
- 2-3 конкретные фичи в одну строку каждая (через 🔵 или —)
- Без шаблонов "крутая игра", "обязательно брать"
- Эмоционально но по делу: "мощно", "огонь", "космос", "находка"
- Можно с юмором

ФОРМАТ (ровно 5 строк):
строка 1: <b>🔥 [Название] — −[скидка]%</b> — короткая цепляющая фраза про игру
строка 2: 🔵 [конкретная фича 1]
строка 3: 🔵 [конкретная фича 2] или 💰 [старая] → <b>[новая]</b>
строка 4: 🔵 [фича 3] или ещё одна причина
строка 5: 👉 [короткий призыв без ссылок]

Примеры:
— "🔵онлайн на 10к человек — не заскучаешь"
— "🔵выживаешь с другом 2 на 2"
— "🔵-building без регистрации и смс"
— "🔵первый час бесплатно — хватит понять"

НЕ ИСПОЛЬЗУЙ:
- "одна из лучших игр"
- "must have"
- "обязательно к покупке"
- "за такую цену — брать"
- Мат и грубости

Пиши как в Steam Community — конкретно, с характером."""


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
    reason_example = random.choice(_REASONS)
    intensifier = random.choice(_INTENSIFIERS)
    cta = random.choice(_CALLS_TO_ACTION)

    genre_str = ", ".join(genres[:2]) if genres else "не указан"
    rating_str = f"{rating_score}%" if rating_score else "нет данных"

    if is_free:
        price_info = f"БЕСПЛАТНО (было {old_price})"
    else:
        price_info = f"{old_price} → {new_price}, скидка -{discount}%"

    user_prompt = f"""Стиль: {style}

Пример фразы про игру: "{reason_example}"
Пример про цену: "{intensifier}"
Пример призыва: "{cta}"

Игра: {title}
Цена: {price_info}
Жанр: {genre_str}
Рейтинг: {rating_str}

Напиши пост ровно 5 строк. Не копируй примеры — используй похожий подход."""

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
            "max_tokens": 200,
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
        price_str = (
            "БЕСПЛАТНО" if deal.is_free else f"{deal.new_price} (было {deal.old_price})"
        )
        genres_str = ", ".join((deal.genres or [])[:2]) or "?"
        lines.append(
            f"{i + 1}. {deal.title} | -{deal.discount}% | {price_str} | "
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
                        log.info(
                            f"AI выбрал сделку #{idx + 1}: {candidates[idx].title}"
                        )
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
                    header = (
                        data["choices"][0]["message"]["content"]
                        .strip()
                        .strip('"')
                        .strip("'")
                    )
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
