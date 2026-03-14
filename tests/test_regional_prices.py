"""
Тесты для regional_prices.py.

Unit-тесты (5.1): extract_appid, format_regional_prices.
Property-тесты (5.2): Properties 16–17 через hypothesis.
"""
from hypothesis import given, settings
from hypothesis import strategies as st

from regional_prices import extract_appid, format_regional_prices


# ---------------------------------------------------------------------------
# Task 5.1 — Unit tests
# ---------------------------------------------------------------------------

def test_extract_appid_valid():
    """WHEN корректная ссылка Steam, SHALL вернуть appid."""
    result = extract_appid("https://store.steampowered.com/app/1091500/")
    assert result == "1091500"


def test_extract_appid_valid_no_trailing_slash():
    """WHEN ссылка без слеша в конце, SHALL вернуть appid."""
    result = extract_appid("https://store.steampowered.com/app/730")
    assert result == "730"


def test_extract_appid_invalid():
    """WHEN строка не содержит ссылку Steam, SHALL вернуть None."""
    assert extract_appid("просто текст") is None
    assert extract_appid("https://example.com/app/123") is None
    assert extract_appid("") is None


def test_format_regional_prices_empty():
    """WHEN пустой список результатов, SHALL содержать 'Не удалось получить цены'."""
    result = format_regional_prices("Cyberpunk 2077", [])
    assert "Не удалось получить цены" in result


def test_format_regional_prices_nonempty():
    """WHEN непустой список, SHALL содержать флаги регионов и 'Дешевле всего'."""
    results = [
        {"flag": "🇷🇺", "country": "RU", "currency": "₽", "formatted": "499 ₽", "discount": 50, "final_cents": 49900},
        {"flag": "🇹🇷", "country": "TR", "currency": "₺", "formatted": "29,99₺", "discount": 0, "final_cents": 2999},
    ]
    text = format_regional_prices("Test Game", results)
    assert "🇷🇺" in text
    assert "🇹🇷" in text
    assert "Дешевле всего" in text


def test_format_regional_prices_discount_display():
    """WHEN discount > 0, SHALL содержать '-N%'."""
    results = [
        {"flag": "🇷🇺", "country": "RU", "currency": "₽", "formatted": "499 ₽", "discount": 75, "final_cents": 49900},
    ]
    text = format_regional_prices("Test Game", results)
    assert "-75%" in text


# ---------------------------------------------------------------------------
# Task 5.2 — Property-based tests (Properties 16–17)
# ---------------------------------------------------------------------------

_REGION = st.fixed_dictionaries({
    "flag": st.sampled_from(["🇷🇺", "🇹🇷", "🇦🇷", "🇰🇿", "🇺🇸"]),
    "country": st.sampled_from(["RU", "TR", "AR", "KZ", "US"]),
    "currency": st.sampled_from(["₽", "₺", "ARS", "₸", "$"]),
    "formatted": st.text(min_size=1, max_size=15),
    "discount": st.integers(min_value=0, max_value=100),
    "final_cents": st.integers(min_value=1, max_value=10_000_000),
})


# Feature: bot-tests-and-docs, Property 16: format_regional_prices with results
@given(results=st.lists(_REGION, min_size=1, max_size=5))
@settings(max_examples=20, deadline=None)
def test_property_format_with_results(results):
    """Property 16: непустой список → строка содержит флаг и 'Дешевле всего'."""
    text = format_regional_prices("Game", results)
    assert "Дешевле всего" in text
    assert any(r["flag"] in text for r in results)


# Feature: bot-tests-and-docs, Property 17: format_regional_prices discount display
@given(
    discount=st.integers(min_value=1, max_value=99),
    other_results=st.lists(_REGION, min_size=0, max_size=3),
)
@settings(max_examples=20, deadline=None)
def test_property_discount_display(discount, other_results):
    """Property 17: если discount > 0, строка содержит '-N%'."""
    results = [{
        "flag": "🇷🇺", "country": "RU", "currency": "₽",
        "formatted": "499 ₽", "discount": discount, "final_cents": 49900,
    }] + other_results
    text = format_regional_prices("Game", results)
    assert f"-{discount}%" in text
