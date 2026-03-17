"""
Тесты логики бота (bot.py).

Unit-тесты (6.1): get_daily_theme, _is_junk.
Property-тесты (6.2): Properties 19–20 через hypothesis.
"""
import os
import sys
from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from parsers.steam import Deal, _is_junk, SKIP_KEYWORDS

# Импортируем функции из правильных модулей
from publisher import get_daily_theme, DAILY_THEMES
from scheduler import deduplicate, theme_score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deal(title="Test Game", discount=50, genres=None) -> Deal:
    return Deal(
        deal_id=f"steam_{title[:5]}",
        title=title,
        store="Steam",
        old_price="999 ₽",
        new_price="499 ₽",
        discount=discount,
        link="https://store.steampowered.com/app/1/",
        is_free=False,
        genres=genres or [],
    )


# ---------------------------------------------------------------------------
# Task 6.1 — Unit tests
# ---------------------------------------------------------------------------

def test_get_daily_theme_monday():
    """WHEN weekday == 0 (понедельник), SHALL вернуть тему с '⚔️' и жанрами ['RPG', 'Ролевые']."""
    monday = datetime(2024, 1, 1)  # понедельник
    import pytz
    MSK = pytz.timezone("Europe/Moscow")
    with patch("publisher.datetime") as mock_dt:
        mock_dt.now.return_value = MSK.localize(monday)
        emoji, name, genres = get_daily_theme()
    assert emoji == "⚔️"
    assert "RPG" in genres
    assert "Ролевые" in genres


def test_get_daily_theme_all_days():
    """Все 7 дней должны возвращать корректные кортежи."""
    import pytz
    MSK = pytz.timezone("Europe/Moscow")
    # 2024-01-01 = понедельник (weekday 0)
    for weekday in range(7):
        with patch("publisher.datetime") as mock_dt:
            from datetime import timedelta
            day = datetime(2024, 1, 1) + timedelta(days=weekday)
            mock_dt.now.return_value = MSK.localize(day)
            result = get_daily_theme()
        assert len(result) == 3
        emoji, name, genres = result
        assert isinstance(emoji, str)
        assert isinstance(name, str)
        assert isinstance(genres, list)


def test_is_junk_ost():
    """WHEN название содержит ' - Soundtrack', _is_junk SHALL вернуть True."""
    assert _is_junk("Game Name - Soundtrack") is True


def test_is_junk_dlc():
    """WHEN название содержит 'DLC', _is_junk SHALL вернуть True."""
    assert _is_junk("Game - Season Pass DLC") is True


def test_is_junk_normal():
    """WHEN обычное название игры, _is_junk SHALL вернуть False."""
    assert _is_junk("Cyberpunk 2077") is False
    assert _is_junk("The Witcher 3") is False


def test_deduplicate_keeps_highest_discount():
    """WHEN два Deal с одинаковым title, SHALL оставить тот у которого discount больше."""
    deals = [
        _deal(title="Game A", discount=50),
        _deal(title="Game A", discount=80),
    ]
    result = deduplicate(deals)
    assert len(result) == 1
    assert result[0].discount == 80


def test_deduplicate_case_insensitive():
    """WHEN title отличается регистром, SHALL считать их дубликатами."""
    deals = [
        _deal(title="game a", discount=50),
        _deal(title="Game A", discount=70),
    ]
    result = deduplicate(deals)
    assert len(result) == 1
    assert result[0].discount == 70


def test_deduplicate_unique_deals():
    """WHEN все Deal уникальны, SHALL вернуть список той же длины."""
    deals = [_deal(title=f"Game {i}", discount=50) for i in range(5)]
    result = deduplicate(deals)
    assert len(result) == 5


def test_theme_score_match():
    """WHEN genres содержит жанр из темы, SHALL вернуть 1."""
    deal = _deal(genres=["RPG", "Action"])
    assert theme_score(deal, ["rpg", "ролевые"]) == 1


def test_theme_score_no_match():
    """WHEN genres не содержит жанров из темы, SHALL вернуть 0."""
    deal = _deal(genres=["Puzzle"])
    assert theme_score(deal, ["rpg", "ролевые"]) == 0


def test_theme_score_empty_theme():
    """WHEN theme_genres пустой, SHALL вернуть 0."""
    deal = _deal(genres=["RPG"])
    assert theme_score(deal, []) == 0


# ---------------------------------------------------------------------------
# Task 6.2 — Property-based tests (Properties 19–20)
# ---------------------------------------------------------------------------

_GENRE = st.sampled_from(["RPG", "Ролевые", "Action", "Экшен", "Инди", "Indie", "Puzzle", "Horror"])

_DEAL_ST = st.builds(
    _deal,
    title=st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs"))),
    discount=st.integers(min_value=0, max_value=100),
    genres=st.lists(_GENRE, min_size=0, max_size=4),
)


# Feature: bot-tests-and-docs, Property 19: deduplicate keeps highest discount
@given(deals=st.lists(_DEAL_ST, min_size=1, max_size=20))
@settings(max_examples=30, deadline=None)
def test_property_deduplicate_highest_discount(deals):
    """Property 19: нет двух Deal с одинаковым title (case-insensitive), у каждого максимальный discount."""
    result = deduplicate(deals)
    titles = [d.title.lower().strip() for d in result]
    assert len(titles) == len(set(titles)), "Дубликаты по title не допускаются"

    # Для каждого title в результате — discount должен быть максимальным среди всех с этим title
    for res_deal in result:
        key = res_deal.title.lower().strip()
        max_discount = max(d.discount for d in deals if d.title.lower().strip() == key)
        assert res_deal.discount == max_discount


# Feature: bot-tests-and-docs, Property 20: theme_score correctness
@given(
    genres=st.lists(_GENRE, min_size=0, max_size=5),
    theme_genres=st.lists(_GENRE, min_size=0, max_size=5),
)
@settings(max_examples=30, deadline=None)
def test_property_theme_score_correctness(genres, theme_genres):
    """Property 20: theme_score == 0 если theme_genres пустой или нет пересечения, иначе 1."""
    deal = _deal(genres=genres)
    score = theme_score(deal, theme_genres)
    genres_lower = [g.lower() for g in genres]
    if not theme_genres:
        assert score == 0
    elif any(g in theme_genres for g in genres_lower):
        assert score == 1
    else:
        assert score == 0
