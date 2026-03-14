import logging
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import ADMIN_ID
from database import get_metrics_summary

log = logging.getLogger(__name__)
router = Router()


def esc(text: str) -> str:
    return escape(str(text))


def _admin_only(message: Message) -> bool:
    return message.from_user.id == ADMIN_ID


@router.message(Command("post"))
async def cmd_post(message: Message):
    if not _admin_only(message):
        await message.answer("⛔ Нет доступа.")
        return
    from scheduler import check_and_post
    import server
    status_msg = await message.answer("🔄 Запускаю публикацию...")
    try:
        post_time = await check_and_post()
        if post_time:
            server.last_post_time = post_time
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
        "vote_fire": "🔥 Голосов огонь",
        "vote_poop": "💩 Голосов мимо",
    }
    lines = ["📊 <b>Метрики за 7 дней:</b>\n"]
    for row in rows:
        label = labels.get(row["event"], row["event"])
        lines.append(f"{label}: <b>{row['total']}</b>")
    await message.answer("\n".join(lines))
