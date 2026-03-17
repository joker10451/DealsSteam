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
    notif_settings_get, notif_queue_add,
    engagement_impression, engagement_event,
)
from enricher import get_steam_rating, get_historical_low, get_steam_description, generate_comment, genres_to_hashtags
from igdb import get_game_info
from collage import make_collage
from currency import to_rubles, format_rub
from price_glitch import check_for_glitch, format_glitch_alert

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


def _utm_link(url: str, store: str) -> str:
    """Добавляет UTM-параметры для отслеживания кликов из бота."""
    if not url:
        return url
    sep = "&" if "?" in url else "?"
    source = store.lower().replace(" ", "_")
    return f"{url}{sep}utm_source=gamedealsbot&utm_medium=telegram&utm_campaign={source}"


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


async def publish_single(deal, prefetched_rating: Optional[dict] = None, is_priority: bool = False) -> tuple[bool, Optional[dict]]:
    """
    Публикует сделку в канал.
    
    Returns:
        (True, historical_low) если публикация успешна, (False, None) при ошибке
    """
    now = datetime.now(MSK).strftime("%d.%m.%Y")
    store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁"}.get(deal.store, "🕹")

    # Проверяем на ошибку цены
    glitch_info = await check_for_glitch(deal)
    
    rating = prefetched_rating
    historical_low = None
    igdb_info = None

    if deal.store == "Steam" and deal.deal_id.startswith("steam_"):
        appid = deal.deal_id.replace("steam_", "")
        if rating is None:
            rating, historical_low, igdb_info, steam_desc = await asyncio.gather(
                get_steam_rating(appid),
                get_historical_low(appid),
                get_game_info(deal.title),
                get_steam_description(appid),
            )
        else:
            historical_low, igdb_info, steam_desc = await asyncio.gather(
                get_historical_low(appid),
                get_game_info(deal.title),
                get_steam_description(appid),
            )
    else:
        igdb_info = await get_game_info(deal.title)
        steam_desc = None

    # Используем флаг is_current_low из ITAD если доступен, иначе fallback по скидке
    is_current_low = bool(historical_low and historical_low.get("is_current_low"))
    is_historic = is_current_low or bool(historical_low and deal.discount >= 70)
    theme_emoji, theme_name, _ = get_daily_theme()

    old_price = await _localize_price(deal.old_price)
    new_price = await _localize_price(deal.new_price)

    lines = []
    adult_prefix = "🔞 " if (igdb_info and igdb_info.get("is_adult")) else ""
    
    # Заголовок: тема дня всегда показывается, поверх неё — статус скидки
    if glitch_info and glitch_info.get('severity') == 'critical':
        lines.append(f"🚨 <b>{adult_prefix}ОШИБКА ЦЕНЫ? СРОЧНО! · {now}</b>")
    elif glitch_info and glitch_info.get('severity') == 'high':
        lines.append(f"🔥 <b>{adult_prefix}АНОМАЛЬНАЯ СКИДКА! · {now}</b>")
    elif deal.is_free:
        lines.append(f"🎁 <b>{adult_prefix}БЕСПЛАТНО · {now}</b>")
    elif is_historic:
        lines.append(f"{theme_emoji} <b>{adult_prefix}{theme_name.upper()} · {now}</b>")
        if is_current_low:
            lines.append(f"📉 <i>Исторический минимум цены!</i>")
        else:
            lines.append(f"⚡️ <i>Близко к историческому минимуму!</i>")
    elif deal.discount >= 80:
        lines.append(f"{theme_emoji} <b>{adult_prefix}{theme_name.upper()} · {now}</b>")
        lines.append(f"🔥 <i>Огонь-скидка −{deal.discount}%!</i>")
    else:
        lines.append(f"{theme_emoji} <b>{adult_prefix}{theme_name.upper()} · {now}</b>")

    # Название игры с пояснением для бандлов
    title_line = f"{store_emoji} <b>{esc(deal.title)}</b>"
    if "bundle" in deal.title.lower():
        title_line += " 📦"
    lines.append(f"\n{title_line}")
    lines.append(f"🏪 Площадка: <b>{esc(deal.store)}</b>")

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
            low_label = "📉 <b>Исторический минимум!</b>" if historical_low.get("is_current_low") else "📉 Истор. минимум:"
            lines.append(f"{low_label} <b>{format_rub(low_rub)}</b>")
    
    # Предупреждение об ошибке цены (если обнаружена)
    if glitch_info:
        glitch_alert = format_glitch_alert(deal, glitch_info)
        lines.append(f"\n{glitch_alert}")

    # Описание игры: Steam (русское) → IGDB (с переводом) → ничего
    description = steam_desc or (igdb_info.get("description") if igdb_info else None)
    if description:
        lines.append(f"\n📖 <i>{esc(description)}</i>")

    # Комментарий бота (с контекстом для старых/неизвестных игр)
    from smart_filter import generate_context_comment
    comment = generate_context_comment(deal, rating, igdb_info)
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

    # Для платных игр — сохраняем цену и добавляем кнопку мини-игры
    price_game_button = None
    if not deal.is_free:
        try:
            old_price_str = str(deal.old_price).replace("₽", "").replace(" ", "").replace(",", "").strip()
            correct = int(float(old_price_str))
            if correct > 0:
                await save_price_game(
                    deal.deal_id, correct,
                    title=deal.title,
                    new_price=str(deal.new_price),
                    link=deal.link,
                    discount=deal.discount,
                )
                price_game_button = InlineKeyboardButton(
                    text="🎲 Угадай цену — заработай баллы!",
                    callback_data=f"pg_start:{_cb_id(deal.deal_id)}"
                )
        except (ValueError, AttributeError):
            pass

    vote_row = [
        InlineKeyboardButton(text="🔥 0", callback_data=f"vote:fire:{_cb_id(deal.deal_id)}"),
        InlineKeyboardButton(text="💩 0", callback_data=f"vote:poop:{_cb_id(deal.deal_id)}"),
        InlineKeyboardButton(text="➕ Вишлист", callback_data=f"wl_add:{deal.title[:40]}"),
    ]
    rows = [
        [InlineKeyboardButton(text=f"🛒 Открыть в {deal.store}", url=_utm_link(deal.link, deal.store))],
        vote_row,
    ]
    if price_game_button:
        rows.append([price_game_button])

    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

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

    # Fallback-цепочка: IGDB → deal.image_url (Steam CDN) → дефолтная картинка
    if not photo and not collage_bytes:
        photo = deal.image_url
    if not photo and not collage_bytes:
        # Steam CDN fallback по appid
        if deal.deal_id.startswith("steam_"):
            appid = deal.deal_id.replace("steam_", "")
            photo = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"
        else:
            # Универсальный дефолт — нейтральная картинка-заглушка
            photo = "https://store.steampowered.com/public/shared/images/header/globalheader_logo.png"

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
        await engagement_impression(deal.deal_id, deal.title, deal.store, deal.discount)
        
        # Случайно публикуем игру со скриншотом (20% шанс)
        import random
        if random.random() < 0.2 and igdb_info and igdb_info.get("screenshots"):
            await asyncio.sleep(30)
            await publish_screenshot_game(deal, igdb_info)

        # Контекстный совет (30% шанс при совпадении темы)
        from tips import get_contextual_tip
        ctx_tip = await get_contextual_tip(deal)
        if ctx_tip:
            await asyncio.sleep(5)
            try:
                await send_with_retry(lambda: get_bot().send_message(
                    CHANNEL_ID, ctx_tip, disable_web_page_preview=True
                ))
                log.info(f"Контекстный совет опубликован для: {deal.title}")
            except Exception as e:
                log.warning(f"Не удалось отправить контекстный совет: {e}")

        return (True, historical_low)
    except Exception as e:
        log.error(f"Ошибка при отправке {deal.title}: {e}")
        await increment_metric("publish_error")
        return (False, None)


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


async def notify_wishlist_users(deal, historical_low: Optional[dict] = None):
    """
    Notifies users who have the game in their wishlist.
    Respects per-user notification settings: min discount, quiet hours, grouping.
    Filters out users who own the game in their Steam library.
    historical_low: передаётся из publish_single чтобы не делать повторный запрос к ITAD.
    """
    from database import steam_library_contains

    user_ids = await get_wishlist_matches(deal.title)
    if not user_ids:
        return

    # Filter out users who own the game in their Steam library
    if deal.store == "Steam" and deal.deal_id.startswith("steam_"):
        try:
            appid = int(deal.deal_id.replace("steam_", ""))
            filtered = []
            for uid in user_ids:
                if not await steam_library_contains(uid, appid):
                    filtered.append(uid)
                else:
                    log.info(f"Skipping wishlist notify user {uid}: owns {deal.title}")
            user_ids = filtered
        except (ValueError, TypeError) as e:
            log.warning(f"Failed to parse Steam appid from {deal.deal_id}: {e}")

    if not user_ids:
        return

    now_msk = datetime.now(MSK)
    current_hour = now_msk.hour
    send_now = []
    queued = 0

    for uid in user_ids:
        settings = await notif_settings_get(uid)

        # Фильтр по магазинам
        deal_store_lower = deal.store.lower()
        preferred = [s.lower() for s in settings["preferred_stores"]]
        if preferred and deal_store_lower not in preferred:
            log.info(f"Skipping notify user {uid}: store '{deal.store}' not in preferred {preferred}")
            continue

        # Фильтр по минимальной скидке
        if not deal.is_free and deal.discount < settings["min_discount"]:
            log.info(f"Skipping notify user {uid}: discount {deal.discount}% < min {settings['min_discount']}%")
            continue

        # Фильтр по жанрам (чёрный список)
        ignored = [g.lower() for g in settings["ignored_genres"]]
        if ignored and deal.genres:
            deal_genres_lower = [g.lower() for g in deal.genres]
            if any(g in ignored for g in deal_genres_lower):
                log.info(f"Skipping notify user {uid}: genre match in ignored list")
                continue

        # Тихие часы
        qs, qe = settings["quiet_start"], settings["quiet_end"]
        in_quiet = (
            (qs > qe and (current_hour >= qs or current_hour < qe)) or
            (qs <= qe and qs <= current_hour < qe)
        )

        if in_quiet or settings["grouping_enabled"]:
            await notif_queue_add(uid, deal)
            queued += 1
        else:
            send_now.append(uid)

    if send_now:
        # Используем переданный historical_low, иначе запрашиваем (fallback для прямых вызовов)
        hist_low = historical_low
        if hist_low is None and deal.store == "Steam" and deal.deal_id.startswith("steam_"):
            appid = deal.deal_id.replace("steam_", "")
            from enricher import get_historical_low
            hist_low = await get_historical_low(appid)
        
        if hist_low and hist_low.get("is_current_low"):
            header = "🔔 <b>Скидка на игру из вишлиста!</b>\n📉 <i>Сейчас — исторический минимум цены!</i>"
        else:
            header = "🔔 <b>Скидка на игру из твоего вишлиста!</b>"
        
        await notify_users(send_now, deal, header)
        await increment_metric("wishlist_notify", len(send_now))

    if queued:
        log.info(f"Queued wishlist notification for {queued} users (quiet hours or grouping)")



async def notify_admin(text: str):
    if ADMIN_ID and get_bot():
        try:
            await get_bot().send_message(ADMIN_ID, f"⚠️ <b>GameDealsBot</b>\n\n{esc(text)}")
        except Exception:
            pass


async def notify_free_game_subscribers(deal):
    """
    Уведомляет подписчиков о бесплатных играх.
    
    Args:
        deal: Объект Deal с бесплатной игрой
    """
    from database import free_game_get_subscribers
    
    if not deal.is_free:
        return
    
    subscribers = await free_game_get_subscribers()
    if not subscribers:
        return
    
    await notify_users(
        subscribers,
        deal,
        "🎁 <b>Новая бесплатная игра!</b>"
    )
    await increment_metric("free_game_notify", len(subscribers))
    log.info(f"Уведомлено {len(subscribers)} подписчиков о бесплатной игре: {deal.title}")


async def publish_screenshot_game(deal, igdb_info):
    """Опубликовать мини-игру 'Угадай игру по скриншоту'."""
    from minigames import create_screenshot_game
    
    game_data = await create_screenshot_game(deal)
    if not game_data:
        return
    
    screenshot_url = game_data["screenshot_url"]
    options = game_data["options"]
    game_id = game_data["game_id"]
    
    # Создаём кнопки с вариантами ответов — используем индекс вместо текста
    # чтобы не превышать лимит callback_data в 64 байта
    # game_id обрезаем до 40 символов: "scr:" (4) + game_id (40) + ":0" (2) = 46 байт макс
    short_gid = game_id[:40]
    buttons = [
        InlineKeyboardButton(
            text=option,
            callback_data=f"scr:{short_gid}:{i}"
        )
        for i, option in enumerate(options)
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


async def flush_notification_queue():
    """
    Отправляет накопленные уведомления пользователям, у которых закончились тихие часы.
    Вызывается по расписанию каждый час.
    """
    from database import (
        notif_queue_get_users_with_pending,
        notif_queue_pop,
        notif_settings_get,
    )

    user_ids = await notif_queue_get_users_with_pending()
    if not user_ids:
        return

    now_msk = datetime.now(MSK)
    current_hour = now_msk.hour
    flushed_users = 0

    for uid in user_ids:
        settings = await notif_settings_get(uid)
        qs, qe = settings["quiet_start"], settings["quiet_end"]
        in_quiet = (
            (qs > qe and (current_hour >= qs or current_hour < qe)) or
            (qs <= qe and qs <= current_hour < qe)
        )
        # Если группировка включена — отправляем только вне тихих часов
        if in_quiet:
            continue

        deals_data = await notif_queue_pop(uid)
        if not deals_data:
            continue

        if len(deals_data) == 1:
            # Одна сделка — обычное уведомление
            d = deals_data[0]
            store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁"}.get(d["deal_store"], "🕹")
            price_line = "🆓 <b>БЕСПЛАТНО</b>" if d["deal_is_free"] else (
                f"<s>{esc(d['deal_old_price'])}</s> → <b>{esc(d['deal_new_price'])}</b> "
                f"<code>-{d['deal_discount']}%</code>"
            )
            text = (
                f"🔔 <b>Скидка на игру из твоего вишлиста!</b>\n\n"
                f"{store_emoji} <b>{esc(d['deal_title'])}</b>\n"
                f"🏪 {esc(d['deal_store'])}\n"
                f"{price_line}\n\n"
                f"<a href='{d['deal_link']}'>Открыть в {esc(d['deal_store'])}</a>"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"🛒 {d['deal_store']}", url=d["deal_link"])]
            ])
        else:
            # Несколько сделок — группируем в одно сообщение
            lines = [f"🔔 <b>Скидки на {len(deals_data)} игры из твоего вишлиста!</b>\n"]
            for d in deals_data:
                store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁"}.get(d["deal_store"], "🕹")
                price_part = "🆓 БЕСПЛАТНО" if d["deal_is_free"] else f"-{d['deal_discount']}%"
                lines.append(
                    f"{store_emoji} <a href='{d['deal_link']}'>{esc(d['deal_title'])}</a> "
                    f"— <b>{price_part}</b>"
                )
            text = "\n".join(lines)
            keyboard = None

        try:
            await get_bot().send_message(
                uid, text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            flushed_users += 1
        except TelegramForbiddenError:
            log.info(f"Пользователь {uid} заблокировал бота, удаляем из вишлиста")
            await wishlist_remove_user(uid)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            log.warning(f"Ошибка отправки очереди user_id={uid}: {e}")
        await asyncio.sleep(0.05)

    if flushed_users:
        log.info(f"Flush очереди уведомлений: отправлено {flushed_users} пользователям")
        await increment_metric("wishlist_notify_flushed", flushed_users)
