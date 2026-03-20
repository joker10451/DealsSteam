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
    increment_metric,
)
from parsers.steam import get_steam_deals
from parsers.epic import get_epic_deals
from parsers.cheapshark import get_cheapshark_deals
from parsers.gamerpower import get_gamerpower_deals
from enricher import get_steam_rating
from igdb import get_game_info
from hidden_gems import find_hidden_gems
from smart_filter import should_publish_deal, generate_context_comment
from publisher import (
    publish_single, notify_wishlist_users, notify_users,
    notify_admin, send_with_retry, get_daily_theme, esc, get_bot,
    notify_free_game_subscribers,
)
from price_glitch import is_price_glitch, check_for_glitch
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")

# Блокировка от параллельного запуска check_and_post
_post_lock = asyncio.Lock()


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
    deal_genres_lower = [g.lower() for g in deal.genres]
    return 1 if any(g in deal_genres_lower for g in theme_genres) else 0


async def publish_top_of_day() -> Optional[str]:
    """
    Публикует ТОП СКИДКУ ДНЯ — игру с максимальным score.
    Возвращает название игры или None если нет достойных.
    """
    log.info("🏆 Поиск ТОП скидки дня...")
    all_deals = []
    
    for fetcher in [get_steam_deals, get_epic_deals]:
        try:
            deals = await fetcher(min_discount=MIN_DISCOUNT_PERCENT)
            all_deals.extend(deals)
        except Exception as e:
            log.error(f"Ошибка при сборе для ТОП дня: {e}")
    
    if not all_deals:
        log.warning("Нет скидок для ТОП дня")
        return None
    
    # Дедупликация
    all_deals = deduplicate(all_deals)
    
    # Фильтруем бандлы
    if FILTER_BUNDLES:
        all_deals = [d for d in all_deals if "bundle" not in d.title.lower()]
    
    # Рассчитываем score для каждой игры
    from publisher import _calculate_deal_score
    scored_deals = []
    
    for deal in all_deals:
        # Пропускаем уже опубликованные
        if await is_already_posted(deal.deal_id):
            continue
        
        # Получаем рейтинг для расчёта score
        rating = None
        if deal.store == "Steam" and deal.deal_id.startswith("steam_"):
            appid = deal.deal_id.replace("steam_", "")
            rating = await get_steam_rating(appid)
        
        # Извлекаем цену в рублях
        try:
            new_price_rub = float(str(deal.new_price).replace("₽", "").replace(" ", "").replace(",", "").strip())
        except (ValueError, AttributeError):
            new_price_rub = 999999
        
        score = _calculate_deal_score(deal, rating, new_price_rub)
        scored_deals.append((deal, rating, score))
    
    if not scored_deals:
        log.warning("Нет неопубликованных скидок для ТОП дня")
        return None
    
    # Находим игру с максимальным score
    top_deal, top_rating, top_score = max(scored_deals, key=lambda x: x[2])
    
    # Не публикуем если score < 5
    if top_score < 5:
        log.info(f"ТОП дня пропущен: лучший score={top_score} < 5")
        return None
    
    # Публикуем с особым форматом
    success = await _publish_top_day_post(top_deal, top_rating, top_score)
    
    if success:
        await mark_as_posted(top_deal.deal_id)
        log.info(f"🏆 ТОП дня опубликован: {top_deal.title} (score={top_score})")
        return top_deal.title
    
    return None


async def _publish_top_day_post(deal, rating: Optional[dict], score: int) -> bool:
    """Публикует пост с форматом ТОП ДНЯ."""
    import random
    from publisher import esc, _utm_link, _cb_id, get_bot, send_with_retry, _localize_price
    from collage import make_collage
    
    # Вариативные заголовки
    top_labels = [
        "🏆 ТОП СКИДКА ДНЯ",
        "🔥 ЛУЧШАЯ СКИДКА СЕГОДНЯ",
        "💎 САМАЯ ВЫГОДНАЯ ИГРА ДНЯ",
    ]
    
    header = random.choice(top_labels)
    
    # Получаем данные
    igdb_info = await get_game_info(deal.title)
    old_price = await _localize_price(deal.old_price)
    new_price = await _localize_price(deal.new_price)
    
    lines = [f"<b>{header}</b>\n"]
    
    # Название + скидка
    if deal.is_free:
        lines.append(f"🎁 <b>{esc(deal.title)} — БЕСПЛАТНО</b>")
    else:
        lines.append(f"🔥 <b>{esc(deal.title)} — −{deal.discount}%</b>")
    
    # Цена
    if deal.is_free:
        lines.append(f"💰 <b>БЕСПЛАТНО</b>")
    else:
        lines.append(f"💰 {esc(old_price)} → <b>{esc(new_price)}</b>")
    
    # Описание (короткое и цепляющее)
    descriptions_top = [
        "Культовая игра с отличными отзывами",
        "Одна из лучших в своём жанре",
        "Сильный сюжет и атмосфера",
        "Высокий рейтинг и куча контента",
        "100+ часов геймплея",
    ]
    
    short_desc = random.choice(descriptions_top)
    if rating and rating['score'] >= 90:
        short_desc += f" ({rating['score']}% положительных)"
    
    lines.append(f"\n🎮 {esc(short_desc)}")
    
    # Социальное доказательство
    if rating and rating['score'] >= 90:
        lines.append("⚡ Игроки в восторге")
    
    # Вердикт (только топовые)
    verdicts = [
        "👉 <b>ЗА ТАКУЮ ЦЕНУ — ОБЯЗАТЕЛЬНО БРАТЬ</b>",
        "👉 <b>ЭТО ПОДАРОК</b>",
        "👉 <b>БРАТЬ НЕ ДУМАЯ</b>",
    ]
    
    # Усиливаем если цена очень низкая
    try:
        price_rub = float(str(deal.new_price).replace("₽", "").replace(" ", "").replace(",", "").strip())
        if price_rub <= 100:
            verdicts.append("👉 <b>ПОЧТИ БЕСПЛАТНО — БРАТЬ</b>")
        elif price_rub <= 300:
            verdicts.append("👉 <b>ДЕШЕВЛЕ ОБЕДА — БРАТЬ</b>")
    except:
        pass
    
    if deal.discount >= 85:
        verdicts.append("👉 <b>ЖИРНАЯ СКИДКА — НЕ УПУСТИ</b>")
    
    lines.append(f"\n{random.choice(verdicts)}")
    
    text = "\n".join(lines)
    
    # Кнопки
    vote_row = [
        InlineKeyboardButton(text="🔥 0", callback_data=f"vote:fire:{_cb_id(deal.deal_id)}"),
        InlineKeyboardButton(text="💩 0", callback_data=f"vote:poop:{_cb_id(deal.deal_id)}"),
        InlineKeyboardButton(text="➕ Вишлист", callback_data=f"wl_add:{deal.title[:40]}"),
    ]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🛒 Открыть в {deal.store}", url=_utm_link(deal.link, deal.store))],
        vote_row,
    ])
    
    # Картинка
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
        from aiogram.types import BufferedInputFile
        if collage_bytes:
            file = BufferedInputFile(collage_bytes, filename="top_day.png")
            await send_with_retry(lambda: get_bot().send_photo(CHANNEL_ID, photo=file, caption=text, reply_markup=keyboard))
        elif photo:
            await send_with_retry(lambda: get_bot().send_photo(CHANNEL_ID, photo=photo, caption=text, reply_markup=keyboard))
        else:
            await send_with_retry(lambda: get_bot().send_message(CHANNEL_ID, text, reply_markup=keyboard, disable_web_page_preview=True))
        
        await increment_metric("published")
        return True
    except Exception as e:
        log.error(f"Ошибка публикации ТОП дня: {e}")
        return False


async def check_and_post() -> Optional[str]:
    """Собирает скидки и публикует лучшую. Возвращает время публикации или None."""
    if _post_lock.locked():
        log.warning("check_and_post уже выполняется, пропускаем")
        return None
    async with _post_lock:
        return await _check_and_post_impl()


async def _check_and_post_impl() -> Optional[str]:
    log.info("Запуск сбора скидок...")
    all_deals = []
    errors = []

    for fetcher, name in [
        (get_steam_deals, "Steam"),
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

    # GamerPower — только бесплатные раздачи, без параметра min_discount
    try:
        gp_deals = await get_gamerpower_deals()
        log.info(f"GamerPower: найдено {len(gp_deals)} раздач")
        all_deals.extend(gp_deals)
    except Exception as e:
        log.error(f"Ошибка при парсинге GamerPower: {e}")
        errors.append(f"GamerPower: {e}")

    if errors:
        # ОТКЛЮЧЕНО: не спамим админа ошибками парсеров
        # await notify_admin("\n".join(errors))
        log.warning(f"Ошибки парсеров (не отправлены админу): {errors}")

    if not all_deals:
        log.warning("Все парсеры вернули 0 скидок — публикация невозможна")
        return None

    log.info(f"Всего скидок от парсеров: {len(all_deals)}")

    filtered = []
    rating_cache: dict[str, Optional[dict]] = {}
    igdb_cache: dict[str, Optional[dict]] = {}
    igdb_ids_seen: set[int] = set()

    for deal in all_deals:
        if await is_already_posted(deal.deal_id):
            log.debug(f"Уже опубликовано: {deal.title} ({deal.deal_id})")
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

        # Получаем рейтинг и IGDB данные
        rating = None
        if deal.store == "Steam":
            appid = deal.deal_id.replace("steam_", "")
            rating = await get_steam_rating(appid)
            rating_cache[deal.deal_id] = rating
        
        igdb_info = await get_game_info(deal.title)
        igdb_cache[deal.deal_id] = igdb_info
        
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
        
        # Умная фильтрация
        should_publish, reason = await should_publish_deal(deal, rating, igdb_info)
        
        if not should_publish:
            log.info(f"Отклонено умным фильтром ({reason}): {deal.title}")
            continue

        log.debug(f"Прошло фильтры: {deal.title} | -{deal.discount}% | reason={reason}")
        filtered.append(deal)

    if not filtered:
        log.warning(
            f"Нет новых скидок для публикации. "
            f"Всего от парсеров: {len(all_deals)}, прошло фильтры: 0"
        )
        return None

    filtered = deduplicate(filtered)
    
    # Разделяем на категории: glitch'и, бесплатные, платные
    glitches = []
    free = []
    paid = []
    
    for d in filtered:
        if await is_price_glitch(d):
            glitches.append(d)
        elif d.is_free:
            free.append(d)
        else:
            paid.append(d)
    
    # Сортируем glitch'и по скидке (самые экстремальные первыми)
    glitches.sort(key=lambda d: d.discount, reverse=True)
    
    # Сортируем платные по теме дня и скидке
    _, _, theme_genres = get_daily_theme()
    paid.sort(key=lambda d: (theme_score(d, theme_genres), d.discount), reverse=True)

    # ОТКЛЮЧЕНО: не спамим админа критическими ошибками цен
    # Алерт администратору о критических ошибках цен — возможность закупить ключи
    # if glitches:
    #     for g in glitches[:3]:
    #         glitch_info = await check_for_glitch(g)
    #         if glitch_info and glitch_info.get("severity") == "critical":
    #             await notify_admin(
    #                 f"🚨 <b>ОШИБКА ЦЕНЫ — ЗАКУПИТЬ КЛЮЧИ!</b>\n\n"
    #                 f"🎮 {g.title}\n"
    #                 f"💥 Скидка: {g.discount}%\n"
    #                 f"💰 Цена: {g.new_price} (было {g.old_price})\n"
    #                 f"🔗 {g.link}\n\n"
    #                 f"⚡️ Купи 10-20 копий — это призовой фонд магазина!\n"
    #                 f"После покупки: /givekey [user_id] [ключ]"
    #             )

    # Приоритет: glitch'и > бесплатные > платные
    combined = glitches + free + paid
    if not combined:
        log.info("Нет новых скидок для публикации.")
        return None

    log.info(f"Кандидаты на публикацию: {len(combined)} (glitch={len(glitches)}, free={len(free)}, paid={len(paid)})")
    for d in combined[:5]:
        log.info(f"  → {d.title} | {d.store} | -{d.discount}% | {d.deal_id}")

    for deal in combined[:5]:  # пробуем до 5 кандидатов
        is_priority = deal in glitches or deal.is_free
        ok, historical_low = await publish_single(
            deal,
            prefetched_rating=rating_cache.get(deal.deal_id),
            is_priority=is_priority
        )
        if ok:
            post_time = datetime.now(MSK).isoformat()
            await mark_as_posted(deal.deal_id, deal.title, deal.store, deal.discount, deal.link, deal.old_price, deal.new_price)
            await notify_wishlist_users(deal, historical_low=historical_low)
            await notify_genre_subscribers(deal)
            
            # Уведомляем подписчиков о бесплатных играх
            if deal.is_free:
                await notify_free_game_subscribers(deal)

            # Дублируем в ВК с рейтингом и IGDB
            try:
                from vk_publisher import post_deal_to_vk
                vk_igdb_info = igdb_cache.get(deal.deal_id)
                await post_deal_to_vk(deal, rating_cache.get(deal.deal_id), igdb_info=vk_igdb_info)
            except Exception as e:
                log.warning(f"VK публикация не удалась: {e}")
            
            # Мини-игра теперь встроена в пост (кнопка pg_start:)
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
    store_emoji = {"Steam": "🎮", "Epic Games": "🎁"}

    lines = [f"📅 <b>ЛУЧШИЕ СКИДКИ НЕДЕЛИ — {now}</b>", "", "🏷 <b>Топ по скидке:</b>"]
    for i, row in enumerate(top_discount, 1):
        emoji = store_emoji.get(row["store"], "🕹")
        link = row.get("link") or ""
        if not link:
            if row["store"] == "Steam" and row["deal_id"].startswith("steam_"):
                appid = row["deal_id"].replace("steam_", "")
                link = f"https://store.steampowered.com/app/{appid}/"
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
            title_part = f"<a href='{link}'>{esc(row['title'])}</a>" if link else esc(row["title"])
            lines.append(f"{i}. {emoji} {title_part} — {row['fire_count']} 🔥")

    lines += ["", "━" * 20, "👾 Следи за каналом — новые скидки каждый день!"]

    from publisher import get_bot, send_with_retry
    try:
        await send_with_retry(lambda: get_bot().send_message(
            CHANNEL_ID, "\n".join(lines), disable_web_page_preview=True
        ))
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
    for fetcher, name, kwargs in [
        (get_steam_deals, "Steam", {"min_discount": MIN_DISCOUNT_PERCENT}),
        (get_epic_deals, "Epic Games", {"min_discount": MIN_DISCOUNT_PERCENT}),
        (get_cheapshark_deals, "CheapShark", {"min_discount": MIN_DISCOUNT_PERCENT}),
        (get_gamerpower_deals, "GamerPower", {}),
    ]:
        try:
            deals = await fetcher(**kwargs)
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


async def run_garbage_collect():
    """
    Еженедельная очистка БД. Запускается по воскресеньям в 03:00 МСК.
    Отправляет краткий отчёт администратору.
    """
    from database import db_garbage_collect
    try:
        stats = await db_garbage_collect()
        total = sum(stats.values())
        if not ADMIN_ID:
            return
        if total == 0:
            await notify_admin("🗑 <b>GC:</b> база чистая, нечего удалять.")
            return
        lines = ["🗑 <b>Еженедельная очистка БД завершена</b>\n"]
        label_map = {
            "price_history": "История цен (>90д)",
            "posted_deals_old": "Старые сделки (>1г)",
            "votes": "Голоса (>1г)",
            "metrics": "Метрики (>90д)",
            "user_score_history": "История баллов (>90д)",
            "screenshot_answers": "Ответы скриншот-игры (>30д)",
            "screenshot_games": "Скриншот-игры (>30д)",
            "daily_challenge_completions": "Выполнения челленджей (>90д)",
            "daily_challenges": "Челленджи (>90д)",
            "price_game_answers": "Ответы угадай-цену (>90д)",
            "price_game": "Игры угадай-цену (>90д)",
            "notification_queue_stale": "Зависшие уведомления (>7д)",
            "onboarding_hints": "Подсказки онбординга (>180д)",
        }
        for key, count in stats.items():
            if count > 0:
                label = label_map.get(key, key)
                lines.append(f"• {label}: <b>{count}</b> строк")
        lines.append(f"\n<b>Итого удалено: {total} строк</b>")
        await notify_admin("\n".join(lines))
    except Exception as e:
        log.error(f"GC: ошибка при очистке БД: {e}")
        await notify_admin(f"❌ <b>GC завершился с ошибкой:</b>\n{e}")


async def check_bot_health():
    """
    Healthcheck: проверяет что последний пост был не позже MAX_SILENCE_HOURS назад.
    Если бот «застрял» — шлёт алерт администратору.
    Запускается каждый час.
    """
    import server as _server
    from database import get_pool

    MAX_SILENCE_HOURS = 3

    last_post_iso = _server.last_post_time

    # Если in-memory значение пустое (перезапуск) — берём из БД
    if last_post_iso is None:
        try:
            pool = await get_pool()
            row = await pool.fetchrow(
                "SELECT posted_at FROM posted_deals ORDER BY posted_at DESC LIMIT 1"
            )
            if row:
                last_post_iso = row["posted_at"].isoformat()
                _server.last_post_time = last_post_iso
        except Exception as e:
            log.warning(f"Healthcheck: не удалось получить last_post из БД: {e}")

    if last_post_iso is None:
        log.info("Healthcheck: нет данных о последнем посте, пропускаем")
        return

    try:
        from datetime import datetime as dt
        now = datetime.now(MSK)
        last_dt = dt.fromisoformat(last_post_iso)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=MSK)
        else:
            last_dt = last_dt.astimezone(MSK)

        silence_hours = (now - last_dt).total_seconds() / 3600

        if silence_hours > MAX_SILENCE_HOURS:
            last_str = last_dt.strftime("%d.%m.%Y %H:%M МСК")
            await notify_admin(
                f"🚨 <b>Бот застрял!</b>\n\n"
                f"Последний пост: <b>{last_str}</b>\n"
                f"Прошло: <b>{silence_hours:.1f} ч</b> (лимит {MAX_SILENCE_HOURS} ч)\n\n"
                f"Проверь логи на Render или запусти /post вручную."
            )
            log.warning(f"Healthcheck FAIL: молчание {silence_hours:.1f}ч > {MAX_SILENCE_HOURS}ч")
        else:
            log.debug(f"Healthcheck OK: последний пост {silence_hours:.1f}ч назад")

    except Exception as e:
        log.error(f"Healthcheck: ошибка при проверке времени: {e}")


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


async def get_top_deals_now(limit: int = 5, user_id: int = None) -> list:
    """
    Возвращает топ текущих скидок (без публикации).
    Если указан user_id, фильтрует игры из Steam библиотеки пользователя.
    
    Requirements: 2.5
    """
    from database import steam_library_filter_deals
    
    all_deals = []
    for fetcher in [get_steam_deals, get_epic_deals, get_cheapshark_deals]:
        try:
            deals = await fetcher(min_discount=MIN_DISCOUNT_PERCENT)
            all_deals.extend(deals)
        except Exception:
            pass
    try:
        all_deals.extend(await get_gamerpower_deals())
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
    result = result[:limit]
    
    # Фильтруем игры из библиотеки пользователя, если указан user_id
    if user_id:
        result = await steam_library_filter_deals(user_id, result)
    
    return result


# --- Steam Integration Jobs ---

async def sync_all_steam_wishlists():
    """
    Syncs Steam wishlists for all users with wishlist_sync_enabled=true.
    Runs daily at 06:00 MSK.
    
    Requirements: 1.6
    """
    from steam_api import fetch_wishlist
    from database import steam_get_all_synced_users, steam_update_sync_time, wishlist_add
    
    log.info("Starting automatic Steam wishlist sync for all users...")
    
    try:
        users = await steam_get_all_synced_users()
        
        if not users:
            log.info("No users with Steam sync enabled")
            return
        
        synced_count = 0
        error_count = 0
        total_games = 0
        
        for user in users:
            if not user.get("wishlist_sync_enabled"):
                continue
            
            user_id = user["user_id"]
            steam_id = user["steam_id"]
            
            try:
                # Fetch wishlist from Steam
                wishlist_games = await fetch_wishlist(steam_id)
                
                if wishlist_games:
                    # Add games to user's wishlist
                    games_added = 0
                    for game in wishlist_games:
                        added = await wishlist_add(user_id, game["name"])
                        if added:
                            games_added += 1
                    
                    # Update sync timestamp
                    await steam_update_sync_time(user_id, "wishlist")
                    
                    synced_count += 1
                    total_games += games_added
                    
                    log.info(
                        f"Synced wishlist for user {user_id}: "
                        f"{games_added} games from Steam ID {steam_id}"
                    )
                else:
                    log.warning(
                        f"No wishlist data for user {user_id}, "
                        f"Steam ID {steam_id} (profile may be private)"
                    )
                    error_count += 1
                
                # Rate limiting: 2 second delay between users
                await asyncio.sleep(2)
            
            except Exception as e:
                log.error(
                    f"Error syncing wishlist for user {user_id}, "
                    f"Steam ID {steam_id}: {e}"
                )
                error_count += 1
        
        log.info(
            f"Steam wishlist sync complete: "
            f"{synced_count} users synced, {total_games} total games, "
            f"{error_count} errors"
        )
    
    except Exception as e:
        log.error(f"Fatal error in sync_all_steam_wishlists: {e}", exc_info=True)


async def sync_all_steam_libraries():
    """
    Syncs Steam libraries for all users with library_sync_enabled=true.
    Runs weekly on Monday at 03:00 MSK.
    
    Requirements: 2.6
    """
    from steam_api import fetch_library
    from database import (
        steam_get_all_synced_users, steam_update_sync_time,
        steam_library_replace
    )
    
    log.info("Starting automatic Steam library sync for all users...")
    
    try:
        users = await steam_get_all_synced_users()
        
        if not users:
            log.info("No users with Steam sync enabled")
            return
        
        synced_count = 0
        error_count = 0
        total_games = 0
        
        for user in users:
            if not user.get("library_sync_enabled"):
                continue
            
            user_id = user["user_id"]
            steam_id = user["steam_id"]
            
            try:
                # Fetch library from Steam
                library_appids = await fetch_library(steam_id)
                
                if library_appids:
                    # Replace user's library in database
                    await steam_library_replace(user_id, library_appids)
                    
                    # Update sync timestamp
                    await steam_update_sync_time(user_id, "library")
                    
                    synced_count += 1
                    total_games += len(library_appids)
                    
                    log.info(
                        f"Synced library for user {user_id}: "
                        f"{len(library_appids)} games from Steam ID {steam_id}"
                    )
                else:
                    log.warning(
                        f"No library data for user {user_id}, "
                        f"Steam ID {steam_id} (profile may be private)"
                    )
                    error_count += 1
                
                # Rate limiting: 2 second delay between users
                await asyncio.sleep(2)
            
            except Exception as e:
                log.error(
                    f"Error syncing library for user {user_id}, "
                    f"Steam ID {steam_id}: {e}"
                )
                error_count += 1
        
        log.info(
            f"Steam library sync complete: "
            f"{synced_count} users synced, {total_games} total games, "
            f"{error_count} errors"
        )
    
    except Exception as e:
        log.error(f"Fatal error in sync_all_steam_libraries: {e}", exc_info=True)


# Кооп-игры с известным Friend's Pass (купил один — играют двое)
_FRIENDS_PASS_GAMES = {
    "it takes two", "a way out", "we were here", "we were here too",
    "we were here together", "we were here forever", "sackboy",
    "hazelight", "far: changing tides",
}

# Кооп-теги в жанрах/тегах Steam
_COOP_KEYWORDS = (
    "co-op", "co op", "coop", "multiplayer", "multi-player",
    "local co-op", "online co-op", "split screen", "кооп",
)


def _is_coop_deal(deal, igdb_info: dict | None) -> bool:
    """Определяет, является ли игра кооперативной."""
    title_lower = deal.title.lower()

    # Friend's Pass — особый случай
    if any(fp in title_lower for fp in _FRIENDS_PASS_GAMES):
        return True

    # IGDB game_modes
    if igdb_info and igdb_info.get("is_coop"):
        return True

    # Жанры/теги из парсера
    genres_lower = [g.lower() for g in (deal.genres or [])]
    if any(kw in g for kw in _COOP_KEYWORDS for g in genres_lower):
        return True

    # Название содержит кооп-слова
    if any(kw in title_lower for kw in _COOP_KEYWORDS):
        return True

    return False


async def post_coop_digest():
    """
    Пятничный дайджест «Во что поиграть с другом?»
    Ищет кооп-игры среди текущих скидок и публикует подборку из 3-4 игр.
    """
    log.info("Запуск пятничного кооп-дайджеста...")

    all_deals = []
    for fetcher in [get_steam_deals, get_epic_deals]:
        try:
            deals = await fetcher(min_discount=MIN_DISCOUNT_PERCENT)
            all_deals.extend(deals)
        except Exception as e:
            log.warning(f"coop_digest: ошибка парсера: {e}")

    if not all_deals:
        log.info("coop_digest: нет скидок")
        return

    # Фильтруем и определяем кооп
    coop_deals = []
    seen_titles: set[str] = set()

    for deal in all_deals:
        key = deal.title.lower().strip()
        if key in seen_titles:
            continue
        if FILTER_BUNDLES and "bundle" in deal.title.lower():
            continue

        igdb_info = await get_game_info(deal.title)
        if _is_coop_deal(deal, igdb_info):
            coop_deals.append((deal, igdb_info))
            seen_titles.add(key)

        if len(coop_deals) >= 4:
            break

    if not coop_deals:
        log.info("coop_digest: кооп-игры не найдены")
        return

    # Формируем пост
    now = datetime.now(MSK).strftime("%d.%m.%Y")
    lines = [
        f"👥 <b>ВО ЧТО ПОИГРАТЬ С ДРУГОМ? · {now}</b>",
        "",
        "Выходные близко! Подборка кооп-игр со скидками 👇",
        "",
    ]

    store_emoji = {"Steam": "🎮", "Epic Games": "🎁"}
    buttons = []

    for deal, igdb_info in coop_deals:
        emoji = store_emoji.get(deal.store, "🕹")
        title_lower = deal.title.lower()

        # Специальная пометка для Friend's Pass
        friends_pass = any(fp in title_lower for fp in _FRIENDS_PASS_GAMES)
        fp_note = "\n   🔑 <i>Friend's Pass — купи одну копию, играйте вдвоём!</i>" if friends_pass else ""

        if deal.is_free:
            price_line = "🆓 <b>БЕСПЛАТНО</b>"
        else:
            price_line = f"<s>{esc(deal.old_price)}</s> → <b>{esc(deal.new_price)}</b> <code>−{deal.discount}%</code>"

        rating_line = ""
        if igdb_info and igdb_info.get("rating"):
            r = igdb_info["rating"]
            rating_line = f"\n   ⭐ IGDB: {r}/100"

        lines.append(f"{emoji} <b>{esc(deal.title)}</b>")
        lines.append(f"   {price_line}{rating_line}{fp_note}")
        lines.append("")

        buttons.append([InlineKeyboardButton(
            text=f"🛒 {deal.title[:30]}",
            url=deal.link,
        )])

    lines.append("#кооп #выходные #скидки #игратьсдругом")

    # Кнопка "Позвать друга" — ведёт на бота (реферальная ссылка генерируется при нажатии в личке)
    from config import BOT_USERNAME
    if BOT_USERNAME:
        buttons.append([InlineKeyboardButton(
            text="👥 Позвать друга (+100 баллов)",
            url=f"https://t.me/{BOT_USERNAME}?start=invite",
        )])

    buttons.append([
        InlineKeyboardButton(text="🔥 0", callback_data="vote:fire:coop_digest"),
        InlineKeyboardButton(text="💩 0", callback_data="vote:poop:coop_digest"),
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = "\n".join(lines)

    try:
        await send_with_retry(lambda: get_bot().send_message(
            CHANNEL_ID, text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        ))
        log.info(f"Кооп-дайджест опубликован: {len(coop_deals)} игр")
        await increment_metric("coop_digest_published")
    except Exception as e:
        log.error(f"coop_digest: ошибка публикации: {e}")
