"""
Обработчики команд онбординга.
"""
import logging
from html import escape as esc

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery

from onboarding import (
    is_new_user, start_tutorial, get_tutorial_step,
    next_tutorial_step, prev_tutorial_step,
    complete_tutorial, skip_tutorial,
    format_tutorial_message, create_tutorial_keyboard,
    get_onboarding_progress, RESTART_MESSAGE
)
from referral import check_and_apply_referral, ensure_referral_code_registered
from publisher import send_with_retry, get_bot

log = logging.getLogger(__name__)
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработка команды /start."""
    user_id = message.from_user.id
    
    try:
        # Регистрируем код пользователя в БД при каждом /start
        # чтобы его реферальная ссылка всегда работала
        await ensure_referral_code_registered(user_id)

        # Проверяем, новый ли пользователь
        new_user = await is_new_user(user_id)
        
        # Обрабатываем реферальный параметр
        start_param = None
        has_referral = False
        
        if message.text and len(message.text.split()) > 1:
            start_param = message.text.split()[1]
            
            # Применяем реферальный код
            if start_param and start_param.startswith("ref_"):
                referral_result = await check_and_apply_referral(user_id, start_param)
                
                if referral_result and referral_result.get("success"):
                    has_referral = True
                    log.info(f"User {user_id} registered via referral")
                elif referral_result and referral_result.get("error"):
                    log.warning(f"Referral error for user {user_id}: {referral_result['error']}")
        
        # Если новый пользователь - запускаем туториал
        if new_user:
            tutorial_data = await start_tutorial(user_id, has_referral)
            
            # Форматируем сообщение
            text = format_tutorial_message(
                tutorial_data['step'],
                tutorial_data
            )
            
            # Создаём клавиатуру
            keyboard = create_tutorial_keyboard(
                tutorial_data['step'],
                tutorial_data['total_steps'],
                can_skip=True
            )
            
            # Отправляем сообщение
            await send_with_retry(
                lambda: message.answer(text, reply_markup=keyboard)
            )
            
            log.info(f"Started tutorial for new user {user_id}")
        
        else:
            # Существующий пользователь - показываем приветствие
            welcome_text = (
                f"👋 С возвращением, {esc(message.from_user.first_name)}!\n\n"
                f"Основные команды:\n"
                f"• /wishlist - управление вишлистом\n"
                f"• /games - мини-игры и баллы\n"
                f"• /shop - магазин призов\n"
                f"• /tutorial - пройти туториал заново\n"
                f"• /help - полный список команд"
            )
            
            await send_with_retry(
                lambda: message.answer(welcome_text)
            )
            
            log.info(f"Existing user {user_id} used /start")
    
    except Exception as e:
        log.error(f"Error in cmd_start for user {user_id}: {e}")
        await message.answer(
            "❌ Произошла ошибка при запуске. Попробуй позже или используй /help"
        )


@router.message(Command("tutorial"))
async def cmd_tutorial(message: Message):
    """Обработка команды /tutorial."""
    user_id = message.from_user.id
    
    try:
        # Проверяем текущий прогресс
        progress = await get_onboarding_progress(user_id)
        
        # Если пользователь уже проходил туториал, показываем сообщение
        if progress and progress.get('status') in ['completed', 'skipped']:
            await message.answer(RESTART_MESSAGE)
        
        # Запускаем туториал с начала (без реферального бонуса)
        tutorial_data = await start_tutorial(user_id, has_referral=False)
        
        # Форматируем сообщение
        text = format_tutorial_message(
            tutorial_data['step'],
            tutorial_data
        )
        
        # Создаём клавиатуру
        keyboard = create_tutorial_keyboard(
            tutorial_data['step'],
            tutorial_data['total_steps'],
            can_skip=True
        )
        
        # Отправляем сообщение
        await send_with_retry(
            lambda: message.answer(text, reply_markup=keyboard)
        )
        
        log.info(f"User {user_id} restarted tutorial")
    
    except Exception as e:
        log.error(f"Error in cmd_tutorial for user {user_id}: {e}")
        await message.answer(
            "❌ Произошла ошибка при запуске туториала. Попробуй позже."
        )


@router.callback_query(lambda c: c.data and c.data.startswith("tutorial:"))
async def tutorial_navigation(callback: CallbackQuery):
    """Обработка навигации по туториалу."""
    user_id = callback.from_user.id
    action = callback.data.split(":")[1] if ":" in callback.data else ""
    
    try:
        # Получаем текущий прогресс
        progress = await get_onboarding_progress(user_id)
        
        if not progress:
            await callback.answer("❌ Туториал не найден. Используй /tutorial")
            return
        
        has_referral = progress.get('has_referral', False)
        
        # Обрабатываем действия
        if action == "next":
            # Переход к следующему шагу
            new_step = await next_tutorial_step(user_id)
            tutorial_data = await get_tutorial_step(user_id, new_step, has_referral)
            
            # Форматируем сообщение
            text = format_tutorial_message(new_step, tutorial_data)
            keyboard = create_tutorial_keyboard(
                new_step,
                tutorial_data['total_steps'],
                can_skip=True
            )
            
            # Обновляем сообщение
            await send_with_retry(
                lambda: callback.message.edit_text(text, reply_markup=keyboard)
            )
            await callback.answer()
            
            log.debug(f"User {user_id} moved to step {new_step}")
        
        elif action == "prev":
            # Возврат к предыдущему шагу
            new_step = await prev_tutorial_step(user_id)
            tutorial_data = await get_tutorial_step(user_id, new_step, has_referral)
            
            # Форматируем сообщение
            text = format_tutorial_message(new_step, tutorial_data)
            keyboard = create_tutorial_keyboard(
                new_step,
                tutorial_data['total_steps'],
                can_skip=True
            )
            
            # Обновляем сообщение
            await send_with_retry(
                lambda: callback.message.edit_text(text, reply_markup=keyboard)
            )
            await callback.answer()
            
            log.debug(f"User {user_id} moved back to step {new_step}")
        
        elif action == "skip":
            # Показываем подтверждение пропуска
            confirm_text = (
                "⚠️ <b>Пропустить туториал?</b>\n\n"
                "Ты не получишь бонус +20 баллов.\n"
                "Ты всегда можешь пройти туториал позже командой /tutorial"
            )
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да, пропустить",
                        callback_data="tutorial:skip_confirm"
                    ),
                    InlineKeyboardButton(
                        text="❌ Нет, продолжить",
                        callback_data="tutorial:skip_cancel"
                    )
                ]
            ])
            
            await send_with_retry(
                lambda: callback.message.edit_text(confirm_text, reply_markup=confirm_keyboard)
            )
            await callback.answer()
            
            log.debug(f"User {user_id} requested skip confirmation")
        
        elif action == "skip_confirm":
            # Подтверждение пропуска
            result = await skip_tutorial(user_id)
            
            if result['success']:
                await send_with_retry(
                    lambda: callback.message.edit_text(result['message'])
                )
                await callback.answer("Туториал пропущен")
                
                log.info(f"User {user_id} skipped tutorial")
            else:
                await callback.answer(result['message'], show_alert=True)
        
        elif action == "skip_cancel":
            # Отмена пропуска - возвращаемся к текущему шагу
            current_step = progress.get('current_step', 0)
            tutorial_data = await get_tutorial_step(user_id, current_step, has_referral)
            
            text = format_tutorial_message(current_step, tutorial_data)
            keyboard = create_tutorial_keyboard(
                current_step,
                tutorial_data['total_steps'],
                can_skip=True
            )
            
            await send_with_retry(
                lambda: callback.message.edit_text(text, reply_markup=keyboard)
            )
            await callback.answer("Продолжаем туториал")
            
            log.debug(f"User {user_id} cancelled skip")
        
        elif action == "complete":
            # Завершение туториала
            result = await complete_tutorial(user_id)
            
            if result['success']:
                await send_with_retry(
                    lambda: callback.message.edit_text(result['message'])
                )
                await callback.answer(f"🎉 +{result['points']} баллов!")
                
                log.info(f"User {user_id} completed tutorial, awarded {result['points']} points")
            else:
                await callback.answer(result['message'], show_alert=True)
        
        elif action == "try_wishlist":
            # Показать инструкцию по вишлисту
            wishlist_text = (
                "💝 <b>Как добавить игру в вишлист:</b>\n\n"
                "Используй команду:\n"
                "<code>/wishlist Название игры</code>\n\n"
                "Пример:\n"
                "<code>/wishlist Cyberpunk 2077</code>\n\n"
                "Также можешь импортировать вишлист из Steam:\n"
                "<code>/steam</code>"
            )
            
            await callback.answer()
            await send_with_retry(
                lambda: get_bot().send_message(user_id, wishlist_text)
            )
            
            log.debug(f"User {user_id} requested wishlist instructions")
        
        elif action == "try_games":
            # Показать список игр
            await callback.answer()
            
            # Вызываем команду /games
            from handlers.games import cmd_games
            fake_message = type('obj', (object,), {
                'from_user': callback.from_user,
                'answer': lambda text, **kwargs: get_bot().send_message(user_id, text, **kwargs)
            })()
            
            await cmd_games(fake_message)
            
            log.debug(f"User {user_id} requested games list")
        
        elif action == "try_shop":
            # Показать магазин
            await callback.answer()
            
            # Вызываем команду /shop
            from handlers.games import cmd_shop
            fake_message = type('obj', (object,), {
                'from_user': callback.from_user,
                'answer': lambda text, **kwargs: get_bot().send_message(user_id, text, **kwargs)
            })()
            
            await cmd_shop(fake_message)
            
            log.debug(f"User {user_id} requested shop")
        
        elif action == "progress":
            # Игнорируем клик на индикатор прогресса
            await callback.answer()
        
        else:
            await callback.answer("❌ Неизвестное действие")
            log.warning(f"Unknown tutorial action: {action}")
    
    except Exception as e:
        log.error(f"Error in tutorial_navigation for user {user_id}, action {action}: {e}")
        await callback.answer("❌ Произошла ошибка. Попробуй /tutorial", show_alert=True)
