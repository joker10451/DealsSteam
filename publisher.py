"""
Публикация сделок в Telegram-канал и уведомления пользователей.
"""
import asyncio
import logging
import re
from datetime import datetime
from html import escape
from typing import Optional

import pytz
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import CHANNEL_ID, ADMIN_ID
from database import (
    get_wishlist_matches, save_price_game,
    increment_metric, wishlist_remove_user,
)
from enricher import get_steam_rating, get_historical_low, generate_comment, genres_to_hashtags
from igdb import get_game_info
from collage import make_collage
from currency import to_rubles, format_rub

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")

# Импортируется из bot.py после инициализации
_bot = None


def set_bot(bot_instance):
    global _bot
    _bot = bot_instance


def get_bot():
    return _bot


def esc(text: str) -> str:
    return escape(str(text))


def _cb_id(deal_id: str) -> str:
    """Обрезает deal_id до 50 символов для callback_data (лимит Telegram 64 байта)."""
    return deal_id[:50]


DAILY_THEMES = {
    0: ("⚔️", "RPG-понедельник",  ["RPG", "Ролевые"]),
    1: ("💥", "Экшен-вторник",    ["Экшен", "Action", "Шутер"]),
    2: ("🧠", "Стратегия-среда",  ["Стратегия", "Strategy"]),
    3: ("🎲", "Инди-четверг",     ["Инди", "Indie"]),
    4: ("👻", "Хоррор-пятница",   ["Хоррор", "Horror"]),
    5: ("🏎️", "Выходные-скидки", []),
    6: ("🏆", "Воскресный топ",   []),
}


def get_daily_theme() -> tuple[str, str, list[str]]:
    return DAILY_THEMES[datetime.now(MSK).weekday()]


async def send_with_retry(coro_fn, retries: int = 3, delay: float = 5.0):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn()
        except TelegramForbiddenError:
            raise
        except TelegramRetryAfter as e:
            log.warning(f"Flood control от Telegram, ждём {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            last_exc = e
        except Exception as e:
            last_exc = e
            log.warning(f"Попытка {attempt}/{retries} не удалась: {e}")
            if attempt < retries:
                await asyncio.sleep(delay * attempt)
    raise last_exc


async def _localize_price(price_str: str) -> str:
    if not price_str or not price_str.startswith("~"):
        return price_str
    match = re.match(r"~([\d.]+)\s+([A-Z]+)", price_str)
    if not match:
        return price_str
    amount, currency = float(match.group(1)), match.group(2)
    rub = await to_rubles(amount, currency)
    return format_rub(rub) if rub else price_str.lstrip("~")


async def publish_single(deal, prefetched_rating: Optional[dict] = None) -> bool:
    now = datetime.now(MSK).strftime("%d.%m.%Y")
    store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁"}.get(deal.store, "🕹")

    rating = prefetched_rating
    historical_low = None
    igdb_info = None

    if deal.store == "Steam" and deal.deal_id.startswith("steam_"):
        appid = deal.deal_id.replace("steam_", "")
        if rating is None:
            rating, historical_low, igdb_info = await asyncio.gather(
                get_steam_rating(appid),
                get_historical_low(appid),
                get_game_info(deal.title),
            )
        else:
            historical_low, igdb_info = await asyncio.gather(
                get_historical_low(appid),
                get_game_info(deal.title),
            )
    else:
        igdb_info = await get_game_info(deal.title)

    is_historic = bool(historical_low and deal.discount >= 70)
    theme_emoji, theme_name, _ = get_daily_theme()

    old_price = await _localize_price(deal.old_price)
    new_price = await _localize_price(deal.new_price)

    lines = []
    adult_prefix = "🔞 " if (igdb_info and igdb_info.get("is_adult")) else ""
    
    # Заголовок с улучшенным форматированием
    if deal.is_free:
        lines.append(f"🎁 <b>{adult_prefix}БЕСПЛАТНО · {now}</b>")
    elif is_historic:
        lines.append(f"⚡️ <b>{adult_prefix}ИСТОРИЧЕСКИЙ МИНИМУМ · {now}</b>")
    elif deal.discount >= 80:
        lines.append(f"🔥 <b>{adult_prefix}ОГОНЬ-СКИДКА · {now}</b>")
    else:
        lines.append(f"{theme_emoji} <b>{adult_prefix}{theme_name.upper()} · {now}</b>")

    # Название игры с пояснением для бандлов
    title_line = f"{store_emoji} <b>{esc(deal.title)}</b>"
    if "bundle" in deal.title.lower():
        title_line += " 📦"
    lines.append(f"\n{title_line}")

    # Цена с улучшенным форматированием
    if deal.is_free:
        lines.append("💸 <s>Платная</s>  →  🆓 <b>БЕСПЛАТНО</b>")
    else:
        discount_emoji = "🔥" if deal.discount >= 80 else "💰"
        lines.append(f"{discount_emoji} <s>{esc(old_price)}</s>  →  ✅ <b>{esc(new_price)}</b>")
        lines.append(f"🏷 Скидка: <b>−{deal.discount}%</b>")

    if getattr(deal, "sale_end", None):
        lines.append(f"⏳ До: <b>{deal.sale_end}</b>")

    # Рейтинг с улучшенным форматированием
    if rating:
        score = rating['score']
        score_emoji = "🏆" if score >= 95 else "👍" if score >= 80 else "🙂" if score >= 70 else "😐"
        score_line = f"{score_emoji} Steam: <b>{score}%</b>"
        if rating.get("label"):
            score_line += f"  ·  {esc(rating['label'])}"
        if rating.get("total"):
            reviews_count = f"{rating['total']:,}".replace(",", " ")
            score_line += f"  ·  {reviews_count} отзывов"
        lines.append(score_line)
    elif igdb_info and igdb_info.get("rating"):
        igdb_rating = igdb_info['rating']
        rating_emoji = "🏆" if igdb_rating >= 90 else "⭐️"
        lines.append(f"{rating_emoji} IGDB: <b>{igdb_rating}/100</b>")

    if historical_low and historical_low.get("price"):
        low_rub = await to_rubles(float(historical_low["price"]), "USD")
        if low_rub:
            lines.append(f"📉 Истор. минимум: <b>{format_rub(low_rub)}</b>")

    # Описание игры
    if igdb_info and igdb_info.get("description"):
        lines.append(f"\n📖 <i>{esc(igdb_info['description'])}</i>")

    # Комментарий бота
    comment = generate_comment(deal, rating)
    lines.append(f"\n💬 <i>{esc(comment)}</i>")

    # Хештеги
    hashtags = genres_to_hashtags(deal.genres)
    if hashtags:
        lines.append(f"\n{hashtags}")

    # Похожие игры
    if igdb_info and igdb_info.get("similar_games"):
        similar = ", ".join(igdb_info["similar_games"][:3])  # Только 3 игры
        lines.append(f"\n🔗 Похожие: <i>{esc(similar)}</i>")

    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🛒 Открыть в {deal.store}", url=deal.link)],
        [
            InlineKeyboardButton(text="🔥 0", callback_data=f"vote:fire:{_cb_id(deal.deal_id)}"),
            InlineKeyboardButton(text="💩 0", callback_data=f"vote:poop:{_cb_id(deal.deal_id)}"),
            InlineKeyboardButton(text="➕ Вишлист", callback_data=f"wl_add:{deal.title[:40]}"),
        ],
    ])

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
            await send_with_retry(lambda: get_bot().send_photo(CHANNEL_ID, photo=file, caption=text, reply_markup=keyboard))
        elif photo:
            await send_with_retry(lambda: get_bot().send_photo(CHANNEL_ID, photo=photo, caption=text, reply_markup=keyboard))
        else:
            await send_with_retry(lambda: get_bot().send_message(CHANNEL_ID, text, reply_markup=keyboard, disable_web_page_preview=True))

        log.info(f"Опубликовано: {deal.title}")
        await increment_metric("published")
        
        # Случайно публикуем игру со скриншотом (20% шанс)
        import random
        if random.random() < 0.2 and igdb_info and igdb_info.get("screenshots"):
            await asyncio.sleep(30)  # Подождём 30 секунд
            await publish_screenshot_game(deal, igdb_info)
        
        return True
    except Exception as e:
        log.error(f"Ошибка при отправке {deal.title}: {e}")
        await increment_metric("publish_error")
        return False


async def notify_users(user_ids: list[int], deal, header: str):
    """Отправляет уведомление о скидке списку пользователей."""
    store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁"}.get(deal.store, "🕹")
    price_line = "🆓 <b>БЕСПЛАТНО</b>" if deal.is_free else (
        f"<s>{esc(deal.old_price)}</s> → <b>{esc(deal.new_price)}</b> <code>-{deal.discount}%</code>"
    )
    text = (
        f"{header}\n\n"
        f"{store_emoji} <b>{esc(deal.title)}</b>\n"
        f"🏪 {esc(deal.store)}\n"
        f"{price_line}\n\n"
        f"<a href='{deal.link}'>Открыть в {esc(deal.store)}</a>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🛒 {deal.store}", url=deal.link)]
    ])

    for user_id in user_ids:
        try:
            await get_bot().send_message(user_id, text, reply_markup=keyboard)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await get_bot().send_message(user_id, text, reply_markup=keyboard)
            except Exception as retry_err:
                log.warning(f"Повторная попытка не удалась user_id={user_id}: {retry_err}")
        except TelegramForbiddenError:
            log.info(f"Пользователь заблокировал бота user_id={user_id}, удаляем")
            await wishlist_remove_user(user_id)
        except Exception as e:
            log.warning(f"Не удалось отправить уведомление user_id={user_id}: {e}")
        await asyncio.sleep(0.05)


async def notify_wishlist_users(deal):
    """
    Notifies users who have the game in their wishlist.
    Filters out users who own the game in their Steam library.
    
    Requirements: 2.7
    """
    from database import steam_library_contains
    
    user_ids = await get_wishlist_matches(deal.title)
    if not user_ids:
        return
    
    # Filter out users who own the game in their Steam library
    # Only check for Steam games
    if deal.store == "Steam" and deal.deal_id.startswith("steam_"):
        try:
            appid = int(deal.deal_id.replace("steam_", ""))
            filtered_user_ids = []
            
            for user_id in user_ids:
                # Check if user owns this game
                owns_game = await steam_library_contains(user_id, appid)
                
                if owns_game:
                    log.info(
                        f"Skipping wishlist notification for user {user_id}: "
                        f"already owns {deal.title} (appid {appid})"
                    )
                else:
                    filtered_user_ids.append(user_id)
            
            user_ids = filtered_user_ids
        
        except (ValueError, TypeError) as e:
            log.warning(f"Failed to parse Steam appid from {deal.deal_id}: {e}")
            # Continue with original user_ids if parsing fails
    
    if not user_ids:
        log.info(f"No users to notify for {deal.title} (all own the game)")
        return
    
    await notify_users(user_ids, deal, "🔔 <b>Скидка на игру из твоего вишлиста!</b>")
    await increment_metric("wishlist_notify", len(user_ids))


async def send_price_game(deal) -> None:
    import random
    try:
        old_price_str = str(deal.old_price).replace("₽", "").replace(" ", "").replace(",", "").strip()
        correct = int(float(old_price_str))
    except (ValueError, AttributeError):
        return

    if correct <= 0:
        return

    variants: set[int] = {correct}
    while len(variants) < 4:
        delta = random.randint(10, 40)
        sign = random.choice([-1, 1])
        fake = round(correct * (1 + sign * delta / 100) / 10) * 10
        if fake > 0 and fake != correct:
            variants.add(fake)

    options = sorted(list(variants))
    random.shuffle(options)
    await save_price_game(deal.deal_id, correct)

    buttons = [
        InlineKeyboardButton(text=f"{p}₽", callback_data=f"pg:{_cb_id(deal.deal_id)}:{p}")
        for p in options
    ]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

    text = (
        f"🎮 <b>Мини-игра: угадай цену!</b>\n\n"
        f"Сколько стоила <b>{esc(deal.title)}</b> до скидки?\n"
        f"Выбери правильный ответ 👇"
    )
    try:
        await get_bot().send_message(CHANNEL_ID, text, reply_markup=keyboard)
    except Exception as e:
        log.warning(f"Мини-игра не отправлена: {e}")


async def notify_admin(text: str):
    if ADMIN_ID and get_bot():
        try:
            await get_bot().send_message(ADMIN_ID, f"⚠️ <b>GameDealsBot</b>\n\n{esc(text)}")
        except Exception:
            pass


async def publish_screenshot_game(deal, igdb_info):
    """Опубликовать мини-игру 'Угадай игру по скриншоту'."""
    from minigames import create_screenshot_game
    
    game_data = await create_screenshot_game(deal)
    if not game_data:
        return
    
    screenshot_url = game_data["screenshot_url"]
    options = game_data["options"]
    game_id = game_data["game_id"]
    
    # Создаём кнопки с вариантами ответов
    buttons = [
        InlineKeyboardButton(
            text=option,
            callback_data=f"screenshot:{game_id}:{option}"
        )
        for option in options
    ]
    
    # Размещаем кнопки по 2 в ряд
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    
    caption = (
        f"🎮 <b>Мини-игра: Угадай игру!</b>\n\n"
        f"Что это за игра? 🤔\n"
        f"Выбери правильный ответ 👇\n\n"
        f"Награда: <b>+10 баллов</b> ⭐️"
    )
    
    try:
        await get_bot().send_photo(
            CHANNEL_ID,
            photo=screenshot_url,
            caption=caption,
            reply_markup=keyboard
        )
        log.info(f"Опубликована игра со скриншотом: {deal.title}")
    except Exception as e:
        log.warning(f"Игра со скриншотом не отправлена: {e}")
