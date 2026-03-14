"""
Система обнаружения ошибок цен (Price Glitches).

Определяет аномальные скидки, которые могут быть ошибками магазинов:
- Скидки более 95%
- Цены ниже исторического минимума более чем на 50%
- Резкие падения цены без объявления распродажи
"""
import logging
import re
from typing import Optional
from parsers.steam import Deal
from enricher import get_historical_low
from currency import to_rubles
from database import get_previous_price, save_price_history

log = logging.getLogger(__name__)


def _extract_price_value(price_str: str) -> Optional[float]:
    """Извлекает числовое значение цены из строки."""
    if not price_str:
        return None
    
    # Убираем валюту и пробелы
    clean = price_str.replace("₽", "").replace("руб", "").replace(" ", "").replace(",", ".")
    
    # Ищем число
    match = re.search(r"[\d.]+", clean)
    if not match:
        return None
    
    try:
        return float(match.group())
    except ValueError:
        return None


async def check_for_glitch(deal: Deal) -> Optional[dict]:
    """
    Проверяет сделку на признаки ошибки цены.
    
    Args:
        deal: Объект Deal для проверки
        
    Returns:
        Dict с информацией об аномалии или None если всё нормально:
        {
            'type': 'extreme_discount' | 'historical_anomaly' | 'suspicious_drop',
            'severity': 'critical' | 'high' | 'medium',
            'message': str,  # Сообщение для пользователя
            'emoji': str,    # Эмодзи для поста
        }
    """
    # Бесплатные игры не проверяем
    if deal.is_free:
        return None
    
    current_price = _extract_price_value(deal.new_price)
    old_price = _extract_price_value(deal.old_price)
    
    if not current_price or not old_price:
        return None
    
    # Сохраняем историю цен для будущих проверок
    try:
        await save_price_history(deal.deal_id, current_price, deal.discount)
    except Exception as e:
        log.warning(f"Не удалось сохранить историю цен для {deal.deal_id}: {e}")
    
    # Условие 1: Экстремальная скидка (95%+)
    if deal.discount >= 98:
        return {
            'type': 'extreme_discount',
            'severity': 'critical',
            'message': (
                f"⚠️ ВНИМАНИЕ: Скидка {deal.discount}% — вероятная ОШИБКА ЦЕНЫ!\n"
                f"Такие скидки почти всегда являются ошибками магазина.\n"
                f"Покупайте срочно — могут отменить заказ!"
            ),
            'emoji': '🚨',
            'discount': deal.discount,
        }
    
    if deal.discount >= 95:
        return {
            'type': 'extreme_discount',
            'severity': 'high',
            'message': (
                f"🔥 АНОМАЛИЯ: Скидка {deal.discount}%!\n"
                f"Это может быть ошибка цены или зачистка склада.\n"
                f"Успейте купить, пока не исправили!"
            ),
            'emoji': '🔥',
            'discount': deal.discount,
        }
    
    # Условие 2: Цена намного ниже исторического минимума
    # Проверяем только для Steam (у нас есть данные CheapShark)
    if deal.store == "Steam" and deal.deal_id.startswith("steam_"):
        appid = deal.deal_id.replace("steam_", "")
        historical_low = await get_historical_low(appid)
        
        if historical_low and historical_low.get("price"):
            try:
                hist_low_usd = float(historical_low["price"])
                hist_low_rub = await to_rubles(hist_low_usd, "USD")
                
                if hist_low_rub:
                    # Если текущая цена ниже исторического минимума более чем на 50%
                    if current_price < (hist_low_rub * 0.5):
                        return {
                            'type': 'historical_anomaly',
                            'severity': 'critical',
                            'message': (
                                f"⚡️ ИСТОРИЧЕСКИЙ МИНИМУМ (АНОМАЛИЯ)!\n"
                                f"Цена {current_price:.0f}₽ — это на {int((1 - current_price/hist_low_rub) * 100)}% "
                                f"ниже предыдущего минимума ({hist_low_rub:.0f}₽)!\n"
                                f"Вероятная ошибка цены — берите немедленно!"
                            ),
                            'emoji': '⚡️',
                            'historical_low': hist_low_rub,
                            'current_price': current_price,
                        }
                    
                    # Если текущая цена ниже исторического минимума на 20-50%
                    elif current_price < (hist_low_rub * 0.8):
                        return {
                            'type': 'historical_anomaly',
                            'severity': 'high',
                            'message': (
                                f"📉 НОВЫЙ ИСТОРИЧЕСКИЙ МИНИМУМ!\n"
                                f"Цена {current_price:.0f}₽ ниже предыдущего минимума ({hist_low_rub:.0f}₽).\n"
                                f"Отличная возможность купить!"
                            ),
                            'emoji': '📉',
                            'historical_low': hist_low_rub,
                            'current_price': current_price,
                        }
            
            except (ValueError, TypeError) as e:
                log.warning(f"Ошибка при проверке исторического минимума для {deal.deal_id}: {e}")
    
    # Условие 3: Резкое падение цены (проверяем по нашей истории)
    previous = await get_previous_price(deal.deal_id)
    if previous:
        prev_price = float(previous['price'])
        
        # Если предыдущая цена была намного выше (падение более чем в 10 раз)
        # И при этом скидка выросла более чем на 50%
        if prev_price > 0 and (prev_price / current_price) >= 10:
            discount_jump = deal.discount - previous['discount']
            
            if discount_jump >= 50:
                return {
                    'type': 'suspicious_drop',
                    'severity': 'high',
                    'message': (
                        f"⚠️ РЕЗКОЕ ПАДЕНИЕ ЦЕНЫ!\n"
                        f"Цена упала с {prev_price:.0f}₽ до {current_price:.0f}₽ "
                        f"(в {int(prev_price/current_price)} раз)!\n"
                        f"Скидка выросла на {discount_jump}%. Возможна ошибка!"
                    ),
                    'emoji': '⚠️',
                    'old_price': prev_price,
                    'current_price': current_price,
                }
    
    # Условие 4: Подозрительное падение цены (по данным из Deal)
    # Если цена упала более чем в 20 раз (например, с 2000₽ до 100₽)
    if old_price > 0 and (old_price / current_price) >= 20:
        return {
            'type': 'suspicious_drop',
            'severity': 'high',
            'message': (
                f"⚠️ ПОДОЗРИТЕЛЬНОЕ ПАДЕНИЕ ЦЕНЫ!\n"
                f"Цена упала с {old_price:.0f}₽ до {current_price:.0f}₽ "
                f"(в {int(old_price/current_price)} раз)!\n"
                f"Возможна ошибка — проверьте перед покупкой."
            ),
            'emoji': '⚠️',
            'old_price': old_price,
            'current_price': current_price,
        }
    
    return None


def format_glitch_alert(deal: Deal, glitch_info: dict) -> str:
    """
    Форматирует предупреждение об ошибке цены для публикации.
    
    Args:
        deal: Объект Deal
        glitch_info: Информация об аномалии из check_for_glitch()
        
    Returns:
        Отформатированный текст предупреждения
    """
    emoji = glitch_info.get('emoji', '⚠️')
    severity = glitch_info.get('severity', 'medium')
    message = glitch_info.get('message', '')
    
    # Заголовок в зависимости от серьёзности
    if severity == 'critical':
        header = f"{emoji} <b>СРОЧНО! ВОЗМОЖНАЯ ОШИБКА ЦЕНЫ!</b>"
    elif severity == 'high':
        header = f"{emoji} <b>ВНИМАНИЕ! АНОМАЛЬНАЯ СКИДКА!</b>"
    else:
        header = f"{emoji} <b>НЕОБЫЧНАЯ ЦЕНА</b>"
    
    return f"{header}\n\n{message}"


async def is_price_glitch(deal: Deal) -> bool:
    """
    Быстрая проверка: является ли сделка вероятной ошибкой цены.
    
    Args:
        deal: Объект Deal для проверки
        
    Returns:
        True если это вероятная ошибка цены (критическая аномалия)
    """
    glitch_info = await check_for_glitch(deal)
    return glitch_info is not None and glitch_info.get('severity') == 'critical'
