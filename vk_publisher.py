"""
Публикация скидок и раздач в группу ВКонтакте.
Работает параллельно с Telegram — дублирует посты с CTA-ссылкой на TG канал.
"""
import asyncio
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

VK_TOKEN = os.getenv("VK_ACCESS_TOKEN", "")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID", "0"))
VK_ENABLED = os.getenv("VK_ENABLED", "false").lower() == "true"
TG_CHANNEL_LINK = os.getenv("TG_CHANNEL_LINK", "https://t.me/GameDealsRadarRu")

VK_API = "https://api.vk.com/method"
VK_VERSION = "5.199"


async def _vk_request(method: str, params: dict) -> Optional[dict]:
    """Выполнить запрос к VK API через общую aiohttp сессию."""
    from parsers.utils import fetch_with_retry
    params["access_token"] = VK_TOKEN
    params["v"] = VK_VERSION
    url = f"{VK_API}/{method}"
    try:
        data = await fetch_with_retry(url, params=params)
        if data and "error" in data:
            log.error(f"VK API error in {method}: {data['error']}")
            return None
        return data.get("response") if data else None
    except Exception as e:
        log.error(f"VK request failed ({method}): {e}")
        return None


async def _upload_photo(image_url: str) -> Optional[str]:
    """Загрузить фото по URL в VK и вернуть attachment-строку."""
    from parsers.utils import fetch_with_retry
    import aiohttp

    # Получаем upload server для стены группы
    server = await _vk_request("photos.getWallUploadServer", {"group_id": VK_GROUP_ID})
    if not server:
        return None
    upload_url = server["upload_url"]

    # Скачиваем картинку и загружаем на VK upload server
    try:
        from parsers.utils import get_session
        session = get_session()
        async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            img_bytes = await resp.read()
        form = aiohttp.FormData()
        form.add_field("photo", img_bytes, filename="photo.jpg", content_type="image/jpeg")
        async with session.post(upload_url, data=form) as resp:
            upload_result = await resp.json(content_type=None)
    except Exception as e:
        log.error(f"VK: ошибка загрузки фото: {e}")
        return None

    # Сохраняем фото
    saved = await _vk_request("photos.saveWallPhoto", {
        "group_id": VK_GROUP_ID,
        "photo": upload_result.get("photo"),
        "server": upload_result.get("server"),
        "hash": upload_result.get("hash"),
    })
    if not saved or not saved[0]:
        return None

    photo = saved[0]
    return f"photo{photo['owner_id']}_{photo['id']}"


async def post_deal_to_vk(deal, rating: Optional[dict] = None, igdb_info: Optional[dict] = None) -> bool:
    """
    Опубликовать скидку в группу ВК.
    Возвращает True если успешно.
    """
    if not VK_ENABLED or not VK_TOKEN or not VK_GROUP_ID:
        return False

    try:
        store_emoji = {"Steam": "🎮", "Epic Games": "🎁"}.get(deal.store, "🕹")

        # Заголовок
        if deal.is_free:
            header = "🎁 БЕСПЛАТНО — забирай прямо сейчас!"
        elif deal.discount >= 80:
            header = f"🔥 ОГОНЬ! Скидка {deal.discount}% — почти даром!"
        elif deal.discount >= 50:
            header = f"💥 Скидка {deal.discount}% — отличная цена!"
        else:
            header = f"🏷 Скидка {deal.discount}% на {deal.store}"

        # Цена
        if deal.is_free:
            price_line = f"💸 Обычно {deal.old_price} — сейчас БЕСПЛАТНО"
        else:
            price_line = f"💰 {deal.old_price}  →  {deal.new_price}  (−{deal.discount}%)"

        # Рейтинг
        rating_line = ""
        if rating and rating.get("score"):
            score = rating["score"]
            score_emoji = "🏆" if score >= 95 else "👍" if score >= 80 else "🙂"
            rating_line = f"\n{score_emoji} Рейтинг Steam: {score}% положительных отзывов"

        # Жанры
        genres_line = ""
        genres = getattr(deal, "genres", [])
        if genres:
            genres_line = f"\n🎯 Жанры: {', '.join(genres[:4])}"

        # Описание из IGDB
        desc_line = ""
        if igdb_info and igdb_info.get("summary"):
            summary = igdb_info["summary"][:200].rstrip()
            if len(igdb_info["summary"]) > 200:
                summary += "..."
            desc_line = f"\n\n📖 {summary}"

        text = (
            f"{header}\n\n"
            f"{store_emoji} {deal.title}"
            f"{desc_line}\n\n"
            f"{price_line}"
            f"{rating_line}"
            f"{genres_line}\n\n"
            f"🔗 Забрать: {deal.link}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎮 Больше скидок, раздачи ключей и мини-игры — в нашем Telegram:\n"
            f"👉 {TG_CHANNEL_LINK}"
        )

        # Картинка: IGDB > deal.image_url > Steam capsule
        attachment = None
        image_url = None
        if igdb_info and igdb_info.get("cover_url"):
            image_url = igdb_info["cover_url"]
        elif getattr(deal, "image_url", None):
            image_url = deal.image_url
        elif deal.store == "Steam" and deal.deal_id.startswith("steam_"):
            appid = deal.deal_id.replace("steam_", "")
            image_url = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg"

        if image_url:
            attachment = await _upload_photo(image_url)
            if not attachment:
                log.warning(f"VK: не удалось загрузить фото для {deal.title}, постим без картинки")

        params = {
            "owner_id": -VK_GROUP_ID,
            "from_group": 1,
            "message": text,
        }
        if attachment:
            params["attachments"] = attachment

        result = await _vk_request("wall.post", params)
        if result and result.get("post_id"):
            log.info(f"VK: опубликован пост {result['post_id']} для {deal.title}")
            return True
        return False

    except Exception as e:
        log.error(f"VK: ошибка публикации {deal.title}: {e}")
        return False


async def post_giveaway_to_vk(title: str, description: str, end_str: str) -> bool:
    """Опубликовать анонс розыгрыша в группу ВК."""
    if not VK_ENABLED or not VK_TOKEN or not VK_GROUP_ID:
        return False

    try:
        text = (
            f"🎁 РОЗЫГРЫШ!\n\n"
            f"🎮 {title}\n\n"
            f"{description}\n\n"
            f"📅 Конец розыгрыша: {end_str}\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"✅ Участвовать можно только в Telegram:\n"
            f"👉 {TG_CHANNEL_LINK}"
        )

        result = await _vk_request("wall.post", {
            "owner_id": -VK_GROUP_ID,
            "from_group": 1,
            "message": text,
        })
        if result and result.get("post_id"):
            log.info(f"VK: опубликован розыгрыш {result['post_id']}")
            return True
        return False

    except Exception as e:
        log.error(f"VK: ошибка публикации розыгрыша: {e}")
        return False
