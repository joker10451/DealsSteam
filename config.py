import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MIN_DISCOUNT_PERCENT = int(os.getenv("MIN_DISCOUNT_PERCENT", "50"))
MIN_STEAM_RATING = int(os.getenv("MIN_STEAM_RATING", "70"))
TOP_DEALS_PER_POST = int(os.getenv("TOP_DEALS_PER_POST", "7"))

# Времена публикации: "9:0,12:0,15:0,18:0,21:0"
_post_times_raw = os.getenv("POST_TIMES", "9:0,12:0,15:0,18:0,21:0")
POST_TIMES: list[tuple[int, int]] = [
    (int(t.split(":")[0]), int(t.split(":")[1]))
    for t in _post_times_raw.split(",")
    if ":" in t
]

DB_CLEANUP_DAYS = int(os.getenv("DB_CLEANUP_DAYS", "30"))
DB_PATH = os.getenv("DB_PATH", "data/deals.db" if os.path.isdir("data") else "deals.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# IGDB API (Twitch) — https://dev.twitch.tv/console
IGDB_CLIENT_ID = os.getenv("IGDB_CLIENT_ID", "")
IGDB_CLIENT_SECRET = os.getenv("IGDB_CLIENT_SECRET", "")

# Фильтр 18+ контента
FILTER_ADULT = os.getenv("FILTER_ADULT", "false").lower() == "true"
