import asyncio
import logging
from datetime import datetime
from html import escape
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, CallbackQuery,
)
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from config import (
    BOT_TOKEN, CHANNEL_ID, ADMIN_ID,
    MIN_DISCOUNT_PERCENT, MIN_STEAM_RATING,
    POST_TIMES, TOP_DEALS_PER_POST,
)
from database import (
    init_db, is_already_posted, mark_as_posted, cleanup_old_records,
    get_weekly_top, wishlist_add, wishlist_remove, wishlist_list, get_wishlist_matches,
    add_vote, get_votes, get_top_voted, save_price_game, get_price_game,
)
from parsers.steam import get_steam_deals
from parsers.gog import get_gog_deals
from parsers.epic import get_epic_deals
from enricher import get_steam_rating, get_historical_low, generate_comment, genres_to_hashtags
from igdb import get_game_info
from collage import make_collage
from regional_prices import extract_appid, get_regional_prices, format_regional_prices
from hidden_gems import find_hidden_gems
from currency import to_rubles, format_rub

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
MSK = pytz.timezone("Europe/Moscow")


def esc(text: str) -> str:
    return escape(str(text))


# ─── Категория дня ───────────────────────────────────────────────────────────

DAILY_THEMES = {
    0: ("⚔️", "RPG-понедельник",     ["RPG", "Ролевые"]),
    1: ("💥", "Экшен-вторник",       ["Экшен", "Action", "Шутер"]),
    2: ("🧠", "Стратегия-среда",     ["Стратегия", "Strategy"]),
    3: ("🎲", "Инди-четверг",        ["Инди", "Indie"]),
    4: ("👻", "Хоррор-пятница",      ["Хоррор", "Horror"]),
    5: ("🏎️", "Выходные-скидки",    []),
    6: ("🏆", "Воскресный топ",      []),
}


def get_daily_theme() -> tuple[str, str, list[str]]:
    weekday = datetime.now(MSK).weekday()
    return DAILY_THEMES[weekday]


def theme_score(deal, theme_genres: list[str]) -> int:
    if not theme_genres:
        return 0
    return 1 if any(g in deal.genres for g in theme_genres) else 0


# ─── Антидубль по названию ───────────────────────────────────────────────────

def deduplicate(deals: list) -> list:
    seen: dict[str, object] = {}
    for deal in deals:
        key = deal.title.lower().strip()
        if key not in seen or deal.discount > seen[key].discount:
            seen[key] = deal
    return list(seen.values())


# ─── Клавиатура для личных чатов ────────────────────────────────────────────

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мой вишлист"), KeyboardButton(text="❌ Удалить из вишлиста")],
        ],
        resize_keyboard=True,
    )


# ─── Хэндлеры wishlist ──────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я слежу за скидками на игры.\n\n"
        "<b>Что я умею:</b>\n"
        "• Напиши название игры — добавлю в вишлист и уведомлю при скидке\n\n"
        "<b>Команды:</b>\n"
        "/wishlist — посмотреть вишлист\n"
        "/remove [название] — удалить из вишлиста\n"
        "/price [ссылка или название] — цены по регионам Steam\n"
        "/find [тег] — найти скидки по жанру (coop, rpg, horror...)",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("wishlist"))
@dp.message(F.text == "📋 Мой вишлист")
async def cmd_wishlist(message: Message):
    items = await wishlist_list(message.from_user.id)
    if not items:
        await message.answer("Твой вишлист пуст. Напиши название игры чтобы добавить.")
        return
    lines = [f"{i+1}. {esc(item)}" for i, item in enumerate(items)]
    await message.answer("📋 <b>Твой вишлист:</b>\n\n" + "\n".join(lines))


@dp.message(Command("remove"))
@dp.message(F.text == "❌ Удалить из вишлиста")
async def cmd_remove_prompt(message: Message):
    items = await wishlist_list(message.from_user.id)
    if not items:
        await message.answer("Вишлист пуст.")
        return
    lines = [f"{i+1}. {esc(item)}" for i, item in enumerate(items)]
    await message.answer(
        "📋 Напиши <b>/remove [название]</b> чтобы удалить.\n\n" + "\n".join(lines)
    )


@dp.message(Command("remove", magic=F.args))
async def cmd_remove(message: Message):
    query = message.text.split(maxsplit=1)[1].strip() if len(message.text.split()) > 1 else ""
    if not query:
        await message.answer("Укажи название: /remove Cyberpunk 2077")
        return
    removed = await wishlist_remove(message.from_user.id, query)
    if removed:
        await message.answer(f"✅ «{esc(query)}» удалено из вишлиста.")
    else:
        await message.answer(f"Не нашёл «{esc(query)}» в твоём вишлисте.")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_wishlist_add(message: Message):
    query = message.text.strip()
    if len(query) < 2:
        return
    added = await wishlist_add(message.from_user.id, query)
    if added:
        await message.answer(
            f"✅ <b>{esc(query)}</b> добавлено в вишлист.\n"
            "Пришлю уведомление когда появится скидка.",
            reply_markup=main_keyboard(),
        )
    else:
        await message.answer(f"«{esc(query)}» уже есть в твоём вишлисте.")


# ─── Региональные цены ───────────────────────────────────────────────────────

@dp.message(Command("price"))
async def cmd_price(message: Message):
    """
    /price <ссылка на Steam или название>
    Показывает цену игры в разных регионах.
    """
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

    # Если передали название — ищем через Steam Search
    if not appid:
        search_url = "https://store.steampowered.com/api/storesearch/"
        try:
            async with __import__("aiohttp").ClientSession() as s:
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
        title = query  # покажем ссылку как есть, цены сами скажут название

    wait_msg = await message.answer("🔍 Проверяю цены по регионам...")
    results = await get_regional_prices(appid)
    text = format_regional_prices(title if not extract_appid(query) else f"App {appid}", results)
    await wait_msg.edit_text(text)


# ─── Поиск по тегам ──────────────────────────────────────────────────────────

# Словарь тег → Steam tag ID
TAG_MAP = {
    "coop":        "3843",   # Co-op
    "кооп":        "3843",
    "roguelike":   "1716",
    "рогалик":     "1716",
    "horror":      "4345",
    "хоррор":      "4345",
    "rpg":         "122",
    "рпг":         "122",
    "strategy":    "9",
    "стратегия":   "9",
    "sandbox":     "3959",
    "песочница":   "3959",
    "survival":    "1662",
    "выживание":   "1662",
    "puzzle":      "1664",
    "головоломка": "1664",
    "racing":      "699",
    "гонки":       "699",
    "shooter":     "1155",
    "шутер":       "1155",
}


@dp.message(Command("find"))
async def cmd_find(message: Message):
    """
    /find <тег> — поиск игр со скидкой по жанру/тегу.
    Пример: /find coop  /find рогалик
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        tags_list = ", ".join(f"/{k}" for k in list(TAG_MAP.keys())[:10])
        await message.answer(
            f"Использование: /find <тег>\n\nДоступные теги:\n{tags_list} ..."
        )
        return

    tag_query = args[1].strip().lower()
    tag_id = TAG_MAP.get(tag_query)

    if not tag_id:
        await message.answer(
            f"Тег «{esc(tag_query)}» не найден.\n\n"
            f"Попробуй: coop, roguelike, horror, rpg, strategy, sandbox, survival, puzzle, racing, shooter"
        )
        return

    wait_msg = await message.answer(f"🔍 Ищу игры по тегу «{esc(tag_query)}»...")

    url = "https://store.steampowered.com/search/results/"
    params = {"json": 1, "tags": tag_id, "specials": 1, "sort_by": "Discount_DESC", "count": 5}

    try:
        async with __import__("aiohttp").ClientSession() as s:
            async with s.get(url, params=params, timeout=__import__("aiohttp").ClientTimeout(total=10)) as r:
                data = await r.json()
    except Exception as e:
        await wait_msg.edit_text(f"Ошибка запроса: {e}")
        return

    items = data.get("items", [])
    if not items:
        await wait_msg.edit_text(f"Скидок по тегу «{esc(tag_query)}» сейчас нет.")
        return

    import re
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

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await wait_msg.edit_text("\n".join(lines), reply_markup=keyboard)


# ─── Голосование 🔥 / 💩 ─────────────────────────────────────────────────────

def vote_keyboard(deal_id: str, fire: int = 0, poop: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"🔥 {fire}", callback_data=f"vote:fire:{deal_id}"),
        InlineKeyboardButton(text=f"💩 {poop}", callback_data=f"vote:poop:{deal_id}"),
    ]])


@dp.callback_query(F.data.startswith("vote:"))
async def handle_vote(callback: CallbackQuery):
    _, vote_type, deal_id = callback.data.split(":", 2)
    saved = await add_vote(deal_id, callback.from_user.id, vote_type)

    if not saved:
        await callback.answer("Ты уже голосовал за эту игру!", show_alert=False)
        return

    counts = await get_votes(deal_id)
    # Обновляем кнопки с новыми счётчиками
    try:
        await callback.message.edit_reply_markup(
            reply_markup=vote_keyboard(deal_id, counts["fire"], counts["poop"])
        )
    except Exception:
        pass

    label = "🔥 Огонь!" if vote_type == "fire" else "💩 Мимо"
    await callback.answer(label, show_alert=False)


# ─── Мини-игра: угадай цену ──────────────────────────────────────────────────

def price_game_keyboard(deal_id: str, options: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{price}₽",
            callback_data=f"pg:{deal_id}:{price}"
        )
        for price in options
    ]
    # 2 кнопки в ряд
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data.startswith("pg:"))
async def handle_price_game(callback: CallbackQuery):
    parts = callback.data.split(":")
    deal_id = parts[1]
    chosen = int(parts[2])

    correct = await get_price_game(deal_id)
    if correct is None:
        await callback.answer("Игра уже закончилась!", show_alert=False)
        return

    if chosen == correct:
        await callback.answer(f"✅ Правильно! Цена была {correct}₽", show_alert=True)
    else:
        await callback.answer(f"❌ Неверно. Правильный ответ: {correct}₽", show_alert=True)

    # Убираем кнопки после ответа
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


async def send_price_game(deal) -> None:
    """Отправляет мини-игру 'угадай цену' после публикации поста."""
    try:
        old_price_str = str(deal.old_price).replace("₽", "").replace(" ", "").replace(",", "").strip()
        correct = int(float(old_price_str))
    except (ValueError, AttributeError):
        return

    if correct <= 0:
        return

    import random
    # Генерируем 3 варианта-ловушки ± 10-40%
    variants = set()
    variants.add(correct)
    while len(variants) < 4:
        delta = random.randint(10, 40)
        sign = random.choice([-1, 1])
        fake = round(correct * (1 + sign * delta / 100) / 10) * 10
        if fake > 0 and fake != correct:
            variants.add(fake)

    options = sorted(list(variants))
    random.shuffle(options)

    await save_price_game(deal.deal_id, correct)

    text = (
        f"🎮 <b>Мини-игра: угадай цену!</b>\n\n"
        f"Сколько стоила <b>{esc(deal.title)}</b> до скидки?\n"
        f"Выбери правильный ответ 👇"
    )
    try:
        await bot.send_message(
            CHANNEL_ID, text,
            reply_markup=price_game_keyboard(deal.deal_id, options)
        )
    except Exception as e:
        log.warning(f"Мини-игра не отправлена: {e}")


# ─── Retry при ошибках Telegram ──────────────────────────────────────────────

async def send_with_retry(coro_fn, retries: int = 3, delay: float = 5.0):
    """Выполняет корутину с повторными попытками при сетевых ошибках."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn()
        except Exception as e:
            last_exc = e
            log.warning(f"Попытка {attempt}/{retries} не удалась: {e}")
            if attempt < retries:
                await asyncio.sleep(delay * attempt)
    raise last_exc


async def _localize_price(price_str: str) -> str:
    """Конвертирует цену вида '~250 EUR' в рубли. Остальное возвращает как есть."""
    import re
    if not price_str or not price_str.startswith("~"):
        return price_str
    match = re.match(r"~([\d.]+)\s+([A-Z]+)", price_str)
    if not match:
        return price_str
    amount, currency = float(match.group(1)), match.group(2)
    rub = await to_rubles(amount, currency)
    return format_rub(rub) if rub else price_str.lstrip("~")


# ─── Публикация одного поста ─────────────────────────────────────────────────

async def publish_single(deal) -> bool:
    now = datetime.now(MSK).strftime("%d.%m.%Y")
    store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁", "CheapShark": "💰"}.get(deal.store, "🕹")

    rating = None
    historical_low = None
    igdb_info = None

    if deal.store == "Steam" and deal.deal_id.startswith("steam_"):
        appid = deal.deal_id.replace("steam_", "")
        rating, historical_low, igdb_info = await asyncio.gather(
            get_steam_rating(appid),
            get_historical_low(appid),
            get_game_info(deal.title),
        )
    else:
        igdb_info = await get_game_info(deal.title)

    is_historic = bool(historical_low and deal.discount >= 70)
    theme_emoji, theme_name, _ = get_daily_theme()

    # Локализуем цены в рубли (для GOG/Epic если пришли не в RUB)
    old_price = await _localize_price(deal.old_price)
    new_price = await _localize_price(deal.new_price)

    lines = []
    if deal.is_free:
        lines.append(f"🎁 <b>БЕСПЛАТНО · {now}</b>")
    elif is_historic:
        lines.append(f"⚡️ <b>ИСТОРИЧЕСКИЙ МИНИМУМ · {now}</b>")
    else:
        lines.append(f"{theme_emoji} <b>{theme_name.upper()} · {now}</b>")

    lines.append(f"\n{store_emoji} <b>{esc(deal.title)}</b>")
    lines.append("━━━━━━━━━━━━━━")

    if deal.is_free:
        lines.append("💸 <s>Платная</s>  →  🆓 <b>БЕСПЛАТНО</b>")
    else:
        lines.append(f"💸 <s>{esc(old_price)}</s>  ➔  ✅ <b>{esc(new_price)}</b>")
        lines.append(f"🏷 Скидка: <b>−{deal.discount}%</b>")

    if getattr(deal, "sale_end", None):
        lines.append(f"⏳ Скидка до: <b>{deal.sale_end}</b>")

    if rating:
        lines.append(f"📊 Steam: <b>{rating['score']}%</b>  {esc(rating['label'])}")
    elif igdb_info and igdb_info.get("rating"):
        lines.append(f"📊 IGDB: <b>{igdb_info['rating']}/100</b>")

    comment = generate_comment(deal, rating)
    lines.append(f"\n📝 <i>{esc(comment)}</i>")

    hashtags = genres_to_hashtags(deal.genres)
    if hashtags:
        lines.append(f"\n{hashtags}")

    text = "\n".join(lines)

    # Клавиатура: кнопка магазина + голосование
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🛒 Открыть в {deal.store}", url=deal.link)],
        [
            InlineKeyboardButton(text="🔥 0", callback_data=f"vote:fire:{deal.deal_id}"),
            InlineKeyboardButton(text="💩 0", callback_data=f"vote:poop:{deal.deal_id}"),
        ],
    ])

    # Коллаж: IGDB скриншоты + обложка
    photo = None
    collage_bytes = None

    if igdb_info:
        urls = []
        if igdb_info.get("cover_url"):
            urls.append(igdb_info["cover_url"])
        urls.extend(igdb_info.get("screenshots", [])[:3])
        if deal.image_url:
            urls.append(deal.image_url)

        if len(urls) >= 2:
            collage_bytes = await make_collage(urls[:4])

        if not collage_bytes and igdb_info.get("cover_url"):
            photo = igdb_info["cover_url"]

    if not photo and not collage_bytes:
        photo = deal.image_url

    try:
        if collage_bytes:
            from aiogram.types import BufferedInputFile
            file = BufferedInputFile(collage_bytes, filename="collage.png")
            await send_with_retry(lambda: bot.send_photo(CHANNEL_ID, photo=file, caption=text, reply_markup=keyboard))
        elif photo:
            await send_with_retry(lambda: bot.send_photo(CHANNEL_ID, photo=photo, caption=text, reply_markup=keyboard))
        else:
            await send_with_retry(lambda: bot.send_message(CHANNEL_ID, text, reply_markup=keyboard, disable_web_page_preview=True))

        log.info(f"Опубликовано: {deal.title}")
        return True
    except Exception as e:
        log.error(f"Ошибка при отправке {deal.title}: {e}")
        return False


# ─── Уведомления по вишлисту ─────────────────────────────────────────────────

async def notify_wishlist_users(deal):
    user_ids = await get_wishlist_matches(deal.title)
    if not user_ids:
        return

    store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁"}.get(deal.store, "🕹")
    if deal.is_free:
        price_line = "🆓 <b>БЕСПЛАТНО</b>"
    else:
        price_line = f"❌ <s>{esc(deal.old_price)}</s> ✅ <b>{esc(deal.new_price)}</b> <code>-{deal.discount}%</code>"

    text = (
        f"🔔 <b>Скидка на игру из твоего вишлиста!</b>\n\n"
        f"{store_emoji} <b>{esc(deal.title)}</b>\n"
        f"{price_line}\n\n"
        f"<a href='{deal.link}'>Открыть в {esc(deal.store)}</a>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🛒 {deal.store}", url=deal.link)]
    ])

    for user_id in user_ids:
        try:
            await bot.send_message(user_id, text, reply_markup=keyboard)
            log.info(f"Wishlist уведомление отправлено user_id={user_id} для '{deal.title}'")
        except Exception as e:
            log.warning(f"Не удалось отправить уведомление user_id={user_id}: {e}")
        await asyncio.sleep(0.1)


# ─── Еженедельный дайджест ───────────────────────────────────────────────────

async def post_weekly_digest():
    top_discount = await get_weekly_top(limit=10)
    top_voted = await get_top_voted(limit=5)

    if not top_discount:
        log.info("Еженедельный дайджест: нет данных за неделю.")
        return

    now = datetime.now(MSK).strftime("%d.%m.%Y")
    store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁", "CheapShark": "💰"}

    lines = [
        f"📅 <b>ЛУЧШИЕ СКИДКИ НЕДЕЛИ — {now}</b>",
        "",
        "🏷 <b>Топ по скидке:</b>",
    ]

    for i, row in enumerate(top_discount, 1):
        emoji = store_emoji.get(row["store"], "🕹")
        link = ""
        if row["store"] == "Steam" and row["deal_id"].startswith("steam_"):
            appid = row["deal_id"].replace("steam_", "")
            link = f"https://store.steampowered.com/app/{appid}/"
        title_part = f"<a href='{link}'>{esc(row['title'])}</a>" if link else esc(row["title"])
        lines.append(f"{i}. {emoji} {title_part} — <code>-{row['discount']}%</code>")

    # Топ по голосам 🔥
    if top_voted:
        lines += ["", "🔥 <b>Топ по голосам подписчиков:</b>"]
        for i, row in enumerate(top_voted, 1):
            emoji = store_emoji.get(row["store"], "🕹")
            lines.append(f"{i}. {emoji} {esc(row['title'])} — {row['fire_count']} 🔥")

    lines += ["", "━" * 20, "👾 Следи за каналом — новые скидки каждый день!"]

    try:
        await bot.send_message(CHANNEL_ID, "\n".join(lines), disable_web_page_preview=True)
        log.info("Еженедельный дайджест опубликован.")
    except Exception as e:
        log.error(f"Ошибка при отправке дайджеста: {e}")


# ─── Авто-тест парсеров ──────────────────────────────────────────────────────

async def run_parser_tests():
    """Каждое утро в 8:00 проверяет все парсеры и шлёт отчёт админу."""
    if not ADMIN_ID:
        return

    results = []
    for fetcher, name in [
        (get_steam_deals, "Steam"),
        (get_gog_deals, "GOG"),
        (get_epic_deals, "Epic Games"),
    ]:
        try:
            deals = await fetcher(min_discount=MIN_DISCOUNT_PERCENT)
            count = len(deals)
            status = "✅" if count > 0 else "⚠️"
            results.append(f"{status} {name}: {count} скидок")
        except Exception as e:
            results.append(f"❌ {name}: {e}")

    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    text = f"🤖 <b>Авто-тест парсеров — {now}</b>\n\n" + "\n".join(results)
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception as e:
        log.error(f"Не удалось отправить отчёт админу: {e}")


# ─── Скрытые жемчужины ───────────────────────────────────────────────────────

async def post_hidden_gems():
    """Раз в день публикует 1-2 малоизвестные инди-игры с высоким рейтингом."""
    gems = await find_hidden_gems(min_discount=70, min_score=85, max_reviews=500, limit=2)
    if not gems:
        log.info("Скрытые жемчужины: ничего не найдено.")
        return

    for gem in gems:
        if await is_already_posted(f"gem_{gem.appid}"):
            continue

        score_bar = "⭐" * (gem.score // 20)  # до 5 звёзд
        text = (
            f"💎 <b>СКРЫТАЯ ЖЕМЧУЖИНА</b>\n\n"
            f"🎮 <b>{esc(gem.title)}</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"💸 <s>{esc(gem.old_price)}</s>  ➔  ✅ <b>{esc(gem.new_price)}</b>\n"
            f"🏷 Скидка: <b>−{gem.discount}%</b>\n\n"
            f"📊 Рейтинг: <b>{gem.score}%</b> {score_bar}\n"
            f"💬 Отзывов: <b>{gem.reviews}</b> (малоизвестная)\n\n"
            f"<i>Маленькая игра, которую почти никто не заметил — но она того стоит.</i>\n\n"
            f"#инди #скрытаяжемчужина #скидки"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Открыть в Steam", url=gem.link)],
            [
                InlineKeyboardButton(text="🔥 0", callback_data=f"vote:fire:gem_{gem.appid}"),
                InlineKeyboardButton(text="💩 0", callback_data=f"vote:poop:gem_{gem.appid}"),
            ],
        ])

        try:
            await send_with_retry(lambda: bot.send_photo(
                CHANNEL_ID, photo=gem.image_url, caption=text, reply_markup=keyboard
            ))
            await mark_as_posted(f"gem_{gem.appid}", gem.title, "Steam", gem.discount)
            log.info(f"Скрытая жемчужина опубликована: {gem.title}")
        except Exception as e:
            log.error(f"Ошибка публикации жемчужины {gem.title}: {e}")

        await asyncio.sleep(2)


# ─── Основной сбор и публикация ──────────────────────────────────────────────

async def check_and_post():
    log.info("Запуск сбора скидок...")
    all_deals = []
    errors = []

    for fetcher, name in [
        (get_steam_deals, "Steam"),
        (get_gog_deals, "GOG"),
        (get_epic_deals, "Epic Games"),
    ]:
        try:
            deals = await fetcher(min_discount=MIN_DISCOUNT_PERCENT)
            log.info(f"{name}: найдено {len(deals)} скидок")
            if not deals:
                errors.append(f"{name}: вернул 0 результатов")
            all_deals.extend(deals)
        except Exception as e:
            log.error(f"Ошибка при парсинге {name}: {e}")
            errors.append(f"{name}: {e}")

    if errors:
        await notify_admin("\n".join(errors))

    filtered = []
    for deal in all_deals:
        if await is_already_posted(deal.deal_id):
            continue
        if deal.store == "Steam" and MIN_STEAM_RATING > 0:
            appid = deal.deal_id.replace("steam_", "")
            rating = await get_steam_rating(appid)
            if rating and rating["score"] < MIN_STEAM_RATING:
                log.info(f"Пропущено (рейтинг {rating['score']}%): {deal.title}")
                continue
        filtered.append(deal)

    if not filtered:
        log.info("Нет новых скидок для публикации.")
        return

    filtered = deduplicate(filtered)

    free = [d for d in filtered if d.is_free]
    paid = [d for d in filtered if not d.is_free]

    _, _, theme_genres = get_daily_theme()
    paid.sort(key=lambda d: (theme_score(d, theme_genres), d.discount), reverse=True)

    combined = free + paid
    if not combined:
        log.info("Нет новых скидок для публикации.")
        return

    deal = combined[0]
    if await publish_single(deal):
        await mark_as_posted(deal.deal_id, deal.title, deal.store, deal.discount)
        await notify_wishlist_users(deal)
        # Мини-игра только для платных игр
        if not deal.is_free:
            await send_price_game(deal)

    deleted = await cleanup_old_records()
    if deleted:
        log.info(f"БД: удалено {deleted} старых записей")


async def notify_admin(text: str):
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, f"⚠️ <b>GameDealsBot</b>\n\n{esc(text)}")
        except Exception:
            pass


# ─── Запуск ──────────────────────────────────────────────────────────────────

async def main():
    await init_db()

    scheduler = AsyncIOScheduler(timezone=MSK)

    # Авто-тест парсеров каждое утро в 8:00
    scheduler.add_job(
        run_parser_tests,
        CronTrigger(hour=8, minute=0, timezone=MSK),
        name="parser_tests",
    )
    log.info("Авто-тест парсеров: каждый день в 08:00 МСК")

    # Скрытые жемчужины — каждый день в 14:00 МСК
    scheduler.add_job(
        post_hidden_gems,
        CronTrigger(hour=14, minute=0, timezone=MSK),
        name="hidden_gems",
    )
    log.info("Скрытые жемчужины: каждый день в 14:00 МСК")

    for hour, minute in POST_TIMES:
        scheduler.add_job(
            check_and_post,
            CronTrigger(hour=hour, minute=minute, timezone=MSK),
            name=f"post_{hour:02d}:{minute:02d}",
        )
        log.info(f"Запланирована публикация в {hour:02d}:{minute:02d} МСК")

    # Еженедельный дайджест — каждое воскресенье в 12:00 МСК
    scheduler.add_job(
        post_weekly_digest,
        CronTrigger(day_of_week="sun", hour=12, minute=0, timezone=MSK),
        name="weekly_digest",
    )
    log.info("Еженедельный дайджест: каждое воскресенье в 12:00 МСК")

    scheduler.start()
    log.info("Бот запущен.")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

