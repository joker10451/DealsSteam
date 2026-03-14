"""
Steam integration command handlers.
Provides commands for linking Steam accounts, syncing wishlists/libraries,
and managing free game notifications.
"""
from html import escape

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from steam_api import resolve_steam_id, fetch_wishlist, fetch_library
from database import (
    steam_link_account, steam_unlink_account, steam_get_user,
    steam_update_sync_time, steam_library_replace,
    wishlist_add, wishlist_list,
    free_game_subscribe, free_game_unsubscribe,
)
from config import STEAM_SYNC_COOLDOWN_HOURS
from datetime import datetime, timedelta
import logging

log = logging.getLogger(__name__)
router = Router()


def esc(text: str) -> str:
    """HTML escape helper."""
    return escape(str(text))


PRIVACY_NOTICE = (
    "🔒 <b>Конфиденциальность:</b>\n"
    "• Мы храним только ваш Steam ID64\n"
    "• Доступ только к публичным данным профиля\n"
    "• Вы можете отключить интеграцию в любой момент через /steamdisconnect\n\n"
)

STEAM_URL_EXAMPLE = (
    "Примеры правильного формата:\n"
    "• https://steamcommunity.com/profiles/76561198012345678\n"
    "• https://steamcommunity.com/id/username\n"
    "• 76561198012345678"
)


@router.message(Command("steam"))
async def cmd_steam(message: Message):
    """
    Initiates Steam account linking.
    Usage: /steam <profile_url_or_id>
    
    Requirements: 6.6, 7.1, 7.6
    """
    args = message.text.split(maxsplit=1) if message.text else []
    
    if len(args) < 2:
        await message.answer(
            "Использование: /steam [ссылка на профиль или Steam ID]\n\n"
            + STEAM_URL_EXAMPLE
        )
        return
    
    profile_input = args[1].strip()
    
    # Resolve Steam ID from input
    steam_id = await resolve_steam_id(profile_input)
    
    if not steam_id:
        await message.answer(
            f"❌ Не удалось распознать Steam профиль: {esc(profile_input)}\n\n"
            + STEAM_URL_EXAMPLE
        )
        return
    
    # Check if this is the first time linking
    existing_user = await steam_get_user(message.from_user.id)
    is_first_link = existing_user is None
    
    # Link the account
    success = await steam_link_account(message.from_user.id, steam_id)
    
    if not success:
        await message.answer(
            "❌ Не удалось привязать Steam аккаунт. Возможно, он уже привязан."
        )
        return
    
    # Display privacy notice on first linkage
    privacy_text = PRIVACY_NOTICE if is_first_link else ""
    
    # Create inline keyboard for sync options
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Синхронизировать сейчас", callback_data="steam_sync_now")],
        [InlineKeyboardButton(text="📋 Статус синхронизации", callback_data="steam_status")],
    ])
    
    await message.answer(
        f"✅ Steam аккаунт успешно привязан!\n"
        f"Steam ID: <code>{esc(steam_id)}</code>\n\n"
        + privacy_text +
        "Используй /steamsync для синхронизации вишлиста и библиотеки.",
        reply_markup=keyboard
    )
    
    log.info(f"User {message.from_user.id} linked Steam account: {steam_id}")


@router.callback_query(F.data == "steam_sync_now")
async def cb_steam_sync_now(callback):
    """Handle inline button for immediate sync."""
    await callback.answer("Запускаю синхронизацию...")
    # Trigger sync by simulating /steamsync command
    await cmd_steam_sync(callback.message, callback.from_user.id)


@router.callback_query(F.data == "steam_status")
async def cb_steam_status(callback):
    """Handle inline button for status display."""
    await callback.answer()
    # Trigger status by simulating /steamstatus command
    await cmd_steam_status(callback.message, callback.from_user.id)


@router.message(Command("steamsync"))
async def cmd_steam_sync(message: Message, user_id: int = None):
    """
    Manually triggers wishlist and library synchronization.
    
    Requirements: 1.7, 2.8, 7.2
    """
    user_id = user_id or message.from_user.id
    
    # Check if user has linked Steam account
    steam_user = await steam_get_user(user_id)
    
    if not steam_user:
        await message.answer(
            "❌ Steam аккаунт не привязан. Используй /steam [ссылка] для привязки."
        )
        return
    
    # Enforce cooldown for manual sync
    last_wishlist_sync = steam_user.get("last_wishlist_sync")
    last_library_sync = steam_user.get("last_library_sync")
    
    # Check the most recent sync time
    last_sync = None
    if last_wishlist_sync and last_library_sync:
        last_sync = max(last_wishlist_sync, last_library_sync)
    elif last_wishlist_sync:
        last_sync = last_wishlist_sync
    elif last_library_sync:
        last_sync = last_library_sync
    
    # Enforce cooldown
    if last_sync:
        cooldown_delta = timedelta(hours=STEAM_SYNC_COOLDOWN_HOURS)
        time_since_sync = datetime.now(last_sync.tzinfo) - last_sync
        
        if time_since_sync < cooldown_delta:
            remaining = cooldown_delta - time_since_sync
            remaining_minutes = int(remaining.total_seconds() / 60)
            
            await message.answer(
                f"⏳ Синхронизация доступна через {remaining_minutes} мин.\n\n"
                f"Последняя синхронизация: {last_sync.strftime('%d.%m.%Y %H:%M')}\n"
                f"Автоматическая синхронизация происходит ежедневно."
            )
            log.info(f"User {user_id} sync blocked by cooldown, {remaining_minutes} minutes remaining")
            return
    
    steam_id = steam_user["steam_id"]
    
    await message.answer("🔄 Синхронизирую данные Steam...")
    
    # Fetch wishlist
    wishlist_games = await fetch_wishlist(steam_id)
    wishlist_count = 0
    
    if wishlist_games:
        # Clear existing wishlist and add Steam games
        existing_wishlist = await wishlist_list(user_id)
        
        # Add games from Steam wishlist
        for game in wishlist_games:
            added = await wishlist_add(user_id, game["name"])
            if added:
                wishlist_count += 1
        
        await steam_update_sync_time(user_id, "wishlist")
        log.info(f"Synced {wishlist_count} wishlist games for user {user_id}")
    else:
        log.warning(f"No wishlist games fetched for user {user_id}, Steam ID: {steam_id}")
    
    # Fetch library
    library_appids = await fetch_library(steam_id)
    library_count = len(library_appids)
    
    if library_appids:
        await steam_library_replace(user_id, library_appids)
        await steam_update_sync_time(user_id, "library")
        log.info(f"Synced {library_count} library games for user {user_id}")
    else:
        log.warning(f"No library games fetched for user {user_id}, Steam ID: {steam_id}")
    
    # Send summary message
    if wishlist_count == 0 and library_count == 0:
        await message.answer(
            "⚠️ Не удалось синхронизировать данные.\n\n"
            "Возможные причины:\n"
            "• Профиль Steam приватный\n"
            "• Вишлист или библиотека скрыты в настройках приватности\n"
            "• Временные проблемы с Steam API\n\n"
            "Проверь настройки приватности в Steam и попробуй снова."
        )
    else:
        summary_parts = []
        if wishlist_count > 0:
            summary_parts.append(f"📋 Вишлист: {wishlist_count} игр")
        if library_count > 0:
            summary_parts.append(f"🎮 Библиотека: {library_count} игр")
        
        await message.answer(
            "✅ Синхронизация завершена!\n\n"
            + "\n".join(summary_parts) +
            "\n\nТеперь ты будешь получать уведомления о скидках на игры из вишлиста."
        )


@router.message(Command("steamdisconnect"))
async def cmd_steam_disconnect(message: Message):
    """
    Unlinks Steam account and deletes all associated data.
    
    Requirements: 6.3, 7.3
    """
    success = await steam_unlink_account(message.from_user.id)
    
    if success:
        await message.answer(
            "✅ Steam аккаунт отключён.\n"
            "Все связанные данные удалены."
        )
        log.info(f"User {message.from_user.id} disconnected Steam account")
    else:
        await message.answer(
            "❌ Steam аккаунт не был привязан."
        )


@router.message(Command("steamstatus"))
async def cmd_steam_status(message: Message, user_id: int = None):
    """
    Displays current sync status and last sync times.
    
    Requirements: 7.4
    """
    user_id = user_id or message.from_user.id
    steam_user = await steam_get_user(user_id)
    
    if not steam_user:
        await message.answer(
            "❌ Steam аккаунт не привязан.\n"
            "Используй /steam [ссылка] для привязки."
        )
        return
    
    steam_id = steam_user["steam_id"]
    wishlist_sync = steam_user.get("last_wishlist_sync")
    library_sync = steam_user.get("last_library_sync")
    wishlist_enabled = steam_user.get("wishlist_sync_enabled", True)
    library_enabled = steam_user.get("library_sync_enabled", True)
    
    # Format sync times
    wishlist_time = wishlist_sync.strftime("%d.%m.%Y %H:%M") if wishlist_sync else "Никогда"
    library_time = library_sync.strftime("%d.%m.%Y %H:%M") if library_sync else "Никогда"
    
    status_text = (
        f"🎮 <b>Статус Steam интеграции</b>\n\n"
        f"Steam ID: <code>{esc(steam_id)}</code>\n\n"
        f"📋 <b>Вишлист:</b>\n"
        f"  Автосинхронизация: {'✅ Включена' if wishlist_enabled else '❌ Выключена'}\n"
        f"  Последняя синхронизация: {wishlist_time}\n\n"
        f"🎮 <b>Библиотека:</b>\n"
        f"  Автосинхронизация: {'✅ Включена' if library_enabled else '❌ Выключена'}\n"
        f"  Последняя синхронизация: {library_time}\n\n"
        f"Используй /steamsync для ручной синхронизации."
    )
    
    await message.answer(status_text)


@router.message(Command("freenotify"))
async def cmd_free_notify(message: Message):
    """
    Toggles free game notification subscription.
    
    Requirements: 4.8, 7.5
    """
    # Try to subscribe first
    subscribed = await free_game_subscribe(message.from_user.id)
    
    if subscribed:
        await message.answer(
            "✅ Подписка на уведомления о бесплатных играх активирована!\n\n"
            "Ты будешь получать личные сообщения когда игры становятся бесплатными "
            "в Epic Games Store или GOG."
        )
        log.info(f"User {message.from_user.id} subscribed to free game notifications")
    else:
        # Already subscribed, so unsubscribe
        await free_game_unsubscribe(message.from_user.id)
        await message.answer(
            "🔕 Подписка на уведомления о бесплатных играх отключена."
        )
        log.info(f"User {message.from_user.id} unsubscribed from free game notifications")
