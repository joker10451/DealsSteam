"""
Обработчики команд для системы конкурсов.
"""
import logging
from html import escape as esc

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMIN_ID

log = logging.getLogger(__name__)
router = Router()


def _admin_only(message: Message) -> bool:
    return message.from_user.id == ADMIN_ID


@router.message(Command("giveaway"))
async def cmd_giveaway(message: Message):
    """Показать активные конкурсы."""
    from giveaways import get_active_giveaways
    from datetime import datetime
    import pytz
    
    MSK = pytz.timezone("Europe/Moscow")
    giveaways = await get_active_giveaways()
    
    if not giveaways:
        await message.answer(
            "🎁 Сейчас нет активных конкурсов\n\n"
            "Следи за каналом — скоро будут новые розыгрыши!"
        )
        return
    
    lines = ["🎁 <b>Активные конкурсы:</b>\n"]
    
    for g in giveaways:
        end_time = g["end_time"].replace(tzinfo=MSK)
        end_str = end_time.strftime("%d.%m %H:%M")
        
        prize_emoji = {
            "steam_key": "🎮",
            "points": "⭐",
            "subscription": "👑"
        }.get(g["prize_type"], "🎁")
        
        lines.append(
            f"{prize_emoji} <b>{esc(g['title'])}</b>\n"
            f"👥 Участников: {g['participants_count']}\n"
            f"⏰ До: {end_str} МСК\n"
        )
    
    lines.append("\n<i>Участвуй через кнопки в постах канала!</i>")
    
    await message.answer("\n".join(lines))


@router.message(Command("creategiveaway"))
async def cmd_create_giveaway(message: Message):
    """Создать конкурс (только админ)."""
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование:\n"
            "<code>/creategiveaway [название] | [описание] | [тип] | [значение] | [часы]</code>\n\n"
            "Типы призов:\n"
            "• <code>steam_key</code> - Steam ключ\n"
            "• <code>points</code> - Баллы\n"
            "• <code>subscription</code> - Подписка/приз из магазина\n\n"
            "Примеры:\n"
            "<code>/creategiveaway Portal 2 | Классика от Valve | steam_key | XXXXX-XXXXX-XXXXX | 72</code>\n"
            "<code>/creategiveaway 1000 баллов | Потрать в магазине | points | 1000 | 48</code>\n"
            "<code>/creategiveaway VIP статус | Месяц VIP | subscription | vip_badge | 72</code>"
        )
        return
    
    try:
        parts = [p.strip() for p in args[1].split("|")]
        if len(parts) < 5:
            await message.answer("❌ Не хватает параметров")
            return
        
        title = parts[0]
        description = parts[1]
        prize_type = parts[2]
        prize_value = parts[3]
        duration_hours = int(parts[4])
        
        if prize_type not in ["steam_key", "points", "subscription"]:
            await message.answer("❌ Неверный тип приза")
            return
        
        from giveaways import create_giveaway, publish_giveaway
        
        giveaway_id = await create_giveaway(
            title=title,
            description=description,
            prize_type=prize_type,
            prize_value=prize_value,
            duration_hours=duration_hours
        )
        
        # Публикуем в канале
        msg_id = await publish_giveaway(giveaway_id)
        
        if msg_id:
            await message.answer(
                f"✅ Конкурс создан и опубликован!\n\n"
                f"ID: <code>{giveaway_id}</code>\n"
                f"Приз: {esc(title)}\n"
                f"Длительность: {duration_hours}ч\n\n"
                f"Завершится автоматически."
            )
        else:
            await message.answer(
                f"✅ Конкурс создан: <code>{giveaway_id}</code>\n"
                f"❌ Ошибка публикации в канале"
            )
        
    except Exception as e:
        log.error(f"Ошибка создания конкурса: {e}")
        await message.answer(f"❌ Ошибка: {esc(str(e))}")


@router.message(Command("endgiveaway"))
async def cmd_end_giveaway(message: Message):
    """Завершить конкурс досрочно (только админ)."""
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование: <code>/endgiveaway [giveaway_id]</code>\n\n"
            "Пример: <code>/endgiveaway giveaway_1234567890</code>"
        )
        return
    
    giveaway_id = args[1].strip()
    
    from giveaways import end_giveaway
    
    status_msg = await message.answer("🔄 Завершаю конкурс...")
    
    try:
        success = await end_giveaway(giveaway_id)
        
        if success:
            await status_msg.edit_text("✅ Конкурс завершён, победитель выбран!")
        else:
            await status_msg.edit_text("❌ Ошибка завершения конкурса")
    
    except Exception as e:
        log.error(f"Ошибка завершения конкурса: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {esc(str(e))}")


@router.callback_query(lambda c: c.data and c.data.startswith("giveaway_join:"))
async def callback_giveaway_join(callback: CallbackQuery):
    """Обработка нажатия кнопки участия в конкурсе."""
    giveaway_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    
    from giveaways import join_giveaway
    from database import get_pool, create_onboarding_progress
    
    # Создаём запись пользователя если её нет
    await create_onboarding_progress(user_id)
    
    success, msg = await join_giveaway(giveaway_id, user_id)
    
    if success:
        # Обновляем счётчик участников в посте
        try:
            pool = await get_pool()
            giveaway = await pool.fetchrow(
                "SELECT * FROM giveaways WHERE giveaway_id = $1", giveaway_id
            )
            
            if giveaway and giveaway["channel_post_id"]:
                from giveaways import get_giveaway_participants
                participants = await get_giveaway_participants(giveaway_id)
                count = len(participants)
                
                end_time = giveaway["end_time"]
                import pytz
                MSK = pytz.timezone("Europe/Moscow")
                end_str = end_time.replace(tzinfo=MSK).strftime("%d.%m.%Y %H:%M МСК")
                
                prize_emoji = {
                    "steam_key": "🎮",
                    "points": "⭐",
                    "subscription": "👑"
                }.get(giveaway["prize_type"], "🎁")
                
                updated_text = (
                    f"🎁 <b>РОЗЫГРЫШ!</b>\n\n"
                    f"{prize_emoji} <b>{esc(giveaway['title'])}</b>\n\n"
                    f"{esc(giveaway['description'])}\n\n"
                    f"📅 Розыгрыш: <b>{end_str}</b>\n"
                    f"👥 Участников: <b>{count}</b>\n\n"
                    f"<i>Нажми кнопку ниже для участия!</i>"
                )
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🎲 Участвовать",
                        callback_data=f"giveaway_join:{giveaway_id}"
                    )]
                ])
                
                from config import CHANNEL_ID
                from publisher import get_bot
                bot = get_bot()
                
                await bot.edit_message_text(
                    updated_text,
                    CHANNEL_ID,
                    giveaway["channel_post_id"],
                    reply_markup=keyboard
                )
        except Exception as e:
            log.warning(f"Не удалось обновить счётчик: {e}")
        
        await callback.answer(msg, show_alert=True)
    else:
        await callback.answer(msg, show_alert=True)


@router.message(Command("mygiveaways"))
async def cmd_my_giveaways(message: Message):
    """Показать конкурсы, в которых участвует пользователь."""
    from database import get_pool
    import pytz
    
    MSK = pytz.timezone("Europe/Moscow")
    user_id = message.from_user.id
    
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT g.*, p.joined_at
        FROM giveaways g
        JOIN giveaway_participants p ON g.giveaway_id = p.giveaway_id
        WHERE p.user_id = $1 AND g.status = 'active'
        ORDER BY g.end_time ASC
    """, user_id)
    
    if not rows:
        await message.answer(
            "🎁 Ты пока не участвуешь в конкурсах\n\n"
            "Следи за каналом и жми кнопку «Участвовать»!"
        )
        return
    
    lines = ["🎁 <b>Твои конкурсы:</b>\n"]
    
    for r in rows:
        end_time = r["end_time"].replace(tzinfo=MSK)
        end_str = end_time.strftime("%d.%m %H:%M")
        
        prize_emoji = {
            "steam_key": "🎮",
            "points": "⭐",
            "subscription": "👑"
        }.get(r["prize_type"], "🎁")
        
        lines.append(
            f"{prize_emoji} <b>{esc(r['title'])}</b>\n"
            f"⏰ До: {end_str} МСК\n"
        )
    
    lines.append("\n<i>Удачи! 🍀</i>")
    
    await message.answer("\n".join(lines))
