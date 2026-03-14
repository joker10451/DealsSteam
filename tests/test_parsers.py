"""
Тесты парсеров: Steam, GOG, Epic.
Unit-тесты (2.1, 2.2, 2.3) + property-тесты через hypothesis (2.4).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, AsyncMock

from parsers.steam import Deal, _is_junk, get_steam_deals, SKIP_KEYWORDS
from parsers.gog import get_gog_deals
from parsers.epic import get_epic_deals

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Helpers — mock HTML / JSON
# ---------------------------------------------------------------------------

def _make_steam_html(appid="12345", title="Cool Game", discount=75,
                     old_price="999 ₽", new_price="249 ₽",
                     item_type="1", extra_class="") -> str:
    return f"""
<div id="search_resultsRows">
  <a class="search_result_row{' ' + extra_class if extra_class else ''}"
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
    """Build Steam HTML with multiple result rows."""
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


def _make_gog_json(slug="some-game", title="Some Game", discount="75%",
                   base="999 ₽", final="249 ₽") -> dict:
    return {
        "products": [
            {
                "slug": slug,
                "title": title,
                "price": {
                    "discount": discount,
                    "base": base,
                    "final": final,
                },
                "genres": [{"name": "Action"}],
                "coverHorizontal": "https://images.gog.com/cover.jpg",
                "storeLink": f"https://www.gog.com/ru/game/{slug}",
            }
        ]
    }


def _make_epic_json(title="Free Epic Game", game_id="epic123",
                    slug="free-epic-game", discount_pct=100,
                    original_price=0, final_price=0) -> dict:
    """Build minimal Epic API response with one active free promotion."""
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
# Task 2.1 — Steam unit tests
# ===========================================================================

@pytest.mark.asyncio
async def test_steam_valid_html_returns_deals():
    """Req 1.1: valid HTML → Deal list with correct fields."""
    html = _make_steam_html(appid="12345", title="Cool Game", discount=75)
    with patch("parsers.steam._fetch_with_retry", new_callable=AsyncMock, return_value=html):
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
    """Req 1.2: HTML with no discount rows → []."""
    with patch("parsers.steam._fetch_with_retry", new_callable=AsyncMock, return_value="<html></html>"):
        deals = await get_steam_deals(min_discount=50)
    assert deals == []


@pytest.mark.asyncio
async def test_steam_none_response_returns_empty_list():
    """Req 1.8: None response → []."""
    with patch("parsers.steam._fetch_with_retry", new_callable=AsyncMock, return_value=None):
        deals = await get_steam_deals(min_discount=50)
    assert deals == []


@pytest.mark.asyncio
async def test_steam_discount_filter():
    """Req 1.3: min_discount=70 keeps only deals with discount >= 70."""
    html = _make_steam_html_multi([
        {"appid": "1", "title": "High Discount", "discount": 80},
        {"appid": "2", "title": "Low Discount", "discount": 55},
    ])
    with patch("parsers.steam._fetch_with_retry", new_callable=AsyncMock, return_value=html):
        deals = await get_steam_deals(min_discount=70)

    assert all(d.discount >= 70 for d in deals)
    titles = [d.title for d in deals]
    assert "High Discount" in titles
    assert "Low Discount" not in titles


@pytest.mark.asyncio
async def test_steam_skips_non_game_item_type():
    """Req 1.4: data-ds-itemtype != '1' is skipped."""
    html = _make_steam_html(appid="99", title="Some DLC Content", discount=80, item_type="2")
    with patch("parsers.steam._fetch_with_retry", new_callable=AsyncMock, return_value=html):
        deals = await get_steam_deals(min_discount=50)
    assert deals == []


@pytest.mark.asyncio
async def test_steam_skips_dlc_ost_by_title():
    """Req 1.4: titles with DLC/OST keywords are skipped."""
    html = _make_steam_html_multi([
        {"appid": "1", "title": "Game - Soundtrack", "discount": 80},
        {"appid": "2", "title": "Game OST", "discount": 80},
        {"appid": "3", "title": "Normal Game", "discount": 80},
    ])
    with patch("parsers.steam._fetch_with_retry", new_callable=AsyncMock, return_value=html):
        deals = await get_steam_deals(min_discount=50)

    titles = [d.title for d in deals]
    assert "Normal Game" in titles
    assert not any("Soundtrack" in t or "OST" in t for t in titles)


# ===========================================================================
# Task 2.2 — GOG unit tests
# ===========================================================================

@pytest.mark.asyncio
async def test_gog_valid_json_returns_deals():
    """Req 1.5: GOG parser returns Deal with store=='GOG' and deal_id starting with 'gog_'."""
    data = _make_gog_json(slug="witcher-3", title="The Witcher 3", discount="75%")
    with patch("parsers.gog._fetch_with_retry", new_callable=AsyncMock, return_value=data):
        deals = await get_gog_deals(min_discount=50)

    assert len(deals) == 1
    d = deals[0]
    assert d.store == "GOG"
    assert d.deal_id.startswith("gog_")
    assert d.deal_id == "gog_witcher-3"
    assert d.title == "The Witcher 3"
    assert d.discount == 75


@pytest.mark.asyncio
async def test_gog_deal_id_prefix():
    """Req 1.5: every GOG deal_id starts with 'gog_'."""
    data = {
        "products": [
            {
                "slug": f"game-{i}",
                "title": f"Game {i}",
                "price": {"discount": "60%", "base": "999 ₽", "final": "399 ₽"},
                "genres": [],
                "coverHorizontal": "",
                "storeLink": f"https://www.gog.com/ru/game/game-{i}",
            }
            for i in range(3)
        ]
    }
    with patch("parsers.gog._fetch_with_retry", new_callable=AsyncMock, return_value=data):
        deals = await get_gog_deals(min_discount=50)

    assert len(deals) == 3
    for d in deals:
        assert d.store == "GOG"
        assert d.deal_id.startswith("gog_")


@pytest.mark.asyncio
async def test_gog_none_response_returns_empty_list():
    """Req 1.8: None response → []."""
    with patch("parsers.gog._fetch_with_retry", new_callable=AsyncMock, return_value=None):
        deals = await get_gog_deals(min_discount=50)
    assert deals == []


# ===========================================================================
# Task 2.3 — Epic unit tests
# ===========================================================================

@pytest.mark.asyncio
async def test_epic_free_game():
    """Req 1.6: Epic free game → is_free=True, discount=100."""
    data = _make_epic_json(title="Free Epic Game", discount_pct=100)
    with patch("parsers.epic._fetch_with_retry", new_callable=AsyncMock, return_value=data):
        deals = await get_epic_deals(min_discount=50)

    assert len(deals) == 1
    d = deals[0]
    assert d.is_free is True
    assert d.discount == 100
    assert d.store == "Epic Games"
    assert d.new_price == "Бесплатно"


@pytest.mark.asyncio
async def test_epic_none_response_returns_empty_list():
    """Req 1.8: None response → []."""
    with patch("parsers.epic._fetch_with_retry", new_callable=AsyncMock, return_value=None):
        deals = await get_epic_deals(min_discount=50)
    assert deals == []


@pytest.mark.asyncio
async def test_epic_no_active_promotions_skipped():
    """Items without active promotions are skipped."""
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
    with patch("parsers.epic._fetch_with_retry", new_callable=AsyncMock, return_value=data):
        deals = await get_epic_deals(min_discount=50)
    assert deals == []


# ===========================================================================
# Task 2.4 — Property-based tests via hypothesis
# ===========================================================================

# ---------------------------------------------------------------------------
# Property 1: Parser output invariant
# Feature: bot-tests-and-docs, Property 1: every returned Deal has non-empty
# deal_id, title, store, link and discount in [0, 100]
# ---------------------------------------------------------------------------

@given(
    appid=st.from_regex(r"[1-9][0-9]{3,6}", fullmatch=True),
    title=st.text(min_size=1, max_size=50, alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" "
    )),
    discount=st.integers(min_value=50, max_value=99),
)
@settings(max_examples=30)
def test_property1_steam_output_invariant(appid, title, discount):
    """
    # Feature: bot-tests-and-docs, Property 1: Parser output invariant
    Every returned Deal has non-empty deal_id, title, store, link and discount in [0, 100].
    Validates: Requirements 1.1, 1.9
    """
    import asyncio
    assume(not _is_junk(title))

    html = _make_steam_html(appid=appid, title=title, discount=discount)
    with patch("parsers.steam._fetch_with_retry", new_callable=AsyncMock, return_value=html):
        deals = asyncio.get_event_loop().run_until_complete(get_steam_deals(min_discount=1))

    for d in deals:
        assert d.deal_id, "deal_id must be non-empty"
        assert d.title, "title must be non-empty"
        assert d.store, "store must be non-empty"
        assert d.link, "link must be non-empty"
        assert 0 <= d.discount <= 100, f"discount {d.discount} out of [0,100]"


@given(
    slug=st.from_regex(r"[a-z][a-z0-9-]{2,20}", fullmatch=True),
    title=st.text(min_size=1, max_size=50, alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" "
    )),
    discount=st.integers(min_value=50, max_value=99),
)
@settings(max_examples=30)
def test_property1_gog_output_invariant(slug, title, discount):
    """
    # Feature: bot-tests-and-docs, Property 1: Parser output invariant (GOG)
    Validates: Requirements 1.5, 1.9
    """
    import asyncio
    data = _make_gog_json(slug=slug, title=title, discount=f"{discount}%")
    with patch("parsers.gog._fetch_with_retry", new_callable=AsyncMock, return_value=data):
        deals = asyncio.get_event_loop().run_until_complete(get_gog_deals(min_discount=1))

    for d in deals:
        assert d.deal_id
        assert d.title
        assert d.store
        assert d.link
        assert 0 <= d.discount <= 100


# ---------------------------------------------------------------------------
# Property 2: Parser discount filter
# Feature: bot-tests-and-docs, Property 2: all returned Deals have discount >= min_discount
# ---------------------------------------------------------------------------

@given(
    min_discount=st.integers(min_value=1, max_value=95),
    discounts=st.lists(st.integers(min_value=1, max_value=99), min_size=1, max_size=5),
)
@settings(max_examples=40)
def test_property2_steam_discount_filter(min_discount, discounts):
    """
    # Feature: bot-tests-and-docs, Property 2: Parser discount filter
    All returned Deals have discount >= min_discount.
    Validates: Requirements 1.3
    """
    import asyncio
    items = [
        {"appid": str(i + 1), "title": f"Game{i}", "discount": d}
        for i, d in enumerate(discounts)
    ]
    html = _make_steam_html_multi(items)
    with patch("parsers.steam._fetch_with_retry", new_callable=AsyncMock, return_value=html):
        deals = asyncio.get_event_loop().run_until_complete(get_steam_deals(min_discount=min_discount))

    for d in deals:
        assert d.discount >= min_discount, (
            f"Deal discount {d.discount} is below min_discount {min_discount}"
        )


# ---------------------------------------------------------------------------
# Property 3: Junk title filter
# Feature: bot-tests-and-docs, Property 3: _is_junk returns True for junk keywords
# ---------------------------------------------------------------------------

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
def test_property3_junk_title_with_keyword(prefix, keyword, suffix):
    """
    # Feature: bot-tests-and-docs, Property 3: Junk title filter
    _is_junk(title) returns True for titles containing junk keywords.
    Validates: Requirements 1.4
    """
    title = prefix + keyword + suffix
    assert _is_junk(title) is True, f"Expected _is_junk({title!r}) to be True"


@given(
    title=st.text(min_size=1, max_size=60, alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" "
    )),
)
@settings(max_examples=50)
def test_property3_clean_title_not_junk(title):
    """
    # Feature: bot-tests-and-docs, Property 3: Junk title filter (clean titles)
    _is_junk(title) returns False for titles without junk keywords.
    Validates: Requirements 1.4
    """
    assume(not any(kw in title.lower() for kw in SKIP_KEYWORDS))
    assert _is_junk(title) is False, f"Expected _is_junk({title!r}) to be False"


# ---------------------------------------------------------------------------
# Property 5: Parser error resilience
# Feature: bot-tests-and-docs, Property 5: parser returns [] on exception or None
# ---------------------------------------------------------------------------

@given(raise_exc=st.booleans())
@settings(max_examples=20)
def test_property5_steam_error_resilience(raise_exc):
    """
    # Feature: bot-tests-and-docs, Property 5: Parser error resilience
    When _fetch_with_retry raises or returns None, parser returns [] without raising.
    Validates: Requirements 1.8
    """
    import asyncio

    async def _failing(*args, **kwargs):
        if raise_exc:
            raise ConnectionError("network error")
        return None

    with patch("parsers.steam._fetch_with_retry", side_effect=_failing):
        result = asyncio.get_event_loop().run_until_complete(get_steam_deals(min_discount=50))

    assert result == []


@given(raise_exc=st.booleans())
@settings(max_examples=20)
def test_property5_gog_error_resilience(raise_exc):
    """
    # Feature: bot-tests-and-docs, Property 5: Parser error resilience (GOG)
    Validates: Requirements 1.8
    """
    import asyncio

    async def _failing(*args, **kwargs):
        if raise_exc:
            raise ConnectionError("network error")
        return None

    with patch("parsers.gog._fetch_with_retry", side_effect=_failing):
        result = asyncio.get_event_loop().run_until_complete(get_gog_deals(min_discount=50))

    assert result == []


@given(raise_exc=st.booleans())
@settings(max_examples=20)
def test_property5_epic_error_resilience(raise_exc):
    """
    # Feature: bot-tests-and-docs, Property 5: Parser error resilience (Epic)
    Validates: Requirements 1.8
    """
    import asyncio

    async def _failing(*args, **kwargs):
        if raise_exc:
            raise ConnectionError("network error")
        return None

    with patch("parsers.epic._fetch_with_retry", side_effect=_failing):
        result = asyncio.get_event_loop().run_until_complete(get_epic_deals(min_discount=50))

    assert result == []


# ---------------------------------------------------------------------------
# Property 6: GOG deal_id prefix
# Feature: bot-tests-and-docs, Property 6: every GOG Deal has store=="GOG" and deal_id starting with "gog_"
# ---------------------------------------------------------------------------

@given(
    slugs=st.lists(
        st.from_regex(r"[a-z][a-z0-9-]{2,15}", fullmatch=True),
        min_size=1, max_size=5, unique=True,
    ),
    discount=st.integers(min_value=50, max_value=99),
)
@settings(max_examples=30)
def test_property6_gog_deal_id_prefix(slugs, discount):
    """
    # Feature: bot-tests-and-docs, Property 6: GOG deal_id prefix
    Every GOG Deal has store=="GOG" and deal_id starting with "gog_".
    Validates: Requirements 1.5
    """
    import asyncio
    data = {
        "products": [
            {
                "slug": slug,
                "title": f"Game {slug}",
                "price": {"discount": f"{discount}%", "base": "999 ₽", "final": "499 ₽"},
                "genres": [],
                "coverHorizontal": "",
                "storeLink": f"https://www.gog.com/ru/game/{slug}",
            }
            for slug in slugs
        ]
    }
    with patch("parsers.gog._fetch_with_retry", new_callable=AsyncMock, return_value=data):
        deals = asyncio.get_event_loop().run_until_complete(get_gog_deals(min_discount=1))

    for d in deals:
        assert d.store == "GOG", f"Expected store='GOG', got {d.store!r}"
        assert d.deal_id.startswith("gog_"), f"deal_id {d.deal_id!r} must start with 'gog_'"
