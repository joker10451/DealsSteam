import re
from html import escape

import aiohttp
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from regional_prices import extract_appid, get_regional_prices, format_regional_prices

router = Router()


def esc(text: str) -> str:
    return escape(str(text))


TAG_MAP = {
    "coop": "3843", "кооп": "3843",
    "roguelike": "1716", "рогалик": "1716",
    "horror": "4345", "хоррор": "4345",
    "rpg": "122", "рпг": "122",
    "strategy": "9", "стратегия": "9",
    "sandbox": "3959", "песочница": "3959",
    "survival": "1662", "выживание": "1662",
    "puzzle": "1664", "головоломка": "1664",
    "racing": "699", "гонки": "699",
    "shooter": "1155", "шутер": "1155",
}


@router.message(Command("price"))
async def cmd_price(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование:\n"
            "/price https://store.steampowered.com/app/1091500\n"
            "или\n"
            "/price Cyberpunk 2077"
        )
        return

    query = args[1].strip()
    appid = extract_appid(query)

    if not appid:
        search_url = "https://store.steampowered.com/api/storesearch/"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(search_url, params={"term": query, "cc": "ru", "l": "russian"}) as r:
                    data = await r.json()
            items = data.get("items", [])
            if items:
                appid = str(items[0]["id"])
                title = items[0]["name"]
            else:
                await message.answer(f"Игра «{esc(query)}» не найдена в Steam.")
                return
        except Exception as e:
            await message.answer(f"Ошибка поиска: {e}")
            return
    else:
        title = f"App {appid}"

    wait_msg = await message.answer("🔍 Проверяю цены по регионам...")
    results = await get_regional_prices(appid)
    text = format_regional_prices(title if not extract_appid(query) else f"App {appid}", results)
    await wait_msg.edit_text(text)


@router.message(Command("find"))
async def cmd_find(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        tags_list = ", ".join(list(TAG_MAP.keys())[:10])
        await message.answer(f"Использование: /find <тег>\n\nДоступные теги:\n{tags_list} ...")
        return

    tag_query = args[1].strip().lower()
    tag_id = TAG_MAP.get(tag_query)
    if not tag_id:
        await message.answer(
            f"Тег «{esc(tag_query)}» не найден.\n\n"
            "Попробуй: coop, roguelike, horror, rpg, strategy, sandbox, survival, puzzle, racing, shooter"
        )
        return

    wait_msg = await message.answer(f"🔍 Ищу игры по тегу «{esc(tag_query)}»...")
    url = "https://store.steampowered.com/search/results/"
    params = {"json": 1, "tags": tag_id, "specials": 1, "sort_by": "Discount_DESC", "count": 5}

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
    except Exception as e:
        await wait_msg.edit_text(f"Ошибка запроса: {e}")
        return

    items = data.get("items", [])
    if not items:
        await wait_msg.edit_text(f"Скидок по тегу «{esc(tag_query)}» сейчас нет.")
        return

    lines = [f"🎮 <b>Скидки по тегу #{tag_query}:</b>\n"]
    buttons = []
    for item in items[:5]:
        name = item.get("name", "?")
        appid = item.get("id", "")
        block = item.get("discount_block", "")
        discount_match = re.search(r"-(\d+)%", block)
        discount = f"-{discount_match.group(1)}%" if discount_match else ""
        link = f"https://store.steampowered.com/app/{appid}/"
        lines.append(f"• <a href='{link}'>{esc(name)}</a> {discount}")
        buttons.append([InlineKeyboardButton(text=f"🛒 {name[:35]}", url=link)])

    await wait_msg.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
