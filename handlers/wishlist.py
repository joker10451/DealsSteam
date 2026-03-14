from html import escape

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

from config import ADMIN_ID
from database import wishlist_add, wishlist_remove, wishlist_list

router = Router()


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
