import asyncpg
from config import DATABASE_URL, DB_CLEANUP_DAYS

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _pool


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS posted_deals (
                id SERIAL PRIMARY KEY,
                deal_id TEXT UNIQUE NOT NULL,
                title TEXT,
                store TEXT,
                discount INTEGER DEFAULT 0,
                link TEXT,
                posted_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Миграции для существующих БД
        await conn.execute("ALTER TABLE posted_deals ADD COLUMN IF NOT EXISTS link TEXT")
        await conn.execute("ALTER TABLE posted_deals ADD COLUMN IF NOT EXISTS old_price TEXT")
        await conn.execute("ALTER TABLE posted_deals ADD COLUMN IF NOT EXISTS new_price TEXT")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS wishlist (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                query TEXT NOT NULL,
                added_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, query)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                id SERIAL PRIMARY KEY,
                deal_id TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                vote TEXT NOT NULL,
                voted_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(deal_id, user_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS price_game (
                id SERIAL PRIMARY KEY,
                deal_id TEXT UNIQUE NOT NULL,
                original_price INTEGER NOT NULL,
                title TEXT DEFAULT '',
                new_price TEXT DEFAULT '',
                link TEXT DEFAULT '',
                discount INTEGER DEFAULT 0,
                posted_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id SERIAL PRIMARY KEY,
                deal_id TEXT NOT NULL,
                price NUMERIC NOT NULL,
                discount INTEGER DEFAULT 0,
                recorded_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_history_deal_id ON price_history(deal_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_history_recorded_at ON price_history(recorded_at)"
        )
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS steam_users (
                user_id BIGINT PRIMARY KEY,
                steam_id TEXT NOT NULL,
                wishlist_sync_enabled BOOLEAN DEFAULT TRUE,
                library_sync_enabled BOOLEAN DEFAULT TRUE,
                last_wishlist_sync TIMESTAMPTZ,
                last_library_sync TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS steam_library (
                user_id BIGINT NOT NULL,
                appid INTEGER NOT NULL,
                UNIQUE(user_id, appid)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS price_cache (
                game_title TEXT PRIMARY KEY,
                prices JSONB NOT NULL,
                cached_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS free_game_subs (
                user_id BIGINT PRIMARY KEY,
                subscribed_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # Индексы для ускорения частых запросов
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wishlist_user_id ON wishlist(user_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_steam_users_steam_id ON steam_users(steam_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_steam_library_user_id ON steam_library(user_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_steam_library_appid ON steam_library(appid)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_votes_deal_id ON votes(deal_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posted_deals_posted_at ON posted_deals(posted_at)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_cache_cached_at ON price_cache(cached_at)"
        )
        await init_metrics_table(conn)
        await init_genre_table(conn)
        await init_onboarding_tables(conn)
        await init_notification_tables(conn)
        await init_price_game_answers_table(conn)
        await init_engagement_table(conn)
    
    # Инициализация таблиц мини-игр
    # ОТКЛЮЧЕНО: мини-игры ломают бота
    # from minigames import init_minigames_db
    # await init_minigames_db()

    # Инициализация таблицы ключей магазина
    await init_shop_keys_table()


# --- posted_deals ---

async def is_already_posted(deal_id: str) -> bool:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT 1 FROM posted_deals WHERE deal_id = $1", deal_id
    )
    return row is not None


async def is_title_recently_posted(title: str, days: int = 14) -> bool:
    """Проверяет публиковалась ли игра с похожим названием за последние N дней.
    Использует частичное совпадение чтобы ловить 'The Outlast Trials' vs 'Outlast Trials'.
    """
    pool = await get_pool()
    # Убираем артикли и лишние слова для более надёжного сравнения
    clean = title.lower().strip()
    for prefix in ("the ", "a ", "an "):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    # Проверяем точное совпадение (без артиклей) ИЛИ вхождение одного в другое
    row = await pool.fetchrow(
        """SELECT 1 FROM posted_deals
           WHERE posted_at > NOW() - INTERVAL '1 day' * $2
             AND (
               LOWER(REGEXP_REPLACE(title, '^(the |a |an )', '', 'i')) = $1
               OR LOWER(title) ILIKE '%' || $3 || '%'
               OR $4 ILIKE '%' || LOWER(title) || '%'
             )
        """,
        clean, days, clean, clean
    )
    return row is not None


async def mark_as_posted(deal_id: str, title: str, store: str, discount: int = 0, link: str = "", old_price: str = "", new_price: str = ""):
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO posted_deals (deal_id, title, store, discount, link, old_price, new_price) VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT DO NOTHING",
        deal_id, title, store, discount, link, old_price, new_price,
    )


async def cleanup_old_records() -> int:
    pool = await get_pool()
    result = await pool.execute(
        f"DELETE FROM posted_deals WHERE posted_at < NOW() - INTERVAL '{DB_CLEANUP_DAYS} days'"
    )
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


async def get_weekly_top(limit: int = 10) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT title, store, discount, deal_id, link
        FROM posted_deals
        WHERE posted_at >= NOW() - INTERVAL '7 days'
        ORDER BY discount DESC
        LIMIT $1
    """, limit)
    return [dict(r) for r in rows]


async def search_posted_deals(query: str, limit: int = 20) -> list[dict]:
    """Поиск по опубликованным сделкам (для inline-режима)."""
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT title, store, discount, deal_id, link, posted_at
        FROM posted_deals
        WHERE title ILIKE $1
          AND posted_at >= NOW() - INTERVAL '30 days'
        ORDER BY discount DESC, posted_at DESC
        LIMIT $2
    """, f"%{query}%", limit)
    return [dict(r) for r in rows]


# --- wishlist ---

async def wishlist_add(user_id: int, query: str) -> bool:
    pool = await get_pool()
    # Проверяем лимит — 50 если есть активный extended_wishlist, иначе 20
    from rewards import has_active_reward
    limit = 50 if await has_active_reward(user_id, "extended_wishlist") else 20
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM wishlist WHERE user_id = $1", user_id
    )
    if count >= limit:
        return None  # None = лимит превышен (отличается от False = уже есть)
    try:
        await pool.execute(
            "INSERT INTO wishlist (user_id, query) VALUES ($1, $2)",
            user_id, query.lower().strip(),
        )
        return True
    except asyncpg.UniqueViolationError:
        return False


async def wishlist_remove(user_id: int, query: str) -> bool:
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM wishlist WHERE user_id = $1 AND query = $2",
        user_id, query.lower().strip(),
    )
    return result == "DELETE 1"


async def wishlist_list(user_id: int) -> list[str]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT query FROM wishlist WHERE user_id = $1 ORDER BY added_at",
        user_id,
    )
    return [r["query"] for r in rows]


async def get_wishlist_matches(title: str) -> list[int]:
    pool = await get_pool()
    title_low = title.lower()
    rows = await pool.fetch(
        """
        SELECT DISTINCT user_id FROM wishlist
        WHERE $1 ILIKE '%' || query || '%'
           OR query ILIKE '%' || $1 || '%'
        """,
        title_low,
    )
    return [r["user_id"] for r in rows]


# --- votes ---

async def add_vote(deal_id: str, user_id: int, vote: str) -> bool:
    pool = await get_pool()
    try:
        await pool.execute(
            "INSERT INTO votes (deal_id, user_id, vote) VALUES ($1, $2, $3)",
            deal_id, user_id, vote,
        )
        return True
    except asyncpg.UniqueViolationError:
        return False


async def get_votes(deal_id: str) -> dict:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT vote, COUNT(*) as cnt FROM votes WHERE deal_id = $1 GROUP BY vote",
        deal_id,
    )
    result = {"fire": 0, "poop": 0}
    for r in rows:
        if r["vote"] in result:
            result[r["vote"]] = r["cnt"]
    return result


async def get_top_voted(limit: int = 5) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT v.deal_id, p.title, p.store, p.link, COUNT(*) as fire_count
        FROM votes v
        JOIN posted_deals p ON p.deal_id = v.deal_id
        WHERE v.vote = 'fire'
          AND v.voted_at >= NOW() - INTERVAL '7 days'
        GROUP BY v.deal_id, p.title, p.store, p.link
        ORDER BY fire_count DESC
        LIMIT $1
    """, limit)
    return [dict(r) for r in rows]


# --- price_game ---

async def save_price_game(deal_id: str, original_price: int, title: str = "", new_price: str = "", link: str = "", discount: int = 0):
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO price_game (deal_id, original_price, title, new_price, link, discount)
           VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (deal_id) DO UPDATE
           SET original_price=$2, title=$3, new_price=$4, link=$5, discount=$6""",
        deal_id, original_price, title, new_price, link, discount,
    )


async def get_price_game(deal_id: str) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT original_price, title, new_price, link, discount FROM price_game WHERE deal_id = $1", deal_id
    )
    if not row:
        return None
    return dict(row)


# --- metrics ---

async def init_metrics_table(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            event TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            date DATE NOT NULL DEFAULT CURRENT_DATE,
            UNIQUE(event, date)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_metrics_date ON metrics(date)"
    )


async def increment_metric(event: str, amount: int = 1):
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO metrics (event, count, date) VALUES ($1, $2, CURRENT_DATE)
        ON CONFLICT (event, date) DO UPDATE SET count = metrics.count + $2
    """, event, amount)


async def get_metrics_summary(days: int = 7) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT event, SUM(count) as total
        FROM metrics
        WHERE date >= CURRENT_DATE - $1::int
        GROUP BY event
        ORDER BY total DESC
    """, days)
    return [dict(r) for r in rows]


async def wishlist_remove_user(user_id: int):
    """Удаляет все записи пользователя из вишлиста (при блокировке бота)."""
    pool = await get_pool()
    await pool.execute("DELETE FROM wishlist WHERE user_id = $1", user_id)


# --- genre subscriptions ---

async def init_genre_table(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS genre_subscriptions (
            user_id BIGINT NOT NULL,
            genre TEXT NOT NULL,
            UNIQUE(user_id, genre)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_genre_sub_user ON genre_subscriptions(user_id)"
    )


async def init_onboarding_tables(conn):
    """Создать таблицы для системы онбординга."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_progress (
            user_id BIGINT PRIMARY KEY,
            current_step INT DEFAULT 0,
            status TEXT DEFAULT 'in_progress',
            has_referral BOOLEAN DEFAULT FALSE,
            completed_at TIMESTAMPTZ,
            skipped_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    # Миграция для существующих БД
    await conn.execute("""
        ALTER TABLE onboarding_progress ADD COLUMN IF NOT EXISTS has_referral BOOLEAN DEFAULT FALSE
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_hints (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            hint_type TEXT NOT NULL,
            shown_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(user_id, hint_type)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_onboarding_hints_user ON onboarding_hints(user_id)"
    )


# --- onboarding progress ---

async def get_onboarding_progress(user_id: int) -> dict | None:
    try:
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT user_id, current_step, status, has_referral, completed_at, skipped_at, created_at, updated_at "
            "FROM onboarding_progress WHERE user_id = $1",
            user_id,
        )
        return dict(row) if row else None
    except Exception as e:
        import logging
        log = logging.getLogger(__name__)
        log.error(f"Error fetching onboarding progress for user {user_id}: {e}")
        return None


async def create_onboarding_progress(user_id: int, has_referral: bool = False) -> bool:
    try:
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO onboarding_progress (user_id, has_referral) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO NOTHING",
            user_id, has_referral,
        )
        return True
    except Exception as e:
        import logging
        log = logging.getLogger(__name__)
        log.error(f"Error creating onboarding progress for user {user_id}: {e}")
        return False


async def update_onboarding_step(user_id: int, step: int) -> bool:
    """
    Обновить текущий шаг онбординга пользователя.
    
    Args:
        user_id: Telegram user ID
        step: Current step number
        
    Returns:
        True on success, False on error
    """
    try:
        pool = await get_pool()
        await pool.execute(
            "UPDATE onboarding_progress SET current_step = $2, updated_at = NOW() WHERE user_id = $1",
            user_id, step,
        )
        return True
    except Exception as e:
        import logging
        log = logging.getLogger(__name__)
        log.error(f"Error updating onboarding step for user {user_id} to step {step}: {e}")
        return False


async def complete_onboarding(user_id: int) -> bool:
    """
    Отметить онбординг как завершённый.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        True on success, False on error
    """
    try:
        pool = await get_pool()
        await pool.execute(
            "UPDATE onboarding_progress SET status = 'completed', completed_at = NOW(), updated_at = NOW() "
            "WHERE user_id = $1",
            user_id,
        )
        return True
    except Exception as e:
        import logging
        log = logging.getLogger(__name__)
        log.error(f"Error completing onboarding for user {user_id}: {e}")
        return False


async def skip_onboarding(user_id: int) -> bool:
    """
    Отметить онбординг как пропущенный.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        True on success, False on error
    """
    try:
        pool = await get_pool()
        await pool.execute(
            "UPDATE onboarding_progress SET status = 'skipped', skipped_at = NOW(), updated_at = NOW() "
            "WHERE user_id = $1",
            user_id,
        )
        return True
    except Exception as e:
        import logging
        log = logging.getLogger(__name__)
        log.error(f"Error skipping onboarding for user {user_id}: {e}")
        return False


async def save_hint_shown(user_id: int, hint_type: str) -> bool:
    """
    Сохранить показанную подсказку.
    
    Args:
        user_id: Telegram user ID
        hint_type: Type of hint shown (e.g., 'wishlist_vote', 'minigame_challenge')
        
    Returns:
        True on success, False on error
    """
    try:
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO onboarding_hints (user_id, hint_type) "
            "VALUES ($1, $2) "
            "ON CONFLICT (user_id, hint_type) DO NOTHING",
            user_id, hint_type,
        )
        return True
    except Exception as e:
        import logging
        log = logging.getLogger(__name__)
        log.error(f"Error saving hint for user {user_id}, hint_type {hint_type}: {e}")
        return False


async def get_shown_hints(user_id: int) -> list[str]:
    """
    Получить список показанных подсказок.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        List of hint types that have been shown to the user
    """
    try:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT hint_type FROM onboarding_hints WHERE user_id = $1",
            user_id,
        )
        return [row["hint_type"] for row in rows]
    except Exception as e:
        import logging
        log = logging.getLogger(__name__)
        log.error(f"Error fetching shown hints for user {user_id}: {e}")
        return []


async def get_user_registration_date(user_id: int):
    """
    Получить дату регистрации пользователя.
    Берёт наиболее раннюю дату из onboarding_progress и user_scores,
    чтобы корректно определять возраст аккаунта независимо от пути входа.

    Returns:
        datetime object или None если пользователь не найден ни в одной таблице
    """
    try:
        pool = await get_pool()
        row = await pool.fetchrow("""
            SELECT LEAST(
                MIN(op.created_at),
                MIN(us.created_at)
            ) AS earliest
            FROM (SELECT created_at FROM onboarding_progress WHERE user_id = $1) op
            FULL OUTER JOIN (SELECT created_at FROM user_scores WHERE user_id = $1) us ON TRUE
        """, user_id)
        if not row or row["earliest"] is None:
            return None
        return row["earliest"]
    except Exception as e:
        import logging
        log = logging.getLogger(__name__)
        log.error(f"Error fetching registration date for user {user_id}: {e}")
        return None


# --- price history ---

async def save_price_history(deal_id: str, price: float, discount: int):
    """
    Сохраняет историю цен для отслеживания аномалий.
    
    Args:
        deal_id: ID сделки
        price: Текущая цена
        discount: Процент скидки
    """
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO price_history (deal_id, price, discount) VALUES ($1, $2, $3)",
        deal_id, price, discount,
    )


async def get_previous_price(deal_id: str) -> dict | None:
    """
    Получает предыдущую цену игры для обнаружения резких падений.
    
    Args:
        deal_id: ID сделки
        
    Returns:
        Dict с предыдущей ценой и скидкой или None
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT price, discount FROM price_history "
        "WHERE deal_id = $1 "
        "ORDER BY recorded_at DESC "
        "LIMIT 1 OFFSET 1",  # Берём предпоследнюю запись
        deal_id,
    )
    return dict(row) if row else None


async def cleanup_price_history():
    """
    Удаляет историю цен старше 30 дней.
    
    Returns:
        Количество удалённых записей
    """
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM price_history WHERE recorded_at < NOW() - INTERVAL '30 days'"
    )
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


async def genre_subscribe(user_id: int, genre: str) -> bool:
    pool = await get_pool()
    try:
        await pool.execute(
            "INSERT INTO genre_subscriptions (user_id, genre) VALUES ($1, $2)",
            user_id, genre.lower().strip(),
        )
        return True
    except asyncpg.UniqueViolationError:
        return False


async def genre_unsubscribe(user_id: int, genre: str) -> bool:
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM genre_subscriptions WHERE user_id = $1 AND genre = $2",
        user_id, genre.lower().strip(),
    )
    return result == "DELETE 1"


async def genre_list(user_id: int) -> list[str]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT genre FROM genre_subscriptions WHERE user_id = $1 ORDER BY genre",
        user_id,
    )
    return [r["genre"] for r in rows]


async def get_genre_subscribers(genre: str) -> list[int]:
    """Возвращает user_id всех подписчиков на жанр."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT user_id FROM genre_subscriptions WHERE genre = $1",
        genre.lower().strip(),
    )
    return [r["user_id"] for r in rows]


async def get_all_genre_subscribers_for_deal(genres: list[str]) -> list[int]:
    """Возвращает уникальных user_id подписанных на любой из жанров сделки."""
    if not genres:
        return []
    pool = await get_pool()
    genres_lower = [g.lower() for g in genres]
    rows = await pool.fetch(
        "SELECT DISTINCT user_id FROM genre_subscriptions WHERE genre = ANY($1::text[])",
        genres_lower,
    )
    return [r["user_id"] for r in rows]


# --- steam integration ---

async def steam_link_account(user_id: int, steam_id: str) -> bool:
    """
    Links a Steam account to a user.
    Returns True on success, False if already linked (duplicate).
    """
    pool = await get_pool()
    try:
        await pool.execute(
            "INSERT INTO steam_users (user_id, steam_id) VALUES ($1, $2)",
            user_id, steam_id,
        )
        return True
    except asyncpg.UniqueViolationError:
        return False


async def steam_unlink_account(user_id: int) -> bool:
    """
    Unlinks a Steam account and deletes all associated data.
    Deletes from steam_users and steam_library for the given user_id.
    Returns True if any records were deleted.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Delete from steam_library first
            library_result = await conn.execute(
                "DELETE FROM steam_library WHERE user_id = $1",
                user_id,
            )
            # Delete from steam_users
            users_result = await conn.execute(
                "DELETE FROM steam_users WHERE user_id = $1",
                user_id,
            )
            # Return True if any records were deleted
            library_deleted = int(library_result.split()[-1]) if library_result.startswith("DELETE") else 0
            users_deleted = int(users_result.split()[-1]) if users_result.startswith("DELETE") else 0
            return (library_deleted + users_deleted) > 0


async def steam_get_user(user_id: int) -> dict | None:
    """
    Fetches steam_users row by user_id.
    Returns dict with user data or None if not found.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT user_id, steam_id, wishlist_sync_enabled, library_sync_enabled, "
        "last_wishlist_sync, last_library_sync, created_at FROM steam_users WHERE user_id = $1",
        user_id,
    )
    return dict(row) if row else None


async def steam_update_sync_time(user_id: int, sync_type: str):
    """
    Updates last_wishlist_sync or last_library_sync to current timestamp.
    sync_type should be 'wishlist' or 'library'.
    """
    pool = await get_pool()
    if sync_type == "wishlist":
        await pool.execute(
            "UPDATE steam_users SET last_wishlist_sync = NOW() WHERE user_id = $1",
            user_id,
        )
    elif sync_type == "library":
        await pool.execute(
            "UPDATE steam_users SET last_library_sync = NOW() WHERE user_id = $1",
            user_id,
        )


async def steam_get_all_synced_users() -> list[dict]:
    """
    Returns list of all users with wishlist_sync_enabled or library_sync_enabled.
    Used by scheduler jobs for automatic synchronization.
    Returns list of dicts with user data.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT user_id, steam_id, wishlist_sync_enabled, library_sync_enabled, "
        "last_wishlist_sync, last_library_sync FROM steam_users "
        "WHERE wishlist_sync_enabled = TRUE OR library_sync_enabled = TRUE"
    )
    return [dict(row) for row in rows]


async def steam_library_replace(user_id: int, appids: list[int]):
    """
    Replaces user's Steam library with new list of app IDs.
    Deletes existing library entries for user_id, then batch inserts new appids.
    Uses ON CONFLICT DO NOTHING to handle duplicates gracefully.
    All operations are performed in a single transaction for atomicity.
    
    Args:
        user_id: Telegram user ID
        appids: List of Steam app IDs to store
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Delete existing library entries for this user
            await conn.execute(
                "DELETE FROM steam_library WHERE user_id = $1",
                user_id,
            )
            
            # Batch insert new appids if list is not empty
            if appids:
                # Prepare values for batch insert
                values = [(user_id, appid) for appid in appids]
                await conn.executemany(
                    "INSERT INTO steam_library (user_id, appid) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    values,
                )


async def steam_library_contains(user_id: int, appid: int) -> bool:
    """
    Checks if a specific app ID exists in the user's Steam library.
    
    Args:
        user_id: Telegram user ID
        appid: Steam app ID to check
        
    Returns:
        True if the game is in the user's library, False otherwise
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT 1 FROM steam_library WHERE user_id = $1 AND appid = $2",
        user_id,
        appid,
    )
    return row is not None


async def steam_library_filter_deals(user_id: int, deals: list) -> list:
    """
    Filters a list of Deal objects, excluding games owned by the user.
    
    Args:
        user_id: Telegram user ID
        deals: List of Deal objects to filter
        
    Returns:
        List of Deal objects excluding owned games
    """
    if not deals:
        return []
    
    pool = await get_pool()
    
    # Get all owned app IDs for this user
    rows = await pool.fetch(
        "SELECT appid FROM steam_library WHERE user_id = $1",
        user_id,
    )
    owned_appids = {row["appid"] for row in rows}
    
    # Filter out deals where the app ID is in the owned set
    # Deal objects have deal_id in format "steam_{appid}" or other store formats
    filtered_deals = []
    for deal in deals:
        # Extract appid from deal_id if it's a Steam deal
        if deal.deal_id.startswith("steam_"):
            try:
                appid = int(deal.deal_id.split("_", 1)[1])
                if appid in owned_appids:
                    continue  # Skip owned games
            except (ValueError, IndexError):
                pass  # If parsing fails, include the deal
        
        filtered_deals.append(deal)
    
    return filtered_deals


# --- price cache ---

async def price_cache_get(game_title: str) -> dict | None:
    """
    Fetches cached price comparison results if cached_at is within 6 hours.
    
    Args:
        game_title: Game title to look up in cache
        
    Returns:
        Dict with prices data if cache is fresh, None otherwise
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT prices, cached_at FROM price_cache "
        "WHERE game_title = $1 AND cached_at >= NOW() - INTERVAL '6 hours'",
        game_title,
    )
    if not row:
        return None
    import json
    result = dict(row)
    if isinstance(result.get("prices"), str):
        result["prices"] = json.loads(result["prices"])
    return result


async def price_cache_set(game_title: str, prices: dict):
    """
    Upserts price comparison results with current timestamp.
    
    Args:
        game_title: Game title as cache key
        prices: Dict with price data to cache (will be stored as JSONB)
    """
    import json
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO price_cache (game_title, prices, cached_at) "
        "VALUES ($1, $2::jsonb, NOW()) "
        "ON CONFLICT (game_title) DO UPDATE SET prices = $2::jsonb, cached_at = NOW()",
        game_title, json.dumps(prices),
    )


async def price_cache_cleanup():
    """
    Deletes price cache entries older than 6 hours.
    Should be called by scheduled job to prevent cache table growth.
    
    Returns:
        Number of deleted records
    """
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM price_cache WHERE cached_at < NOW() - INTERVAL '6 hours'"
    )
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


# --- free game subscriptions ---

async def free_game_subscribe(user_id: int) -> bool:
    """
    Subscribes a user to free game notifications.
    Inserts user_id into free_game_subs table.
    
    Args:
        user_id: Telegram user ID to subscribe
        
    Returns:
        True on success, False if already subscribed (duplicate)
    """
    pool = await get_pool()
    try:
        await pool.execute(
            "INSERT INTO free_game_subs (user_id) VALUES ($1)",
            user_id,
        )
        return True
    except asyncpg.UniqueViolationError:
        return False


async def free_game_unsubscribe(user_id: int) -> bool:
    """
    Unsubscribes a user from free game notifications.
    Deletes user_id from free_game_subs table.
    
    Args:
        user_id: Telegram user ID to unsubscribe
        
    Returns:
        True if user was unsubscribed, False if user was not subscribed
    """
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM free_game_subs WHERE user_id = $1",
        user_id,
    )
    return result == "DELETE 1"


async def free_game_get_subscribers() -> list[int]:
    """
    Returns list of all user IDs subscribed to free game notifications.
    Used by free game monitor to send direct messages to subscribers.
    
    Returns:
        List of Telegram user IDs
    """
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT user_id FROM free_game_subs ORDER BY subscribed_at"
    )
    return [row["user_id"] for row in rows]


# --- notification settings ---

async def init_notification_tables(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_notification_settings (
            user_id BIGINT PRIMARY KEY,
            min_discount INTEGER DEFAULT 0,
            quiet_start INTEGER DEFAULT 23,
            quiet_end INTEGER DEFAULT 8,
            grouping_enabled BOOLEAN DEFAULT FALSE,
        preferred_stores TEXT[] DEFAULT ARRAY['steam','epic games'],
            ignored_genres TEXT[] DEFAULT ARRAY[]::TEXT[],
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    # Миграция для существующих БД
    await conn.execute("""
        ALTER TABLE user_notification_settings
        ADD COLUMN IF NOT EXISTS preferred_stores TEXT[] DEFAULT ARRAY['steam','epic games']
    """)
    await conn.execute("""
        ALTER TABLE user_notification_settings
        ADD COLUMN IF NOT EXISTS ignored_genres TEXT[] DEFAULT ARRAY[]::TEXT[]
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_queue (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            deal_id TEXT NOT NULL,
            deal_title TEXT NOT NULL,
            deal_store TEXT NOT NULL,
            deal_old_price TEXT NOT NULL,
            deal_new_price TEXT NOT NULL,
            deal_discount INTEGER NOT NULL,
            deal_link TEXT NOT NULL,
            deal_is_free BOOLEAN DEFAULT FALSE,
            queued_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(user_id, deal_id)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notif_queue_user_id ON notification_queue(user_id)"
    )


async def notif_settings_get(user_id: int) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT min_discount, quiet_start, quiet_end, grouping_enabled, "
        "preferred_stores, ignored_genres "
        "FROM user_notification_settings WHERE user_id = $1",
        user_id,
    )
    if row:
        d = dict(row)
        # asyncpg возвращает массивы как list, но может быть None при старой схеме
        if d["preferred_stores"] is None:
            d["preferred_stores"] = ["steam", "epic games"]
        if d["ignored_genres"] is None:
            d["ignored_genres"] = []
        return d
    return {
        "min_discount": 0,
        "quiet_start": 23,
        "quiet_end": 8,
        "grouping_enabled": False,
        "preferred_stores": ["steam", "epic games"],
        "ignored_genres": [],
    }


async def notif_settings_set(user_id: int, **kwargs):
    pool = await get_pool()
    current = await notif_settings_get(user_id)
    current.update(kwargs)
    await pool.execute(
        """
        INSERT INTO user_notification_settings
            (user_id, min_discount, quiet_start, quiet_end, grouping_enabled,
             preferred_stores, ignored_genres, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            min_discount = $2, quiet_start = $3, quiet_end = $4,
            grouping_enabled = $5, preferred_stores = $6,
            ignored_genres = $7, updated_at = NOW()
        """,
        user_id,
        current["min_discount"],
        current["quiet_start"],
        current["quiet_end"],
        current["grouping_enabled"],
        current["preferred_stores"],
        current["ignored_genres"],
    )


async def notif_queue_add(user_id: int, deal) -> bool:
    pool = await get_pool()
    try:
        await pool.execute(
            """
            INSERT INTO notification_queue
                (user_id, deal_id, deal_title, deal_store, deal_old_price,
                 deal_new_price, deal_discount, deal_link, deal_is_free)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (user_id, deal_id) DO NOTHING
            """,
            user_id, deal.deal_id, deal.title, deal.store,
            str(deal.old_price), str(deal.new_price),
            deal.discount, deal.link, deal.is_free,
        )
        return True
    except Exception:
        return False


async def notif_queue_pop(user_id: int) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "DELETE FROM notification_queue WHERE user_id = $1 RETURNING *",
        user_id,
    )
    return [dict(r) for r in rows]


async def notif_queue_get_users_with_pending() -> list[int]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT DISTINCT user_id FROM notification_queue"
    )
    return [r["user_id"] for r in rows]


# --- price game answers (anti-abuse) ---

async def init_price_game_answers_table(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS price_game_answers (
            user_id BIGINT NOT NULL,
            deal_id TEXT NOT NULL,
            answered_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (user_id, deal_id)
        )
    """)


async def record_price_game_answer(user_id: int, deal_id: str) -> bool:
    """Записать ответ пользователя на игру угадай цену.
    Возвращает True если ответ принят (первый раз), False если уже отвечал."""
    pool = await get_pool()
    result = await pool.execute(
        "INSERT INTO price_game_answers (user_id, deal_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        user_id, deal_id,
    )
    return result == "INSERT 0 1"


# --- shop_keys: хранилище ключей для магазина ---

async def init_shop_keys_table():
    """Создать таблицу ключей магазина."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS shop_keys (
                id SERIAL PRIMARY KEY,
                reward_id TEXT NOT NULL,
                game_title TEXT NOT NULL,
                key_value TEXT NOT NULL,
                platform TEXT DEFAULT 'steam',
                original_price_usd NUMERIC DEFAULT 0,
                source TEXT DEFAULT 'manual',
                added_at TIMESTAMPTZ DEFAULT NOW(),
                claimed_by BIGINT,
                claimed_at TIMESTAMPTZ,
                is_available BOOLEAN DEFAULT TRUE
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_shop_keys_reward_id ON shop_keys(reward_id, is_available)"
        )


async def add_shop_key(
    reward_id: str,
    game_title: str,
    key_value: str,
    platform: str = "steam",
    original_price_usd: float = 0.0,
    source: str = "manual",
) -> int:
    """Добавить ключ в магазин. Возвращает id записи."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row_id = await conn.fetchval("""
            INSERT INTO shop_keys (reward_id, game_title, key_value, platform, original_price_usd, source)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """, reward_id, game_title, key_value, platform, original_price_usd, source)
        return row_id


async def claim_shop_key(reward_id: str, user_id: int) -> dict | None:
    """
    Атомарно выдать один доступный ключ пользователю.
    Возвращает dict с key_value и game_title или None если ключей нет.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("""
                SELECT id, key_value, game_title
                FROM shop_keys
                WHERE reward_id = $1 AND is_available = TRUE
                ORDER BY added_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """, reward_id)
            if not row:
                return None
            await conn.execute("""
                UPDATE shop_keys
                SET is_available = FALSE, claimed_by = $2, claimed_at = NOW()
                WHERE id = $1
            """, row["id"], user_id)
            return {"key_value": row["key_value"], "game_title": row["game_title"]}


async def get_shop_key_stats() -> list[dict]:
    """Статистика ключей по reward_id: сколько доступно и выдано."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                reward_id,
                game_title,
                COUNT(*) FILTER (WHERE is_available) AS available,
                COUNT(*) FILTER (WHERE NOT is_available) AS claimed
            FROM shop_keys
            GROUP BY reward_id, game_title
            ORDER BY reward_id
        """)
        return [dict(r) for r in rows]


async def get_available_key_reward_ids() -> list[str]:
    """Список reward_id у которых есть хотя бы один доступный ключ."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT reward_id FROM shop_keys WHERE is_available = TRUE
        """)
        return [r["reward_id"] for r in rows]


# --- weekly garbage collection ---

async def db_garbage_collect() -> dict[str, int]:
    """
    Еженедельная очистка базы данных.

    Удаляет:
    - price_history          > 90 дней
    - posted_deals           > 365 дней  (мёртвые игры)
    - votes                  > 365 дней
    - metrics                > 90 дней
    - user_score_history     > 90 дней
    - screenshot_games       > 30 дней   (+ связанные ответы)
    - screenshot_answers     > 30 дней
    - daily_challenges       > 90 дней   (+ completions)
    - daily_challenge_completions > 90 дней
    - price_game             > 90 дней   (+ answers)
    - price_game_answers     > 90 дней
    - notification_queue     > 7 дней    (зависшие)
    - onboarding_hints       > 180 дней

    Возвращает словарь {таблица: кол-во удалённых строк}.
    """
    import logging as _logging
    log = _logging.getLogger(__name__)

    pool = await get_pool()
    stats: dict[str, int] = {}

    def _count(result: str) -> int:
        try:
            return int(result.split()[-1])
        except Exception:
            return 0

    async with pool.acquire() as conn:
        # price_history — 90 дней
        r = await conn.execute(
            "DELETE FROM price_history WHERE recorded_at < NOW() - INTERVAL '90 days'"
        )
        stats["price_history"] = _count(r)

        # posted_deals — 365 дней (мёртвые игры)
        r = await conn.execute(
            "DELETE FROM posted_deals WHERE posted_at < NOW() - INTERVAL '365 days'"
        )
        stats["posted_deals_old"] = _count(r)

        # votes — 365 дней
        r = await conn.execute(
            "DELETE FROM votes WHERE voted_at < NOW() - INTERVAL '365 days'"
        )
        stats["votes"] = _count(r)

        # metrics — 90 дней
        r = await conn.execute(
            "DELETE FROM metrics WHERE date < CURRENT_DATE - INTERVAL '90 days'"
        )
        stats["metrics"] = _count(r)

        # user_score_history — 90 дней
        r = await conn.execute(
            "DELETE FROM user_score_history WHERE recorded_at < NOW() - INTERVAL '90 days'"
        )
        stats["user_score_history"] = _count(r)

        # screenshot_answers — 30 дней (сначала, т.к. нет FK cascade)
        r = await conn.execute(
            "DELETE FROM screenshot_answers WHERE answered_at < NOW() - INTERVAL '30 days'"
        )
        stats["screenshot_answers"] = _count(r)

        # screenshot_games — 30 дней
        r = await conn.execute(
            "DELETE FROM screenshot_games WHERE created_at < NOW() - INTERVAL '30 days'"
        )
        stats["screenshot_games"] = _count(r)

        # daily_challenge_completions — 90 дней
        r = await conn.execute(
            "DELETE FROM daily_challenge_completions "
            "WHERE challenge_date < CURRENT_DATE - INTERVAL '90 days'"
        )
        stats["daily_challenge_completions"] = _count(r)

        # daily_challenges — 90 дней
        r = await conn.execute(
            "DELETE FROM daily_challenges "
            "WHERE challenge_date < CURRENT_DATE - INTERVAL '90 days'"
        )
        stats["daily_challenges"] = _count(r)

        # price_game_answers — 90 дней
        r = await conn.execute(
            "DELETE FROM price_game_answers WHERE answered_at < NOW() - INTERVAL '90 days'"
        )
        stats["price_game_answers"] = _count(r)

        # price_game — 90 дней
        r = await conn.execute(
            "DELETE FROM price_game WHERE posted_at < NOW() - INTERVAL '90 days'"
        )
        stats["price_game"] = _count(r)

        # notification_queue — зависшие старше 7 дней
        r = await conn.execute(
            "DELETE FROM notification_queue WHERE queued_at < NOW() - INTERVAL '7 days'"
        )
        stats["notification_queue_stale"] = _count(r)

        # onboarding_hints — 180 дней
        r = await conn.execute(
            "DELETE FROM onboarding_hints WHERE shown_at < NOW() - INTERVAL '180 days'"
        )
        stats["onboarding_hints"] = _count(r)

    total = sum(stats.values())
    log.info(
        f"GC завершён: удалено {total} строк — "
        + ", ".join(f"{k}={v}" for k, v in stats.items() if v > 0)
    )
    return stats


# --- deal engagement analytics ---

async def init_engagement_table(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS deal_engagement (
            deal_id     TEXT PRIMARY KEY,
            title       TEXT NOT NULL DEFAULT '',
            store       TEXT NOT NULL DEFAULT '',
            discount    INTEGER NOT NULL DEFAULT 0,
            impressions INTEGER NOT NULL DEFAULT 0,
            fire_votes  INTEGER NOT NULL DEFAULT 0,
            poop_votes  INTEGER NOT NULL DEFAULT 0,
            wl_adds     INTEGER NOT NULL DEFAULT 0,
            store_clicks INTEGER NOT NULL DEFAULT 0,
            first_seen  TIMESTAMPTZ DEFAULT NOW(),
            last_event  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_engagement_store ON deal_engagement(store)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_engagement_last_event ON deal_engagement(last_event)"
    )


async def engagement_impression(deal_id: str, title: str, store: str, discount: int):
    """Фиксирует публикацию поста (impression)."""
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO deal_engagement (deal_id, title, store, discount, impressions, last_event)
        VALUES ($1, $2, $3, $4, 1, NOW())
        ON CONFLICT (deal_id) DO UPDATE SET
            impressions = deal_engagement.impressions + 1,
            last_event  = NOW()
    """, deal_id, title, store, discount)


async def engagement_event(deal_id: str, event: str):
    """
    Инкрементирует счётчик события для сделки.
    event: 'fire' | 'poop' | 'wl_add' | 'store_click'
    """
    col_map = {
        "fire":        "fire_votes",
        "poop":        "poop_votes",
        "wl_add":      "wl_adds",
        "store_click": "store_clicks",
    }
    col = col_map.get(event)
    if not col:
        return
    pool = await get_pool()
    # Только обновляем — запись должна уже существовать после impression
    # col берётся из whitelist-словаря выше, интерполяция безопасна
    await pool.execute(f"""
        UPDATE deal_engagement
        SET {col} = {col} + 1, last_event = NOW()
        WHERE deal_id = $1
    """, deal_id)


async def get_engagement_top(days: int = 7, limit: int = 10) -> list[dict]:
    """Топ сделок по вовлечённости за последние N дней."""
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT
            deal_id, title, store, discount,
            impressions, fire_votes, poop_votes, wl_adds, store_clicks,
            -- engagement score: взвешенная сумма
            (fire_votes * 3 + wl_adds * 5 + store_clicks * 2 - poop_votes) AS eng_score,
            -- CTR вишлиста (%)
            CASE WHEN impressions > 0
                 THEN ROUND(wl_adds::numeric / impressions * 100, 1)
                 ELSE 0 END AS wl_ctr
        FROM deal_engagement
        WHERE last_event >= NOW() - ($1 || ' days')::INTERVAL
        ORDER BY eng_score DESC
        LIMIT $2
    """, str(days), limit)
    return [dict(r) for r in rows]


async def get_engagement_summary(days: int = 7) -> dict:
    """Агрегированная статистика по всем сделкам за N дней."""
    pool = await get_pool()
    row = await pool.fetchrow("""
        SELECT
            COUNT(*)            AS total_deals,
            SUM(impressions)    AS total_impressions,
            SUM(fire_votes)     AS total_fire,
            SUM(poop_votes)     AS total_poop,
            SUM(wl_adds)        AS total_wl_adds,
            SUM(store_clicks)   AS total_clicks,
            CASE WHEN SUM(impressions) > 0
                 THEN ROUND(SUM(wl_adds)::numeric / SUM(impressions) * 100, 1)
                 ELSE 0 END     AS avg_wl_ctr
        FROM deal_engagement
        WHERE last_event >= NOW() - ($1 || ' days')::INTERVAL
    """, str(days))
    return dict(row) if row else {}


async def mark_reward_claimed(user_id: int, reward_id_prefix: str) -> bool:
    """Отмечает последний невыданный приз пользователя как выданный."""
    pool = await get_pool()
    result = await pool.execute(
        """
        UPDATE user_rewards
        SET is_claimed = TRUE
        WHERE id = (
            SELECT id FROM user_rewards
            WHERE user_id = $1 AND reward_id LIKE $2 AND is_claimed = FALSE
            ORDER BY purchased_at DESC
            LIMIT 1
        )
        """,
        user_id,
        f"{reward_id_prefix}%",
    )
    return result == "UPDATE 1"
