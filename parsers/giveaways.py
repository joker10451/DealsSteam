"""
Парсер бесплатных раздач игр через GamerPower API.
Источник призов для магазина: https://www.gamerpower.com/api/giveaways
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from parsers.utils import fetch_with_retry

log = logging.getLogger(__name__)

GAMERPOWER_API = "https://www.gamerpower.com/api/giveaways"


@dataclass
class Giveaway:
    id: int
    title: str
    worth: str          # "$29.99" или "N/A"
    platforms: str      # "Steam", "Epic Games Store", etc.
    end_date: str       # "2026-03-16 00:00:00" или "N/A"
    open_giveaway_url: str
    giveaway_url: str
    type: str           # "game", "dlc", "beta"
    status: str         # "Active", "Expired"
    description: str = ""
    image: str = ""
    instructions: str = ""
    worth_float: float = field(default=0.0, init=False)

    def __post_init__(self):
        try:
            self.worth_float = float(self.worth.replace("$", "").strip())
        except (ValueError, AttributeError):
            self.worth_float = 0.0

    @property
    def is_steam(self) -> bool:
        return "steam" in self.platforms.lower()

    @property
    def is_active(self) -> bool:
        return self.status.lower() == "active"

    @property
    def days_left(self) -> Optional[int]:
        if self.end_date == "N/A":
            return None
        try:
            end = datetime.strptime(self.end_date, "%Y-%m-%d %H:%M:%S")
            delta = end - datetime.utcnow()
            return max(0, delta.days)
        except ValueError:
            return None


async def get_active_giveaways(
    platform: str = "steam",
    giveaway_type: str = "game",
    min_worth: float = 0.0,
) -> list[Giveaway]:
    """
    Получает активные раздачи с GamerPower API.

    Args:
        platform: "steam", "epic-games-store", "all" и т.д.
        giveaway_type: "game", "dlc", "beta", "all"
        min_worth: минимальная стоимость раздачи в USD (0 = любая)

    Returns:
        Список активных раздач, отсортированных по стоимости (дорогие первые).
    """
    params: dict = {"status": "active"}
    if platform != "all":
        params["platform"] = platform
    if giveaway_type != "all":
        params["type"] = giveaway_type

    try:
        data = await fetch_with_retry(GAMERPOWER_API, params=params)
    except Exception as e:
        log.error(f"GamerPower API error: {e}")
        return []

    if not data or not isinstance(data, list):
        log.warning("GamerPower вернул пустой или неверный ответ")
        return []

    giveaways = []
    for item in data:
        try:
            g = Giveaway(
                id=item["id"],
                title=item["title"],
                worth=item.get("worth", "N/A"),
                platforms=item.get("platforms", ""),
                end_date=item.get("end_date", "N/A"),
                open_giveaway_url=item.get("open_giveaway_url", ""),
                giveaway_url=item.get("giveaway_url", ""),
                type=item.get("type", "game"),
                status=item.get("status", "Active"),
                description=item.get("description", ""),
                image=item.get("image", ""),
                instructions=item.get("instructions", ""),
            )
            if g.is_active and g.worth_float >= min_worth:
                giveaways.append(g)
        except (KeyError, TypeError) as e:
            log.debug(f"Пропущена раздача: {e}")
            continue

    giveaways.sort(key=lambda x: x.worth_float, reverse=True)
    log.info(f"GamerPower: найдено {len(giveaways)} активных раздач (platform={platform})")
    return giveaways


async def get_all_active_giveaways(min_worth: float = 0.0) -> list[Giveaway]:
    """Получает все активные раздачи игр (Steam + Epic + другие)."""
    return await get_active_giveaways(platform="all", giveaway_type="game", min_worth=min_worth)


def format_giveaways_message(giveaways: list[Giveaway], limit: int = 10) -> str:
    """Форматирует список раздач для отправки в Telegram (HTML)."""
    if not giveaways:
        return "🎁 Активных раздач сейчас нет."

    lines = [f"🎁 <b>Бесплатные игры прямо сейчас ({len(giveaways)} шт.):</b>\n"]
    for g in giveaways[:limit]:
        worth = f" (~{g.worth})" if g.worth != "N/A" else ""
        days = f" | ⏳ {g.days_left}д." if g.days_left is not None else ""
        platform_emoji = "🎮" if g.is_steam else "🕹"
        lines.append(
            f"{platform_emoji} <a href=\"{g.open_giveaway_url}\">{g.title}</a>"
            f"{worth}{days}"
        )

    total_worth = sum(g.worth_float for g in giveaways if g.worth_float > 0)
    if total_worth > 0:
        lines.append(f"\n💰 Суммарная стоимость: ~${total_worth:.0f}")

    return "\n".join(lines)
