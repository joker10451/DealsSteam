"""
Планировщик задач: сбор скидок, дайджест, жемчужины, тесты парсеров.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import pytz

from config import (
    CHANNEL_ID, ADMIN_ID,
    MIN_DISCOUNT_PERCENT, MIN_STEAM_RATING,
    FILTER_ADULT, FILTER_BUNDLES, MIN_PRICE_RUB,
)
from database import (
    is_already_posted, mark_as_posted, cleanup_old_records,
    get_weekly_top, get_top_voted,
    get_all_genre_subscribers_for_deal,
)
from parsers.steam import get_steam_deals
from parsers.gog import get_gog_deals
from parsers.epic import get_epic_deals
from enricher import get_steam_rating
from igdb import get_game_info
from hidden_gems import find_hidden_gems
from publisher import (
    publish_single, notify_wishlist_users, notify_users, send_price_game,
    notify_admin, send_with_retry, get_daily_theme, esc, get_bot,
)
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")


def deduplicate(deals: list) -> list:
    seen: dict[str, object] = {}
    for deal in deals:
        key = deal.title.lower().strip()
        if key not in seen or deal.discount > seen[key].discount:
            seen[key] = deal
    return list(seen.values())


def theme_score(deal, theme_genres: list[str]) -> int:
    if not theme_genres:
        return 0
    return 1 if any(g in deal.genres for g in theme_genres) else 0


async def check_and_post() -> Optional[str]:
    """Собирает скидки и публикует лучшую. Возвращает время публикации или None."""
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
    rating_cache: dict[str, Optional[dict]] = {}
    igdb_ids_seen: set[int] = set()

    for deal in all_deals:
        if await is_already_posted(deal.deal_id):
            continue

        # Фильтр бандлов
        if FILTER_BUNDLES and "bundle" in deal.title.lower():
            log.info(f"Пропущено (бандл): {deal.title}")
            continue

        # Фильтр по минимальной цене (только платные)
        if not deal.is_free and MIN_PRICE_RUB > 0:
            try:
                price_str = str(deal.new_price).replace("₽", "").replace(" ", "").replace(",", "").strip()
                if float(price_str) < MIN_PRICE_RUB:
                    log.info(f"Пропущено (цена {price_str}₽ < {MIN_PRICE_RUB}₽): {deal.title}")
                    continue
            except (ValueError, AttributeError):
                pass

        if deal.store == "Steam" and MIN_STEAM_RATING > 0:
            appid = deal.deal_id.replace("steam_", "")
            rating = await get_steam_rating(appid)
            rating_cache[deal.deal_id] = rating
            if rating and rating["score"] < MIN_STEAM_RATING:
                log.info(f"Пропущено (рейтинг {rating['score']}%): {deal.title}")
                continue
        igdb_info = await get_game_info(deal.title)
        if igdb_info:
            igdb_id = igdb_info.get("igdb_id")
            if igdb_id:
                if igdb_id in igdb_ids_seen:
                    log.info(f"Пропущено (дубль IGDB {igdb_id}): {deal.title}")
                    continue
                igdb_ids_seen.add(igdb_id)
            if FILTER_ADULT and igdb_info.get("is_adult"):
                log.info(f"Пропущено (18+): {deal.title}")
                continue
        filtered.append(deal)

    if not filtered:
        log.info("Нет новых скидок для публикации.")
        return None

    filtered = deduplicate(filtered)
    free = [d for d in filtered if d.is_free]
    paid = [d for d in filtered if not d.is_free]

    _, _, theme_genres = get_daily_theme()
    paid.sort(key=lambda d: (theme_score(d, theme_genres), d.discount), reverse=True)

    combined = free + paid
    if not combined:
        log.info("Нет новых скидок для публикации.")
        return None

    for deal in combined[:5]:  # пробуем до 5 кандидатов
        published = await publish_single(deal, prefetched_rating=rating_cache.get(deal.deal_id))
        if published:
            post_time = datetime.now(MSK).isoformat()
            await mark_as_posted(deal.deal_id, deal.title, deal.store, deal.discount, deal.link)
            await notify_wishlist_users(deal)
            await notify_genre_subscribers(deal)
            if not deal.is_free:
                await send_price_game(deal)
            deleted = await cleanup_old_records()
            if deleted:
                log.info(f"БД: удалено {deleted} старых записей")
            return post_time
        else:
            await mark_as_posted(deal.deal_id, deal.title, deal.store, deal.discount, deal.link)

    log.warning("Не удалось опубликовать ни одну сделку из топ-5")
    return None

async def post_weekly_digest():
    top_discount = await get_weekly_top(limit=10)
    top_voted = await get_top_voted(limit=5)

    if not top_discount:
        log.info("Еженедельный дайджест: нет данных за неделю.")
        return

    now = datetime.now(MSK).strftime("%d.%m.%Y")
    store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁"}

    lines = [f"📅 <b>ЛУЧШИЕ СКИДКИ НЕДЕЛИ — {now}</b>", "", "🏷 <b>Топ по скидке:</b>"]
    for i, row in enumerate(top_discount, 1):
        emoji = store_emoji.get(row["store"], "🕹")
        link = row.get("link") or ""
        # Фолбэк: строим ссылку если link не сохранён
        if not link:
            if row["store"] == "Steam" and row["deal_id"].startswith("steam_"):
                appid = row["deal_id"].replace("steam_", "")
                link = f"https://store.steampowered.com/app/{appid}/"
            elif row["store"] == "GOG" and row["deal_id"].startswith("gog_"):
                slug = row["deal_id"].replace("gog_", "")
                link = f"https://www.gog.com/ru/game/{slug}"
            elif row["store"] == "Epic Games" and row["deal_id"].startswith("epic_"):
                link = "https://store.epicgames.com/ru/free-games"
        title_part = f"<a href='{link}'>{esc(row['title'])}</a>" if link else esc(row["title"])
        lines.append(f"{i}. {emoji} {title_part} — <code>-{row['discount']}%</code> <i>({esc(row['store'])})</i>")

    if top_voted:
        lines += ["", "🔥 <b>Топ по голосам подписчиков:</b>"]
        for i, row in enumerate(top_voted, 1):
            emoji = store_emoji.get(row["store"], "🕹")
            link = row.get("link") or ""
            if not link:
                if row["store"] == "Steam" and row["deal_id"].startswith("steam_"):
                    appid = row["deal_id"].replace("steam_", "")
                    link = f"https://store.steampowered.com/app/{appid}/"
                elif row["store"] == "GOG" and row["deal_id"].startswith("gog_"):
                    link = f"https://www.gog.com/ru/game/{row['deal_id'].replace('gog_', '')}"
            title_part = f"<a href='{link}'>{esc(row['title'])}</a>" if link else esc(row["title"])
            lines.append(f"{i}. {emoji} {title_part} — {row['fire_count']} 🔥")

    lines += ["", "━" * 20, "👾 Следи за каналом — новые скидки каждый день!"]

    from publisher import get_bot
    try:
        await get_bot().send_message(CHANNEL_ID, "\n".join(lines), disable_web_page_preview=True)
        log.info("Еженедельный дайджест опубликован.")
    except Exception as e:
        log.error(f"Ошибка при отправке дайджеста: {e}")


async def post_hidden_gems():
    gems = await find_hidden_gems(min_discount=50, min_score=80, max_reviews=1000, limit=2)
    if not gems:
        gems = await find_hidden_gems(min_discount=40, min_score=75, max_reviews=2000, limit=2)
    if not gems:
        log.info("Скрытые жемчужины: ничего не найдено.")
        return

    from publisher import get_bot
    for gem in gems:
        if await is_already_posted(f"gem_{gem.appid}"):
            continue

        score_bar = "⭐" * (gem.score // 20)
        text = (
            f"💎 <b>СКРЫТАЯ ЖЕМЧУЖИНА</b>\n\n"
            f"🎮 <b>{esc(gem.title)}</b>\n\n"
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
            await send_with_retry(lambda: get_bot().send_photo(
                CHANNEL_ID, photo=gem.image_url, caption=text, reply_markup=keyboard
            ))
            await mark_as_posted(f"gem_{gem.appid}", gem.title, "Steam", gem.discount, gem.link)
            log.info(f"Скрытая жемчужина опубликована: {gem.title}")
        except Exception as e:
            log.error(f"Ошибка публикации жемчужины {gem.title}: {e}")
        await asyncio.sleep(2)


async def run_parser_tests():
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
            if count == 0:
                results.append(f"⚠️ {name}: 0 скидок")
                continue
            bad = [d.title for d in deals[:5] if not d.deal_id or not d.title or not d.link or d.discount <= 0]
            if bad:
                results.append(f"⚠️ {name}: {count} скидок, битые записи: {bad}")
            else:
                results.append(f"✅ {name}: {count} скидок")
        except Exception as e:
            results.append(f"❌ {name}: {e}")

    now = datetime.now(MSK).strftime("%d.%m.%Y %H:%M")
    text = f"🤖 <b>Авто-тест парсеров — {now}</b>\n\n" + "\n".join(results)
    from publisher import get_bot
    try:
        await get_bot().send_message(ADMIN_ID, text)
    except Exception as e:
        log.error(f"Не удалось отправить отчёт админу: {e}")


async def notify_genre_subscribers(deal):
    """Уведомляет подписчиков на жанры из сделки."""
    if not deal.genres:
        return
    user_ids = await get_all_genre_subscribers_for_deal(deal.genres)
    if not user_ids:
        return

    from database import increment_metric
    genres_str = ", ".join(f"#{g.lower().replace(' ', '_')}" for g in deal.genres[:3])
    header = f"🎯 <b>Скидка по твоей подписке на жанр!</b>\n🏷 {genres_str}"
    await notify_users(user_ids, deal, header)
    await increment_metric("genre_notify", len(user_ids))


async def get_top_deals_now(limit: int = 5) -> list:
    """Возвращает топ текущих скидок (без публикации)."""
    all_deals = []
    for fetcher in [get_steam_deals, get_gog_deals, get_epic_deals]:
        try:
            deals = await fetcher(min_discount=MIN_DISCOUNT_PERCENT)
            all_deals.extend(deals)
        except Exception:
            pass

    # Фильтруем бандлы и уже опубликованные
    result = []
    for deal in all_deals:
        if FILTER_BUNDLES and "bundle" in deal.title.lower():
            continue
        if await is_already_posted(deal.deal_id):
            continue
        result.append(deal)

    result.sort(key=lambda d: d.discount, reverse=True)
    return result[:limit]
