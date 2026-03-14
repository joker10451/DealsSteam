"""
Обработчики для мини-игр и челленджей.
"""
from html import escape
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from minigames import (
    get_user_score, get_leaderboard, check_screenshot_answer,
    get_daily_challenge, complete_daily_challenge
)

router = Router()


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

    lines = ["🏆 <b>Таблица лидеров</b>\n"]
    
    medals = ["🥇", "🥈", "🥉"]
    for i, leader in enumerate(leaders, 1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        score = leader["total_score"]
        games = leader["games_played"]
        badges = await get_user_badges(leader["user_id"])
        badge_str = f" {badges}" if badges else ""
        lines.append(f"{medal}{badge_str} <b>{score}</b> баллов ({games} игр)")
    
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


@router.message(Command("challenge"))
async def cmd_challenge(message: Message):
    """Показать челлендж дня и прогресс."""
    from daily_challenges import check_challenge_progress, format_challenge_message
    
    challenge = await get_daily_challenge()
    
    if not challenge:
        await message.answer(
            "🎯 Челлендж дня ещё не готов.\n"
            "Попробуй позже!"
        )
        return
    
    # Получаем прогресс пользователя
    progress = await check_challenge_progress(message.from_user.id)
    
    # Форматируем сообщение
    text = format_challenge_message(challenge)
    
    # Добавляем прогресс
    if progress:
        text += f"\n\n📊 <b>Твой прогресс:</b>\n"
        if progress.get("completed"):
            text += "✅ Челлендж выполнен!"
        else:
            text += f"{progress.get('message', 'В процессе...')}\n"
            if "current" in progress and "target" in progress:
                bar_length = 10
                filled = int((progress["progress"] / 100) * bar_length)
                bar = "█" * filled + "░" * (bar_length - filled)
                text += f"[{bar}] {progress['progress']}%"
    
    await message.answer(text.strip())


@router.callback_query(lambda c: c.data and c.data.startswith("screenshot:"))
async def handle_screenshot_answer(callback: CallbackQuery):
    """Обработать ответ на игру со скриншотом."""
    user_id = callback.from_user.id
    data_parts = callback.data.split(":", 2)
    
    if len(data_parts) != 3:
        await callback.answer("Ошибка данных")
        return
    
    game_id = data_parts[1]
    answer = data_parts[2]
    
    result = await check_screenshot_answer(user_id, game_id, answer)
    
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
    from publisher import send_with_retry
    hint_text = await show_hint(user_id, "minigame_challenge")
    if hint_text:
        await send_with_retry(lambda: callback.message.answer(hint_text))
    
    # Show hint on first achievement unlock
    if new_achievements:
        hint_text = await show_hint(user_id, "achievement_system")
        if hint_text:
            await send_with_retry(lambda: callback.message.answer(hint_text))


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
    from rewards import REWARDS_CATALOG, purchase_reward
    
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2:
        # Показываем список доступных призов с ID
        lines = ["🏪 <b>Доступные призы:</b>\n"]
        for reward_id, reward in REWARDS_CATALOG.items():
            lines.append(f"<code>{reward_id}</code> — {reward['name']}")
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
    text = f"""
✅ <b>Приз куплен!</b>

{reward['name']}
{esc(reward['description'])}

💰 Списано: <b>{result['cost']}</b> баллов
💳 Новый баланс: <b>{result['new_balance']}</b> баллов

📦 Используй /myrewards для просмотра призов
"""
    
    # Если это ключ, уведомляем админа
    if "steam_key" in reward_id:
        from publisher import notify_admin
        username = message.from_user.username or f"ID: {user_id}"
        await notify_admin(
            f"🎮 Новая покупка ключа!\n\n"
            f"Пользователь: @{username}\n"
            f"Приз: {reward['name']}\n"
            f"Стоимость: {result['cost']} баллов\n\n"
            f"Используй /givekey {user_id} [ключ] для выдачи"
        )
    
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
    text = f"""
✅ <b>Приз куплен!</b>

{reward['emoji']} {reward['name']}
{esc(reward['description'])}

💰 Списано: <b>{result['cost']}</b> баллов
💳 Новый баланс: <b>{result['new_balance']}</b> баллов
"""
    
    # Если это ключ, уведомляем админа
    if "steam_key" in reward_id:
        from publisher import notify_admin
        username = callback.from_user.username or f"ID: {user_id}"
        await notify_admin(
            f"🎮 Новая покупка ключа!\n\n"
            f"Пользователь: @{username}\n"
            f"Приз: {reward['name']}\n"
            f"Стоимость: {result['cost']} баллов\n\n"
            f"Используй /givekey {user_id} [ключ] для выдачи"
        )
        text += "\n📬 Администратор скоро отправит тебе ключ в личные сообщения."
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Мои призы", callback_data="show_my_rewards")],
        [InlineKeyboardButton(text="🏪 Магазин", callback_data="show_shop")]
    ])
    
    await callback.message.edit_text(text.strip(), reply_markup=keyboard)


@router.message(Command("invite"))
async def cmd_invite(message: Message):
    """Показать реферальную ссылку и статистику."""
    from referral import get_referral_stats, format_referral_message
    from config import BOT_TOKEN
    
    user_id = message.from_user.id
    
    # Получаем username бота из токена
    bot_username = (await message.bot.get_me()).username
    
    stats = await get_referral_stats(user_id)
    text = format_referral_message(user_id, bot_username, stats)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Топ рефереров", callback_data="show_top_referrers")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="show_profile")]
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
