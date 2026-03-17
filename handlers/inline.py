"""
Inline-режим: поиск скидок через @bot в любом чате.
"""
import logging
from html import escape

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from database import search_posted_deals, get_weekly_top
from config import CHANNEL_ID

router = Router()
log = logging.getLogger(__name__)

STORE_EMOJI = {"Steam": "🎮", "Epic Games": "🎁"}


def _store_emoji(store: str) -> str:
    return STORE_EMOJI.get(store, "🕹")


def _format_share_text(deal: dict) -> str:
    emoji = _store_emoji(deal["store"])
    lines = [
        f"{emoji} <b>{escape(deal['title'])}</b>",
        f"🏷 Скидка: <b>−{deal['discount']}%</b>  ·  {escape(deal['store'])}",
    ]
    if deal.get("link"):
        lines.append(f"🛒 {deal['link']}")
    lines.append("\n📢 Больше скидок: @GameDealsRadarRu")
    return "\n".join(lines)


@router.inline_query()
async def handle_inline_query(query: InlineQuery):
    text = query.query.strip()

    # Пустой запрос — показываем топ скидок за неделю
    if len(text) < 2:
        deals = await get_weekly_top(limit=10)
        if not deals:
            await query.answer(
                [],
                cache_time=60,
                switch_pm_text="Открыть бота",
                switch_pm_parameter="start",
            )
            return
    else:
        deals = await search_posted_deals(text, limit=20)

    results = []
    for deal in deals:
        emoji = _store_emoji(deal["store"])
        share_text = _format_share_text(deal)

        keyboard = None
        if deal.get("link"):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text=f"🛒 Открыть в {deal['store']}",
                    url=deal["link"]
                )
            ]])

        results.append(InlineQueryResultArticle(
            id=deal["deal_id"][:64],
            title=f"{emoji} {deal['title']}",
            description=f"−{deal['discount']}%  ·  {deal['store']}",
            input_message_content=InputTextMessageContent(
                message_text=share_text,
                parse_mode="HTML",
            ),
            reply_markup=keyboard,
        ))

    await query.answer(
        results,
        cache_time=30,
        is_personal=False,
        switch_pm_text="🔍 Искать скидки",
        switch_pm_parameter="start",
    )
