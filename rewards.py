"""
Система призов и магазин для обмена баллов.
"""
import logging
from typing import Optional, List
from datetime import datetime, timedelta

import pytz
from database import get_pool

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")


# Каталог призов — только то что реально выдаётся
REWARDS_CATALOG = {
    # Подписки (работают автоматически через has_active_reward)
    "priority_notify": {
        "name": "⚡️ Приоритетные уведомления",
        "description": "Получай уведомления о скидках на 5 минут раньше всех",
        "cost": 200,
        "type": "subscription",
        "duration_days": 7,
        "category": "subscriptions",
        "emoji": "⚡️",
    },
    "extended_wishlist": {
        "name": "💎 Расширенный вишлист",
        "description": "Лимит вишлиста увеличивается с 20 до 50 игр на 30 дней",
        "cost": 300,
        "type": "subscription",
        "duration_days": 30,
        "category": "subscriptions",
        "emoji": "💎",
    },

    # Разовые услуги (выдаются вручную администратором)
    "personal_deal": {
        "name": "🎯 Персональная подборка",
        "description": "Администратор составит подборку из 10 игр по твоим предпочтениям",
        "cost": 150,
        "type": "one_time",
        "category": "services",
        "emoji": "🎯",
    },

    # Значки в профиле (выдаются автоматически, хранятся в БД)
    "badge_vip": {
        "name": "👑 VIP значок",
        "description": "VIP значок в профиле навсегда",
        "cost": 500,
        "type": "permanent",
        "category": "badges",
        "emoji": "👑",
    },
    "badge_founder": {
        "name": "⭐️ Значок основателя",
        "description": "Эксклюзивный значок для первых 100 участников навсегда",
        "cost": 1000,
        "type": "permanent",
        "category": "badges",
        "emoji": "⭐️",
        "limited": 100,
    },
}

# Временные акции (скидки на призы)
ACTIVE_PROMOTIONS: dict = {}

# Эксклюзивные призы (пока пусто — добавим когда появятся реальные товары)
EXCLUSIVE_REWARDS: dict = {}


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
        
        if balance is None or balance < cost:
            return {"error": f"Недостаточно баллов. Нужно: {cost}, у тебя: {balance if balance is not None else 0}"}

        # Проверяем, не куплен ли уже permanent приз
        if reward["type"] == "permanent":
            existing = await conn.fetchval("""
                SELECT 1 FROM user_rewards
                WHERE user_id = $1 AND reward_id = $2
            """, user_id, reward_id)
            if existing:
                return {"error": "Ты уже купил этот приз!"}

        # Атомарно списываем баллы и добавляем приз в одной транзакции
        async with conn.transaction():
            # Повторно проверяем баланс внутри транзакции (защита от race condition)
            locked_balance = await conn.fetchval("""
                SELECT total_score FROM user_scores WHERE user_id = $1 FOR UPDATE
            """, user_id)
            if locked_balance is None or locked_balance < cost:
                return {"error": f"Недостаточно баллов. Нужно: {cost}, у тебя: {locked_balance or 0}"}

            await conn.execute("""
                UPDATE user_scores SET total_score = total_score - $2 WHERE user_id = $1
            """, user_id, cost)

            expires_at = None
            if reward["type"] == "subscription":
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


# ============================================================================
# Новые функции для улучшенного магазина
# ============================================================================

async def get_user_rank(user_id: int) -> int:
    """Получить место пользователя в рейтинге."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rank = await conn.fetchval("""
            SELECT COUNT(*) + 1
            FROM user_scores
            WHERE total_score > (
                SELECT total_score FROM user_scores WHERE user_id = $1
            )
        """, user_id)
        return rank or 999


async def get_reward_price(reward_id: str, user_id: int = None) -> dict:
    """Получить цену приза с учётом акций."""
    if reward_id not in REWARDS_CATALOG and reward_id not in EXCLUSIVE_REWARDS:
        return {"error": "Приз не найден"}
    
    reward = REWARDS_CATALOG.get(reward_id) or EXCLUSIVE_REWARDS.get(reward_id)
    original_cost = reward["cost"]
    final_cost = original_cost
    discount = 0
    
    # Проверяем акции
    if reward_id in ACTIVE_PROMOTIONS:
        promo = ACTIVE_PROMOTIONS[reward_id]
        discount = promo.get("discount", 0)
        final_cost = int(original_cost * (1 - discount / 100))
    
    return {
        "original_cost": original_cost,
        "final_cost": final_cost,
        "discount": discount,
        "has_promotion": discount > 0,
    }


async def can_purchase_exclusive(user_id: int, reward_id: str) -> dict:
    """Проверить, может ли пользователь купить эксклюзивный приз."""
    if reward_id not in EXCLUSIVE_REWARDS:
        return {"can_purchase": True}
    
    reward = EXCLUSIVE_REWARDS[reward_id]
    required_rank = reward.get("required_rank", 999)
    
    user_rank = await get_user_rank(user_id)
    
    if user_rank > required_rank:
        return {
            "can_purchase": False,
            "error": f"Нужно быть в топ-{required_rank}. Твоё место: {user_rank}",
            "required_rank": required_rank,
            "user_rank": user_rank,
        }
    
    return {"can_purchase": True, "user_rank": user_rank}


async def reserve_reward(user_id: int, reward_id: str) -> dict:
    """Забронировать приз (для ограниченных призов)."""
    if reward_id not in REWARDS_CATALOG:
        return {"error": "Приз не найден"}
    
    reward = REWARDS_CATALOG[reward_id]
    
    # Проверяем, есть ли лимит
    if "limited" not in reward:
        return {"error": "Этот приз нельзя забронировать"}
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Проверяем, сколько уже куплено
        sold_count = await conn.fetchval("""
            SELECT COUNT(*) FROM user_rewards WHERE reward_id = $1
        """, reward_id)
        
        if sold_count >= reward["limited"]:
            return {"error": "Все экземпляры этого приза уже раскуплены!"}
        
        # Проверяем баланс
        balance = await get_user_balance(user_id)
        price_info = await get_reward_price(reward_id, user_id)
        
        if balance < price_info["final_cost"]:
            return {
                "error": f"Недостаточно баллов. Нужно: {price_info['final_cost']}, у тебя: {balance}",
                "needed": price_info["final_cost"] - balance,
            }
        
        # Создаём бронь (покупаем приз)
        result = await purchase_reward(user_id, reward_id)
        
        if "success" in result:
            remaining = reward["limited"] - sold_count - 1
            result["remaining"] = remaining
            result["limited"] = reward["limited"]
        
        return result


def format_rewards_shop_improved(user_id: int = None, balance: int = 0, category: str = "all") -> str:
    """Улучшенное форматирование магазина с категориями."""
    from html import escape as esc
    
    lines = [
        "🏪 <b>МАГАЗИН ПРИЗОВ</b>",
        f"💰 Баланс: <b>{balance}</b> баллов\n",
    ]
    
    # Если показываем все категории - краткий обзор
    if category == "all":
        lines.append("📂 <b>Выбери категорию:</b>\n")
        
        # Подсчитываем количество призов в каждой категории
        categories_count = {
            "subscriptions": 0,
            "games": 0,
            "services": 0,
            "badges": 0,
        }
        
        for reward in REWARDS_CATALOG.values():
            cat = reward.get("category", "other")
            if cat in categories_count:
                categories_count[cat] += 1
        
        lines.append(f"📅 <b>Подписки</b> — {categories_count['subscriptions']} шт.")
        lines.append("   Приоритетные уведомления, расширенный вишлист\n")
        
        lines.append(f"🎮 <b>Игровые ключи</b> — {categories_count['games']} шт.")
        lines.append("   Steam ключи на игры $5-20\n")
        
        lines.append(f"🌟 <b>Сервисы</b> — {categories_count['services']} шт.")
        lines.append("   Discord Nitro, Spotify, Xbox Game Pass\n")
        
        lines.append(f"⭐️ <b>Значки</b> — {categories_count['badges']} шт.")
        lines.append("   VIP статусы и эксклюзивные значки\n")
        
        lines.append("👇 Используй кнопки ниже для выбора категории")
        
        return "\n".join(lines)
    
    # Показываем конкретную категорию
    category_names = {
        "subscriptions": "📅 Подписки",
        "games": "🎮 Игровые ключи",
        "services": "🌟 Сервисы",
        "badges": "⭐️ Значки и статусы",
    }
    
    lines.append(f"<b>{category_names.get(category, 'Призы')}</b>\n")
    
    items = []
    for reward_id, reward in REWARDS_CATALOG.items():
        if reward.get("category") != category:
            continue
        
        # Получаем цену
        cost = reward["cost"]
        
        # Проверяем акции
        discount_text = ""
        if reward_id in ACTIVE_PROMOTIONS:
            promo = ACTIVE_PROMOTIONS[reward_id]
            discount = promo.get("discount", 0)
            final_cost = int(cost * (1 - discount / 100))
            discount_text = f" 🔥 <s>{cost}</s> → {final_cost}"
            cost = final_cost
        
        # Проверяем доступность
        can_afford = balance >= cost
        price_emoji = "💰" if can_afford else "🔒"
        
        # Длительность
        duration = ""
        if reward["type"] == "subscription":
            days = reward.get("duration_days", 0)
            duration = f" • {days}д"
        elif reward["type"] == "permanent":
            duration = " • навсегда"
        
        # Лимит
        limited = ""
        if "limited" in reward:
            limited = f" ⚠️ Лимит: {reward['limited']}"
        
        item = (
            f"{reward['emoji']} <b>{reward['name']}</b>{duration}\n"
            f"   {esc(reward['description'])}\n"
            f"   {price_emoji} <b>{cost}</b> баллов{discount_text}{limited}\n"
        )
        
        items.append((reward_id, item, can_afford))
    
    # Сортируем: сначала доступные, потом недоступные
    items.sort(key=lambda x: (not x[2], REWARDS_CATALOG[x[0]]["cost"]))
    
    for _, item, _ in items:
        lines.append(item)
    
    lines.append("💡 Нажми на кнопку приза для покупки")
    
    return "\n".join(lines)


def format_user_rewards_improved(rewards: List[dict], balance: int) -> str:
    """Улучшенное форматирование призов пользователя."""
    from html import escape as esc
    
    lines = [
        "📦 <b>МОИ ПРИЗЫ</b>",
        f"💰 Баланс: <b>{balance}</b> баллов\n",
    ]
    
    if not rewards:
        lines.append("📭 У тебя пока нет призов")
        lines.append("\n💡 Зарабатывай баллы в мини-играх")
        lines.append("и покупай крутые призы в /shop!")
        return "\n".join(lines)
    
    # Группируем по статусу
    active = []
    expired = []
    unclaimed = []
    
    for reward in rewards:
        if not reward.get("is_claimed", True) and "ключ" in reward["name"].lower():
            unclaimed.append(reward)
        elif reward.get("expires_at") and reward["expires_at"] < datetime.now(MSK):
            expired.append(reward)
        else:
            active.append(reward)
    
    # Неактивированные призы
    if unclaimed:
        lines.append("🎁 <b>Ожидают активации:</b>")
        for reward in unclaimed:
            lines.append(f"   {reward['name']}")
            lines.append(f"   ❗️ Используй /claim для получения")
        lines.append("")
    
    # Активные призы
    if active:
        lines.append("✅ <b>Активные призы:</b>")
        for reward in active:
            lines.append(f"   {reward['name']}")
            
            if reward.get("expires_at"):
                expires = reward["expires_at"].strftime("%d.%m.%Y")
                days_left = (reward["expires_at"] - datetime.now(MSK)).days
                lines.append(f"   ⏳ До {expires} ({days_left} дн.)")
            else:
                lines.append(f"   ♾ Навсегда")
        lines.append("")
    
    lines.append("💡 Используй /shop для покупки новых призов")
    
    return "\n".join(lines)
