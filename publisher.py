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
    store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁", "CheapShark": "💰"}.get(deal.store, "🕹")

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
        lines.append(f"💸 <s>{esc(old_price)}</s>  →  ✅ <b>{esc(new_price)}</b>  <b>−{deal.discount}%</b>")

    if getattr(deal, "sale_end", None):
        lines.append(f"⏳ До: <b>{deal.sale_end}</b>")

    if rating:
        score_line = f"⭐️ Steam: <b>{rating['score']}%</b>"
        if rating.get("label"):
            score_line += f"  ·  {esc(rating['label'])}"
        lines.append(score_line)
    elif igdb_info and igdb_info.get("rating"):
        lines.append(f"⭐️ IGDB: <b>{igdb_info['rating']}/100</b>")

    comment = generate_comment(deal, rating)
    lines.append(f"\n📝 <i>{esc(comment)}</i>")

    hashtags = genres_to_hashtags(deal.genres)
    if hashtags:
        lines.append(f"\n{hashtags}")

    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🛒 Открыть в {deal.store}", url=deal.link)],
        [
            InlineKeyboardButton(text="🔥 0", callback_data=f"vote:fire:{deal.deal_id}"),
            InlineKeyboardButton(text="💩 0", callback_data=f"vote:poop:{deal.deal_id}"),
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
        return True
    except Exception as e:
        log.error(f"Ошибка при отправке {deal.title}: {e}")
        await increment_metric("publish_error")
        return False


async def notify_wishlist_users(deal):
    user_ids = await get_wishlist_matches(deal.title)
    if not user_ids:
        return

    store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁"}.get(deal.store, "🕹")
    price_line = "🆓 <b>БЕСПЛАТНО</b>" if deal.is_free else (
        f"❌ <s>{esc(deal.old_price)}</s> ✅ <b>{esc(deal.new_price)}</b> <code>-{deal.discount}%</code>"
    )
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
            await get_bot().send_message(user_id, text, reply_markup=keyboard)
            log.info(f"Wishlist уведомление отправлено user_id={user_id} для '{deal.title}'")
            await increment_metric("wishlist_notify")
        except TelegramRetryAfter as e:
            log.warning(f"Flood control, ждём {e.retry_after}s для user_id={user_id}")
            await asyncio.sleep(e.retry_after)
            try:
                await get_bot().send_message(user_id, text, reply_markup=keyboard)
                await increment_metric("wishlist_notify")
            except Exception as retry_err:
                log.warning(f"Повторная попытка не удалась user_id={user_id}: {retry_err}")
        except TelegramForbiddenError:
            log.info(f"Пользователь заблокировал бота user_id={user_id}, удаляем из вишлиста")
            await wishlist_remove_user(user_id)
        except Exception as e:
            log.warning(f"Не удалось отправить уведомление user_id={user_id}: {e}")
        await asyncio.sleep(0.05)


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
        InlineKeyboardButton(text=f"{p}₽", callback_data=f"pg:{deal.deal_id}:{p}")
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
