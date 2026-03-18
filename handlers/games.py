"""
Обработчики для мини-игр и челленджей.
"""
import logging
import random
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from achievements import check_and_unlock_achievements
from database import get_price_game, record_price_game_answer, get_pool
from minigames import (
    get_user_score, get_leaderboard, check_screenshot_answer,
    add_score,
)
from publisher import get_bot, send_with_retry

router = Router()
log = logging.getLogger(__name__)


def esc(text: str) -> str:
    return escape(str(text))

@router.message(Command("score"))
async def cmd_score(message: Message):
    """Показать баллы пользователя."""
    user_id = message.from_user.id
    stats = await get_user_score(user_id)
    
    total = stats["total_score"]
    played = stats["games_played"]
    correct = stats["correct_answers"]
    accuracy = int(correct / played * 100) if played > 0 else 0
    
    text = f"""
🎮 <b>Твоя статистика</b>

⭐️ Баллы: <b>{total}</b>
🎯 Игр сыграно: <b>{played}</b>
✅ Правильных ответов: <b>{correct}</b>
📊 Точность: <b>{accuracy}%</b>

Играй больше, чтобы заработать баллы!
Используй /games для списка игр.
"""
    await message.answer(text.strip())


@router.message(Command("leaderboard"))
async def cmd_leaderboard(message: Message):
    """Показать таблицу лидеров."""
    leaders = await get_leaderboard(10)
    
    if not leaders:
        await message.answer("Таблица лидеров пока пуста. Будь первым!")
        return

    from rewards import get_user_badges

    def games_word(n: int) -> str:
        if 11 <= n % 100 <= 19:
            return "игр"
        r = n % 10
        if r == 1:
            return "игра"
        if 2 <= r <= 4:
            return "игры"
        return "игр"

    lines = ["🏆 <b>Таблица лидеров</b>\n"]
    medals = ["🥇", "🥈", "🥉"]

    for i, leader in enumerate(leaders, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        score = leader["total_score"]
        games = leader["games_played"]
        name = esc(leader.get("username") or f"Игрок {leader['user_id'] % 10000}")
        badges = await get_user_badges(leader["user_id"])
        badge_str = f" {badges}" if badges else ""
        lines.append(f"{medal} <b>{name}</b>{badge_str} — {score} баллов, {games} {games_word(games)}")

    lines.append("\n💡 Играй в мини-игры, чтобы попасть в топ!")
    
    await message.answer("\n".join(lines))


@router.message(Command("games"))
async def cmd_games(message: Message):
    """Показать список доступных игр."""
    text = """
🎮 <b>Мини-игры</b>

<b>Доступные игры:</b>

1️⃣ <b>Угадай цену</b>
Появляется автоматически после каждой скидки в канале.
Награда: 5 баллов за правильный ответ

2️⃣ <b>Угадай игру по скриншоту</b>
Появляется случайно в канале.
Награда: 10 баллов за правильный ответ

3️⃣ <b>Ежедневный челлендж</b>
Новый челлендж каждый день!
Награда: 50 баллов

📊 Твоя статистика: /score
🏆 Таблица лидеров: /leaderboard
🎯 Челлендж дня: /challenge
"""
    await message.answer(text.strip())



@router.callback_query(lambda c: c.data and (c.data.startswith("scr:") or c.data.startswith("screenshot:")))
async def handle_screenshot_answer(callback: CallbackQuery):
    """Обработать ответ на игру со скриншотом."""
    user_id = callback.from_user.id

    # Новый формат: scr:<short_gid>:<index>
    if callback.data.startswith("scr:"):
        data_parts = callback.data.split(":", 2)
        if len(data_parts) != 3:
            await callback.answer("Ошибка данных")
            return
        short_gid = data_parts[1]
        try:
            option_idx = int(data_parts[2])
        except ValueError:
            await callback.answer("Ошибка данных")
            return

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT game_id, options FROM screenshot_games WHERE game_id LIKE $1 || '%' LIMIT 1",
                short_gid
            )
        if not row:
            await callback.answer("Игра не найдена или устарела", show_alert=True)
            return
        game_id = row["game_id"]
        options = row["options"]
        if option_idx >= len(options):
            await callback.answer("Ошибка данных")
            return
        answer = options[option_idx]

    # Старый формат: screenshot:<game_id>:<answer_text>
    else:
        data_parts = callback.data.split(":", 2)
        if len(data_parts) != 3:
            await callback.answer("Ошибка данных")
            return
        game_id = data_parts[1]
        answer = data_parts[2]

    result = await check_screenshot_answer(
        user_id, game_id, answer,
        username=callback.from_user.username or callback.from_user.first_name
    )
    
    if "error" in result:
        await callback.answer(result["error"], show_alert=True)
        return
    
    is_correct = result["is_correct"]
    correct_title = result["correct_title"]
    points = result["points"]
    new_achievements = result.get("new_achievements", [])
    
    if is_correct:
        response = f"✅ Правильно! +{points} баллов\n\nЭто была игра: {correct_title}"
        await callback.answer("Правильно! 🎉", show_alert=False)
    else:
        response = f"❌ Неверно.\n\nПравильный ответ: {correct_title}"
        await callback.answer("Неверно 😔", show_alert=False)
    
    # Если есть новые достижения, добавляем их в ответ
    if new_achievements:
        response += "\n\n🏆 <b>Новые достижения:</b>\n"
        for ach in new_achievements:
            response += f"\n{ach['name']}\n{esc(ach['description'])}\n+{ach['reward']} баллов!"
    
    # Обновляем сообщение
    try:
        await callback.message.edit_caption(
            caption=f"{callback.message.caption}\n\n{response}",
            reply_markup=None
        )
    except Exception:
        # Если не получилось отредактировать, отправляем новое сообщение
        await callback.message.answer(response)
    
    # Show hint after first play
    from onboarding import show_hint
    hint_text = await show_hint(user_id, "minigame_challenge")
    if hint_text:
        await send_with_retry(lambda: callback.message.answer(hint_text))
    
    # Show hint on first achievement unlock
    if new_achievements:
        hint_text = await show_hint(user_id, "achievement_system")
        if hint_text:
            await send_with_retry(lambda: callback.message.answer(hint_text))


@router.callback_query(lambda c: c.data and c.data.startswith("pg_start:"))
async def handle_price_game_start(callback: CallbackQuery):
    """Пользователь нажал кнопку 'Угадай цену' под постом — отправляем игру в личку."""
    deal_id = callback.data.split(":", 1)[1]
    data = await get_price_game(deal_id)

    if not data or data["original_price"] <= 0:
        await callback.answer("Игра уже недоступна 😔", show_alert=True)
        return

    correct = data["original_price"]
    title = data.get("title") or "игра"
    new_price = data.get("new_price") or "?"
    discount = data.get("discount") or 0
    link = data.get("link") or ""

    # Умная генерация вариантов — правдоподобные значения рядом с правильным
    # Для аномальных скидок (>= 90%) варианты строим от original_price, не от new_price
    variants: set = {correct}
    attempts = 0
    while len(variants) < 4 and attempts < 50:
        attempts += 1
        # Диапазон ±15–50% от правильного ответа, округляем до красивых чисел
        pct = random.randint(15, 50)
        sign = random.choice([-1, 1])
        raw = correct * (1 + sign * pct / 100)
        # Округляем до ближайшего "красивого" числа (99, 149, 199, 299...)
        if raw < 100:
            fake = round(raw / 10) * 10
        elif raw < 1000:
            fake = round(raw / 50) * 50
        else:
            fake = round(raw / 100) * 100
        if fake > 0 and fake != correct:
            variants.add(fake)

    options = list(variants)
    random.shuffle(options)

    buttons = [
        InlineKeyboardButton(text=f"{p}₽", callback_data=f"pg:{deal_id}:{p}")
        for p in options
    ]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

    # Красивый текст с контекстом игры
    glitch_note = ""
    if discount >= 90:
        glitch_note = "\n⚡️ <i>Аномальная скидка — это редкость!</i>"

    text = (
        f"🎲 <b>Угадай цену!</b>\n\n"
        f"🎮 <b>{esc(title)}</b>\n"
        f"🏷 Скидка: <b>−{discount}%</b>  →  сейчас <b>{esc(new_price)}</b>{glitch_note}\n\n"
        f"Сколько стоила игра <b>до скидки</b>? 👇"
    )

    try:
        await send_with_retry(lambda: get_bot().send_message(
            callback.from_user.id, text, reply_markup=keyboard
        ))
        await callback.answer("Игра отправлена в личку! 🎮")
    except Exception:
        await callback.answer(
            "Сначала напиши боту в личку — нажми /start",
            show_alert=True
        )


@router.callback_query(lambda c: c.data and c.data.startswith("pg:"))
async def handle_price_game_answer(callback: CallbackQuery):
    """Обработать ответ на мини-игру 'угадай цену'."""
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Ошибка данных")
        return

    deal_id = parts[1]
    try:
        chosen = int(parts[2])
    except ValueError:
        await callback.answer("Ошибка данных")
        return

    data = await get_price_game(deal_id)
    if not data:
        await callback.answer("Игра уже недоступна 😔", show_alert=True)
        return

    correct = data["original_price"]
    link = data.get("link") or ""

    user_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.first_name

    # Защита от повторных ответов
    accepted = await record_price_game_answer(user_id, deal_id)
    if not accepted:
        await callback.answer("Ты уже отвечал на этот вопрос 😉", show_alert=True)
        return

    if chosen == correct:
        points = 20
        await add_score(user_id, points, correct=True, reason="price_game", username=username)
        new_achievements = await check_and_unlock_achievements(user_id)
        score_data = await get_user_score(user_id)
        balance = score_data.get("total_score", 0)

        response = (
            f"✅ <b>Верно!</b> Цена была <b>{correct}₽</b>\n"
            f"💰 +{points} баллов  ·  Баланс: <b>{balance}</b> 🏆"
        )
        if link:
            response += f"\n\n🛒 <a href=\"{link}\">Купить, пока скидка!</a>"
        if new_achievements:
            response += "\n\n🏆 <b>Новые достижения:</b>"
            for ach in new_achievements:
                response += f"\n{esc(ach['name'])} +{ach['reward']} баллов!"
        await callback.answer("Верно! 🎉", show_alert=False)
    else:
        await add_score(user_id, 0, correct=False, reason="price_game", username=username)
        response = (
            f"❌ <b>Неверно.</b> Правильный ответ: <b>{correct}₽</b>"
        )
        if link:
            response += f"\n\n🛒 <a href=\"{link}\">Всё равно посмотреть сделку</a>"
        await callback.answer("Неверно 😔", show_alert=False)

    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n{response}",
            reply_markup=None
        )
    except Exception as e:
        log.warning(f"pg answer edit failed: {e}")
        await callback.message.answer(response)


@router.message(Command("profile"))
async def cmd_profile(message: Message):
    """Показать профиль пользователя."""
    user_id = message.from_user.id
    username = message.from_user.username or "Игрок"
    
    # Получаем статистику игр
    stats = await get_user_score(user_id)
    
    # Получаем статистику вишлиста
    from database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        wishlist_count = await conn.fetchval(
            "SELECT COUNT(*) FROM wishlist WHERE user_id = $1",
            user_id
        )
        votes_count = await conn.fetchval(
            "SELECT COUNT(*) FROM votes WHERE user_id = $1",
            user_id
        )
        
        # Получаем дополнительную статистику для достижений
        extended_stats = await conn.fetchrow("""
            SELECT 
                current_streak,
                best_streak,
                daily_streak,
                screenshot_correct,
                challenges_completed
            FROM user_scores
            WHERE user_id = $1
        """, user_id)
    
    total = stats["total_score"]
    played = stats["games_played"]
    correct = stats["correct_answers"]
    accuracy = int(correct / played * 100) if played > 0 else 0
    
    # Получаем количество достижений
    from achievements import get_user_achievements
    achievements_data = await get_user_achievements(user_id)
    unlocked_count = achievements_data['unlocked_count']
    total_achievements = achievements_data['total']

    # Получаем значки
    from rewards import get_user_badges
    badges = await get_user_badges(user_id)
    badge_str = f" {badges}" if badges else ""

    text = f"""
👤 <b>Профиль: {esc(username)}{badge_str}</b>

🎮 <b>Мини-игры:</b>
⭐️ Баллы: <b>{total}</b>
🎯 Игр сыграно: <b>{played}</b>
✅ Правильных: <b>{correct}</b>
📊 Точность: <b>{accuracy}%</b>

🔥 <b>Серии:</b>
⚡️ Текущая: <b>{extended_stats['current_streak'] if extended_stats else 0}</b>
🏆 Лучшая: <b>{extended_stats['best_streak'] if extended_stats else 0}</b>
📅 Дней подряд: <b>{extended_stats['daily_streak'] if extended_stats else 0}</b>

📋 <b>Активность:</b>
💝 Игр в вишлисте: <b>{wishlist_count}</b>
🔥 Голосов: <b>{votes_count}</b>
📸 Скриншотов угадано: <b>{extended_stats['screenshot_correct'] if extended_stats else 0}</b>
🎯 Челленджей выполнено: <b>{extended_stats['challenges_completed'] if extended_stats else 0}</b>

🏆 <b>Достижения: {unlocked_count}/{total_achievements}</b>

💡 Играй в мини-игры, чтобы заработать больше баллов!
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Мои достижения", callback_data="show_achievements")],
        [InlineKeyboardButton(text="🎮 Мини-игры", callback_data="show_games")],
        [InlineKeyboardButton(text="📊 Таблица лидеров", callback_data="show_leaderboard")]
    ])
    
    await message.answer(text.strip(), reply_markup=keyboard)


@router.callback_query(lambda c: c.data == "show_games")
async def show_games_callback(callback: CallbackQuery):
    """Показать список игр через callback."""
    await callback.answer()
    await cmd_games(callback.message)


@router.callback_query(lambda c: c.data == "show_leaderboard")
async def show_leaderboard_callback(callback: CallbackQuery):
    """Показать таблицу лидеров через callback."""
    await callback.answer()
    await cmd_leaderboard(callback.message)


@router.message(Command("achievements"))
async def cmd_achievements(message: Message):
    """Показать достижения пользователя."""
    user_id = message.from_user.id
    
    from achievements import get_user_achievements, format_achievements_message
    achievements_data = await get_user_achievements(user_id)
    
    text = format_achievements_message(achievements_data)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="show_profile")],
        [InlineKeyboardButton(text="🎮 Мини-игры", callback_data="show_games")]
    ])
    
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(lambda c: c.data == "show_achievements")
async def show_achievements_callback(callback: CallbackQuery):
    """Показать достижения через callback."""
    await callback.answer()
    await cmd_achievements(callback.message)


@router.callback_query(lambda c: c.data == "show_profile")
async def show_profile_callback(callback: CallbackQuery):
    """Показать профиль через callback."""
    await callback.answer()
    await cmd_profile(callback.message)


@router.message(Command("shop"))
async def cmd_shop(message: Message):
    """Показать магазин призов с категориями."""
    from rewards import format_rewards_shop_improved, get_user_balance
    
    user_id = message.from_user.id
    balance = await get_user_balance(user_id)
    
    text = format_rewards_shop_improved(user_id, balance, category="all")
    
    # Кнопки для выбора категории
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Подписки", callback_data="shop_cat:subscriptions"),
            InlineKeyboardButton(text="🎮 Ключи", callback_data="shop_cat:games"),
        ],
        [
            InlineKeyboardButton(text="🌟 Сервисы", callback_data="shop_cat:services"),
            InlineKeyboardButton(text="⭐️ Значки", callback_data="shop_cat:badges"),
        ],
        [InlineKeyboardButton(text="📦 Мои призы", callback_data="show_my_rewards")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="show_profile")]
    ])
    
    await message.answer(text, reply_markup=keyboard)
    
    # Show hint on first shop open
    from onboarding import show_hint
    from publisher import send_with_retry
    hint_text = await show_hint(user_id, "shop_earn")
    if hint_text:
        await send_with_retry(lambda: message.answer(hint_text))


@router.message(Command("myrewards"))
async def cmd_my_rewards(message: Message):
    """Показать купленные призы пользователя с улучшенным оформлением."""
    from rewards import get_user_rewards, get_user_balance, format_user_rewards_improved
    
    user_id = message.from_user.id
    rewards = await get_user_rewards(user_id)
    balance = await get_user_balance(user_id)
    
    text = format_user_rewards_improved(rewards, balance)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏪 Магазин", callback_data="show_shop")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="show_profile")]
    ])
    
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("buy"))
async def cmd_buy(message: Message):
    """Купить приз за баллы."""
    from rewards import REWARDS_CATALOG, get_key_rewards_catalog, purchase_reward
    
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2:
        # Показываем список доступных призов с ID (статичные + ключи из БД)
        key_catalog = await get_key_rewards_catalog()
        full_catalog = {**REWARDS_CATALOG, **key_catalog}
        lines = ["🏪 <b>Доступные призы:</b>\n"]
        for reward_id, reward in full_catalog.items():
            avail = f" [{reward['available']} шт.]" if reward.get("available") else ""
            lines.append(f"<code>{reward_id}</code> — {reward['name']}{avail}")
            lines.append(f"💰 {reward['cost']} баллов\n")
        lines.append("Используй: /buy [id_приза]")
        await message.answer("\n".join(lines))
        return
    
    reward_id = args[1].strip()
    
    result = await purchase_reward(user_id, reward_id)
    
    if "error" in result:
        await message.answer(f"❌ {result['error']}")
        return
    
    reward = result["reward"]

    # Ключ выдаётся автоматически — показываем сразу
    if result.get("key_value"):
        text = (
            f"🎮 <b>{esc(result['game_title'])}</b>\n\n"
            f"Твой ключ:\n<code>{esc(result['key_value'])}</code>\n\n"
            f"Активируй в Steam: Игры → Активировать продукт\n\n"
            f"💰 Списано: <b>{result['cost']}</b> баллов\n"
            f"💳 Новый баланс: <b>{result['new_balance']}</b> баллов"
        )
        await message.answer(text)
        return

    text = f"""
✅ <b>Приз куплен!</b>

{reward['name']}
{esc(reward['description'])}

💰 Списано: <b>{result['cost']}</b> баллов
💳 Новый баланс: <b>{result['new_balance']}</b> баллов

📦 Используй /myrewards для просмотра призов
"""
    await message.answer(text.strip())


@router.message(Command("claim"))
async def cmd_claim(message: Message):
    """Активировать приз (получить ключ)."""
    from rewards import get_user_rewards
    
    user_id = message.from_user.id
    rewards = await get_user_rewards(user_id)
    
    # Ищем неактивированные ключи
    unclaimed = [r for r in rewards if not r["is_claimed"] and "ключ" in r["name"].lower()]
    
    if not unclaimed:
        await message.answer("У тебя нет неактивированных ключей.")
        return
    
    text = """
🎮 <b>Твои ключи ожидают активации</b>

Администратор скоро отправит тебе ключи в личные сообщения.
Обычно это занимает до 24 часов.

Если прошло больше времени, напиши @Joker104_97
"""
    
    await message.answer(text.strip())


@router.callback_query(lambda c: c.data == "show_shop")
async def show_shop_callback(callback: CallbackQuery):
    """Показать магазин через callback."""
    await callback.answer()
    await cmd_shop(callback.message)


@router.callback_query(lambda c: c.data.startswith("shop_cat:"))
async def show_shop_category_callback(callback: CallbackQuery):
    """Показать категорию магазина."""
    from rewards import format_rewards_shop_improved, get_user_balance, REWARDS_CATALOG
    
    await callback.answer()
    
    category = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    balance = await get_user_balance(user_id)
    
    text = format_rewards_shop_improved(user_id, balance, category=category)
    
    # Создаём кнопки для покупки призов в этой категории
    buttons = []
    for reward_id, reward in REWARDS_CATALOG.items():
        if reward.get("category") != category:
            continue
        
        # Проверяем, хватает ли баллов
        cost = reward["cost"]
        can_afford = balance >= cost
        
        # Эмодзи в зависимости от доступности
        emoji = reward["emoji"] if can_afford else "🔒"
        
        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji} {reward['name']} • {cost}💰",
                callback_data=f"buy:{reward_id}"
            )
        ])
    
    # Добавляем кнопки навигации
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="show_shop")])
    buttons.append([InlineKeyboardButton(text="📦 Мои призы", callback_data="show_my_rewards")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await callback.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(lambda c: c.data == "show_my_rewards")
async def show_my_rewards_callback(callback: CallbackQuery):
    """Показать призы через callback."""
    await callback.answer()
    await cmd_my_rewards(callback.message)


@router.callback_query(lambda c: c.data and c.data.startswith("buy:"))
async def buy_reward_callback(callback: CallbackQuery):
    """Купить приз через callback с подтверждением."""
    from rewards import REWARDS_CATALOG, get_user_balance
    
    await callback.answer()
    
    reward_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    
    # Проверяем, существует ли приз
    if reward_id not in REWARDS_CATALOG:
        await callback.message.answer("❌ Приз не найден")
        return
    
    reward = REWARDS_CATALOG[reward_id]
    balance = await get_user_balance(user_id)
    
    # Показываем подтверждение покупки
    text = f"""
🛒 <b>Подтверждение покупки</b>

{reward['emoji']} <b>{reward['name']}</b>
{esc(reward['description'])}

💰 Стоимость: <b>{reward['cost']}</b> баллов
💳 Твой баланс: <b>{balance}</b> баллов
"""
    
    if balance < reward['cost']:
        text += f"\n❌ Недостаточно баллов (не хватает {reward['cost'] - balance})"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="show_shop")]
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Купить", callback_data=f"confirm_buy:{reward_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="show_shop"),
            ]
        ])
    
    await callback.message.edit_text(text.strip(), reply_markup=keyboard)


@router.callback_query(lambda c: c.data and c.data.startswith("confirm_buy:"))
async def confirm_buy_callback(callback: CallbackQuery):
    """Подтвердить покупку приза."""
    from rewards import purchase_reward, REWARDS_CATALOG
    
    await callback.answer()
    
    reward_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    
    result = await purchase_reward(user_id, reward_id)
    
    if "error" in result:
        text = f"❌ {result['error']}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад в магазин", callback_data="show_shop")]
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        return
    
    reward = result["reward"]

    # Ключ выдаётся автоматически — показываем сразу
    if result.get("key_value"):
        text = (
            f"🎮 <b>{esc(result['game_title'])}</b>\n\n"
            f"Твой ключ:\n<code>{esc(result['key_value'])}</code>\n\n"
            f"Активируй в Steam: Игры → Активировать продукт\n\n"
            f"💰 Списано: <b>{result['cost']}</b> баллов\n"
            f"💳 Новый баланс: <b>{result['new_balance']}</b> баллов"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏪 Магазин", callback_data="show_shop")]
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
        return

    text = f"""
✅ <b>Приз куплен!</b>

{reward['emoji']} {reward['name']}
{esc(reward['description'])}

💰 Списано: <b>{result['cost']}</b> баллов
💳 Новый баланс: <b>{result['new_balance']}</b> баллов
"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Мои призы", callback_data="show_my_rewards")],
        [InlineKeyboardButton(text="🏪 Магазин", callback_data="show_shop")]
    ])
    
    await callback.message.edit_text(text.strip(), reply_markup=keyboard)


@router.message(Command("invite"))
async def cmd_invite(message: Message):
    """Показать реферальную ссылку и статистику."""
    from referral import get_referral_stats, format_referral_message, ensure_referral_code_registered
    
    user_id = message.from_user.id
    
    bot_username = (await message.bot.get_me()).username
    
    # Регистрируем код в БД, чтобы его можно было декодировать
    await ensure_referral_code_registered(user_id)
    
    stats = await get_referral_stats(user_id)
    text = format_referral_message(user_id, bot_username, stats)
    
    from referral import get_referral_link
    link = get_referral_link(user_id, bot_username)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📤 Поделиться ссылкой",
            switch_inline_query=f"Присоединяйся к боту со скидками на игры! {link}"
        )],
        [InlineKeyboardButton(text="📊 Топ рефереров", callback_data="show_top_referrers")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="show_profile")],
    ])

    await message.answer(text, reply_markup=keyboard)


@router.callback_query(lambda c: c.data == "show_top_referrers")
async def show_top_referrers_callback(callback: CallbackQuery):
    """Показать топ рефереров."""
    from referral import get_top_referrers, REFERRER_BONUS
    
    top = await get_top_referrers(10)
    
    if not top:
        await callback.answer("Пока никто не пригласил друзей", show_alert=True)
        return
    
    lines = ["🏆 <b>Топ рефереров</b>\n"]
    
    medals = ["🥇", "🥈", "🥉"]
    for i, ref in enumerate(top, 1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        count = ref["referral_count"]
        earned = ref["total_earned"]
        lines.append(f"{medal} <b>{count}</b> друзей ({earned} баллов)")
    
    lines.append(f"\n💡 Приглашай друзей: /invite")
    
    await callback.answer()
    await callback.message.answer("\n".join(lines))
