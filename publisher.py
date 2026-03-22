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

from config import CHANNEL_ID, ADMIN_ID, BOT_USERNAME
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
from smart_filter import generate_context_comment
from ai_writer import generate_post_text

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
    0: ("⚔️", "RPG-понедельник",  ["rpg", "role-playing", "ролевые", "ролевые игры"]),
    1: ("💥", "Экшен-вторник",    ["action", "shooter", "fighting", "экшен", "шутер", "боевик"]),
    2: ("🧠", "Стратегия-среда",  ["strategy", "turn-based strategy", "real time strategy", "стратегия"]),
    3: ("🎲", "Инди-четверг",     ["indie", "инди"]),
    4: ("👻", "Хоррор-пятница",   ["horror", "survival horror", "хоррор", "ужасы"]),
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


def _calculate_deal_score(deal, rating: Optional[dict], new_price_rub: float) -> int:
    """
    Рассчитывает score игры для фильтрации и типа поста.
    
    Логика:
    - Рейтинг: 90%+ = 3, 80%+ = 2, 70%+ = 1
    - Скидка: 80%+ = 3, 70%+ = 2, 50%+ = 1
    - Цена: ≤100₽ = 2, ≤300₽ = 1
    - Бесплатные игры: автоматически score = 6
    
    Returns:
        score (int): 0-8, где 6+ = ТОП, 4-5 = НОРМ, <4 = СЛАБО
    """
    if deal.is_free:
        return 6  # Бесплатные игры всегда публикуем
    
    score = 0
    
    # Рейтинг
    if rating:
        rating_score = rating.get('score', 0)
        if rating_score >= 90:
            score += 3
        elif rating_score >= 80:
            score += 2
        elif rating_score >= 70:
            score += 1
    
    # Скидка
    if deal.discount >= 80:
        score += 3
    elif deal.discount >= 70:
        score += 2
    elif deal.discount >= 50:
        score += 1
    
    # Цена (в рублях)
    if new_price_rub <= 100:
        score += 2
    elif new_price_rub <= 300:
        score += 1
    
    return score


async def publish_single(deal, prefetched_rating: Optional[dict] = None, is_priority: bool = False) -> tuple[bool, Optional[dict]]:
    """
    Публикует сделку в канал.
    
    Returns:
        (True, historical_low) если публикация успешна, (False, None) при ошибке
    """
    now = datetime.now(MSK).strftime("%d.%m.%Y")
    store_emoji = {"Steam": "🎮", "Epic Games": "🎁"}.get(deal.store, "🕹")
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
    theme_emoji, theme_name, theme_genres = get_daily_theme()

    # Показываем тему дня только если жанр игры совпадает с темой (или тема универсальная)
    deal_genres_lower = [g.lower() for g in (deal.genres or [])]
    theme_matches = not theme_genres or any(g in deal_genres_lower for g in theme_genres)
    header_emoji = theme_emoji if theme_matches else "🎮"
    header_name = theme_name if theme_matches else "СКИДКА ДНЯ"

    old_price = await _localize_price(deal.old_price)
    new_price = await _localize_price(deal.new_price)
    
    # СКОРИНГ И ФИЛЬТРАЦИЯ
    # Извлекаем цену в рублях для расчёта score
    if deal.is_free:
        new_price_rub = 0.0
    else:
        try:
            new_price_rub = float(str(deal.new_price).replace("₽", "").replace(" ", "").replace(",", "").strip())
        except (ValueError, AttributeError):
            new_price_rub = 999999  # Если не удалось распарсить — считаем дорогой
    
    score = _calculate_deal_score(deal, rating, new_price_rub)
    
    # ФИЛЬТР: не публикуем мусор (score < 3), кроме приоритетных
    if score < 3 and not is_priority:
        log.info(f"Пропущено (низкий score={score}): {deal.title}")
        return (False, None)

    lines = []
    adult_prefix = "🔞 " if (igdb_info and igdb_info.get("is_adult")) else ""

    # Пробуем сгенерировать текст через AI (Groq)
    ai_text = await generate_post_text(
        title=deal.title,
        old_price=str(deal.old_price),
        new_price=str(deal.new_price),
        discount=deal.discount,
        is_free=deal.is_free,
        rating_score=rating["score"] if rating else None,
        genres=deal.genres or [],
        igdb_description=igdb_info.get("description") if igdb_info else None,
    )

    if ai_text:
        # AI сгенерировал текст — используем его, добавляем только adult-префикс если нужно
        if adult_prefix:
            ai_text = f"{adult_prefix}\n{ai_text}"
        text = ai_text
    else:
        # Fallback: шаблонная генерация
        import random

        # Строка 1: Название + скидка
        if deal.is_free:
            lines.append(f"🎁 <b>{adult_prefix}{esc(deal.title)} — БЕСПЛАТНО</b>")
        else:
            lines.append(f"🔥 <b>{adult_prefix}{esc(deal.title)} — −{deal.discount}%</b>")

        # Строка 2: Цена
        if deal.is_free:
            if old_price and old_price not in ("—", "Платная", ""):
                lines.append(f"💰 Было: {esc(old_price)} → <b>БЕСПЛАТНО</b>")
            else:
                lines.append(f"💰 <b>БЕСПЛАТНО</b>")
        else:
            lines.append(f"💰 Было: {esc(old_price)} → <b>{esc(new_price)}</b>")

        descriptions_top = [
            "Культовая игра с отличными отзывами",
            "Одна из лучших в своём жанре",
            "Сильный сюжет и атмосфера",
            "Высокий рейтинг и куча контента",
            "100+ часов геймплея",
        ]
        descriptions_good = [
            "Отличный вариант за свои деньги",
            "Игроки очень хвалят геймплей",
            "Качественная игра с хорошими отзывами",
            "Затягивает с первых минут",
            "Открытый мир и свобода действий",
        ]
        descriptions_ok = [
            "Интересный вариант для фанатов жанра",
            "Неплохая игра со смешанными отзывами",
            "Может зайти, если нравится жанр",
        ]

        if score >= 6:
            short_desc = random.choice(descriptions_top)
        elif score >= 4:
            short_desc = random.choice(descriptions_good)
        else:
            short_desc = random.choice(descriptions_ok)

        if rating and rating['score'] >= 80:
            short_desc += f" ({rating['score']}% положительных)"

        lines.append(f"\n🎮 {esc(short_desc)}")

        verdicts_top = [
            "👉 <b>ЗА ТАКУЮ ЦЕНУ — ОБЯЗАТЕЛЬНО БРАТЬ</b>",
            "👉 <b>ЭТО ПОДАРОК</b>",
            "👉 <b>БРАТЬ НЕ ДУМАЯ</b>",
        ]
        if new_price_rub <= 100:
            verdicts_top.append("👉 <b>ПОЧТИ БЕСПЛАТНО — БРАТЬ</b>")
        elif new_price_rub <= 300:
            verdicts_top.append("👉 <b>ДЕШЕВЛЕ ОБЕДА — БРАТЬ</b>")
        if deal.discount >= 85:
            verdicts_top.append("👉 <b>ЖИРНАЯ СКИДКА — НЕ УПУСТИ</b>")

        verdicts_good = [
            "👉 <b>СТОИТ ВЗЯТЬ</b>",
            "👉 <b>Отличная цена</b>",
            "👉 <b>Хорошая сделка</b>",
        ]
        verdicts_ok = [
            "👉 Только если нравится жанр",
            "👉 Проверь отзывы перед покупкой",
        ]

        if deal.is_free:
            verdict = "👉 <b>Бесплатно — забирай не думая!</b>"
        elif score >= 6:
            verdict = random.choice(verdicts_top)
        elif score >= 4:
            verdict = random.choice(verdicts_good)
        else:
            verdict = random.choice(verdicts_ok)

        lines.append(f"\n{verdict}")

        # Автодожим
        from config import TG_CHANNEL_LINK
        closers = [
            f"🔥 Больше таких скидок — <a href='{TG_CHANNEL_LINK}'>в канале</a>",
            f"Подписывайся, чтобы не пропускать такие цены → <a href='{TG_CHANNEL_LINK}'>канал</a>",
        ]
        lines.append(f"\n{random.choice(closers)}")

        text = "\n".join(lines)

    # ОТКЛЮЧЕНО: мини-игры ломают бота
    # Для платных игр — сохраняем цену и добавляем кнопку мини-игры
    # price_game_button = None
    # if not deal.is_free:
    #     try:
    #         old_price_str = str(deal.old_price).replace("₽", "").replace(" ", "").replace(",", "").strip()
    #         correct = int(float(old_price_str))
    #         if correct > 0:
    #             await save_price_game(
    #                 deal.deal_id, correct,
    #                 title=deal.title,
    #                 new_price=str(deal.new_price),
    #                 link=deal.link,
    #                 discount=deal.discount,
    #             )
    #             price_game_button = InlineKeyboardButton(
    #                 text="🎲 Угадай цену — заработай баллы!",
    #                 callback_data=f"pg_start:{_cb_id(deal.deal_id)}"
    #             )
    #     except (ValueError, AttributeError):
    #         pass

    vote_row = [
        InlineKeyboardButton(text="🔥 0", callback_data=f"vote:fire:{_cb_id(deal.deal_id)}"),
        InlineKeyboardButton(text="💩 0", callback_data=f"vote:poop:{_cb_id(deal.deal_id)}"),
        InlineKeyboardButton(text="➕ Вишлист", callback_data=f"wl_add:{deal.title[:40].replace(':', '').replace('|', '')}"),
    ]
    rows = [
        [InlineKeyboardButton(text=f"🛒 Открыть в {deal.store}", url=_utm_link(deal.link, deal.store))],
        vote_row,
    ]
    # ОТКЛЮЧЕНО: кнопка мини-игры
    # if price_game_button:
    #     rows.append([price_game_button])

    # Кнопка "Отправить другу" — ведёт в бота, тот выдаёт персональную share-ссылку
    if BOT_USERNAME:
        share_param = f"share_{_cb_id(deal.deal_id)}"
        rows.append([InlineKeyboardButton(
            text="🎁 Отправить другу",
            url=f"https://t.me/{BOT_USERNAME}?start={share_param}",
        )])

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
            if appid.isdigit():
                photo = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"

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
        
        # ОТКЛЮЧЕНО: мини-игры ломают бота
        # Случайно публикуем игру со скриншотом (20% шанс) — в фоне, не блокируем
        # import random
        # if random.random() < 0.2 and igdb_info and igdb_info.get("screenshots"):
        #     async def _delayed_screenshot():
        #         await asyncio.sleep(30)
        #         await publish_screenshot_game(deal, igdb_info)
        #     asyncio.create_task(_delayed_screenshot())

        # Контекстный совет отключён
        # from tips import get_contextual_tip
        # ctx_tip = await get_contextual_tip(deal)

        return (True, historical_low)
    except Exception as e:
        log.error(f"Ошибка при отправке {deal.title}: {e}")
        await increment_metric("publish_error")
        return (False, None)


async def notify_users(user_ids: list[int], deal, header: str):
    """Отправляет уведомление о скидке списку пользователей."""
    store_emoji = {"Steam": "🎮", "Epic Games": "🎁"}.get(deal.store, "🕹")
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
        await send_with_retry(lambda: get_bot().send_photo(
            CHANNEL_ID,
            photo=screenshot_url,
            caption=caption,
            reply_markup=keyboard,
        ))
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
            store_emoji = {"Steam": "🎮", "Epic Games": "🎁"}.get(d["deal_store"], "🕹")
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
                store_emoji = {"Steam": "🎮", "Epic Games": "🎁"}.get(d["deal_store"], "🕹")
                price_part = "🆓 БЕСПЛАТНО" if d["deal_is_free"] else f"-{d['deal_discount']}%"
                lines.append(
                    f"{store_emoji} <a href='{d['deal_link']}'>{esc(d['deal_title'])}</a> "
                    f"— <b>{price_part}</b>"
                )
            text = "\n".join(lines)
            keyboard = None

        try:
            await send_with_retry(lambda uid=uid, text=text, keyboard=keyboard: get_bot().send_message(
                uid, text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            ))
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
