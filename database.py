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
                posted_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
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
                posted_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)


# --- posted_deals ---

async def is_already_posted(deal_id: str) -> bool:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT 1 FROM posted_deals WHERE deal_id = $1", deal_id
    )
    return row is not None


async def mark_as_posted(deal_id: str, title: str, store: str, discount: int = 0):
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO posted_deals (deal_id, title, store, discount) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
        deal_id, title, store, discount,
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
        SELECT title, store, discount, deal_id
        FROM posted_deals
        WHERE posted_at >= NOW() - INTERVAL '7 days'
        ORDER BY discount DESC
        LIMIT $1
    """, limit)
    return [dict(r) for r in rows]


# --- wishlist ---

async def wishlist_add(user_id: int, query: str) -> bool:
    pool = await get_pool()
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
    rows = await pool.fetch("SELECT user_id, query FROM wishlist")
    title_low = title.lower()
    matched = set()
    for r in rows:
        if r["query"] in title_low or title_low in r["query"]:
            matched.add(r["user_id"])
    return list(matched)


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
        SELECT v.deal_id, p.title, p.store, COUNT(*) as fire_count
        FROM votes v
        JOIN posted_deals p ON p.deal_id = v.deal_id
        WHERE v.vote = 'fire'
          AND v.voted_at >= NOW() - INTERVAL '7 days'
        GROUP BY v.deal_id, p.title, p.store
        ORDER BY fire_count DESC
        LIMIT $1
    """, limit)
    return [dict(r) for r in rows]


# --- price_game ---

async def save_price_game(deal_id: str, original_price: int):
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO price_game (deal_id, original_price) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        deal_id, original_price,
    )


async def get_price_game(deal_id: str) -> int | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT original_price FROM price_game WHERE deal_id = $1", deal_id
    )
    return row["original_price"] if row else None
