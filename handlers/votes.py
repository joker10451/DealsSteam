from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from database import add_vote, get_votes, increment_metric

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

    # Восстанавливаем все кнопки из существующей клавиатуры, обновляя только счётчики
    try:
        existing = callback.message.reply_markup
        if existing:
            new_rows = []
            for row in existing.inline_keyboard:
                new_row = []
                for btn in row:
                    cb = getattr(btn, "callback_data", None)
                    if cb and cb.startswith("vote:fire:"):
                        new_row.append(InlineKeyboardButton(
                            text=f"🔥 {counts['fire']}", callback_data=cb
                        ))
                    elif cb and cb.startswith("vote:poop:"):
                        new_row.append(InlineKeyboardButton(
                            text=f"💩 {counts['poop']}", callback_data=cb
                        ))
                    else:
                        new_row.append(btn)
                new_rows.append(new_row)
            await callback.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup(inline_keyboard=new_rows)
            )
    except Exception:
        pass

    await callback.answer("🔥 Огонь!" if vote_type == "fire" else "💩 Мимо", show_alert=False)
