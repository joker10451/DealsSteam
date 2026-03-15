import logging
import time
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import ADMIN_ID, POST_COOLDOWN_SEC
from database import get_metrics_summary

log = logging.getLogger(__name__)
router = Router()

_last_manual_post: float = 0


def esc(text: str) -> str:
    return escape(str(text))


def _admin_only(message: Message) -> bool:
    return message.from_user.id == ADMIN_ID


@router.message(Command("post"))
async def cmd_post(message: Message):
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return

    global _last_manual_post
    elapsed = time.time() - _last_manual_post
    if elapsed < POST_COOLDOWN_SEC:
        remaining = int(POST_COOLDOWN_SEC - elapsed)
        await message.answer(
            f"⏳ Подожди ещё {remaining} сек. перед следующей публикацией."
        )
        return

    from scheduler import check_and_post
    import server

    status_msg = await message.answer("🔄 Запускаю публикацию...")
    try:
        post_time = await check_and_post()
        if post_time:
            server.last_post_time = post_time
            _last_manual_post = time.time()
        await status_msg.edit_text("✅ Готово.")
    except Exception as e:
        log.error(f"Ошибка ручной публикации: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {esc(str(e))}")


@router.message(Command("gems"))
async def cmd_gems(message: Message):
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return
    from scheduler import post_hidden_gems

    status_msg = await message.answer("🔄 Ищу скрытые жемчужины...")
    try:
        await post_hidden_gems()
        await status_msg.edit_text("✅ Готово.")
    except Exception as e:
        log.error(f"Ошибка ручной публикации жемчужин: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {esc(str(e))}")


@router.message(Command("digest"))
async def cmd_digest(message: Message):
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return
    from scheduler import post_weekly_digest

    status_msg = await message.answer("🔄 Формирую дайджест...")
    try:
        await post_weekly_digest()
        await status_msg.edit_text("✅ Готово.")
    except Exception as e:
        log.error(f"Ошибка ручной публикации дайджеста: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {esc(str(e))}")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return
    rows = await get_metrics_summary(days=7)
    if not rows:
        await message.answer("Метрик пока нет.")
        return
    labels = {
        "published": "📢 Публикаций",
        "publish_error": "❌ Ошибок публикации",
        "wishlist_notify": "🔔 Уведомлений вишлиста",
        "genre_notify": "🎯 Уведомлений по жанру",
        "vote_fire": "🔥 Голосов огонь",
        "vote_poop": "💩 Голосов мимо",
    }
    lines = ["📊 <b>Метрики за 7 дней:</b>\n"]
    for row in rows:
        label = labels.get(row["event"], row["event"])
        lines.append(f"{label}: <b>{row['total']}</b>")
    await message.answer("\n".join(lines))


@router.message(Command("givekey"))
async def cmd_give_key(message: Message):
    """Выдать Steam ключ пользователю (только админ)."""
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "Использование: /givekey [user_id] [ключ]\n"
            "Пример: /givekey 123456789 XXXXX-XXXXX-XXXXX"
        )
        return

    try:
        user_id = int(args[1])
        key = args[2].strip()
    except ValueError:
        await message.answer("❌ Неверный формат user_id")
        return

    # Отправляем ключ пользователю
    from publisher import get_bot

    bot = get_bot()

    try:
        await bot.send_message(
            user_id,
            f"🎮 <b>Твой Steam ключ готов!</b>\n\n"
            f"<code>{key}</code>\n\n"
            f"Активируй его в Steam:\n"
            f"1. Открой Steam\n"
            f"2. Игры → Активировать продукт\n"
            f"3. Введи ключ\n\n"
            f"Приятной игры! 🎉",
        )

        # Отмечаем приз как выданный
        from database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE user_rewards
                SET is_claimed = TRUE
                WHERE user_id = $1 
                AND reward_id LIKE 'steam_key_%'
                AND is_claimed = FALSE
                ORDER BY purchased_at DESC
                LIMIT 1
            """,
                user_id,
            )

        await message.answer(f"✅ Ключ отправлен пользователю {user_id}")

    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {esc(str(e))}")


@router.message(Command("addpoints"))
async def cmd_add_points(message: Message):
    """Начислить баллы пользователю (только админ)."""
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "Использование: /addpoints [user_id] [количество]\n"
            "Пример: /addpoints 123456789 500"
        )
        return

    try:
        user_id = int(args[1])
        points = int(args[2])
    except ValueError:
        await message.answer("❌ Неверный формат")
        return

    from database import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_scores (user_id, total_score)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET total_score = user_scores.total_score + $2
        """,
            user_id,
            points,
        )

    await message.answer(f"✅ Начислено {points} баллов пользователю {user_id}")


@router.message(Command("addkey"))
async def cmd_add_key(message: Message):
    """
    Добавить ключ в магазин (только админ).
    Использование: /addkey [reward_id] [game_title] | [KEY-VALUE] [price_usd]
    Пример: /addkey key_deponia Deponia | XXXXX-XXXXX-XXXXX 29.99
    Если reward_id не указан — генерируется автоматически из названия игры.
    """
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return

    # Парсим аргументы: /addkey [reward_id] [game_title] | [key] [price]
    # Разделитель | отделяет мета-данные от самого ключа
    text = message.text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование:\n"
            "<code>/addkey [reward_id] [название игры] | [КЛЮЧ] [цена_usd]</code>\n\n"
            "Примеры:\n"
            "<code>/addkey key_deponia Deponia | XXXXX-XXXXX-XXXXX 29.99</code>\n"
            "<code>/addkey key_deponia Deponia | XXXXX-XXXXX-XXXXX</code>\n\n"
            "reward_id должен начинаться с <code>key_</code>\n"
            "Добавить несколько ключей — отправь команду несколько раз."
        )
        return

    raw = parts[1]
    if "|" not in raw:
        await message.answer(
            "❌ Не хватает разделителя <code>|</code> между названием и ключом."
        )
        return

    meta_part, key_part = raw.split("|", 1)
    meta_tokens = meta_part.strip().split(maxsplit=1)
    key_tokens = key_part.strip().split()

    if not meta_tokens or not key_tokens:
        await message.answer("❌ Неверный формат.")
        return

    # Определяем reward_id и game_title
    if len(meta_tokens) == 2 and meta_tokens[0].startswith("key_"):
        reward_id = meta_tokens[0]
        game_title = meta_tokens[1]
    elif len(meta_tokens) == 1 and meta_tokens[0].startswith("key_"):
        reward_id = meta_tokens[0]
        game_title = meta_tokens[0].replace("key_", "").replace("_", " ").title()
    else:
        # Всё — название игры, reward_id генерируем
        game_title = meta_part.strip()
        reward_id = "key_" + game_title.lower().replace(" ", "_")[:30]

    key_value = key_tokens[0]
    price_usd = 0.0
    if len(key_tokens) > 1:
        try:
            price_usd = float(key_tokens[1])
        except ValueError:
            pass

    from database import add_shop_key

    try:
        key_id = await add_shop_key(
            reward_id=reward_id,
            game_title=game_title,
            key_value=key_value,
            platform="steam",
            original_price_usd=price_usd,
            source="admin",
        )
        await message.answer(
            f"✅ Ключ добавлен (id={key_id})\n"
            f"🎮 Игра: {esc(game_title)}\n"
            f"🔑 reward_id: <code>{esc(reward_id)}</code>\n"
            f"💰 Цена: ${price_usd:.2f}\n\n"
            f"Теперь этот ключ появится в магазине автоматически."
        )
    except Exception as e:
        log.error(f"Ошибка добавления ключа: {e}")
        await message.answer(f"❌ Ошибка: {esc(str(e))}")


@router.message(Command("keystats"))
async def cmd_key_stats(message: Message):
    """Статистика ключей в магазине (только админ)."""
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return

    from database import get_shop_key_stats

    try:
        stats = await get_shop_key_stats()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {esc(str(e))}")
        return

    if not stats:
        await message.answer("🔑 Ключей в магазине пока нет.\nДобавь: /addkey")
        return

    lines = ["🔑 <b>Ключи в магазине:</b>\n"]
    for row in stats:
        avail = row["available"]
        claimed = row["claimed"]
        status = "✅" if avail > 0 else "❌"
        lines.append(
            f"{status} {esc(row['game_title'])}\n"
            f"   reward_id: <code>{esc(row['reward_id'])}</code>\n"
            f"   В наличии: {avail} | Выдано: {claimed}"
        )

    await message.answer("\n\n".join(lines))


@router.message(Command("testpost"))
async def cmd_test_post(message: Message):
    """Отправить тестовый пост в личку админу (не в канал)."""
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return

    status_msg = await message.answer("🔄 Собираю скидки для теста...")

    from scheduler import get_steam_deals, get_gog_deals, get_epic_deals, MIN_DISCOUNT_PERCENT, FILTER_BUNDLES, is_already_posted
    from publisher import get_bot
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    import random

    try:
        all_deals = []
        debug_lines = []
        for fetcher in [get_steam_deals, get_gog_deals, get_epic_deals]:
            name = fetcher.__name__
            try:
                fetched = await fetcher(min_discount=MIN_DISCOUNT_PERCENT)
                all_deals.extend(fetched)
                debug_lines.append(f"✅ {name}: {len(fetched)} шт.")
            except Exception as e:
                debug_lines.append(f"❌ {name}: {e}")

        await status_msg.edit_text("🔄 Парсинг:\n" + "\n".join(debug_lines))

        if FILTER_BUNDLES:
            all_deals = [d for d in all_deals if "bundle" not in d.title.lower()]

        # Фильтруем уже опубликованные
        fresh = []
        for d in all_deals:
            if not await is_already_posted(d.deal_id):
                fresh.append(d)

        # Если все уже опубликованы — берём из всех
        pool = fresh if fresh else all_deals
        if not pool:
            await status_msg.edit_text(
                "❌ Нет подходящих скидок прямо сейчас.\n\n" + "\n".join(debug_lines)
            )
            return

        deal = random.choice(pool)

        # Импортируем форматирование из publisher напрямую
        from publisher import (
            esc,
            get_daily_theme,
            _localize_price,
            generate_comment,
            genres_to_hashtags,
            make_collage,
        )
        from enricher import get_steam_rating, get_historical_low
        from igdb import get_game_info
        from price_glitch import check_for_glitch, format_glitch_alert
        from currency import to_rubles
        from datetime import datetime
        import pytz, asyncio

        MSK = pytz.timezone("Europe/Moscow")
        now = datetime.now(MSK).strftime("%d.%m.%Y")
        store_emoji = {"Steam": "🎮", "GOG": "🟣", "Epic Games": "🎁"}.get(
            deal.store, "🕹"
        )

        glitch_info = await check_for_glitch(deal)
        rating = historical_low = igdb_info = None

        if deal.store == "Steam" and deal.deal_id.startswith("steam_"):
            appid = deal.deal_id.replace("steam_", "")
            rating, historical_low, igdb_info = await asyncio.gather(
                get_steam_rating(appid),
                get_historical_low(appid),
                get_game_info(deal.title),
            )
        else:
            igdb_info = await get_game_info(deal.title)

        theme_emoji, theme_name, _ = get_daily_theme()
        old_price = await _localize_price(deal.old_price)
        new_price = await _localize_price(deal.new_price)

        lines = []
        if glitch_info and glitch_info.get("severity") == "critical":
            lines.append(f"🚨 <b>ОШИБКА ЦЕНЫ? СРОЧНО! · {now}</b>")
        elif deal.is_free:
            lines.append(f"🎁 <b>БЕСПЛАТНО · {now}</b>")
        elif deal.discount >= 80:
            lines.append(f"🔥 <b>ОГОНЬ-СКИДКА · {now}</b>")
        else:
            lines.append(f"{theme_emoji} <b>{theme_name.upper()} · {now}</b>")

        lines.append(f"\n{store_emoji} <b>{esc(deal.title)}</b>")

        if deal.is_free:
            lines.append("💸 <s>Платная</s>  →  🆓 <b>БЕСПЛАТНО</b>")
        else:
            lines.append(f"💰 <s>{esc(old_price)}</s>  →  ✅ <b>{esc(new_price)}</b>")
            lines.append(f"🏷 Скидка: <b>−{deal.discount}%</b>")

        if rating:
            score = rating["score"]
            score_emoji = "🏆" if score >= 95 else "👍" if score >= 80 else "🙂"
            lines.append(f"{score_emoji} Steam: <b>{score}%</b>")

        if glitch_info:
            lines.append(f"\n{format_glitch_alert(deal, glitch_info)}")

        comment = generate_comment(deal, rating)
        lines.append(f"\n💬 <i>{esc(comment)}</i>")

        hashtags = genres_to_hashtags(deal.genres)
        if hashtags:
            lines.append(f"\n{hashtags}")

        lines.append(f"\n\n<i>🧪 Тестовый пост — в канал не отправлялся</i>")
        text = "\n".join(lines)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"🛒 Открыть в {deal.store}", url=deal.link
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔥 0", callback_data=f"vote:fire:{deal.deal_id[:40]}"
                    ),
                    InlineKeyboardButton(
                        text="💩 0", callback_data=f"vote:poop:{deal.deal_id[:40]}"
                    ),
                    InlineKeyboardButton(
                        text="➕ Вишлист", callback_data=f"wl_add:{deal.title[:40]}"
                    ),
                ],
            ]
        )

        bot = get_bot()
        photo = igdb_info.get("cover_url") if igdb_info else None
        if not photo:
            photo = deal.image_url

        if photo:
            await bot.send_photo(
                message.from_user.id,
                photo=photo,
                caption=text,
                reply_markup=keyboard,
            )
        else:
            await bot.send_message(
                message.from_user.id,
                text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )

        await status_msg.edit_text("✅ Тестовый пост отправлен тебе в личку.")

        # Мини-игра "угадай цену" — только для платных игр со скидкой
        if not deal.is_free:
            try:
                old_price_str = str(deal.old_price).replace("₽", "").replace(" ", "").replace(",", "").strip()
                correct = int(float(old_price_str))
                if correct > 0:
                    variants: set = {correct}
                    while len(variants) < 4:
                        delta = random.randint(10, 40)
                        sign = random.choice([-1, 1])
                        fake = round(correct * (1 + sign * delta / 100) / 10) * 10
                        if fake > 0 and fake != correct:
                            variants.add(fake)
                    options = list(variants)
                    random.shuffle(options)

                    from database import save_price_game
                    await save_price_game(deal.deal_id, correct)

                    pg_buttons = [
                        InlineKeyboardButton(text=f"{p}₽", callback_data=f"pg:{deal.deal_id[:40]}:{p}")
                        for p in options
                    ]
                    rows = [pg_buttons[i:i+2] for i in range(0, len(pg_buttons), 2)]
                    pg_keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
                    pg_text = (
                        f"🎮 <b>Мини-игра: угадай цену!</b>\n\n"
                        f"Сколько стоила <b>{esc(deal.title)}</b> до скидки?\n"
                        f"Выбери правильный ответ 👇\n\n"
                        f"<i>🧪 Тест — баллы начислятся как обычно</i>"
                    )
                    await bot.send_message(message.from_user.id, pg_text, reply_markup=pg_keyboard)
            except Exception as e:
                log.warning(f"Тест: мини-игра не отправлена: {e}")

    except Exception as e:
        log.error(f"Ошибка тестового поста: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {esc(str(e))}")


@router.message(Command("giveaways"))
async def cmd_giveaways(message: Message):
    """Показать активные раздачи игр (только админ)."""
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return

    status_msg = await message.answer("🔄 Ищу активные раздачи...")
    try:
        from parsers.giveaways import get_all_active_giveaways, format_giveaways_message

        giveaways = await get_all_active_giveaways()
        text = format_giveaways_message(giveaways, limit=15)
        await status_msg.edit_text(text, disable_web_page_preview=True)
    except Exception as e:
        log.error(f"Ошибка получения раздач: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {esc(str(e))}")


@router.message(Command("rewardstats"))
async def cmd_reward_stats(message: Message):
    """Статистика по купленным призам (только админ)."""
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return

    from database import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        stats = await conn.fetch("""
            SELECT 
                reward_id,
                COUNT(*) as purchases,
                SUM(CASE WHEN is_claimed THEN 1 ELSE 0 END) as claimed
            FROM user_rewards
            GROUP BY reward_id
            ORDER BY purchases DESC
        """)

    if not stats:
        await message.answer("📊 Призы ещё не покупали")
        return

    from rewards import REWARDS_CATALOG

    lines = ["📊 <b>Статистика призов:</b>\n"]
    for row in stats:
        reward_id = row["reward_id"]
        reward = REWARDS_CATALOG.get(reward_id, {"name": reward_id})
        purchases = row["purchases"]
        claimed = row["claimed"]

        lines.append(f"{reward['name']}")
        lines.append(f"Куплено: {purchases}, Выдано: {claimed}\n")

    await message.answer("\n".join(lines))
