"""
Система онбординга для новых пользователей.
Включает интерактивный туториал и систему подсказок.
"""
import logging
from typing import Optional, Dict, List
from datetime import datetime, timedelta

import pytz
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import get_pool

log = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")

# Константы
TUTORIAL_STEPS = 5  # Базовое количество шагов
COMPLETION_BONUS = 20  # Баллы за завершение
HINT_DURATION_DAYS = 3  # Длительность показа подсказок

# Типы подсказок
HINT_TYPES = {
    "wishlist_vote": "Голосуй за скидки в канале (🔥 или 👎) и зарабатывай баллы!",
    "minigame_challenge": "Не забудь про ежедневный челлендж! Команда /challenge",
    "shop_earn": "Зарабатывай баллы в мини-играх (/games) и покупай призы!",
    "achievement_system": "Разблокируй все достижения! Команда /achievements",
}

# Контент туториала
TUTORIAL_CONTENT = {
    0: {
        'title': '🎮 Добро пожаловать!',
        'message': (
            '🎮 Привет! Я бот для поиска лучших скидок на игры!\n\n'
            'Я помогу тебе:\n'
            '• 📢 Находить скидки в Steam, GOG, Epic Games\n'
            '• 💝 Отслеживать цены на игры из твоего вишлиста\n'
            '• 🎯 Зарабатывать баллы в мини-играх\n'
            '• 🏆 Получать призы за активность\n\n'
            'Пройди короткий туториал и получи +20 баллов!'
        )
    },
    1: {
        'title': '📢 Канал со скидками',
        'message': (
            'Все скидки публикуются в нашем канале.\n\n'
            'Там ты найдёшь:\n'
            '• Скидки до 90% на топовые игры\n'
            '• Бесплатные раздачи Epic и GOG\n'
            '• Еженедельные дайджесты\n'
            '• Скрытые жемчужины\n\n'
            'Подпишись, чтобы не пропустить!'
        )
    },
    2: {
        'title': '💝 Персональный вишлист',
        'message': (
            'Добавляй игры в вишлист, и я пришлю уведомление, когда на них будет скидка!\n\n'
            'Как добавить:\n'
            '/wishlist Название игры\n\n'
            'Пример:\n'
            '/wishlist Cyberpunk 2077\n\n'
            'Также можешь импортировать вишлист из Steam: /steam'
        )
    },
    3: {
        'title': '🎮 Мини-игры и баллы',
        'message': (
            'Играй в мини-игры и зарабатывай баллы:\n\n'
            '• 📸 Угадай игру по скриншоту — 10 баллов\n'
            '• 🎯 Ежедневный челлендж — 50 баллов\n'
            '• 🗳 Голосуй за скидки — 2 балла\n\n'
            'Баллы можно обменять на призы в магазине!\n\n'
            'Посмотреть игры: /games'
        )
    },
    4: {
        'title': '🏪 Магазин призов',
        'message': (
            'Обменивай баллы на реальные призы:\n\n'
            '• 🎮 Steam ключи (1000-3500 баллов)\n'
            '• 💜 Discord Nitro (2500 баллов)\n'
            '• 🎵 Spotify Premium (2200 баллов)\n'
            '• 👑 VIP статусы (2000+ баллов)\n\n'
            'Открыть магазин: /shop'
        )
    },
    5: {
        'title': '👥 Реферальная программа',
        'message': (
            'Ты получил +50 баллов за переход по реферальной ссылке!\n\n'
            'Приглашай друзей и зарабатывай:\n'
            '• +100 баллов за каждого друга\n'
            '• Друг получает +50 баллов\n\n'
            'Твоя реферальная ссылка: /invite'
        )
    }
}

# Сообщения о завершении и пропуске
COMPLETION_MESSAGE = (
    '🎉 Поздравляем!\n\n'
    'Ты прошёл туториал и получил +{points} баллов!\n\n'
    'Теперь ты знаешь все возможности бота. Начни с:\n'
    '• Подписки на канал со скидками\n'
    '• Добавления игр в вишлист /wishlist\n'
    '• Игры в мини-игры /games\n\n'
    'Удачи в поиске скидок! 🎮'
)

SKIP_MESSAGE = (
    'Туториал пропущен.\n\n'
    'Ты можешь пройти его в любое время командой /tutorial\n\n'
    'Основные команды:\n'
    '• /wishlist - управление вишлистом\n'
    '• /games - мини-игры и баллы\n'
    '• /shop - магазин призов\n'
    '• /help - полный список команд'
)

RESTART_MESSAGE = (
    '🔄 Туториал перезапущен\n\n'
    'Ты можешь пройти его заново, но бонус начисляется только один раз.'
)


async def is_new_user(user_id: int) -> bool:
    """
    Проверить, новый ли пользователь.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        True if user has no onboarding progress record, False otherwise
    """
    from database import get_onboarding_progress
    progress = await get_onboarding_progress(user_id)
    return progress is None


async def start_tutorial(user_id: int, has_referral: bool = False) -> Dict:
    from database import create_onboarding_progress, update_onboarding_step
    
    await create_onboarding_progress(user_id, has_referral=has_referral)
    await update_onboarding_step(user_id, 0)
    
    return await get_tutorial_step(user_id, 0, has_referral)


async def get_tutorial_step(user_id: int, step: int, has_referral: bool = False) -> Dict:
    """
    Получить контент шага туториала.
    
    Args:
        user_id: Telegram user ID
        step: Tutorial step number (0-5)
        has_referral: Whether user came from referral link
        
    Returns:
        Dict with step content: {
            'step': int,
            'total_steps': int,
            'title': str,
            'message': str,
            'has_referral': bool
        }
    """
    # Determine total steps based on referral status
    total_steps = TUTORIAL_STEPS + 1 if has_referral else TUTORIAL_STEPS
    
    # Validate step boundaries
    if step < 0:
        step = 0
    if step > total_steps:
        step = total_steps
    
    # Get content for current step
    content = TUTORIAL_CONTENT.get(step, TUTORIAL_CONTENT[0])
    
    # Build message with step indicator (except for step 0)
    message = content['message']
    if step > 0:
        # Add step indicator at the end
        step_indicator = f'\n\nШаг {step} из {total_steps}'
        message = message + step_indicator
    
    return {
        'step': step,
        'total_steps': total_steps,
        'title': content['title'],
        'message': message,
        'has_referral': has_referral
    }


async def next_tutorial_step(user_id: int) -> int:
    """
    Перейти к следующему шагу.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        New step number after increment, or current step if at boundary
    """
    from database import get_onboarding_progress, update_onboarding_step
    
    progress = await get_onboarding_progress(user_id)
    if not progress:
        log.warning(f"No onboarding progress found for user {user_id}")
        return 0
    
    current_step = progress.get('current_step', 0)
    
    # Determine max step (5 for referral users, 4 for regular users)
    # We don't have referral info here, so use max possible
    max_step = TUTORIAL_STEPS + 1
    
    # Increment step with boundary check
    new_step = min(current_step + 1, max_step)
    
    await update_onboarding_step(user_id, new_step)
    return new_step


async def prev_tutorial_step(user_id: int) -> int:
    """
    Вернуться к предыдущему шагу.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        New step number after decrement, or 0 if at start
    """
    from database import get_onboarding_progress, update_onboarding_step
    
    progress = await get_onboarding_progress(user_id)
    if not progress:
        log.warning(f"No onboarding progress found for user {user_id}")
        return 0
    
    current_step = progress.get('current_step', 0)
    
    # Decrement step with boundary check
    new_step = max(current_step - 1, 0)
    
    await update_onboarding_step(user_id, new_step)
    return new_step


async def complete_tutorial(user_id: int) -> Dict:
    """
    Завершить туториал и начислить бонус.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        Dict with completion result: {
            'success': bool,
            'points': int,
            'message': str,
            'new_achievements': list (optional)
        }
    """
    from database import complete_onboarding, get_onboarding_progress
    from minigames import add_score
    
    # Check if user has onboarding progress
    progress = await get_onboarding_progress(user_id)
    if not progress:
        log.warning(f"No onboarding progress found for user {user_id}")
        return {
            'success': False,
            'message': 'Онбординг не найден. Используй /tutorial для начала.'
        }
    
    # Check if already completed or skipped
    status = progress.get('status')
    if status == 'completed':
        log.info(f"User {user_id} already completed onboarding")
        return {
            'success': False,
            'message': 'Ты уже завершил туториал ранее!'
        }
    
    if status == 'skipped':
        log.info(f"User {user_id} already skipped onboarding, cannot complete")
        return {
            'success': False,
            'message': 'Ты пропустил туториал. Используй /tutorial для повторного прохождения.'
        }
    
    # Mark as completed in database
    success = await complete_onboarding(user_id)
    if not success:
        log.error(f"Failed to mark onboarding as completed for user {user_id}")
        return {
            'success': False,
            'message': 'Ошибка при завершении туториала. Попробуй позже.'
        }
    
    # Award completion bonus
    new_achievements = await add_score(
        user_id, 
        COMPLETION_BONUS, 
        correct=True, 
        reason="onboarding_completed"
    )
    
    log.info(f"User {user_id} completed onboarding, awarded {COMPLETION_BONUS} points")
    
    return {
        'success': True,
        'points': COMPLETION_BONUS,
        'message': COMPLETION_MESSAGE.format(points=COMPLETION_BONUS),
        'new_achievements': new_achievements if new_achievements else []
    }


async def skip_tutorial(user_id: int) -> Dict:
    """
    Пропустить туториал без начисления бонуса.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        Dict with skip result: {
            'success': bool,
            'message': str
        }
    """
    from database import skip_onboarding, get_onboarding_progress
    
    # Check if user has onboarding progress
    progress = await get_onboarding_progress(user_id)
    if not progress:
        log.warning(f"No onboarding progress found for user {user_id}")
        return {
            'success': False,
            'message': 'Онбординг не найден.'
        }
    
    # Check if already completed or skipped
    status = progress.get('status')
    if status == 'completed':
        log.info(f"User {user_id} already completed onboarding, cannot skip")
        return {
            'success': False,
            'message': 'Ты уже завершил туториал!'
        }
    
    if status == 'skipped':
        log.info(f"User {user_id} already skipped onboarding")
        return {
            'success': False,
            'message': 'Ты уже пропустил туториал ранее.'
        }
    
    # Mark as skipped in database
    success = await skip_onboarding(user_id)
    if not success:
        log.error(f"Failed to mark onboarding as skipped for user {user_id}")
        return {
            'success': False,
            'message': 'Ошибка при пропуске туториала. Попробуй позже.'
        }
    
    log.info(f"User {user_id} skipped onboarding")
    
    return {
        'success': True,
        'message': SKIP_MESSAGE
    }


async def get_onboarding_progress(user_id: int) -> Optional[Dict]:
    """
    Получить прогресс онбординга пользователя.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        Dict with progress data if exists: {
            'user_id': int,
            'current_step': int,
            'status': str ('in_progress', 'completed', 'skipped'),
            'completed_at': datetime (optional),
            'skipped_at': datetime (optional),
            'created_at': datetime,
            'updated_at': datetime
        }
        None if no progress record exists
    """
    from database import get_onboarding_progress as db_get_progress
    
    progress = await db_get_progress(user_id)
    
    if progress:
        log.debug(f"Retrieved onboarding progress for user {user_id}: step {progress.get('current_step')}, status {progress.get('status')}")
    else:
        log.debug(f"No onboarding progress found for user {user_id}")
    
    return progress


async def should_show_hints(user_id: int) -> bool:
    """
    Проверить, нужно ли показывать подсказки.
    
    Подсказки показываются только в первые 3 дня с момента регистрации.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        True if hints should be shown (within 3 days of registration), False otherwise
    """
    from database import get_user_registration_date
    
    # Get user registration date
    registration_date = await get_user_registration_date(user_id)
    
    if not registration_date:
        log.debug(f"No registration date found for user {user_id}, hints disabled")
        return False
    
    # Calculate time since registration
    now = datetime.now(MSK)
    
    # Ensure registration_date is timezone-aware
    if registration_date.tzinfo is None:
        registration_date = registration_date.replace(tzinfo=MSK)
    
    days_since_registration = (now - registration_date).days
    
    # Show hints only within first 3 days
    should_show = days_since_registration < HINT_DURATION_DAYS
    
    log.debug(
        f"User {user_id} registered {days_since_registration} days ago, "
        f"hints {'enabled' if should_show else 'disabled'}"
    )
    
    return should_show


async def show_hint(user_id: int, hint_type: str) -> Optional[str]:
    """
    Показать подсказку если она ещё не была показана.
    
    Проверяет:
    1. Находится ли пользователь в первых 3 днях с момента регистрации
    2. Не была ли эта подсказка уже показана
    
    Args:
        user_id: Telegram user ID
        hint_type: Type of hint to show (must be in HINT_TYPES)
        
    Returns:
        Hint message text if hint should be shown, None otherwise
    """
    from database import get_shown_hints, save_hint_shown
    
    # Validate hint type
    if hint_type not in HINT_TYPES:
        log.warning(f"Invalid hint type: {hint_type}")
        return None
    
    # Check if user is within hint duration period
    if not await should_show_hints(user_id):
        log.debug(f"User {user_id} is outside hint duration period")
        return None
    
    # Check if hint was already shown
    shown_hints = await get_shown_hints(user_id)
    
    if hint_type in shown_hints:
        log.debug(f"Hint {hint_type} already shown to user {user_id}")
        return None
    
    # Mark hint as shown
    success = await save_hint_shown(user_id, hint_type)
    
    if not success:
        log.error(f"Failed to save hint {hint_type} for user {user_id}")
        return None
    
    # Get hint message
    hint_message = HINT_TYPES[hint_type]
    
    log.info(f"Showing hint {hint_type} to user {user_id}")
    
    return f"💡 {hint_message}"


def format_tutorial_message(step: int, content: Dict) -> str:
    """
    Форматировать сообщение туториала с HTML-разметкой.
    
    Args:
        step: Current tutorial step number
        content: Dict with step content (title, message, etc.)
        
    Returns:
        Formatted HTML message string with escaped text
    """
    import html
    
    # Extract content fields
    title = content.get('title', '')
    message = content.get('message', '')
    
    # Escape HTML in title and message
    title_escaped = html.escape(title)
    message_escaped = html.escape(message)
    
    # Format the complete message
    formatted = f"<b>{title_escaped}</b>\n\n{message_escaped}"
    
    return formatted


def create_tutorial_keyboard(step: int, total_steps: int, can_skip: bool = True) -> InlineKeyboardMarkup:
    """
    Создать клавиатуру для навигации по туториалу.
    
    Args:
        step: Current tutorial step number (0-indexed)
        total_steps: Total number of tutorial steps
        can_skip: Whether to show skip button (default: True)
        
    Returns:
        InlineKeyboardMarkup with navigation buttons
    """
    buttons = []
    
    # First row: Back and Next buttons
    nav_row = []
    
    # Add "Назад" button if not on first step
    if step > 0:
        nav_row.append(InlineKeyboardButton(
            text="◀️ Назад",
            callback_data="tutorial:prev"
        ))
    
    # Add "Далее" button if not on last step
    if step < total_steps:
        nav_row.append(InlineKeyboardButton(
            text="Далее ▶️",
            callback_data="tutorial:next"
        ))
    else:
        # On last step, show "Завершить" button
        nav_row.append(InlineKeyboardButton(
            text="✅ Завершить",
            callback_data="tutorial:complete"
        ))
    
    if nav_row:
        buttons.append(nav_row)
    
    # Second row: Progress indicator (non-clickable text as button)
    progress_text = f"Шаг {step} из {total_steps}"
    buttons.append([InlineKeyboardButton(
        text=progress_text,
        callback_data="tutorial:progress"  # Dummy callback, will be ignored
    )])
    
    # Third row: Skip button
    if can_skip and step < total_steps:
        buttons.append([InlineKeyboardButton(
            text="⏭ Пропустить туториал",
            callback_data="tutorial:skip"
        )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)
