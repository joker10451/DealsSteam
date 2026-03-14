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
    
    lines = ["🏆 <b>Таблица лидеров</b>\n"]
    
    medals = ["🥇", "🥈", "🥉"]
    for i, leader in enumerate(leaders, 1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        score = leader["total_score"]
        games = leader["games_played"]
        lines.append(f"{medal} <b>{score}</b> баллов ({games} игр)")
    
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

━━━━━━━━━━━━━━

📊 Твоя статистика: /score
🏆 Таблица лидеров: /leaderboard
🎯 Челлендж дня: /challenge
"""
    await message.answer(text.strip())


@router.message(Command("challenge"))
async def cmd_challenge(message: Message):
    """Показать челлендж дня."""
    challenge = await get_daily_challenge()
    
    if not challenge:
        await message.answer(
            "🎯 Челлендж дня ещё не готов.\n"
            "Попробуй позже!"
        )
        return
    
    challenge_type = challenge["type"]
    data = challenge["data"]
    
    if challenge_type == "find_cheapest":
        # Челлендж: найти самую дешёвую игру
        text = f"""
🎯 <b>Челлендж дня</b>

<b>Задание:</b> Найди самую дешёвую игру дня!

Посмотри все скидки в канале и найди игру с самой низкой ценой.
Напиши боту название этой игры.

Награда: <b>50 баллов</b> 🏆

Подсказка: цена должна быть меньше {data.get('hint', '100')}₽
"""
    else:
        text = "🎯 Челлендж дня скоро появится!"
    
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
    
    text = f"""
👤 <b>Профиль: {esc(username)}</b>

━━━━━━━━━━━━━━

🎮 <b>Мини-игры:</b>
⭐️ Баллы: <b>{total}</b>
🎯 Игр сыграно: <b>{played}</b>
✅ Правильных: <b>{correct}</b>
📊 Точность: <b>{accuracy}%</b>

━━━━━━━━━━━━━━

🔥 <b>Серии:</b>
⚡️ Текущая: <b>{extended_stats['current_streak'] if extended_stats else 0}</b>
🏆 Лучшая: <b>{extended_stats['best_streak'] if extended_stats else 0}</b>
📅 Дней подряд: <b>{extended_stats['daily_streak'] if extended_stats else 0}</b>

━━━━━━━━━━━━━━

📋 <b>Активность:</b>
💝 Игр в вишлисте: <b>{wishlist_count}</b>
🔥 Голосов: <b>{votes_count}</b>
📸 Скриншотов угадано: <b>{extended_stats['screenshot_correct'] if extended_stats else 0}</b>
🎯 Челленджей выполнено: <b>{extended_stats['challenges_completed'] if extended_stats else 0}</b>

━━━━━━━━━━━━━━

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
