"""
Система призов и магазин для обмена баллов.
"""
import logging
from typing import Optional, List
from datetime import datetime

import pytz
from database import get_pool

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")


# Каталог призов
REWARDS_CATALOG = {
    "priority_notify": {
        "name": "⚡️ Приоритетные уведомления",
        "description": "Получай уведомления о скидках на 5 минут раньше всех (на 7 дней)",
        "cost": 200,
        "type": "subscription",
        "duration_days": 7,
    },
    "custom_wishlist": {
        "name": "💎 Расширенный вишлист",
        "description": "Увеличь лимит вишлиста с 20 до 50 игр (на 30 дней)",
        "cost": 300,
        "type": "subscription",
        "duration_days": 30,
    },
    "exclusive_deals": {
        "name": "🔥 Эксклюзивные скидки",
        "description": "Доступ к закрытому каналу с эксклюзивными скидками (на 30 дней)",
        "cost": 500,
        "type": "subscription",
        "duration_days": 30,
    },
    "steam_key_5": {
        "name": "🎮 Steam ключ 5$",
        "description": "Случайный Steam ключ на игру стоимостью ~5$",
        "cost": 1000,
        "type": "one_time",
    },
    "steam_key_10": {
        "name": "🎮 Steam ключ 10$",
        "description": "Случайный Steam ключ на игру стоимостью ~10$",
        "cost": 1800,
        "type": "one_time",
    },
    "steam_key_20": {
        "name": "🎮 Steam ключ 20$",
        "description": "Случайный Steam ключ на игру стоимостью ~20$",
        "cost": 3500,
        "type": "one_time",
    },
    "personal_deal": {
        "name": "🎯 Персональная подборка",
        "description": "Получи персональную подборку из 10 игр по твоим предпочтениям",
        "cost": 150,
        "type": "one_time",
    },
    "badge_vip": {
        "name": "👑 VIP значок",
        "description": "Эксклюзивный VIP значок в профиле (навсегда)",
        "cost": 2000,
        "type": "permanent",
    },
    "badge_legend": {
        "name": "🏆 Значок легенды",
        "description": "Легендарный значок в профиле (навсегда)",
        "cost": 5000,
        "type": "permanent",
    },
}


async def init_rewards_table():
    """Создать таблицы для системы призов."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Таблица купленных призов
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_rewards (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                reward_id TEXT NOT NULL,
                purchased_at TIMESTAMPTZ DEFAULT NOW(),
                expires_at TIMESTAMPTZ,
                is_active BOOLEAN DEFAULT TRUE,
                is_claimed BOOLEAN DEFAULT FALSE
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_rewards_user_id ON user_rewards(user_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_rewards_active ON user_rewards(user_id, is_active)
        """)


async def get_user_balance(user_id: int) -> int:
    """Получить баланс баллов пользователя."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        balance = await conn.fetchval("""
            SELECT total_score FROM user_scores WHERE user_id = $1
        """, user_id)
        return balance or 0


async def purchase_reward(user_id: int, reward_id: str) -> dict:
    """Купить приз за баллы."""
    if reward_id not in REWARDS_CATALOG:
        return {"error": "Приз не найден"}
    
    reward = REWARDS_CATALOG[reward_id]
    cost = reward["cost"]
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем баланс
        balance = await conn.fetchval("""
            SELECT total_score FROM user_scores WHERE user_id = $1
        """, user_id)
        
        if not balance or balance < cost:
            return {"error": f"Недостаточно баллов. Нужно: {cost}, у тебя: {balance or 0}"}
        
        # Проверяем, не куплен ли уже permanent приз
        if reward["type"] == "permanent":
            existing = await conn.fetchval("""
                SELECT 1 FROM user_rewards
                WHERE user_id = $1 AND reward_id = $2
            """, user_id, reward_id)
            if existing:
                return {"error": "Ты уже купил этот приз!"}
        
        # Списываем баллы
        await conn.execute("""
            UPDATE user_scores
            SET total_score = total_score - $2
            WHERE user_id = $1
        """, user_id, cost)
        
        # Добавляем приз
        expires_at = None
        if reward["type"] == "subscription":
            from datetime import timedelta
            expires_at = datetime.now(MSK) + timedelta(days=reward["duration_days"])
        
        await conn.execute("""
            INSERT INTO user_rewards (user_id, reward_id, expires_at)
            VALUES ($1, $2, $3)
        """, user_id, reward_id, expires_at)
        
        log.info(f"Пользователь {user_id} купил приз {reward_id} за {cost} баллов")
    
    return {
        "success": True,
        "reward": reward,
        "cost": cost,
        "new_balance": balance - cost,
    }


async def get_user_rewards(user_id: int) -> List[dict]:
    """Получить все активные призы пользователя."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT reward_id, purchased_at, expires_at, is_claimed
            FROM user_rewards
            WHERE user_id = $1 AND is_active = TRUE
            ORDER BY purchased_at DESC
        """, user_id)
        
        result = []
        for row in rows:
            reward_id = row["reward_id"]
            if reward_id not in REWARDS_CATALOG:
                continue
            
            reward = REWARDS_CATALOG[reward_id]
            
            # Проверяем, не истёк ли срок
            if row["expires_at"] and row["expires_at"] < datetime.now(MSK):
                # Деактивируем истёкший приз
                await conn.execute("""
                    UPDATE user_rewards
                    SET is_active = FALSE
                    WHERE user_id = $1 AND reward_id = $2
                """, user_id, reward_id)
                continue
            
            result.append({
                "id": reward_id,
                "name": reward["name"],
                "description": reward["description"],
                "purchased_at": row["purchased_at"],
                "expires_at": row["expires_at"],
                "is_claimed": row["is_claimed"],
            })
        
        return result


async def has_active_reward(user_id: int, reward_id: str) -> bool:
    """Проверить, есть ли у пользователя активный приз."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchval("""
            SELECT 1 FROM user_rewards
            WHERE user_id = $1 AND reward_id = $2 AND is_active = TRUE
            AND (expires_at IS NULL OR expires_at > NOW())
        """, user_id, reward_id)
        return bool(result)


async def claim_reward(user_id: int, reward_id: str) -> dict:
    """Активировать приз (для one_time призов типа ключей)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем, есть ли неактивированный приз
        reward = await conn.fetchrow("""
            SELECT id FROM user_rewards
            WHERE user_id = $1 AND reward_id = $2 
            AND is_active = TRUE AND is_claimed = FALSE
            ORDER BY purchased_at DESC
            LIMIT 1
        """, user_id, reward_id)
        
        if not reward:
            return {"error": "Приз не найден или уже активирован"}
        
        # Отмечаем как активированный
        await conn.execute("""
            UPDATE user_rewards
            SET is_claimed = TRUE
            WHERE id = $1
        """, reward["id"])
        
        return {"success": True}


def format_rewards_shop() -> str:
    """Форматирует сообщение с магазином призов."""
    from html import escape as esc
    
    lines = ["🏪 <b>Магазин призов</b>\n"]
    
    # Группируем призы по типам
    subscriptions = []
    one_time = []
    permanent = []
    
    for reward_id, reward in REWARDS_CATALOG.items():
        item = f"{reward['name']}\n{esc(reward['description'])}\n💰 Цена: <b>{reward['cost']}</b> баллов"
        
        if reward["type"] == "subscription":
            subscriptions.append(item)
        elif reward["type"] == "one_time":
            one_time.append(item)
        else:
            permanent.append(item)
    
    if subscriptions:
        lines.append("<b>📅 Подписки:</b>\n")
        lines.extend(subscriptions)
        lines.append("")
    
    if one_time:
        lines.append("<b>🎁 Разовые призы:</b>\n")
        lines.extend(one_time)
        lines.append("")
    
    if permanent:
        lines.append("<b>⭐️ Навсегда:</b>\n")
        lines.extend(permanent)
        lines.append("")
    
    lines.append("💡 Используй /buy [название] для покупки")
    lines.append("📦 Используй /myrewards для просмотра купленных призов")
    
    return "\n".join(lines)


def format_user_rewards(rewards: List[dict], balance: int) -> str:
    """Форматирует сообщение с призами пользователя."""
    from html import escape as esc
    
    lines = [
        f"📦 <b>Мои призы</b>",
        f"💰 Баланс: <b>{balance}</b> баллов\n",
    ]
    
    if not rewards:
        lines.append("У тебя пока нет призов.")
        lines.append("\nИспользуй /shop для покупки призов!")
        return "\n".join(lines)
    
    lines.append("<b>Активные призы:</b>\n")
    
    for reward in rewards:
        lines.append(f"{reward['name']}")
        
        if reward["expires_at"]:
            expires = reward["expires_at"].strftime("%d.%m.%Y")
            lines.append(f"⏳ Действует до: {expires}")
        else:
            lines.append("♾ Навсегда")
        
        if not reward["is_claimed"] and "ключ" in reward["name"].lower():
            lines.append("❗️ Используй /claim для получения ключа")
        
        lines.append("")
    
    return "\n".join(lines)
