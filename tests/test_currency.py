"""
Тесты для currency.py.

Unit-тесты (5.3): get_rate, format_rub, _fetch_rates при ошибке API.
Property-тесты (5.4): Property 18 — to_rubles conversion.
"""
import pytest
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

import currency
from currency import get_rate, format_rub, to_rubles, _fetch_rates


# ---------------------------------------------------------------------------
# Task 5.3 — Unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_rate_rub():
    """WHEN get_rate('RUB'), SHALL вернуть 1.0 без обращения к API."""
    result = await get_rate("RUB")
    assert result == 1.0


@pytest.mark.asyncio
async def test_get_rate_rub_lowercase():
    """WHEN get_rate('rub'), SHALL вернуть 1.0 (case-insensitive)."""
    result = await get_rate("rub")
    assert result == 1.0


def test_format_rub_1234():
    """WHEN format_rub(1234), SHALL вернуть '1 234 ₽'."""
    assert format_rub(1234) == "1 234 ₽"


def test_format_rub_zero():
    """WHEN format_rub(0), SHALL вернуть '0 ₽'."""
    assert format_rub(0) == "0 ₽"


def test_format_rub_large():
    """WHEN format_rub(1000000), SHALL вернуть '1 000 000 ₽'."""
    assert format_rub(1_000_000) == "1 000 000 ₽"


@pytest.mark.asyncio
async def test_fetch_rates_api_error():
    """WHEN API возвращает ошибку, _fetch_rates SHALL вернуть {}."""
    class MockResp:
        status = 500
        async def json(self, **kwargs): return {}
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    class MockSession:
        def get(self, *a, **kw): return MockResp()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    with patch("aiohttp.ClientSession", return_value=MockSession()):
        result = await _fetch_rates()
    assert result == {}


@pytest.mark.asyncio
async def test_to_rubles_known_currency():
    """WHEN валюта есть в мок-ответе, to_rubles SHALL вернуть корректное значение."""
    currency._cache = {"USD": 90.0}
    currency._cache_time = float("inf")  # не обновлять кеш
    result = await to_rubles(10.0, "USD")
    assert result == 900


@pytest.mark.asyncio
async def test_to_rubles_unknown_currency():
    """WHEN валюта недоступна, to_rubles SHALL вернуть None."""
    currency._cache = {}
    currency._cache_time = float("inf")
    result = await to_rubles(10.0, "XYZ")
    assert result is None


# ---------------------------------------------------------------------------
# Task 5.4 — Property-based tests (Property 18)
# ---------------------------------------------------------------------------

# Feature: bot-tests-and-docs, Property 18: to_rubles conversion
@given(
    amount=st.floats(min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False),
    rate=st.floats(min_value=0.01, max_value=200.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=20, deadline=None)
@pytest.mark.asyncio
async def test_property_to_rubles_conversion(amount, rate):
    """Property 18: to_rubles(amount, currency) == round(amount * rate) при известном курсе."""
    currency._cache = {"TST": rate}
    currency._cache_time = float("inf")
    result = await to_rubles(amount, "TST")
    assert result == round(amount * rate)
