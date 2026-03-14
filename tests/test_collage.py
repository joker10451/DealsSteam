"""
Тесты для collage.py.

Unit-тесты (6.3): make_collage с пустым списком, одним и четырьмя мок-PNG.
Property-тесты (6.4): Property 21 — collage output dimensions.
"""
import io
import pytest
from unittest.mock import patch, AsyncMock

from hypothesis import given, settings
from hypothesis import strategies as st

from PIL import Image
from collage import make_collage, BANNER_WIDTH, BANNER_HEIGHT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png_bytes(w: int = 200, h: int = 150) -> bytes:
    """Создаёт минимальный валидный PNG в памяти через Pillow."""
    img = Image.new("RGB", (w, h), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _patch_download(png_bytes: bytes):
    """Патчит _download_image чтобы возвращать заданные байты."""
    return patch("collage._download_image", new=AsyncMock(return_value=png_bytes))


# ---------------------------------------------------------------------------
# Task 6.3 — Unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_make_collage_empty_urls():
    """WHEN пустой список URL, SHALL вернуть None."""
    result = await make_collage([])
    assert result is None


@pytest.mark.asyncio
async def test_make_collage_single_image():
    """WHEN один валидный мок-PNG, SHALL вернуть непустые байты."""
    png = _make_png_bytes()
    with _patch_download(png):
        result = await make_collage(["http://example.com/img.jpg"])
    assert result is not None
    assert len(result) > 0
    # Проверяем что это валидный PNG
    img = Image.open(io.BytesIO(result))
    assert img.format == "PNG"


@pytest.mark.asyncio
async def test_make_collage_four_images_dimensions():
    """WHEN 4 мок-PNG, SHALL вернуть PNG с шириной BANNER_WIDTH и высотой BANNER_HEIGHT."""
    png = _make_png_bytes()
    with patch("collage._download_image", new=AsyncMock(return_value=png)):
        result = await make_collage([
            "http://example.com/1.jpg",
            "http://example.com/2.jpg",
            "http://example.com/3.jpg",
            "http://example.com/4.jpg",
        ])
    assert result is not None
    img = Image.open(io.BytesIO(result))
    assert img.width == BANNER_WIDTH
    assert img.height == BANNER_HEIGHT


@pytest.mark.asyncio
async def test_make_collage_all_downloads_fail():
    """WHEN все загрузки провалились, SHALL вернуть None."""
    with patch("collage._download_image", new=AsyncMock(return_value=None)):
        result = await make_collage(["http://example.com/img.jpg"])
    assert result is None


@pytest.mark.asyncio
async def test_make_collage_two_images_dimensions():
    """WHEN 2 мок-PNG, SHALL вернуть PNG с шириной BANNER_WIDTH и высотой BANNER_HEIGHT."""
    png = _make_png_bytes()
    with patch("collage._download_image", new=AsyncMock(return_value=png)):
        result = await make_collage(["http://a.com/1.jpg", "http://a.com/2.jpg"])
    assert result is not None
    img = Image.open(io.BytesIO(result))
    assert img.width == BANNER_WIDTH
    assert img.height == BANNER_HEIGHT


# ---------------------------------------------------------------------------
# Task 6.4 — Property-based tests (Property 21)
# ---------------------------------------------------------------------------

# Feature: bot-tests-and-docs, Property 21: collage output dimensions
@given(count=st.integers(min_value=1, max_value=4))
@settings(max_examples=10, deadline=None)
@pytest.mark.asyncio
async def test_property_collage_dimensions(count):
    """Property 21: для 1–4 мок-PNG make_collage возвращает PNG размером BANNER_WIDTH x BANNER_HEIGHT."""
    png = _make_png_bytes()
    urls = [f"http://example.com/{i}.jpg" for i in range(count)]
    with patch("collage._download_image", new=AsyncMock(return_value=png)):
        result = await make_collage(urls)
    assert result is not None
    img = Image.open(io.BytesIO(result))
    assert img.width == BANNER_WIDTH
    assert img.height == BANNER_HEIGHT
