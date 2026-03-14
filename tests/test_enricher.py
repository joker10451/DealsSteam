"""
Тесты для enricher.py.

Unit-тесты (4.1): generate_comment, genres_to_hashtags, rating_label.
Property-тесты (4.2): Properties 13–15 через hypothesis.
"""
import pytest
from unittest.mock import AsyncMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from parsers.steam import Deal
from enricher import generate_comment, genres_to_hashtags, rating_label, get_steam_rating


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deal(**kwargs) -> Deal:
    defaults = dict(
        deal_id="steam_1", title="Test Game", store="Steam",
        old_price="999 ₽", new_price="499 ₽", discount=50,
        link="https://store.steampowered.com/app/1/",
        is_free=False, genres=[],
    )
    defaults.update(kwargs)
    return Deal(**defaults)


# ---------------------------------------------------------------------------
# Task 4.1 — Unit tests
# ---------------------------------------------------------------------------

def test_generate_comment_high_rating():
    """WHEN rating.score >= 95, SHALL содержать 'лучших' или 'не раздумывая'."""
    deal = _deal()
    comment = generate_comment(deal, {"score": 97, "total": 5000, "label": "🏆"})
    assert "лучших" in comment or "не раздумывая" in comment


def test_generate_comment_free_game():
    """WHEN is_free == True, SHALL содержать 'Бесплатно' или 'берём'."""
    deal = _deal(is_free=True)
    comment = generate_comment(deal, None)
    assert "Бесплатно" in comment or "берём" in comment


def test_generate_comment_rpg_no_rating():
    """WHEN genres содержит 'RPG' и rating == None, SHALL содержать 'RPG'."""
    deal = _deal(genres=["RPG"])
    comment = generate_comment(deal, None)
    assert "RPG" in comment


def test_genres_to_hashtags_empty():
    """WHEN genres пустой список, SHALL вернуть пустую строку."""
    assert genres_to_hashtags([]) == ""


def test_genres_to_hashtags_known():
    """WHEN genres содержит известные жанры, SHALL вернуть хэштеги."""
    result = genres_to_hashtags(["RPG", "Action"])
    assert "#RPG" in result
    assert "#Экшен" in result


def test_genres_to_hashtags_max_three():
    """SHALL вернуть не более 3 хэштегов."""
    result = genres_to_hashtags(["RPG", "Action", "Indie", "Horror", "Puzzle"])
    tags = result.split()
    assert len(tags) <= 3


def test_genres_to_hashtags_no_duplicates():
    """WHEN genres содержит дубликаты ('RPG', 'Ролевые'), SHALL вернуть каждый хэштег один раз."""
    result = genres_to_hashtags(["RPG", "Ролевые"])
    assert result.count("#RPG") == 1


def test_rating_label_excellent():
    """WHEN score >= 95, SHALL вернуть строку с 'Крайне положительные'."""
    label = rating_label(95)
    assert "Крайне положительные" in label


def test_rating_label_very_positive():
    """WHEN 80 <= score < 95, SHALL вернуть строку с 'Очень положительные'."""
    label = rating_label(85)
    assert "Очень положительные" in label


@pytest.mark.asyncio
async def test_get_steam_rating_low_reviews():
    """WHEN total_reviews < 10, SHALL вернуть None."""
    mock_data = {
        "query_summary": {"total_reviews": 5, "total_positive": 4}
    }

    class MockResp:
        status = 200
        async def json(self, **kwargs): return mock_data
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    class MockSession:
        def get(self, *a, **kw): return MockResp()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    with patch("aiohttp.ClientSession", return_value=MockSession()):
        result = await get_steam_rating("12345")
    assert result is None


# ---------------------------------------------------------------------------
# Task 4.2 — Property-based tests (Properties 13–15)
# ---------------------------------------------------------------------------

# Feature: bot-tests-and-docs, Property 13: generate_comment for high-rated deals
@given(score=st.integers(min_value=95, max_value=100))
@settings(max_examples=10, deadline=None)
def test_property_generate_comment_high_rated(score):
    """Property 13: для любого score >= 95 комментарий содержит 'лучших' или 'не раздумывая'."""
    deal = _deal()
    comment = generate_comment(deal, {"score": score, "total": 1000, "label": "🏆"})
    assert "лучших" in comment or "не раздумывая" in comment


# Feature: bot-tests-and-docs, Property 14: generate_comment for free deals
@given(score=st.one_of(st.none(), st.integers(min_value=0, max_value=100)))
@settings(max_examples=10, deadline=None)
def test_property_generate_comment_free(score):
    """Property 14: для любого is_free == True комментарий содержит 'Бесплатно' или 'берём'."""
    deal = _deal(is_free=True)
    rating = {"score": score, "total": 100, "label": ""} if score is not None else None
    comment = generate_comment(deal, rating)
    assert "Бесплатно" in comment or "берём" in comment


# Feature: bot-tests-and-docs, Property 15: genres_to_hashtags invariants
@given(genres=st.lists(
    st.sampled_from(["RPG", "Ролевые", "Action", "Экшен", "Indie", "Инди", "Horror", "Puzzle", "Strategy"]),
    min_size=0, max_size=10,
))
@settings(max_examples=20, deadline=None)
def test_property_genres_to_hashtags_invariants(genres):
    """Property 15: каждый токен начинается с '#', не более 3 штук, нет дубликатов."""
    result = genres_to_hashtags(genres)
    if not result:
        return
    tags = result.split()
    assert all(t.startswith("#") for t in tags), "Все токены должны начинаться с #"
    assert len(tags) <= 3, "Не более 3 хэштегов"
    assert len(tags) == len(set(tags)), "Нет дубликатов"
