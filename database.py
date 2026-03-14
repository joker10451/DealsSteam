import aiosqlite
from config import DB_PATH, DB_CLEANUP_DAYS


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS posted_deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id TEXT UNIQUE NOT NULL,
                title TEXT,
                store TEXT,
                discount INTEGER DEFAULT 0,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        try:
            await db.execute("ALTER TABLE posted_deals ADD COLUMN discount INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wishlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                query TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, query)
            )
        """)
        # Голосования: fire/poop за каждый пост
        await db.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                vote TEXT NOT NULL,
                voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(deal_id, user_id)
            )
        """)
        # Мини-игра: угадай цену
        await db.execute("""
            CREATE TABLE IF NOT EXISTS price_game (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id TEXT UNIQUE NOT NULL,
                original_price INTEGER NOT NULL,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


# --- posted_deals ---

async def is_already_posted(deal_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM posted_deals WHERE deal_id = ?", (deal_id,)
        )
        return await cursor.fetchone() is not None


async def mark_as_posted(deal_id: str, title: str, store: str, discount: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO posted_deals (deal_id, title, store, discount) VALUES (?, ?, ?, ?)",
            (deal_id, title, store, discount),
        )
        await db.commit()


async def cleanup_old_records():
    async with aiosqlite.connect(DB_PATH) as db:
        result = await db.execute(
            "DELETE FROM posted_deals WHERE posted_at < datetime('now', ?)",
            (f"-{DB_CLEANUP_DAYS} days",),
        )
        await db.commit()
        return result.rowcount


async def get_weekly_top(limit: int = 10) -> list[dict]:
    """Топ скидок за последние 7 дней по проценту скидки."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT title, store, discount, deal_id
            FROM posted_deals
            WHERE posted_at >= datetime('now', '-7 days')
            ORDER BY discount DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# --- wishlist ---

async def wishlist_add(user_id: int, query: str) -> bool:
    """Добавляет игру в вишлист. Возвращает False если уже есть."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO wishlist (user_id, query) VALUES (?, ?)",
                (user_id, query.lower().strip()),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def wishlist_remove(user_id: int, query: str) -> bool:
    """Удаляет игру из вишлиста."""
    async with aiosqlite.connect(DB_PATH) as db:
        result = await db.execute(
            "DELETE FROM wishlist WHERE user_id = ? AND query = ?",
            (user_id, query.lower().strip()),
        )
        await db.commit()
        return result.rowcount > 0


async def wishlist_list(user_id: int) -> list[str]:
    """Возвращает список запросов пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT query FROM wishlist WHERE user_id = ? ORDER BY added_at",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]


async def get_wishlist_matches(title: str) -> list[int]:
    """Возвращает user_id всех, у кого в вишлисте есть совпадение с названием игры."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id, query FROM wishlist")
        rows = await cursor.fetchall()

    title_low = title.lower()
    matched = set()
    for user_id, query in rows:
        if query in title_low or title_low in query:
            matched.add(user_id)
    return list(matched)


# --- votes ---

async def add_vote(deal_id: str, user_id: int, vote: str) -> bool:
    """Сохраняет голос. Возвращает False если уже голосовал."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO votes (deal_id, user_id, vote) VALUES (?, ?, ?)",
                (deal_id, user_id, vote),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def get_votes(deal_id: str) -> dict:
    """Возвращает {'fire': N, 'poop': N}"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT vote, COUNT(*) FROM votes WHERE deal_id = ? GROUP BY vote",
            (deal_id,),
        )
        rows = await cursor.fetchall()
    result = {"fire": 0, "poop": 0}
    for vote, count in rows:
        if vote in result:
            result[vote] = count
    return result


async def get_top_voted(limit: int = 5) -> list[dict]:
    """Топ игр по голосам 🔥 за последние 7 дней."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT v.deal_id, p.title, p.store, COUNT(*) as fire_count
            FROM votes v
            JOIN posted_deals p ON p.deal_id = v.deal_id
            WHERE v.vote = 'fire'
              AND v.voted_at >= datetime('now', '-7 days')
            GROUP BY v.deal_id
            ORDER BY fire_count DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# --- price_game ---

async def save_price_game(deal_id: str, original_price: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO price_game (deal_id, original_price) VALUES (?, ?)",
            (deal_id, original_price),
        )
        await db.commit()


async def get_price_game(deal_id: str) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT original_price FROM price_game WHERE deal_id = ?", (deal_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None
