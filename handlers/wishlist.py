from html import escape

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
)

router = Router()

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
    await message.answer(
        "👋 Привет! Я слежу за скидками на игры.\n\n"
        "<b>Что я умею:</b>\n"
        "• Напиши название игры — добавлю в вишлист и уведомлю при скидке\n\n"
        "<b>Команды:</b>\n"
        "/wishlist — посмотреть вишлист\n"
        "/remove [название] — удалить из вишлиста\n"
        "/cancel — удалить из вишлиста кнопками\n"
        "/genre [жанр] — подписаться на уведомления по жанру\n"
        "/genres — мои подписки на жанры\n"
        "/top — топ скидок прямо сейчас\n"
        "/price [ссылка или название] — цены по регионам Steam\n"
        "/find [тег] — найти скидки по жанру (coop, rpg, horror...)\n"
        + (
            "\n<b>Админ:</b>\n"
            "/post — опубликовать скидки прямо сейчас\n"
            "/gems — опубликовать скрытые жемчужины\n"
            "/digest — опубликовать дайджест недели\n"
            "/stats — метрики за 7 дней"
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
    lines = [f"{i+1}. {esc(item)}" for i, item in enumerate(items)]
    await message.answer("📋 Напиши <b>/remove [название]</b> чтобы удалить.\n\n" + "\n".join(lines))


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
    added = await wishlist_add(message.from_user.id, query)
    if added is None:
        await message.answer("❌ В вишлисте максимум 20 игр. Удали что-нибудь через /remove.")
    elif added:
        await message.answer(
            f"✅ <b>{esc(query)}</b> добавлено в вишлист.\n"
            "Пришлю уведомление когда появится скидка.",
            reply_markup=main_keyboard(),
        )
    else:
        await message.answer(f"«{esc(query)}» уже есть в твоём вишлисте.")
