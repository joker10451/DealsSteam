"""
Генерация коллажа из обложек игр через Pillow.
Склеивает до 4 обложек горизонтально в один баннер.
"""
import asyncio
import aiohttp
import io
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

BANNER_WIDTH = 1280
BANNER_HEIGHT = 400
THUMB_W = BANNER_WIDTH // 4
THUMB_H = BANNER_HEIGHT


async def _download_image(url: str) -> Optional[bytes]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.read()
    except Exception:
        pass
    return None


def _fit_image(img: "Image.Image", w: int, h: int) -> "Image.Image":
    """Обрезает и масштабирует изображение под нужный размер (crop center)."""
    img = img.convert("RGB")
    ratio = max(w / img.width, h / img.height)
    new_w = int(img.width * ratio)
    new_h = int(img.height * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


async def make_collage(image_urls: list[str]) -> Optional[bytes]:
    """
    Принимает список URL обложек (1-4 штуки).
    Возвращает bytes PNG-коллажа или None если не удалось.
    """
    if not PILLOW_AVAILABLE:
        return None

    urls = [u for u in image_urls if u][:4]
    if not urls:
        return None

    # Скачиваем все картинки параллельно
    raw_images = await asyncio.gather(*[_download_image(u) for u in urls])

    images = []
    for raw in raw_images:
        if raw:
            try:
                img = Image.open(io.BytesIO(raw))
                images.append(img)
            except Exception:
                pass

    if not images:
        return None

    count = len(images)
    tile_w = BANNER_WIDTH // count
    canvas = Image.new("RGB", (BANNER_WIDTH, BANNER_HEIGHT), (20, 20, 30))

    for i, img in enumerate(images):
        tile = _fit_image(img, tile_w, BANNER_HEIGHT)
        canvas.paste(tile, (i * tile_w, 0))

    # Тонкая тёмная полоска-разделитель между тайлами
    draw = ImageDraw.Draw(canvas)
    for i in range(1, count):
        x = i * tile_w
        draw.line([(x, 0), (x, BANNER_HEIGHT)], fill=(0, 0, 0), width=3)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()
