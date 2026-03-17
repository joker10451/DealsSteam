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

# RAWG API — https://rawg.io/apidocs
RAWG_API_KEY = os.getenv("RAWG_API_KEY", "")

# Фильтры публикации
FILTER_BUNDLES = os.getenv("FILTER_BUNDLES", "true").lower() == "true"
MIN_PRICE_RUB = int(os.getenv("MIN_PRICE_RUB", "50"))

# Кулдаун ручной публикации (секунды)
POST_COOLDOWN_SEC = int(os.getenv("POST_COOLDOWN_SEC", "300"))

# Steam Web API integration
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
STEAM_INTEGRATION_ENABLED = os.getenv("STEAM_INTEGRATION_ENABLED", "true").lower() == "true"
FREE_GAME_MONITOR_ENABLED = os.getenv("FREE_GAME_MONITOR_ENABLED", "true").lower() == "true"
PRICE_COMPARISON_ENABLED = os.getenv("PRICE_COMPARISON_ENABLED", "true").lower() == "true"

# Steam sync rate limiting
STEAM_SYNC_COOLDOWN_HOURS = int(os.getenv("STEAM_SYNC_COOLDOWN_HOURS", "1"))
ITAD_API_KEY = os.getenv("ITAD_API_KEY", "")

# Username бота без @ (нужен для реферальных ссылок в постах канала)
BOT_USERNAME = os.getenv("BOT_USERNAME", "")

# VK Integration
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN", "")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID", "0"))
VK_ENABLED = os.getenv("VK_ENABLED", "false").lower() == "true"
TG_CHANNEL_LINK = os.getenv("TG_CHANNEL_LINK", "https://t.me/GameDealsRadarRu")
