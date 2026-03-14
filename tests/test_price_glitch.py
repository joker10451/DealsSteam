"""
Тесты для системы обнаружения ошибок цен (price glitches).
"""
import pytest
from parsers.steam import Deal
from price_glitch import check_for_glitch, format_glitch_alert, is_price_glitch


@pytest.fixture
def normal_deal():
    """Обычная скидка без аномалий."""
    return Deal(
        deal_id="steam_12345",
        title="Test Game",
        store="Steam",
        old_price="1000 ₽",
        new_price="500 ₽",
        discount=50,
        link="https://store.steampowered.com/app/12345/",
    )


@pytest.fixture
def extreme_discount_deal():
    """Сделка с экстремальной скидкой 98%."""
    return Deal(
        deal_id="steam_99999",
        title="Glitch Game",
        store="Steam",
        old_price="2000 ₽",
        new_price="40 ₽",
        discount=98,
        link="https://store.steampowered.com/app/99999/",
    )


@pytest.fixture
def high_discount_deal():
    """Сделка с высокой скидкой 95%."""
    return Deal(
        deal_id="steam_88888",
        title="Sale Game",
        store="Steam",
        old_price="1000 ₽",
        new_price="50 ₽",
        discount=95,
        link="https://store.steampowered.com/app/88888/",
    )


@pytest.fixture
def suspicious_drop_deal():
    """Сделка с подозрительным падением цены."""
    return Deal(
        deal_id="steam_77777",
        title="Suspicious Game",
        store="Steam",
        old_price="5000 ₽",
        new_price="100 ₽",
        discount=98,
        link="https://store.steampowered.com/app/77777/",
    )


@pytest.fixture
def free_deal():
    """Бесплатная игра (не должна проверяться)."""
    return Deal(
        deal_id="epic_free",
        title="Free Game",
        store="Epic Games",
        old_price="1000 ₽",
        new_price="Бесплатно",
        discount=100,
        link="https://store.epicgames.com/",
        is_free=True,
    )


async def test_normal_deal_no_glitch(normal_deal):
    """Обычная скидка не должна определяться как glitch."""
    result = await check_for_glitch(normal_deal)
    assert result is None


async def test_extreme_discount_critical(extreme_discount_deal):
    """Скидка 98% должна определяться как критическая аномалия."""
    result = await check_for_glitch(extreme_discount_deal)
    assert result is not None
    assert result['type'] == 'extreme_discount'
    assert result['severity'] == 'critical'
    assert result['emoji'] == '🚨'
    assert '98%' in result['message']


async def test_high_discount_high_severity(high_discount_deal):
    """Скидка 95% должна определяться как высокая аномалия."""
    result = await check_for_glitch(high_discount_deal)
    assert result is not None
    assert result['type'] == 'extreme_discount'
    assert result['severity'] == 'high'
    assert result['emoji'] == '🔥'
    assert '95%' in result['message']


async def test_suspicious_drop(suspicious_drop_deal):
    """Падение цены в 50 раз должно определяться как подозрительное."""
    result = await check_for_glitch(suspicious_drop_deal)
    assert result is not None
    # Может быть либо extreme_discount (98%), либо suspicious_drop
    assert result['severity'] in ['critical', 'high']


async def test_free_game_no_check(free_deal):
    """Бесплатные игры не должны проверяться на glitch."""
    result = await check_for_glitch(free_deal)
    assert result is None


async def test_format_glitch_alert_critical(extreme_discount_deal):
    """Форматирование критического предупреждения."""
    glitch_info = await check_for_glitch(extreme_discount_deal)
    alert = format_glitch_alert(extreme_discount_deal, glitch_info)
    
    assert '🚨' in alert
    assert 'СРОЧНО' in alert
    assert 'ОШИБКА ЦЕНЫ' in alert


async def test_format_glitch_alert_high(high_discount_deal):
    """Форматирование предупреждения высокой серьёзности."""
    glitch_info = await check_for_glitch(high_discount_deal)
    alert = format_glitch_alert(high_discount_deal, glitch_info)
    
    assert '🔥' in alert
    assert 'АНОМАЛЬНАЯ СКИДКА' in alert


async def test_is_price_glitch_true(extreme_discount_deal):
    """is_price_glitch должен возвращать True для критических аномалий."""
    result = await is_price_glitch(extreme_discount_deal)
    assert result is True


async def test_is_price_glitch_false_normal(normal_deal):
    """is_price_glitch должен возвращать False для обычных скидок."""
    result = await is_price_glitch(normal_deal)
    assert result is False


async def test_is_price_glitch_false_high(high_discount_deal):
    """is_price_glitch должен возвращать False для высоких (но не критических) аномалий."""
    result = await is_price_glitch(high_discount_deal)
    assert result is False


async def test_invalid_price_format():
    """Сделка с невалидным форматом цены не должна вызывать ошибку."""
    deal = Deal(
        deal_id="steam_invalid",
        title="Invalid Price",
        store="Steam",
        old_price="N/A",
        new_price="Unknown",
        discount=50,
        link="https://store.steampowered.com/",
    )
    result = await check_for_glitch(deal)
    assert result is None
