"""
Система наград и достижений для мини-игр.
"""
import logging
from typing import Optional, List
from datetime import datetime

import pytz
from database import get_pool

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")


# Определение всех достижений
ACHIEVEMENTS = {
    # Достижения за баллы
    "first_points": {
        "name": "🎯 Первые шаги",
        "description": "Заработай первые баллы",
        "requirement": "score >= 1",
        "reward_points": 5,
    },
    "score_100": {
        "name": "💯 Сотка",
        "description": "Набери 100 баллов",
        "requirement": "score >= 100",
        "reward_points": 20,
    },
    "score_500": {
        "name": "⭐️ Звезда",
        "description": "Набери 500 баллов",
        "requirement": "score >= 500",
        "reward_points": 50,
    },
    "score_1000": {
        "name": "🏆 Легенда",
        "description": "Набери 1000 баллов",
        "requirement": "score >= 1000",
        "reward_points": 100,
    },
    
    # Достижения за количество игр
    "games_10": {
        "name": "🎮 Игроман",
        "description": "Сыграй 10 игр",
        "requirement": "games_played >= 10",
        "reward_points": 15,
    },
    "games_50": {
        "name": "🎯 Профи",
        "description": "Сыграй 50 игр",
        "requirement": "games_played >= 50",
        "reward_points": 50,
    },
    "games_100": {
        "name": "🔥 Мастер",
        "description": "Сыграй 100 игр",
        "requirement": "games_played >= 100",
        "reward_points": 100,
    },
    
    # Достижения за точность
    "accuracy_80": {
        "name": "🎯 Снайпер",
        "description": "Достигни 80% точности (минимум 10 игр)",
        "requirement": "accuracy >= 80 and games_played >= 10",
        "reward_points": 30,
    },
    "accuracy_90": {
        "name": "🏹 Меткий стрелок",
        "description": "Достигни 90% точности (минимум 20 игр)",
        "requirement": "accuracy >= 90 and games_played >= 20",
        "reward_points": 75,
    },
    "perfect_10": {
        "name": "💎 Безупречный",
        "description": "10 правильных ответов подряд",
        "requirement": "streak >= 10",
        "reward_points": 50,
    },
    
    # Достижения за активность
    "daily_player": {
        "name": "📅 Ежедневник",
        "description": "Играй 7 дней подряд",
        "requirement": "daily_streak >= 7",
        "reward_points": 40,
    },
    "weekly_champion": {
        "name": "👑 Чемпион недели",
        "description": "Стань первым в таблице лидеров",
        "requirement": "leaderboard_position == 1",
        "reward_points": 100,
    },
    
    # Специальные достижения
    "challenge_master": {
        "name": "🎯 Мастер челленджей",
        "description": "Выполни 10 ежедневных челленджей",
        "requirement": "challenges_completed >= 10",
        "reward_points": 60,
    },
    "screenshot_expert": {
        "name": "📸 Эксперт скриншотов",
        "description": "Угадай 20 игр по скриншоту",
        "requirement": "screenshot_correct >= 20",
        "reward_points": 50,
    },
}


async def init_achievements_table():
    """Создать таблицу для достижений пользователей."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_achievements (
                user_id BIGINT,
                achievement_id TEXT,
                unlocked_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (user_id, achievement_id)
            )
        """)
        # Добавляем колонки для отслеживания прогресса
        await conn.execute("""
            ALTER TABLE user_scores 
            ADD COLUMN IF NOT EXISTS current_streak INT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS best_streak INT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS daily_streak INT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS last_daily_play DATE,
            ADD COLUMN IF NOT EXISTS screenshot_correct INT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS challenges_completed INT DEFAULT 0
        """)


async def check_and_unlock_achievements(user_id: int) -> List[dict]:
    """
    Проверяет и разблокирует новые достижения для пользователя.
    Возвращает список разблокированных достижений.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Получаем статистику пользователя
        stats = await conn.fetchrow("""
            SELECT 
                total_score as score,
                games_played,
                correct_answers,
                current_streak as streak,
                daily_streak,
                screenshot_correct,
                challenges_completed
            FROM user_scores
            WHERE user_id = $1
        """, user_id)
        
        if not stats:
            return []
        
        # Вычисляем точность
        accuracy = 0
        if stats['games_played'] > 0:
            accuracy = int(stats['correct_answers'] / stats['games_played'] * 100)
        
        # Получаем позицию в таблице лидеров
        leaderboard_position = await conn.fetchval("""
            SELECT position FROM (
                SELECT user_id, ROW_NUMBER() OVER (ORDER BY total_score DESC) as position
                FROM user_scores
            ) ranked
            WHERE user_id = $1
        """, user_id)
        
        # Получаем уже разблокированные достижения
        unlocked = await conn.fetch("""
            SELECT achievement_id FROM user_achievements WHERE user_id = $1
        """, user_id)
        unlocked_ids = {row['achievement_id'] for row in unlocked}
        
        # Проверяем каждое достижение
        newly_unlocked = []
        
        for achievement_id, achievement in ACHIEVEMENTS.items():
            if achievement_id in unlocked_ids:
                continue
            
            # Проверяем условие
            requirement = achievement['requirement']
            context = {
                'score': stats['score'],
                'games_played': stats['games_played'],
                'accuracy': accuracy,
                'streak': stats['streak'],
                'daily_streak': stats['daily_streak'],
                'leaderboard_position': leaderboard_position or 999,
                'challenges_completed': stats['challenges_completed'],
                'screenshot_correct': stats['screenshot_correct'],
            }
            
            try:
                if eval(requirement, {"__builtins__": {}}, context):
                    # Разблокируем достижение
                    await conn.execute("""
                        INSERT INTO user_achievements (user_id, achievement_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                    """, user_id, achievement_id)
                    
                    # Начисляем бонусные баллы
                    reward = achievement['reward_points']
                    await conn.execute("""
                        UPDATE user_scores
                        SET total_score = total_score + $2
                        WHERE user_id = $1
                    """, user_id, reward)
                    
                    newly_unlocked.append({
                        'id': achievement_id,
                        'name': achievement['name'],
                        'description': achievement['description'],
                        'reward': reward,
                    })
                    
                    log.info(f"Пользователь {user_id} разблокировал достижение: {achievement['name']}")
            except Exception as e:
                log.warning(f"Ошибка при проверке достижения {achievement_id}: {e}")
        
        return newly_unlocked


async def get_user_achievements(user_id: int) -> dict:
    """Получить все достижения пользователя."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        unlocked = await conn.fetch("""
            SELECT achievement_id, unlocked_at
            FROM user_achievements
            WHERE user_id = $1
            ORDER BY unlocked_at DESC
        """, user_id)
        
        unlocked_dict = {row['achievement_id']: row['unlocked_at'] for row in unlocked}
        
        result = {
            'unlocked': [],
            'locked': [],
            'total': len(ACHIEVEMENTS),
            'unlocked_count': len(unlocked_dict),
        }
        
        for achievement_id, achievement in ACHIEVEMENTS.items():
            achievement_data = {
                'id': achievement_id,
                'name': achievement['name'],
                'description': achievement['description'],
                'reward': achievement['reward_points'],
            }
            
            if achievement_id in unlocked_dict:
                achievement_data['unlocked_at'] = unlocked_dict[achievement_id]
                result['unlocked'].append(achievement_data)
            else:
                result['locked'].append(achievement_data)
        
        return result


async def update_streak(user_id: int, is_correct: bool):
    """Обновить серию правильных ответов."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if is_correct:
            await conn.execute("""
                UPDATE user_scores
                SET 
                    current_streak = current_streak + 1,
                    best_streak = GREATEST(best_streak, current_streak + 1)
                WHERE user_id = $1
            """, user_id)
        else:
            await conn.execute("""
                UPDATE user_scores
                SET current_streak = 0
                WHERE user_id = $1
            """, user_id)


async def update_daily_streak(user_id: int):
    """Обновить серию ежедневных игр."""
    today = datetime.now(MSK).date()
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        last_play = await conn.fetchval("""
            SELECT last_daily_play FROM user_scores WHERE user_id = $1
        """, user_id)
        
        if last_play is None:
            # Первая игра
            await conn.execute("""
                UPDATE user_scores
                SET daily_streak = 1, last_daily_play = $2
                WHERE user_id = $1
            """, user_id, today)
        elif last_play == today:
            # Уже играл сегодня
            pass
        elif (today - last_play).days == 1:
            # Играл вчера - продолжаем серию
            await conn.execute("""
                UPDATE user_scores
                SET daily_streak = daily_streak + 1, last_daily_play = $2
                WHERE user_id = $1
            """, user_id, today)
        else:
            # Пропустил дни - сбрасываем серию
            await conn.execute("""
                UPDATE user_scores
                SET daily_streak = 1, last_daily_play = $2
                WHERE user_id = $1
            """, user_id, today)


async def increment_screenshot_correct(user_id: int):
    """Увеличить счётчик правильных ответов в игре со скриншотами."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE user_scores
            SET screenshot_correct = screenshot_correct + 1
            WHERE user_id = $1
        """, user_id)


async def increment_challenges_completed(user_id: int):
    """Увеличить счётчик выполненных челленджей."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE user_scores
            SET challenges_completed = challenges_completed + 1
            WHERE user_id = $1
        """, user_id)


def format_achievements_message(achievements_data: dict) -> str:
    """Форматирует сообщение со списком достижений."""
    from html import escape as esc
    
    unlocked = achievements_data['unlocked']
    locked = achievements_data['locked']
    total = achievements_data['total']
    unlocked_count = achievements_data['unlocked_count']
    
    lines = [
        f"🏆 <b>Достижения ({unlocked_count}/{total})</b>\n",
    ]
    
    if unlocked:
        lines.append("<b>✅ Разблокированные:</b>\n")
        for ach in unlocked[:10]:  # Показываем только последние 10
            lines.append(f"{ach['name']} — {esc(ach['description'])}")
        if len(unlocked) > 10:
            lines.append(f"\n... и ещё {len(unlocked) - 10}")
    
    if locked:
        lines.append("\n<b>🔒 Заблокированные:</b>\n")
        for ach in locked[:5]:  # Показываем только первые 5
            lines.append(f"{ach['name']} — {esc(ach['description'])} (+{ach['reward']} баллов)")
        if len(locked) > 5:
            lines.append(f"\n... и ещё {len(locked) - 5}")
    
    return "\n".join(lines)
