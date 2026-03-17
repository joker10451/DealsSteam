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
        end_time = _to_msk(g["end_time"])
        end_str = end_time.strftime("%d.%m %H:%M")
        
        prize_emoji = {
            "steam_key": "🎮",
            "points": "⭐",
            "subscription": "👑"
        }.get(g["prize_type"], "🎁")

        channel_link = ""
        if g.get("channel_post_id"):
            from config import CHANNEL_ID
            channel_username = str(CHANNEL_ID).lstrip("-100")
            channel_link = f"\n🔗 <a href=\"https://t.me/GameDealsRadarRu/{g['channel_post_id']}\">Перейти к розыгрышу</a>"
        
        lines.append(
            f"{prize_emoji} <b>{esc(g['title'])}</b>\n"
            f"👥 Участников: {g['participants_count']}\n"
            f"⏰ До: {end_str} МСК{channel_link}\n"
        )
    
    lines.append("\n<i>Участвуй через кнопки в постах канала!</i>")
    
    await message.answer("\n".join(lines))


def _to_msk(dt) -> "datetime":
    """Безопасно конвертировать datetime в MSK: если уже aware — конвертируем, иначе replace."""
    import pytz
    MSK = pytz.timezone("Europe/Moscow")
    if dt.tzinfo is not None:
        return dt.astimezone(MSK)
    return dt.replace(tzinfo=MSK)


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
            "<code>/creategiveaway [название] | [описание] | [тип] | [значение] | [часы] | [мин. возраст дней]</code>\n\n"
            "Типы призов:\n"
            "• <code>steam_key</code> - Steam ключ\n"
            "• <code>points</code> - Баллы\n"
            "• <code>subscription</code> - Подписка/приз из магазина\n\n"
            "Последний параметр необязателен (по умолчанию 7 дней, 0 = без ограничений)\n\n"
            "Примеры:\n"
            "<code>/creategiveaway Portal 2 | Классика от Valve | steam_key | XXXXX-XXXXX-XXXXX | 72</code>\n"
            "<code>/creategiveaway Portal 2 | Классика от Valve | steam_key | XXXXX-XXXXX-XXXXX | 72 | 0</code>\n"
            "<code>/creategiveaway 1000 баллов | Потрать в магазине | points | 1000 | 48 | 0</code>"
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
        min_account_age_days = int(parts[5]) if len(parts) >= 6 else 7
        
        if prize_type not in ["steam_key", "points", "subscription"]:
            await message.answer("❌ Неверный тип приза")
            return
        
        from giveaways import create_giveaway, publish_giveaway
        
        giveaway_id = await create_giveaway(
            title=title,
            description=description,
            prize_type=prize_type,
            prize_value=prize_value,
            duration_hours=duration_hours,
            min_account_age_days=min_account_age_days
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


@router.message(Command("deletegiveaway"))
async def cmd_delete_giveaway(message: Message):
    """Удалить конкурс (только админ)."""
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование: <code>/deletegiveaway [giveaway_id]</code>\n\n"
            "Пример: <code>/deletegiveaway giveaway_1234567890</code>"
        )
        return

    giveaway_id = args[1].strip()
    from giveaways import delete_giveaway

    status_msg = await message.answer("🔄 Удаляю конкурс...")
    try:
        success, msg = await delete_giveaway(giveaway_id)
        await status_msg.edit_text("✅ Конкурс удалён!" if success else f"❌ {esc(msg)}")
    except Exception as e:
        log.error(f"Ошибка удаления конкурса: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {esc(str(e))}")


@router.message(Command("giveawayhistory"))
async def cmd_giveaway_history(message: Message):
    """История завершённых конкурсов (только админ)."""
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return

    from database import get_pool
    import pytz
    MSK = pytz.timezone("Europe/Moscow")

    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT g.*, COUNT(p.user_id) as participants_count
        FROM giveaways g
        LEFT JOIN giveaway_participants p ON g.giveaway_id = p.giveaway_id
        WHERE g.status = 'ended'
        GROUP BY g.giveaway_id
        ORDER BY g.end_time DESC
        LIMIT 10
    """)

    if not rows:
        await message.answer("📭 Завершённых конкурсов пока нет.")
        return

    lines = ["📋 <b>История конкурсов (последние 10):</b>\n"]
    bot = message.bot
    for r in rows:
        end_str = _to_msk(r["end_time"]).strftime("%d.%m.%Y")
        winner_str = "нет участников"
        if r["winner_user_id"]:
            try:
                winner = await bot.get_chat(r["winner_user_id"])
                winner_str = f"@{winner.username}" if winner.username else f'<a href="tg://user?id={r["winner_user_id"]}">{esc(winner.first_name)}</a>'
            except Exception:
                winner_str = f"id:{r['winner_user_id']}"

        prize_emoji = {"steam_key": "🎮", "points": "⭐", "subscription": "👑"}.get(r["prize_type"], "🎁")
        lines.append(
            f"{prize_emoji} <b>{esc(r['title'])}</b> ({end_str})\n"
            f"👥 {r['participants_count']} уч. | 🏆 {winner_str}\n"
        )

    await message.answer("\n".join(lines))


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
    from database import get_pool

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
                end_str = _to_msk(end_time).strftime("%d.%m.%Y %H:%M МСК")
                
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
                    f"🎲 <b>Больше друзей = больше шансов!</b>\n"
                    f"За каждого приглашённого друга ты получаешь +1 дополнительный шанс на победу.\n"
                    f"Своя ссылка — в боте: /invite\n\n"
                    f"<i>Нажми кнопку ниже для участия!</i>"
                )
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🎲 Участвовать",
                        callback_data=f"giveaway_join:{giveaway_id}"
                    )],
                    [InlineKeyboardButton(
                        text="📢 Поделиться",
                        switch_inline_query=f"🎁 Розыгрыш {esc(giveaway['title'])}! Участвуй → t.me/GameDealsRadarRu"
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


@router.message(Command("giveawaystat"))
async def cmd_giveaway_stat(message: Message):
    """Статистика участников конкурса (только админ)."""
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование: <code>/giveawaystat [giveaway_id]</code>\n\n"
            "Пример: <code>/giveawaystat giveaway_1234567890</code>"
        )
        return

    giveaway_id = args[1].strip()

    from database import get_pool
    from giveaways import get_giveaway_participants

    pool = await get_pool()
    giveaway = await pool.fetchrow("SELECT * FROM giveaways WHERE giveaway_id = $1", giveaway_id)

    if not giveaway:
        await message.answer("❌ Конкурс не найден")
        return

    participants = await get_giveaway_participants(giveaway_id)

    if not participants:
        await message.answer(f"👥 Участников пока нет в конкурсе <code>{giveaway_id}</code>")
        return

    # Считаем шансы каждого
    rows = []
    total_slots = 0
    for user_id in participants:
        referral_count = await pool.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = $1", user_id
        ) or 0
        slots = 1 + int(referral_count)
        total_slots += slots
        rows.append((user_id, slots, int(referral_count)))

    rows.sort(key=lambda x: x[1], reverse=True)

    lines = [
        f"📊 <b>{esc(giveaway['title'])}</b>\n"
        f"👥 Участников: {len(participants)} | Всего слотов: {total_slots}\n"
    ]

    for user_id, slots, refs in rows[:30]:  # показываем топ 30
        chance = slots / total_slots * 100
        ref_str = f" (+{refs} реф.)" if refs > 0 else ""
        try:
            user = await message.bot.get_chat(user_id)
            if user.username:
                name = f"@{user.username}"
            else:
                display = esc(user.first_name or str(user_id))
                name = f'<a href="tg://user?id={user_id}">{display}</a>'
        except Exception:
            name = str(user_id)
        lines.append(f"• {name}{ref_str} — {slots} шанс. ({chance:.1f}%)")

    if len(participants) > 30:
        lines.append(f"\n<i>...и ещё {len(participants) - 30} участников</i>")

    await message.answer("\n".join(lines))


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
        end_time = _to_msk(r["end_time"])
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
