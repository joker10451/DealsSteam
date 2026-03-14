from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from database import add_vote, get_votes, get_price_game, increment_metric

router = Router()


@router.callback_query(F.data.startswith("vote:"))
async def handle_vote(callback: CallbackQuery):
    _, vote_type, deal_id = callback.data.split(":", 2)
    saved = await add_vote(deal_id, callback.from_user.id, vote_type)

    if not saved:
        await callback.answer("Ты уже голосовал за эту игру!", show_alert=False)
        return

    counts = await get_votes(deal_id)
    await increment_metric(f"vote_{vote_type}")

    # Сохраняем кнопку ссылки на магазин при обновлении счётчиков
    store_url = None
    store_name = None
    try:
        existing = callback.message.reply_markup
        if existing:
            for row in existing.inline_keyboard:
                for btn in row:
                    if getattr(btn, "url", None):
                        store_url = btn.url
                        store_name = btn.text.replace("🛒 Открыть в ", "")
                        break
    except Exception:
        pass

    rows = []
    if store_url and store_name:
        rows.append([InlineKeyboardButton(text=f"🛒 Открыть в {store_name}", url=store_url)])
    rows.append([
        InlineKeyboardButton(text=f"🔥 {counts['fire']}", callback_data=f"vote:fire:{deal_id}"),
        InlineKeyboardButton(text=f"💩 {counts['poop']}", callback_data=f"vote:poop:{deal_id}"),
    ])

    try:
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        pass

    await callback.answer("🔥 Огонь!" if vote_type == "fire" else "💩 Мимо", show_alert=False)


@router.callback_query(F.data.startswith("pg:"))
async def handle_price_game(callback: CallbackQuery):
    parts = callback.data.split(":")
    deal_id = parts[1]
    chosen = int(parts[2])

    correct = await get_price_game(deal_id)
    if correct is None:
        await callback.answer("Игра уже закончилась!", show_alert=False)
        return

    is_correct = (chosen == correct)
    
    # Начисляем баллы за игру
    from minigames import add_score
    user_id = callback.from_user.id
    points = 5 if is_correct else 0
    new_achievements = await add_score(user_id, points, is_correct)
    
    # Формируем ответ
    if is_correct:
        answer_text = f"✅ Правильно! Цена была {correct}₽\n+{points} баллов"
    else:
        answer_text = f"❌ Неверно. Правильный ответ: {correct}₽"
    
    # Если есть новые достижения, добавляем их
    if new_achievements:
        answer_text += f"\n\n🏆 Новое достижение!\n{new_achievements[0]['name']}"
    
    await callback.answer(answer_text, show_alert=True)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
