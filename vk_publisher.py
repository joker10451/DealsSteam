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
    """Выполнить запрос к VK API через POST."""
    from parsers.utils import get_session
    import aiohttp

    params = dict(params)
    params["access_token"] = VK_TOKEN
    params["v"] = VK_VERSION
    url = f"{VK_API}/{method}"
    try:
        session = get_session()
        async with session.post(url, data=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json(content_type=None)
        if data and "error" in data:
            log.error(f"VK API error in {method}: {data['error']}")
            return None
        return data.get("response") if data else None
    except Exception as e:
        log.error(f"VK request failed ({method}): {e}")
        return None


async def _vk_request_debug(method: str, params: dict) -> dict:
    """Как _vk_request, но возвращает сырой ответ для отладки."""
    from parsers.utils import get_session
    import aiohttp

    params = dict(params)
    params["access_token"] = VK_TOKEN
    params["v"] = VK_VERSION
    url = f"{VK_API}/{method}"
    try:
        session = get_session()
        async with session.post(url, data=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            return await resp.json(content_type=None)
    except Exception as e:
        return {"exception": str(e)}


async def _upload_photo(image_url: str) -> Optional[str]:
    """Загрузить фото по URL в VK и вернуть attachment-строку."""
    import aiohttp
    from parsers.utils import get_session

    try:
        # 1. Получаем upload server для стены группы
        server_resp = await _vk_request("photos.getWallUploadServer", {"group_id": VK_GROUP_ID})
        if not server_resp or not server_resp.get("upload_url"):
            log.error("VK: не получили upload_url от photos.getWallUploadServer")
            return None
        upload_url = server_resp["upload_url"]

        # 2. Скачиваем картинку
        session = get_session()
        async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                log.error(f"VK: не удалось скачать картинку {image_url}, status={resp.status}")
                return None
            img_bytes = await resp.read()

        # 3. Загружаем на VK upload server через multipart
        form = aiohttp.FormData()
        form.add_field("photo", img_bytes, filename="photo.jpg", content_type="image/jpeg")
        async with session.post(upload_url, data=form, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            upload_result = await resp.json(content_type=None)

        log.debug(f"VK upload result: {upload_result}")

        if not upload_result.get("photo") or upload_result.get("photo") == "[]":
            log.error(f"VK: upload вернул пустое фото: {upload_result}")
            return None

        # 4. Сохраняем фото — передаём group_id (без минуса), photo, server, hash
        saved = await _vk_request("photos.saveWallPhoto", {
            "group_id": VK_GROUP_ID,
            "photo": upload_result["photo"],
            "server": upload_result["server"],
            "hash": upload_result["hash"],
        })

        if not saved or not isinstance(saved, list) or not saved[0]:
            log.error(f"VK: photos.saveWallPhoto вернул пустой ответ: {saved}")
            return None

        photo = saved[0]
        attachment = f"photo{photo['owner_id']}_{photo['id']}"
        log.info(f"VK: фото загружено успешно: {attachment}")
        return attachment

    except Exception as e:
        log.error(f"VK: ошибка загрузки фото: {e}", exc_info=True)
        return None


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

        # Цена — если парсер вернул "—", показываем только новую цену со скидкой
        if deal.is_free:
            price_line = f"💸 Обычно {deal.old_price} — сейчас БЕСПЛАТНО"
        elif deal.old_price and deal.old_price != "—" and deal.new_price and deal.new_price != "—":
            price_line = f"💰 {deal.old_price}  →  {deal.new_price}  (−{deal.discount}%)"
        elif deal.new_price and deal.new_price != "—":
            price_line = f"💰 {deal.new_price}  (скидка −{deal.discount}%)"
        else:
            price_line = f"🏷 Скидка −{deal.discount}%"

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

        # Картинка: пытаемся загрузить через photos API
        # Если не получается - публикуем без картинки (VK сам подтянет превью по ссылке в тексте)
        image_url = None
        if igdb_info and igdb_info.get("cover_url"):
            image_url = igdb_info["cover_url"]
        
        attachments = []
        if image_url:
            photo_attachment = await _upload_photo(image_url)
            if photo_attachment:
                attachments.append(photo_attachment)
            else:
                log.warning(f"VK: не удалось загрузить фото для {deal.title}, постим без картинки")

        params = {
            "owner_id": -VK_GROUP_ID,
            "from_group": 1,
            "message": text,
        }
        if attachments:
            params["attachments"] = ",".join(attachments)

        result = await _vk_request("wall.post", params)
        log.debug(f"VK wall.post result: {result}")
        # result может быть dict {'post_id': N} или int (в старых версиях API)
        post_id = None
        if isinstance(result, dict):
            post_id = result.get("post_id")
        elif isinstance(result, int):
            post_id = result
        if post_id:
            log.info(f"VK: опубликован пост {post_id} для {deal.title}")
            return True
        log.error(f"VK: wall.post не вернул post_id, result={result}")
        return False

    except Exception as e:
        log.error(f"VK: ошибка публикации {deal.title}: {e}")
        return False


async def post_giveaway_to_vk(title: str, description: str, end_str: str, channel_post_id: Optional[int] = None) -> bool:
    """Опубликовать анонс розыгрыша в группу ВК."""
    if not VK_ENABLED or not VK_TOKEN or not VK_GROUP_ID:
        return False

    try:
        tg_link = f"{TG_CHANNEL_LINK}/{channel_post_id}" if channel_post_id else TG_CHANNEL_LINK

        text = (
            f"🎁 РОЗЫГРЫШ!\n\n"
            f"🎮 {title}\n\n"
            f"{description}\n\n"
            f"📅 Конец розыгрыша: {end_str}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Участвовать — только в Telegram:\n"
            f"👉 {tg_link}"
        )

        result = await _vk_request("wall.post", {
            "owner_id": -VK_GROUP_ID,
            "from_group": 1,
            "message": text,
        })
        post_id = result.get("post_id") if isinstance(result, dict) else result if isinstance(result, int) else None
        if post_id:
            log.info(f"VK: опубликован розыгрыш {post_id}")
            return True
        log.error(f"VK: giveaway wall.post не вернул post_id, result={result}")
        return False

    except Exception as e:
        log.error(f"VK: ошибка публикации розыгрыша: {e}")
        return False
