"""
Модуль для публикации тематических подборок игр.
"""
import logging
from datetime import datetime
import pytz
from typing import Optional

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")

# Темы подборок
COLLECTION_THEMES = {
    "weekend_coop": {
        "title": "🎮 Кооперативы на выходные",
        "description": "Лучшие игры для игры с друзьями",
        "genres": ["multiplayer", "co-op", "cooperative"],
        "emoji": "👥"
    },
    "budget_games": {
        "title": "💰 Игры до 300₽",
        "description": "Качественные игры по низкой цене",
        "max_price": 300,
        "emoji": "💸"
    },
    "story_rich": {
        "title": "📖 Игры с сюжетом",
        "description": "Для тех, кто любит истории",
        "genres": ["story rich", "narrative", "adventure"],
        "emoji": "📚"
    },
    "indie_gems": {
        "title": "💎 Инди-жемчужины",
        "description": "Малоизвестные шедевры от независимых разработчиков",
        "genres": ["indie"],
        "emoji": "✨"
    },
    "low_spec": {
        "title": "🖥 Для слабых ПК",
        "description": "Отличные игры, которые пойдут на любом компьютере",
        "emoji": "⚡️"
    },
    "short_games": {
        "title": "⏱ Короткие игры",
        "description": "Пройдешь за вечер (2-6 часов)",
        "emoji": "🎯"
    },
}


async def get_themed_deals(theme_key: str, limit: int = 10) -> list:
    """
    Получает подборку игр по теме.
    """
    from database import get_pool
    
    theme = COLLECTION_THEMES.get(theme_key)
    if not theme:
        log.error(f"Неизвестная тема: {theme_key}")
        return []
    
    pool = get_pool()
    
    try:
        # Базовый запрос: игры со скидками за последние 7 дней
        query = """
            SELECT DISTINCT deal_id, title, store, discount, new_price, old_price, 
                   url, genres, first_seen
            FROM deals
            WHERE first_seen >= NOW() - INTERVAL '7 days'
              AND discount >= 30
        """
        params = []
        
        # Фильтр по жанрам
        if "genres" in theme:
            genre_conditions = " OR ".join([f"genres ILIKE $%d" % (i+1) for i in range(len(theme["genres"]))])
            query += f" AND ({genre_conditions})"
            params.extend([f"%{g}%" for g in theme["genres"]])
        
        # Фильтр по цене
        if "max_price" in theme:
            # Извлекаем числовое значение из строки цены
            query += f" AND CAST(REGEXP_REPLACE(new_price, '[^0-9.]', '', 'g') AS FLOAT) <= ${len(params)+1}"
            params.append(theme["max_price"])
        
        query += f" ORDER BY discount DESC, first_seen DESC LIMIT ${len(params)+1}"
        params.append(limit)
        
        rows = await pool.fetch(query, *params)
        
        deals = []
        for row in rows:
            deals.append({
                "deal_id": row["deal_id"],
                "title": row["title"],
                "store": row["store"],
                "discount": row["discount"],
                "new_price": row["new_price"],
                "old_price": row["old_price"],
                "url": row["url"],
                "genres": row["genres"],
            })
        
        return deals
        
    except Exception as e:
        log.error(f"Ошибка получения подборки {theme_key}: {e}")
        return []


async def format_collection_message(theme_key: str, deals: list) -> str:
    """
    Форматирует подборку для публикации.
    """
    from html import escape as esc
    
    theme = COLLECTION_THEMES[theme_key]
    now = datetime.now(MSK).strftime("%d.%m.%Y")
    
    lines = [
        f"{theme['emoji']} <b>{theme['title'].upper()} · {now}</b>\n",
        f"📝 <i>{esc(theme['description'])}</i>\n",
        "━━━━━━━━━━━━━━━\n"
    ]
    
    for i, deal in enumerate(deals, 1):
        store_emoji = {"Steam": "🎮", "GOG": "🕹", "Epic Games": "🎁"}.get(deal["store"], "🎲")
        lines.append(
            f"{i}. {store_emoji} <b>{esc(deal['title'])}</b>\n"
            f"   💰 <s>{esc(deal['old_price'])}</s> → <b>{esc(deal['new_price'])}</b> (−{deal['discount']}%)"
        )
        if i < len(deals):
            lines.append("")
    
    lines.append("\n━━━━━━━━━━━━━━━")
    lines.append("💬 <i>Все игры из подборки сейчас со скидками!</i>")
    
    return "\n".join(lines)


async def post_themed_collection(theme_key: str):
    """
    Публикует тематическую подборку в канал.
    """
    from publisher import get_bot, send_with_retry
    from config import CHANNEL_ID
    
    try:
        deals = await get_themed_deals(theme_key, limit=8)
        
        if not deals:
            log.warning(f"Нет игр для подборки {theme_key}")
            return False
        
        message = await format_collection_message(theme_key, deals)
        
        bot = get_bot()
        await send_with_retry(
            lambda: bot.send_message(CHANNEL_ID, message)
        )
        
        log.info(f"✅ Подборка '{theme_key}' опубликована ({len(deals)} игр)")
        return True
        
    except Exception as e:
        log.error(f"❌ Ошибка публикации подборки {theme_key}: {e}")
        return False
