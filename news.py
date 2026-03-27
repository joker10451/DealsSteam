"""
Публикация новостей в стиле Steam Community.
"""

import logging
from html import escape
from typing import Optional

from config import CHANNEL_ID, ADMIN_ID
from publisher import get_bot, send_with_retry

log = logging.getLogger(__name__)


def esc(text: str) -> str:
    return escape(str(text))


async def publish_news(
    title: str,
    features: list[str],
    intro: str = "",
    link: str = None,
    link_text: str = "Подробнее",
    photo_url: str = None,
    target_chat_id: Optional[int] = None,
) -> bool:
    """
    Публикует новость в канал или указанный чат.

    Args:
        title: Заголовок новости
        features: Список фич/пунктов (3-6 штук)
        intro: Вступление перед списком (опционально)
        link: Ссылка на источник (опционально)
        link_text: Текст кнопки ссылки
        photo_url: URL картинки (опционально)
        target_chat_id: ID чата для отправки (None = канал)

    Returns:
        True если опубликовано успешно
    """
    if not title or not features:
        log.error("Новость без заголовка или фич")
        return False

    chat_id = target_chat_id if target_chat_id is not None else CHANNEL_ID

    lines = []

    lines.append(f"🎮 <b>{esc(title)}</b>")

    if intro:
        lines.append(f"\n{esc(intro)}")

    for feature in features[:6]:
        feature = feature.strip()
        if feature:
            lines.append(f"🔵 {esc(feature)}")

    if link:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=link_text, url=link)]]
        )
    else:
        keyboard = None

    text = "\n".join(lines)
    bot = get_bot()

    try:
        if photo_url:
            from aiogram.types import BufferedInputFile
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(photo_url) as resp:
                    if resp.status == 200:
                        photo_bytes = await resp.read()
                        file = BufferedInputFile(photo_bytes, filename="news.jpg")
                        await send_with_retry(
                            lambda: bot.send_photo(
                                chat_id,
                                photo=file,
                                caption=text,
                                reply_markup=keyboard,
                            )
                        )
                    else:
                        await send_with_retry(
                            lambda: bot.send_message(
                                chat_id, text, reply_markup=keyboard
                            )
                        )
        else:
            await send_with_retry(
                lambda: bot.send_message(
                    chat_id,
                    text,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
            )

        log.info(f"Новость опубликована: {title} (chat_id={chat_id})")
        return True

    except Exception as e:
        log.error(f"Ошибка публикации новости '{title}': {e}")
        return False


async def publish_simple_news(
    title: str, text: str, target_chat_id: Optional[int] = None
) -> bool:
    """Публикует простую новость одним текстом."""
    bot = get_bot()
    chat_id = target_chat_id if target_chat_id is not None else CHANNEL_ID
    try:
        await send_with_retry(
            lambda: bot.send_message(
                chat_id,
                f"🎮 <b>{esc(title)}</b>\n\n{esc(text)}",
                disable_web_page_preview=True,
            )
        )
        return True
    except Exception as e:
        log.error(f"Ошибка публикации новости: {e}")
        return False
