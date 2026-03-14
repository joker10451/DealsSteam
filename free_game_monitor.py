"""
Free game monitoring module.
Tracks and notifies users about free game promotions on Epic Games and GOG.
"""
import logging
from datetime import datetime
from html import escape
from typing import Optional

from parsers.epic import get_epic_deals
from parsers.gog import get_gog_deals
from parsers.steam import Deal
from database import (
    is_already_posted, mark_as_posted,
    free_game_get_subscribers
)
from publisher import get_bot, send_with_retry
from config import CHANNEL_ID

log = logging.getLogger(__name__)


def esc(text: str) -> str:
    """HTML escape helper."""
    return escape(str(text))


async def check_epic_free_games():
    """
    Checks Epic Games Store for free games.
    Publishes new free games to channel and notifies subscribers.
    Runs every 2 hours via scheduler.
    
    Requirements: 4.1, 4.3, 4.5, 4.6
    """
    log.info("Checking Epic Games Store for free games...")
    
    try:
        # Get free games (100% discount)
        deals = await get_epic_deals(min_discount=100)
        
        if not deals:
            log.info("No free games found on Epic Games Store")
            return
        
        log.info(f"Found {len(deals)} free games on Epic Games Store")
        
        # Process each free game
        for deal in deals:
            # Check if already posted
            if await is_already_posted(deal.deal_id):
                log.debug(f"Free game already posted: {deal.title}")
                continue
            
            # Parse promotion end date if available
            expiration_date = _parse_epic_expiration(deal)
            
            # Publish to channel and notify subscribers
            await publish_free_game(deal, expiration_date)
            
            # Mark as posted to prevent duplicates
            await mark_as_posted(
                deal.deal_id,
                deal.title,
                deal.store,
                deal.discount,
                deal.link
            )
            
            log.info(f"Published free game: {deal.title} from {deal.store}")
    
    except Exception as e:
        log.error(f"Error checking Epic free games: {e}", exc_info=True)


async def check_gog_free_games():
    """
    Checks GOG for free games.
    Publishes new free games to channel and notifies subscribers.
    Runs every 6 hours via scheduler.
    
    Requirements: 4.2, 4.3, 4.5
    """
    log.info("Checking GOG for free games...")
    
    try:
        # Get free games (100% discount)
        deals = await get_gog_deals(min_discount=100)
        
        if not deals:
            log.info("No free games found on GOG")
            return
        
        log.info(f"Found {len(deals)} free games on GOG")
        
        # Process each free game
        for deal in deals:
            # Check if already posted
            if await is_already_posted(deal.deal_id):
                log.debug(f"Free game already posted: {deal.title}")
                continue
            
            # GOG doesn't provide expiration dates in the same way
            expiration_date = None
            
            # Publish to channel and notify subscribers
            await publish_free_game(deal, expiration_date)
            
            # Mark as posted to prevent duplicates
            await mark_as_posted(
                deal.deal_id,
                deal.title,
                deal.store,
                deal.discount,
                deal.link
            )
            
            log.info(f"Published free game: {deal.title} from {deal.store}")
    
    except Exception as e:
        log.error(f"Error checking GOG free games: {e}", exc_info=True)


def _parse_epic_expiration(deal: Deal) -> Optional[str]:
    """
    Parses promotion end date from Epic deal data.
    
    Args:
        deal: Deal object from Epic parser
        
    Returns:
        Formatted expiration date string or None
        
    Requirements: 4.6
    """
    # Epic parser doesn't currently expose expiration date in Deal object
    # This would require extending the parser to include promotion end time
    # For now, return None and use "Limited time offer" fallback
    return None


async def publish_free_game(deal: Deal, expiration_date: Optional[str] = None):
    """
    Publishes free game to channel and sends DMs to subscribers.
    Includes expiration date if available.
    
    Args:
        deal: Deal object with free game info
        expiration_date: Optional expiration date string
        
    Requirements: 4.4, 4.7, 4.9, 4.10
    """
    bot = get_bot()
    if not bot:
        log.error("Bot instance not available, cannot publish free game")
        return
    
    # Format store emoji
    store_emoji = {
        "Epic Games": "🎁",
        "GOG": "🟣",
        "Steam": "🎮"
    }.get(deal.store, "🎮")
    
    # Format expiration text
    if expiration_date:
        expiration_text = f"\n⏰ <b>До:</b> {esc(expiration_date)}"
    else:
        expiration_text = "\n⏰ <b>Ограниченное предложение!</b>"
    
    # Build message
    message_text = (
        f"🎉 <b>БЕСПЛАТНАЯ ИГРА!</b>\n\n"
        f"{store_emoji} <b>{esc(deal.title)}</b>\n"
        f"🏪 <b>Магазин:</b> {esc(deal.store)}\n"
        f"💰 <b>Цена:</b> Бесплатно (было {esc(deal.old_price)})"
        f"{expiration_text}\n\n"
        f"🔗 <a href='{deal.link}'>Забрать игру</a>\n\n"
        f"#БесплатнаяИгра #{deal.store.replace(' ', '')}"
    )
    
    # Publish to channel
    try:
        if deal.image_url:
            await send_with_retry(
                lambda: bot.send_photo(
                    CHANNEL_ID,
                    photo=deal.image_url,
                    caption=message_text,
                    parse_mode="HTML"
                )
            )
        else:
            await send_with_retry(
                lambda: bot.send_message(
                    CHANNEL_ID,
                    text=message_text,
                    parse_mode="HTML",
                    disable_web_page_preview=False
                )
            )
        
        log.info(f"Published free game to channel: {deal.title}")
    
    except Exception as e:
        log.error(f"Failed to publish free game to channel: {e}", exc_info=True)
        return
    
    # Send DMs to subscribers
    subscribers = await free_game_get_subscribers()
    
    if not subscribers:
        log.info("No subscribers for free game notifications")
        return
    
    log.info(f"Sending free game DMs to {len(subscribers)} subscribers")
    
    # Send DMs with delay to avoid flood control
    success_count = 0
    failed_count = 0
    
    for user_id in subscribers:
        try:
            if deal.image_url:
                await send_with_retry(
                    lambda: bot.send_photo(
                        user_id,
                        photo=deal.image_url,
                        caption=message_text,
                        parse_mode="HTML"
                    )
                )
            else:
                await send_with_retry(
                    lambda: bot.send_message(
                        user_id,
                        text=message_text,
                        parse_mode="HTML",
                        disable_web_page_preview=False
                    )
                )
            
            success_count += 1
            
        except Exception as e:
            # User may have blocked the bot or deleted account
            log.warning(f"Failed to send free game DM to user {user_id}: {e}")
            failed_count += 1
    
    log.info(
        f"Free game DM delivery complete: "
        f"{success_count} sent, {failed_count} failed"
    )
