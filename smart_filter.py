"""
Умная фильтрация игр для повышения качества контента.
Предотвращает публикацию низкокачественных игр и потерю подписчиков.
"""
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


async def should_publish_deal(deal, rating: Optional[dict] = None, igdb_info: Optional[dict] = None) -> tuple[bool, str]:
    """
    Определяет, стоит ли публиковать сделку.
    
    Returns:
        (should_publish, reason) - публиковать ли и причина решения
    """
    # Всегда публикуем бесплатные игры
    if deal.is_free:
        return True, "free_game"
    
    # Всегда публикуем огромные скидки (90%+)
    if deal.discount >= 90:
        return True, "huge_discount"
    
    # Проверяем рейтинг Steam
    if deal.store == "Steam" and rating:
        score = rating["score"]
        
        # Жёсткий фильтр: рейтинг < 75%
        if score < 75:
            # Исключение: очень большая скидка (85%+) может компенсировать низкий рейтинг
            if deal.discount >= 85:
                log.info(f"Пропущен фильтр рейтинга (огромная скидка): {deal.title} ({score}%, -{deal.discount}%)")
                return True, "huge_discount_compensates"
            
            log.info(f"Отклонено (низкий рейтинг): {deal.title} ({score}%)")
            return False, f"low_rating_{score}"
        
        # Средний рейтинг (75-80%) требует хорошую скидку
        if 75 <= score < 80:
            if deal.discount < 70:
                log.info(f"Отклонено (средний рейтинг + малая скидка): {deal.title} ({score}%, -{deal.discount}%)")
                return False, f"mediocre_rating_small_discount"
    
    # Проверяем возраст игры
    if igdb_info:
        release_date = igdb_info.get("release_date")
        if release_date:
            try:
                release_year = datetime.fromtimestamp(release_date).year
                current_year = datetime.now().year
                age = current_year - release_year
                
                # Игра старше 10 лет И скидка < 90%
                if age > 10 and deal.discount < 90:
                    # Исключение: высокий рейтинг (классика)
                    if rating and rating["score"] >= 85:
                        log.info(f"Старая игра с высоким рейтингом: {deal.title} ({age} лет, {rating['score']}%)")
                        return True, "old_classic"
                    
                    log.info(f"Отклонено (старая игра + малая скидка): {deal.title} ({age} лет, -{deal.discount}%)")
                    return False, f"old_game_small_discount"
            except Exception:
                pass
    
    return True, "passed_filters"



def generate_context_comment(deal, rating: Optional[dict], igdb_info: Optional[dict]) -> str:
    """
    Генерирует контекстный комментарий для старых/неизвестных игр.
    Объясняет, зачем на неё смотреть.
    """
    from enricher import generate_comment
    
    # Для новых игр с хорошим рейтингом используем стандартный комментарий
    if rating and rating["score"] >= 80:
        if igdb_info:
            release_date = igdb_info.get("release_date")
            if release_date:
                release_year = datetime.fromtimestamp(release_date).year
                if datetime.now().year - release_year <= 3:
                    return generate_comment(deal, rating)
    
    # Для старых/неизвестных игр добавляем контекст
    score = rating["score"] if rating else 0
    
    # Низкий рейтинг — предупреждаем
    if score < 75:
        context_parts = []
        
        # Ищем уникальные особенности в описании IGDB
        if igdb_info:
            summary = igdb_info.get("summary", "")
            
            # Ключевые слова для контекста
            if "unique" in summary.lower() or "стиль" in summary.lower():
                context_parts.append("Уникальный визуальный стиль")
            if "short" in summary.lower() or "короткий" in summary.lower():
                context_parts.append("короткое прохождение")
            if "cult" in summary.lower() or "культ" in summary.lower():
                context_parts.append("культовая среди фанатов")
        
        if context_parts:
            context = ", ".join(context_parts)
            return f"{context}. ⚠️ Игра на любителя, рейтинг {score}%."
        else:
            return f"⚠️ Игра на любителя, рейтинг {score}%. Берите только если знаете, что это."
    
    # Средний рейтинг — нейтральный комментарий
    if 75 <= score < 80:
        return f"Крепкий середняк (рейтинг {score}%). Фанатам жанра зайдёт."
    
    # Хороший рейтинг — стандартный комментарий
    return generate_comment(deal, rating)
