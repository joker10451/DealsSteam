import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MIN_DISCOUNT_PERCENT = 50
MIN_STEAM_RATING = 70
TOP_DEALS_PER_POST = 7

POST_TIMES = [
    (9, 0),
    (12, 0),
    (15, 0),
    (18, 0),
    (21, 0),
]

DB_CLEANUP_DAYS = 30
DB_PATH = os.getenv("DB_PATH", "data/deals.db" if os.path.isdir("data") else "deals.db")  # для локального SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "")  # Supabase PostgreSQL connection string

# IGDB API (Twitch) — https://dev.twitch.tv/console
IGDB_CLIENT_ID = os.getenv("IGDB_CLIENT_ID", "")
IGDB_CLIENT_SECRET = os.getenv("IGDB_CLIENT_SECRET", "")
