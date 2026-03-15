from html import escape
import hashlib
import re

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
)

from config import ADMIN_ID
from database import (
    wishlist_add, wishlist_remove, wishlist_list,
    genre_subscribe, genre_unsubscribe, genre_list,
    free_game_subscribe, free_game_unsubscribe,
)

router = Router()

# Временный кэш Steam URL для обхода лимита callback_data (64 байта)
# key: 8-символьный hex-хэш, value: полный URL
_steam_url_cache: dict[str, str] = {}

STEAM_PROFILE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?steamcommunity\.com/(?:id|profiles)/[\w-]+/?"
    r"|^7656119\d{10}$",  # Steam ID64
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)

GENRE_MAP = {
    "rpg": "rpg", "рпг": "rpg",
    "action": "action", "экшен": "action",
    "strategy": "strategy", "стратегия": "strategy",
    "horror": "horror", "хоррор": "horror",
    "indie": "indie", "инди": "indie",
    "coop": "co-op", "кооп": "co-op",
    "roguelike": "roguelike", "рогалик": "roguelike",
    "survival": "survival", "выживание": "survival",
    "puzzle": "puzzle", "головоломка": "puzzle",
    "racing": "racing", "гонки": "racing",
    "shooter": "shooter", "шутер": "shooter",
    "adventure": "adventure", "приключения": "adventure",
    "simulation": "simulation", "симулятор": "simulation",
    "sports": "sports", "спорт": "sports",
}


def esc(text: str) -> str:
    return escape(str(text))


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мой вишлист"), KeyboardButton(text="❌ Удалить из вишлиста")],
        ],
        resize_keyboard=True,
    )


@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    
    # Проверяем реферальный код
    start_param = None
    if message.text and len(message.text.split()) > 1:
        start_param = message.text.split()[1]
    
    # Обрабатываем реферальную ссылку
    referral_bonus_text = ""
    if start_param:
        from referral import check_and_apply_referral
        result = await check_and_apply_referral(user_id, start_param)
        
        if result and "success" in result:
            referral_bonus_text = (
                f"\n\n🎉 <b>Бонус за приглашение!</b>\n"
                f"Ты получил <b>+{result['referee_bonus']}</b> баллов\n"
                f"Твой друг получил <b>+{result['referrer_bonus']}</b> баллов\n"
            )
    
    await message.answer(
        "👋 Привет! Я слежу за скидками на игры.\n\n"
        "<b>Что я умею:</b>\n"
        "• Напиши название игры — добавлю в вишлист и уведомлю при скидке\n"
        "• Играй в мини-игры и зарабатывай баллы\n"
        "• Обменивай баллы на Steam ключи и призы\n"
        "• Ловлю ошибки цен и бесплатные игры\n\n"
        "<b>Основные команды:</b>\n"
        "/wishlist — посмотреть вишлист\n"
        "/free — подписаться на бесплатные игры\n"
        "/games — мини-игры\n"
        "/shop — магазин призов\n"
        "/invite — пригласи друга и получи 100 баллов\n"
        "/profile — мой профиль\n\n"
        "<b>Дополнительно:</b>\n"
        "/top — топ скидок прямо сейчас\n"
        "/price [ссылка] — цены по регионам\n"
        "/find [тег] — найти по жанру\n"
        "/genre [жанр] — подписаться на жанр\n"
        + referral_bonus_text
        + (
            "\n<b>Админ:</b>\n"
            "/post — опубликовать скидки\n"
            "/gems — скрытые жемчужины\n"
            "/digest — дайджест недели\n"
            "/stats — метрики"
            if message.from_user.id == ADMIN_ID else ""
        ),
        reply_markup=main_keyboard(),
    )


@router.message(Command("wishlist"))
@router.message(F.text == "📋 Мой вишлист")
async def cmd_wishlist(message: Message):
    items = await wishlist_list(message.from_user.id)
    if not items:
        await message.answer("Твой вишлист пуст. Напиши название игры чтобы добавить.")
        return
    lines = [f"{i+1}. {esc(item)}" for i, item in enumerate(items)]
    await message.answer("📋 <b>Твой вишлист:</b>\n\n" + "\n".join(lines))


@router.message(Command("remove"))
@router.message(F.text == "❌ Удалить из вишлиста")
async def cmd_remove_prompt(message: Message):
    items = await wishlist_list(message.from_user.id)
    if not items:
        await message.answer("Вишлист пуст.")
        return
    buttons = [
        [InlineKeyboardButton(text=f"❌ {item}", callback_data=f"wl_del:{item[:40]}")]
        for item in items
    ]
    buttons.append([InlineKeyboardButton(text="✅ Готово", callback_data="wl_done")])
    await message.answer(
        "Нажми на игру чтобы удалить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.message(Command("remove", magic=F.args))
async def cmd_remove(message: Message):
    query = message.text.split(maxsplit=1)[1].strip() if len(message.text.split()) > 1 else ""
    if not query:
        await message.answer("Укажи название: /remove Cyberpunk 2077")
        return
    removed = await wishlist_remove(message.from_user.id, query)
    if removed:
        await message.answer(f"✅ «{esc(query)}» удалено из вишлиста.")
    else:
        await message.answer(f"Не нашёл «{esc(query)}» в твоём вишлисте.")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message):
    """Интерактивное удаление из вишлиста через inline-кнопки."""
    items = await wishlist_list(message.from_user.id)
    if not items:
        await message.answer("Вишлист пуст.")
        return
    buttons = [
        [InlineKeyboardButton(text=f"❌ {item}", callback_data=f"wl_del:{item[:40]}")]
        for item in items
    ]
    buttons.append([InlineKeyboardButton(text="✅ Готово", callback_data="wl_done")])
    await message.answer(
        "Нажми на игру чтобы удалить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("wl_del:"))
async def cb_wishlist_delete(callback: CallbackQuery):
    query = callback.data.split(":", 1)[1]
    await wishlist_remove(callback.from_user.id, query)
    items = await wishlist_list(callback.from_user.id)
    if not items:
        await callback.message.edit_text("✅ Вишлист пуст.")
        return
    buttons = [
        [InlineKeyboardButton(text=f"❌ {item}", callback_data=f"wl_del:{item[:40]}")]
        for item in items
    ]
    buttons.append([InlineKeyboardButton(text="✅ Готово", callback_data="wl_done")])
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer(f"«{esc(query)}» удалено")


@router.callback_query(F.data == "wl_done")
async def cb_wishlist_done(callback: CallbackQuery):
    await callback.message.edit_text("✅ Готово.")
    await callback.answer()


@router.callback_query(F.data.startswith("wl_add:"))
async def cb_wishlist_add_from_post(callback: CallbackQuery):
    title = callback.data.split(":", 1)[1].strip()
    added = await wishlist_add(callback.from_user.id, title)
    if added is None:
        await callback.answer("❌ Вишлист полон (макс. 20 игр)", show_alert=True)
    elif added:
        await callback.answer(f"✅ «{title}» добавлено в вишлист", show_alert=True)
    else:
        await callback.answer("Уже есть в вишлисте", show_alert=True)


@router.message(Command("genre"))
async def cmd_genre(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        genres_list = ", ".join(sorted(set(GENRE_MAP.keys()))[:12])
        await message.answer(
            "Использование: /genre [жанр]\n\n"
            f"Доступные жанры:\n{genres_list} ..."
        )
        return

    raw = args[1].strip().lower()
    genre = GENRE_MAP.get(raw)
    if not genre:
        await message.answer(
            f"Жанр «{esc(raw)}» не найден.\n\n"
            "Попробуй: rpg, action, horror, indie, coop, roguelike, survival, puzzle, shooter, strategy"
        )
        return

    added = await genre_subscribe(message.from_user.id, genre)
    if added:
        await message.answer(f"✅ Подписался на жанр <b>{genre}</b>. Буду присылать уведомления о скидках.")
    else:
        # Уже подписан — отписываемся
        await genre_unsubscribe(message.from_user.id, genre)
        await message.answer(f"🔕 Отписался от жанра <b>{genre}</b>.")


@router.message(Command("genres"))
async def cmd_genres(message: Message):
    items = await genre_list(message.from_user.id)
    if not items:
        await message.answer("Нет подписок на жанры. Используй /genre [жанр] чтобы подписаться.")
        return
    lines = [f"• {item}" for item in items]
    await message.answer(
        "🎯 <b>Твои подписки на жанры:</b>\n\n" + "\n".join(lines) +
        "\n\n<i>Напиши /genre [жанр] ещё раз чтобы отписаться.</i>"
    )


@router.message(F.text & ~F.text.startswith("/"))
async def handle_wishlist_add(message: Message):
    query = message.text.strip()
    if len(query) < 2:
        return

    # Если пользователь прислал ссылку на Steam-профиль — предлагаем импорт
    if STEAM_PROFILE_RE.search(query):
        # Сохраняем URL в кэш, передаём только короткий ключ (8 hex-символов = 16 байт в callback)
        url_key = hashlib.md5(query.encode()).hexdigest()[:8]
        _steam_url_cache[url_key] = query
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="📥 Импортировать вишлист Steam",
                callback_data=f"swl:{url_key}",
            )],
            [InlineKeyboardButton(
                text="📚 Импортировать библиотеку Steam",
                callback_data=f"slib:{url_key}",
            )],
        ])
        await message.answer(
            "🔗 Похоже, это ссылка на Steam-профиль.\n\n"
            "Хочешь импортировать игры из него?",
            reply_markup=keyboard,
        )
        return

    # Блокируем любые другие URL — они не могут быть названием игры
    if URL_RE.search(query):
        await message.answer(
            "❌ Ссылки нельзя добавить в вишлист.\n\n"
            "Напиши название игры, например: <code>Cyberpunk 2077</code>"
        )
        return

    user_id = message.from_user.id
    added = await wishlist_add(user_id, query)
    if added is None:
        from rewards import has_active_reward
        limit = 50 if await has_active_reward(user_id, "extended_wishlist") else 20
        await message.answer(
            f"❌ В вишлисте максимум {limit} игр. Удали что-нибудь через /remove.\n"
            + ("" if limit == 50 else "💎 Купи расширенный вишлист в /shop чтобы увеличить до 50.")
        )
    elif added:
        await message.answer(
            f"✅ <b>{esc(query)}</b> добавлено в вишлист.\n"
            "Пришлю уведомление когда появится скидка.",
            reply_markup=main_keyboard(),
        )
        
        # Show hint after first wishlist add
        from onboarding import show_hint
        from publisher import send_with_retry
        hint_text = await show_hint(message.from_user.id, "wishlist_vote")
        if hint_text:
            await send_with_retry(lambda: message.answer(hint_text))
    else:
        await message.answer(f"«{esc(query)}» уже есть в твоём вишлисте.")


@router.callback_query(F.data.startswith("swl:"))
async def cb_steam_import_wishlist_from_url(callback: CallbackQuery):
    """Импорт вишлиста по ссылке из кнопки под сообщением."""
    await callback.answer()
    url_key = callback.data.split(":", 1)[1]
    profile_url = _steam_url_cache.get(url_key)
    if not profile_url:
        await callback.message.edit_text(
            "❌ Ссылка устарела. Отправь профиль ещё раз."
        )
        return

    user_id = callback.from_user.id
    from steam_api import resolve_steam_id, fetch_wishlist
    from database import wishlist_add as db_wishlist_add

    status = await callback.message.edit_text("🔄 Загружаю вишлист Steam...")

    steam_id = await resolve_steam_id(profile_url)
    if not steam_id:
        await status.edit_text("❌ Не удалось распознать Steam профиль. Попробуй /steam [ссылка]")
        return

    games = await fetch_wishlist(steam_id)
    if not games:
        await status.edit_text(
            "❌ Вишлист пуст или профиль закрыт.\n"
            "Убедись что вишлист публичный в настройках Steam."
        )
        return

    added_count = 0
    for title in games[:100]:
        result = await db_wishlist_add(user_id, title)
        if result is True:
            added_count += 1
        elif result is None:
            break  # лимит достигнут

    _steam_url_cache.pop(url_key, None)
    await status.edit_text(
        f"✅ Импортировано <b>{added_count}</b> игр из Steam вишлиста.\n"
        f"Пришлю уведомление когда появятся скидки."
    )


@router.callback_query(F.data.startswith("slib:"))
async def cb_steam_import_library_from_url(callback: CallbackQuery):
    """Импорт библиотеки по ссылке из кнопки под сообщением."""
    await callback.answer()
    url_key = callback.data.split(":", 1)[1]
    profile_url = _steam_url_cache.get(url_key)
    if not profile_url:
        await callback.message.edit_text(
            "❌ Ссылка устарела. Отправь профиль ещё раз."
        )
        return

    user_id = callback.from_user.id
    from steam_api import resolve_steam_id, fetch_library
    from database import steam_link_account, steam_library_replace

    status = await callback.message.edit_text("🔄 Загружаю библиотеку Steam...")

    steam_id = await resolve_steam_id(profile_url)
    if not steam_id:
        await status.edit_text("❌ Не удалось распознать Steam профиль. Попробуй /steam [ссылка]")
        return

    appids = await fetch_library(steam_id)
    if not appids:
        await status.edit_text(
            "❌ Библиотека пуста или профиль закрыт.\n"
            "Убедись что библиотека публичная в настройках Steam."
        )
        return

    await steam_link_account(user_id, steam_id)
    await steam_library_replace(user_id, appids)

    _steam_url_cache.pop(url_key, None)
    await status.edit_text(
        f"✅ Библиотека синхронизирована: <b>{len(appids)}</b> игр.\n"
        f"Теперь бот не будет уведомлять о скидках на игры, которые у тебя уже есть."
    )


@router.message(Command("free"))
async def cmd_free_subscribe(message: Message):
    """Подписка на уведомления о бесплатных играх."""
    subscribed = await free_game_subscribe(message.from_user.id)
    if subscribed:
        await message.answer(
            "🎁 <b>Подписка активирована!</b>\n\n"
            "Теперь ты будешь получать уведомления о бесплатных играх "
            "в Steam, GOG и Epic Games Store.\n\n"
            "Чтобы отписаться, используй /free_off"
        )
    else:
        await message.answer(
            "Ты уже подписан на уведомления о бесплатных играх.\n\n"
            "Чтобы отписаться, используй /free_off"
        )


@router.message(Command("free_off"))
async def cmd_free_unsubscribe(message: Message):
    """Отписка от уведомлений о бесплатных играх."""
    unsubscribed = await free_game_unsubscribe(message.from_user.id)
    if unsubscribed:
        await message.answer(
            "🔕 Отписался от уведомлений о бесплатных играх.\n\n"
            "Чтобы снова подписаться, используй /free"
        )
    else:
        await message.answer(
            "Ты не был подписан на уведомления о бесплатных играх.\n\n"
            "Чтобы подписаться, используй /free"
        )

