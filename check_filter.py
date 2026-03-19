#!/usr/bin/env python3
"""Проверка фильтра скоринга - какие игры проходят."""
import asyncio
from parsers.steam import get_steam_deals
from enricher import get_steam_rating
from publisher import _calculate_deal_score
import parsers.utils


async def check_filter():
    await parsers.utils.init_session()
    
    print("🔍 Проверяю скидки Steam (минимум 50%)...\n")
    deals = await get_steam_deals(min_discount=50)
    print(f"Всего найдено скидок: {len(deals)}\n")
    
    passed = []
    checked = 0
    
    for deal in deals[:30]:  # Проверяем первые 30
        if not deal.deal_id.startswith("steam_"):
            continue
            
        appid = deal.deal_id.replace("steam_", "")
        rating = await get_steam_rating(appid)
        
        try:
            price_rub = float(str(deal.new_price).replace("₽", "").replace(" ", "").replace(",", "").strip())
        except (ValueError, AttributeError):
            price_rub = 999999
        
        score = _calculate_deal_score(deal, rating, price_rub)
        checked += 1
        
        if score >= 3:
            rating_str = f"{rating['score']}%" if rating else "N/A"
            print(f"✅ Score: {score}/8 | {deal.title[:50]}")
            print(f"   Скидка: {deal.discount}% | Цена: {deal.new_price} | Рейтинг: {rating_str}\n")
            passed.append((deal, score))
        elif score >= 5:
            # ТОП игры
            rating_str = f"{rating['score']}%" if rating else "N/A"
            print(f"🏆 Score: {score}/8 | {deal.title[:50]}")
            print(f"   Скидка: {deal.discount}% | Цена: {deal.new_price} | Рейтинг: {rating_str}\n")
            passed.append((deal, score))
    
    await parsers.utils.close_session()
    
    print(f"\n{'='*70}")
    print(f"Проверено: {checked} игр")
    print(f"Прошло фильтр (score >= 3): {len(passed)} игр ({len(passed)/checked*100:.1f}%)")
    print(f"{'='*70}")
    
    if not passed:
        print("\n⚠️ НИ ОДНА ИГРА НЕ ПРОШЛА ФИЛЬТР!")
        print("Возможные причины:")
        print("- Низкие рейтинги (< 70%)")
        print("- Недостаточные скидки (< 50%)")
        print("- Высокие цены (> 300₽)")
        print("\n💡 Рекомендация: снизить порог фильтра с 3 до 2")


if __name__ == "__main__":
    asyncio.run(check_filter())
