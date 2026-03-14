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
        await message.answer(f"⏳ Подожди ещё {remaining} сек. перед следующей публикацией.")
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
            f"Приятной игры! 🎉"
        )
        
        # Отмечаем приз как выданный
        from database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                UPDATE user_rewards
                SET is_claimed = TRUE
                WHERE user_id = $1 
                AND reward_id LIKE 'steam_key_%'
                AND is_claimed = FALSE
                ORDER BY purchased_at DESC
                LIMIT 1
            """, user_id)
        
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
        await conn.execute("""
            INSERT INTO user_scores (user_id, total_score)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET total_score = user_scores.total_score + $2
        """, user_id, points)
    
    await message.answer(f"✅ Начислено {points} баллов пользователю {user_id}")


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
