import re
from html import escape

import aiohttp
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from regional_prices import extract_appid, get_regional_prices, format_regional_prices

router = Router()


def esc(text: str) -> str:
    return escape(str(text))


TAG_MAP = {
    "coop": "3843", "кооп": "3843",
    "roguelike": "1716", "рогалик": "1716",
    "horror": "4345", "хоррор": "4345",
    "rpg": "122", "рпг": "122",
    "strategy": "9", "стратегия": "9",
    "sandbox": "3959", "песочница": "3959",
    "survival": "1662", "выживание": "1662",
    "puzzle": "1664", "головоломка": "1664",
    "racing": "699", "гонки": "699",
    "shooter": "1155", "шутер": "1155",
}


@router.message(Command("price"))
async def cmd_price(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование:\n"
            "/price https://store.steampowered.com/app/1091500\n"
            "или\n"
            "/price Cyberpunk 2077"
        )
        return

    query = args[1].strip()
    appid = extract_appid(query)

    if not appid:
        search_url = "https://store.steampowered.com/api/storesearch/"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(search_url, params={"term": query, "cc": "ru", "l": "russian"}) as r:
                    data = await r.json()
            items = data.get("items", [])
            if items:
                appid = str(items[0]["id"])
                title = items[0]["name"]
            else:
                await message.answer(f"Игра «{esc(query)}» не найдена в Steam.")
                return
        except Exception as e:
            await message.answer(f"Ошибка поиска: {e}")
            return
    else:
        title = f"App {appid}"

    wait_msg = await message.answer("🔍 Проверяю цены по регионам...")
    results = await get_regional_prices(appid)
    text = format_regional_prices(title if not extract_appid(query) else f"App {appid}", results)
    await wait_msg.edit_text(text)


@router.message(Command("find"))
async def cmd_find(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        tags_list = ", ".join(list(TAG_MAP.keys())[:10])
        await message.answer(f"Использование: /find <тег>\n\nДоступные теги:\n{tags_list} ...")
        return

    tag_query = args[1].strip().lower()
    tag_id = TAG_MAP.get(tag_query)
    if not tag_id:
        await message.answer(
            f"Тег «{esc(tag_query)}» не найден.\n\n"
            "Попробуй: coop, roguelike, horror, rpg, strategy, sandbox, survival, puzzle, racing, shooter"
        )
        return

    wait_msg = await message.answer(f"🔍 Ищу игры по тегу «{esc(tag_query)}»...")
    url = "https://store.steampowered.com/search/results/"
    params = {"json": 1, "tags": tag_id, "specials": 1, "sort_by": "Discount_DESC", "count": 5}

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
    except Exception as e:
        await wait_msg.edit_text(f"Ошибка запроса: {e}")
        return

    items = data.get("items", [])
    if not items:
        await wait_msg.edit_text(f"Скидок по тегу «{esc(tag_query)}» сейчас нет.")
        return

    lines = [f"🎮 <b>Скидки по тегу #{tag_query}:</b>\n"]
    buttons = []
    for item in items[:5]:
        name = item.get("name", "?")
        appid = item.get("id", "")
        block = item.get("discount_block", "")
        discount_match = re.search(r"-(\d+)%", block)
        discount = f"-{discount_match.group(1)}%" if discount_match else ""
        link = f"https://store.steampowered.com/app/{appid}/"
        lines.append(f"• <a href='{link}'>{esc(name)}</a> {discount}")
        buttons.append([InlineKeyboardButton(text=f"🛒 {name[:35]}", url=link)])

    await wait_msg.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.message(Command("top"))
async def cmd_top(message: Message):
    """
    Топ текущих скидок в личку (без публикации в канал).
    Фильтрует игры из Steam библиотеки пользователя.
    
    Requirements: 2.5
    """
    from scheduler import get_top_deals_now
    
    wait_msg = await message.answer("🔍 Ищу лучшие скидки...")
    
    # Pass user_id to filter out owned games
    deals = await get_top_deals_now(limit=5, user_id=message.from_user.id)
    
    if not deals:
        await wait_msg.edit_text("Сейчас нет новых скидок.")
        return

    store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁"}.get
    lines = ["🏆 <b>Топ скидок прямо сейчас:</b>\n"]
    buttons = []
    for i, deal in enumerate(deals, 1):
        emoji = store_emoji(deal.store, "🕹")
        price = "Бесплатно" if deal.is_free else f"{esc(deal.new_price)} (-{deal.discount}%)"
        lines.append(f"{i}. {emoji} <b>{esc(deal.title)}</b> — {price}")
        buttons.append([InlineKeyboardButton(text=f"🛒 {deal.title[:35]}", url=deal.link)])

    await wait_msg.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Показать статистику канала."""
    from database import get_pool
    from datetime import datetime, timedelta
    import pytz
    
    MSK = pytz.timezone("Europe/Moscow")
    now = datetime.now(MSK)
    week_ago = now - timedelta(days=7)
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Всего опубликовано за неделю
        total_posts = await conn.fetchval(
            "SELECT COUNT(*) FROM posted_deals WHERE posted_at >= $1",
            week_ago
        )
        
        # Средняя скидка
        avg_discount = await conn.fetchval(
            "SELECT AVG(discount) FROM posted_deals WHERE posted_at >= $1 AND discount > 0",
            week_ago
        )
        
        # Максимальная скидка
        max_discount = await conn.fetchval(
            "SELECT MAX(discount) FROM posted_deals WHERE posted_at >= $1",
            week_ago
        )
        
        # Бесплатных раздач
        free_games = await conn.fetchval(
            "SELECT COUNT(*) FROM posted_deals WHERE posted_at >= $1 AND discount = 100",
            week_ago
        )
        
        # Топ по голосам
        top_voted = await conn.fetch(
            """
            SELECT deal_id, COUNT(*) as votes 
            FROM votes 
            WHERE vote_type = 'fire' AND voted_at >= $1
            GROUP BY deal_id 
            ORDER BY votes DESC 
            LIMIT 3
            """,
            week_ago
        )
        
        # Активных пользователей вишлиста
        wishlist_users = await conn.fetchval(
            "SELECT COUNT(DISTINCT user_id) FROM wishlist"
        )
    
    lines = [
        "📊 <b>Статистика канала за неделю</b>\n",
        f"📝 Опубликовано скидок: <b>{total_posts or 0}</b>",
        f"💰 Средняя скидка: <b>{int(avg_discount or 0)}%</b>",
        f"🔥 Максимальная скидка: <b>{int(max_discount or 0)}%</b>",
        f"🎁 Бесплатных раздач: <b>{free_games or 0}</b>",
        f"👥 Пользователей вишлиста: <b>{wishlist_users or 0}</b>",
    ]
    
    if top_voted:
        lines.append("\n🏆 <b>Топ по голосам:</b>")
        for i, row in enumerate(top_voted, 1):
            lines.append(f"{i}. {row['deal_id'][:30]} — {row['votes']} 🔥")
    
    await message.answer("\n".join(lines))


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Показать список всех команд."""
    help_text = (
        "🤖 <b>Команды бота GameDeals Radar</b>\n\n"
        "<b>Вишлист:</b>\n"
        "• Напиши название игры — добавлю в вишлист\n"
        "• /wishlist — посмотреть список (до 20 игр)\n"
        "• /remove [название] — удалить из вишлиста\n"
        "• /cancel — отменить добавление\n\n"
        "<b>Поиск скидок:</b>\n"
        "• /top — топ-5 скидок прямо сейчас\n"
        "• /find [тег] — найти по жанру (coop, rpg, horror...)\n"
        "• /price [ссылка или название] — цены по регионам Steam\n\n"
        "<b>Уведомления:</b>\n"
        "• /notify_settings — настройки уведомлений вишлиста\n"
        "• /min_discount — мин. скидка для уведомлений\n"
        "• /quiet_hours — тихие часы (не беспокоить)\n"
        "• /freenotify — уведомления о бесплатных играх\n\n"
        "<b>Мини-игры и баллы:</b>\n"
        "• /games — мини-игры\n"
        "• /score — мои баллы\n"
        "• /leaderboard — таблица лидеров\n"
        "• /challenge — челлендж дня\n"
        "• /achievements — мои достижения\n\n"
        "<b>Магазин:</b>\n"
        "• /shop — магазин призов\n"
        "• /buy [id] — купить приз\n"
        "• /myrewards — мои призы\n\n"
        "<b>Steam:</b>\n"
        "• /steam — привязать аккаунт\n"
        "• /steamsync — синхронизировать вишлист\n\n"
        "<b>Прочее:</b>\n"
        "• /invite — пригласить друга (+100 баллов)\n"
        "• /profile — мой профиль\n"
        "• /help — показать это сообщение\n\n"
        "📢 Канал: <a href='https://t.me/GameDealsRadarRu'>@GameDealsRadarRu</a>\n"
        "💬 Вопросы: @Joker104_97"
    )
    await message.answer(help_text, disable_web_page_preview=True)


