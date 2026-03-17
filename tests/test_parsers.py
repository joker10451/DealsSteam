"""
Тесты парсеров: Steam, Epic.
Unit-тесты + property-тесты через hypothesis.
GOG парсер удалён из проекта.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import pytest
from unittest.mock import patch, AsyncMock

from parsers.steam import _is_junk, get_steam_deals, SKIP_KEYWORDS
from parsers.epic import get_epic_deals

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_steam_html(appid="12345", title="Cool Game", discount=75,
                     old_price="999 ₽", new_price="249 ₽",
                     item_type="1") -> str:
    return f"""
<div id="search_resultsRows">
  <a class="search_result_row"
     data-ds-appid="{appid}"
     data-ds-itemtype="{item_type}">
    <div class="search_capsule">
      <img src="https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg">
    </div>
    <div class="responsive_search_name_combined">
      <div class="col search_name">
        <span class="title">{title}</span>
      </div>
      <div class="col search_discount_and_price">
        <div class="search_discount">
          <span class="discount_pct">-{discount}%</span>
        </div>
        <div class="search_price_discount_combined">
          <div class="search_price">
            <span class="discount_original_price">{old_price}</span>
            <span class="discount_final_price">{new_price}</span>
          </div>
        </div>
      </div>
    </div>
  </a>
</div>
"""


def _make_steam_html_multi(items: list[dict]) -> str:
    rows = ""
    for it in items:
        appid = it.get("appid", "1")
        title = it.get("title", "Game")
        discount = it.get("discount", 50)
        old_price = it.get("old_price", "999 ₽")
        new_price = it.get("new_price", "499 ₽")
        item_type = it.get("item_type", "1")
        rows += f"""
  <a class="search_result_row"
     data-ds-appid="{appid}"
     data-ds-itemtype="{item_type}">
    <img src="https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg">
    <span class="title">{title}</span>
    <span class="discount_pct">-{discount}%</span>
    <span class="discount_original_price">{old_price}</span>
    <span class="discount_final_price">{new_price}</span>
  </a>"""
    return f'<div id="search_resultsRows">{rows}</div>'


def _make_epic_json(title="Free Epic Game", game_id="epic123",
                    slug="free-epic-game", discount_pct=100,
                    original_price=0, final_price=0) -> dict:
    return {
        "data": {
            "Catalog": {
                "searchStore": {
                    "elements": [
                        {
                            "id": game_id,
                            "title": title,
                            "productSlug": slug,
                            "urlSlug": slug,
                            "price": {
                                "totalPrice": {
                                    "originalPrice": original_price,
                                    "discountPrice": final_price,
                                }
                            },
                            "promotions": {
                                "promotionalOffers": [
                                    {
                                        "promotionalOffers": [
                                            {
                                                "discountSetting": {
                                                    "discountPercentage": 100 - discount_pct
                                                }
                                            }
                                        ]
                                    }
                                ]
                            },
                            "tags": [],
                            "keyImages": [],
                        }
                    ]
                }
            }
        }
    }


# ===========================================================================
# Steam unit tests
# ===========================================================================

@pytest.mark.asyncio
async def test_steam_valid_html_returns_deals():
    html = _make_steam_html(appid="12345", title="Cool Game", discount=75)
    with patch("parsers.steam.fetch_with_retry", new_callable=AsyncMock, return_value=html):
        deals = await get_steam_deals(min_discount=50)

    assert len(deals) == 1
    d = deals[0]
    assert d.deal_id == "steam_12345"
    assert d.title == "Cool Game"
    assert d.store == "Steam"
    assert d.discount == 75
    assert d.link == "https://store.steampowered.com/app/12345/"
    assert d.old_price == "999 ₽"
    assert d.new_price == "249 ₽"


@pytest.mark.asyncio
async def test_steam_empty_html_returns_empty_list():
    with patch("parsers.steam.fetch_with_retry", new_callable=AsyncMock, return_value="<html></html>"):
        deals = await get_steam_deals(min_discount=50)
    assert deals == []


@pytest.mark.asyncio
async def test_steam_none_response_returns_empty_list():
    with patch("parsers.steam.fetch_with_retry", new_callable=AsyncMock, return_value=None):
        deals = await get_steam_deals(min_discount=50)
    assert deals == []


@pytest.mark.asyncio
async def test_steam_discount_filter():
    html = _make_steam_html_multi([
        {"appid": "1", "title": "High Discount", "discount": 80},
        {"appid": "2", "title": "Low Discount", "discount": 55},
    ])
    with patch("parsers.steam.fetch_with_retry", new_callable=AsyncMock, return_value=html):
        deals = await get_steam_deals(min_discount=70)

    assert all(d.discount >= 70 for d in deals)
    titles = [d.title for d in deals]
    assert "High Discount" in titles
    assert "Low Discount" not in titles


@pytest.mark.asyncio
async def test_steam_skips_non_game_item_type():
    html = _make_steam_html(appid="99", title="Some DLC Content", discount=80, item_type="2")
    with patch("parsers.steam.fetch_with_retry", new_callable=AsyncMock, return_value=html):
        deals = await get_steam_deals(min_discount=50)
    assert deals == []


@pytest.mark.asyncio
async def test_steam_skips_dlc_ost_by_title():
    html = _make_steam_html_multi([
        {"appid": "1", "title": "Game - Soundtrack", "discount": 80},
        {"appid": "2", "title": "Game OST", "discount": 80},
        {"appid": "3", "title": "Normal Game", "discount": 80},
    ])
    with patch("parsers.steam.fetch_with_retry", new_callable=AsyncMock, return_value=html):
        deals = await get_steam_deals(min_discount=50)

    titles = [d.title for d in deals]
    assert "Normal Game" in titles
    assert not any("Soundtrack" in t or "OST" in t for t in titles)


# ===========================================================================
# Epic unit tests
# ===========================================================================

@pytest.mark.asyncio
async def test_epic_free_game():
    data = _make_epic_json(title="Free Epic Game", discount_pct=100)
    with patch("parsers.epic.fetch_with_retry", new_callable=AsyncMock, return_value=data):
        deals = await get_epic_deals(min_discount=50)

    assert len(deals) == 1
    d = deals[0]
    assert d.is_free is True
    assert d.discount == 100
    assert d.store == "Epic Games"
    assert d.new_price == "Бесплатно"


@pytest.mark.asyncio
async def test_epic_none_response_returns_empty_list():
    with patch("parsers.epic.fetch_with_retry", new_callable=AsyncMock, return_value=None):
        deals = await get_epic_deals(min_discount=50)
    assert deals == []


@pytest.mark.asyncio
async def test_epic_no_active_promotions_skipped():
    data = {
        "data": {
            "Catalog": {
                "searchStore": {
                    "elements": [
                        {
                            "id": "abc",
                            "title": "No Promo Game",
                            "productSlug": "no-promo",
                            "urlSlug": "no-promo",
                            "price": {"totalPrice": {"originalPrice": 100, "discountPrice": 100}},
                            "promotions": {"promotionalOffers": []},
                            "tags": [],
                            "keyImages": [],
                        }
                    ]
                }
            }
        }
    }
    with patch("parsers.epic.fetch_with_retry", new_callable=AsyncMock, return_value=data):
        deals = await get_epic_deals(min_discount=50)
    assert deals == []


# ===========================================================================
# Property-based tests
# ===========================================================================

@given(
    appid=st.from_regex(r"[1-9][0-9]{3,6}", fullmatch=True),
    title=st.text(min_size=1, max_size=50, alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" "
    )),
    discount=st.integers(min_value=50, max_value=99),
)
@settings(max_examples=30)
def test_property_steam_output_invariant(appid, title, discount):
    """Every returned Steam Deal has non-empty fields and discount in [0, 100]."""
    assume(not _is_junk(title))
    html = _make_steam_html(appid=appid, title=title, discount=discount)
    with patch("parsers.steam.fetch_with_retry", new_callable=AsyncMock, return_value=html):
        deals = asyncio.get_event_loop().run_until_complete(get_steam_deals(min_discount=1))
    for d in deals:
        assert d.deal_id
        assert d.title
        assert d.store
        assert d.link
        assert 0 <= d.discount <= 100


@given(
    min_discount=st.integers(min_value=1, max_value=95),
    discounts=st.lists(st.integers(min_value=1, max_value=99), min_size=1, max_size=5),
)
@settings(max_examples=40)
def test_property_steam_discount_filter(min_discount, discounts):
    """All returned Steam Deals have discount >= min_discount."""
    items = [{"appid": str(i + 1), "title": f"Game{i}", "discount": d} for i, d in enumerate(discounts)]
    html = _make_steam_html_multi(items)
    with patch("parsers.steam.fetch_with_retry", new_callable=AsyncMock, return_value=html):
        deals = asyncio.get_event_loop().run_until_complete(get_steam_deals(min_discount=min_discount))
    for d in deals:
        assert d.discount >= min_discount


@given(
    prefix=st.text(min_size=0, max_size=20, alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" "
    )),
    keyword=st.sampled_from(list(SKIP_KEYWORDS)),
    suffix=st.text(min_size=0, max_size=20, alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" "
    )),
)
@settings(max_examples=50)
def test_property_junk_title_with_keyword(prefix, keyword, suffix):
    """_is_junk returns True for titles containing junk keywords."""
    assert _is_junk(prefix + keyword + suffix) is True


@given(
    title=st.text(min_size=1, max_size=60, alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" "
    )),
)
@settings(max_examples=50)
def test_property_clean_title_not_junk(title):
    """_is_junk returns False for titles without junk keywords."""
    assume(not any(kw in title.lower() for kw in SKIP_KEYWORDS))
    assert _is_junk(title) is False


@given(raise_exc=st.booleans())
@settings(max_examples=20)
def test_property_steam_error_resilience(raise_exc):
    """Steam parser returns [] on exception or None."""
    async def _failing(*args, **kwargs):
        if raise_exc:
            raise ConnectionError("network error")
        return None

    with patch("parsers.steam.fetch_with_retry", side_effect=_failing):
        result = asyncio.get_event_loop().run_until_complete(get_steam_deals(min_discount=50))
    assert result == []


@given(raise_exc=st.booleans())
@settings(max_examples=20)
def test_property_epic_error_resilience(raise_exc):
    """Epic parser returns [] on exception or None."""
    async def _failing(*args, **kwargs):
        if raise_exc:
            raise ConnectionError("network error")
        return None

    with patch("parsers.epic.fetch_with_retry", side_effect=_failing):
        result = asyncio.get_event_loop().run_until_complete(get_epic_deals(min_discount=50))
    assert result == []
