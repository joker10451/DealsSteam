"""
Настройки уведомлений через inline-меню /settings.
Также сохранены legacy-команды для обратной совместимости.
"""
import logging
from html import escape

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from database import notif_settings_get, notif_settings_set

log = logging.getLogger(__name__)
router = Router()

STORES = ["steam", "gog", "epic games"]
STORE_LABELS = {"steam": "🎮 Steam", "gog": "🟣 GOG", "epic games": "🎁 Epic Games"}

GENRES = ["action", "rpg", "strategy", "horror", "indie", "co-op",
          "roguelike", "survival", "puzzle", "shooter", "adventure", "simulation"]
GENRE_LABELS = {
    "action": "💥 Экшен", "rpg": "⚔️ RPG", "strategy": "🧠 Стратегия",
    "horror": "👻 Хоррор", "indie": "🎲 Инди", "co-op": "🤝 Кооп",
    "roguelike": "🎰 Рогалик", "survival": "🏕 Выживание", "puzzle": "🧩 Головоломка",
    "shooter": "🔫 Шутер", "adventure": "🗺 Приключения", "simulation": "🚗 Симулятор",
}

DISCOUNT_STEPS = [0, 25, 50, 75, 90]


def esc(text: str) -> str:
    return escape(str(text))


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

def _settings_main_kb(s: dict) -> InlineKeyboardMarkup:
    """Главное меню настроек."""
    stores = s["preferred_stores"]
    ignored = s["ignored_genres"]
    min_d = s["min_discount"]
    qs, qe = s["quiet_start"], s["quiet_end"]
    grouping = s["grouping_enabled"]

    stores_label = ", ".join(STORE_LABELS[st].split(" ", 1)[1] for st in stores) if stores else "нет"
    ignored_label = f"{len(ignored)} жанров" if ignored else "нет"
    quiet_label = "выкл" if qs == qe else f"{qs:02d}–{qe:02d}"
    group_label = "✅" if grouping else "❌"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🏪 Магазины: {stores_label}",
            callback_data="ns:stores",
        )],
        [InlineKeyboardButton(
            text=f"💸 Мин. скидка: {min_d}%",
            callback_data="ns:discount",
        )],
        [InlineKeyboardButton(
            text=f"🚫 Игнор жанров: {ignored_label}",
            callback_data="ns:genres",
        )],
        [InlineKeyboardButton(
            text=f"🌙 Тихие часы: {quiet_label} МСК",
            callback_data="ns:quiet",
        )],
        [InlineKeyboardButton(
            text=f"📦 Группировка: {group_label}",
            callback_data="ns:toggle_group",
        )],
        [InlineKeyboardButton(text="🔄 Сбросить всё", callback_data="ns:reset")],
    ])


def _stores_kb(s: dict) -> InlineKeyboardMarkup:
    active = s["preferred_stores"]
    rows = []
    for store in STORES:
        tick = "✅" if store in active else "❌"
        rows.append([InlineKeyboardButton(
            text=f"{tick} {STORE_LABELS[store]}",
            callback_data=f"ns:store_toggle:{store}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="ns:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _discount_kb(s: dict) -> InlineKeyboardMarkup:
    current = s["min_discount"]
    rows = []
    row = []
    for step in DISCOUNT_STEPS:
        tick = "●" if step == current else ""
        row.append(InlineKeyboardButton(
            text=f"{tick}{step}%" if not tick else f"[{step}%]",
            callback_data=f"ns:disc_set:{step}",
        ))
    rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="ns:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _genres_kb(s: dict) -> InlineKeyboardMarkup:
    ignored = s["ignored_genres"]
    rows = []
    # 2 кнопки в ряд
    genre_list = list(GENRES)
    for i in range(0, len(genre_list), 2):
        pair = genre_list[i:i+2]
        row = []
        for g in pair:
            tick = "🚫" if g in ignored else "✅"
            row.append(InlineKeyboardButton(
                text=f"{tick} {GENRE_LABELS[g]}",
                callback_data=f"ns:genre_toggle:{g}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="ns:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _quiet_kb() -> InlineKeyboardMarkup:
    """Пресеты тихих часов."""
    presets = [
        ("Выкл", "0:0"),
        ("23–08", "23:8"),
        ("00–09", "0:9"),
        ("22–07", "22:7"),
    ]
    rows = [[
        InlineKeyboardButton(text=label, callback_data=f"ns:quiet_set:{val}")
        for label, val in presets
    ]]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="ns:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _settings_text(s: dict) -> str:
    stores = [STORE_LABELS[st] for st in s["preferred_stores"]] if s["preferred_stores"] else ["нет"]
    ignored = [GENRE_LABELS.get(g, g) for g in s["ignored_genres"]]
    qs, qe = s["quiet_start"], s["quiet_end"]
    quiet = "выкл" if qs == qe else f"{qs:02d}:00 – {qe:02d}:00 МСК"
    grouping = "✅ включена" if s["grouping_enabled"] else "❌ выключена"

    lines = [
        "⚙️ <b>Настройки уведомлений</b>\n",
        f"🏪 Магазины: {', '.join(stores)}",
        f"💸 Мин. скидка: <b>{s['min_discount']}%</b>",
        f"🚫 Игнор жанров: {', '.join(ignored) if ignored else 'нет'}",
        f"🌙 Тихие часы: <b>{quiet}</b>",
        f"📦 Группировка: {grouping}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

@router.message(Command("settings"))
async def cmd_settings(message: Message):
    s = await notif_settings_get(message.from_user.id)
    await message.answer(
        _settings_text(s),
        reply_markup=_settings_main_kb(s),
    )


# --- главное меню: назад ---
@router.callback_query(F.data == "ns:back")
async def cb_back(callback: CallbackQuery):
    s = await notif_settings_get(callback.from_user.id)
    await callback.message.edit_text(_settings_text(s), reply_markup=_settings_main_kb(s))
    await callback.answer()


# --- магазины ---
@router.callback_query(F.data == "ns:stores")
async def cb_stores_menu(callback: CallbackQuery):
    s = await notif_settings_get(callback.from_user.id)
    await callback.message.edit_text(
        "🏪 <b>Выбери магазины</b>\nУведомления придут только по выбранным площадкам.",
        reply_markup=_stores_kb(s),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ns:store_toggle:"))
async def cb_store_toggle(callback: CallbackQuery):
    store = callback.data.split(":", 2)[2]
    if store not in STORES:
        await callback.answer("Неизвестный магазин", show_alert=True)
        return
    s = await notif_settings_get(callback.from_user.id)
    active = list(s["preferred_stores"])
    if store in active:
        if len(active) == 1:
            await callback.answer("Нужен хотя бы один магазин!", show_alert=True)
            return
        active.remove(store)
    else:
        active.append(store)
    await notif_settings_set(callback.from_user.id, preferred_stores=active)
    s["preferred_stores"] = active
    await callback.message.edit_reply_markup(reply_markup=_stores_kb(s))
    await callback.answer()


# --- минимальная скидка ---
@router.callback_query(F.data == "ns:discount")
async def cb_discount_menu(callback: CallbackQuery):
    s = await notif_settings_get(callback.from_user.id)
    await callback.message.edit_text(
        "💸 <b>Минимальная скидка</b>\nУведомлять только если скидка не меньше выбранного значения.",
        reply_markup=_discount_kb(s),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ns:disc_set:"))
async def cb_disc_set(callback: CallbackQuery):
    try:
        pct = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    await notif_settings_set(callback.from_user.id, min_discount=pct)
    s = await notif_settings_get(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=_discount_kb(s))
    label = "любой скидке" if pct == 0 else f"скидкам от {pct}%"
    await callback.answer(f"✅ Буду уведомлять о {label}")


# --- жанры (чёрный список) ---
@router.callback_query(F.data == "ns:genres")
async def cb_genres_menu(callback: CallbackQuery):
    s = await notif_settings_get(callback.from_user.id)
    await callback.message.edit_text(
        "🚫 <b>Игнорируемые жанры</b>\n"
        "Уведомления об играх этих жанров приходить не будут.\n"
        "✅ — уведомлять, 🚫 — игнорировать.",
        reply_markup=_genres_kb(s),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ns:genre_toggle:"))
async def cb_genre_toggle(callback: CallbackQuery):
    genre = callback.data.split(":", 2)[2]
    if genre not in GENRES:
        await callback.answer()
        return
    s = await notif_settings_get(callback.from_user.id)
    ignored = list(s["ignored_genres"])
    if genre in ignored:
        ignored.remove(genre)
        await callback.answer(f"✅ {GENRE_LABELS.get(genre, genre)} — уведомления включены")
    else:
        ignored.append(genre)
        await callback.answer(f"🚫 {GENRE_LABELS.get(genre, genre)} — игнорируется")
    await notif_settings_set(callback.from_user.id, ignored_genres=ignored)
    s["ignored_genres"] = ignored
    await callback.message.edit_reply_markup(reply_markup=_genres_kb(s))


# --- тихие часы ---
@router.callback_query(F.data == "ns:quiet")
async def cb_quiet_menu(callback: CallbackQuery):
    s = await notif_settings_get(callback.from_user.id)
    qs, qe = s["quiet_start"], s["quiet_end"]
    current = "выкл" if qs == qe else f"{qs:02d}:00 – {qe:02d}:00 МСК"
    await callback.message.edit_text(
        f"🌙 <b>Тихие часы</b>\nСейчас: <b>{current}</b>\n\n"
        "Выбери пресет или отправь команду:\n"
        "<code>/quiet_hours 23 8</code>",
        reply_markup=_quiet_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ns:quiet_set:"))
async def cb_quiet_set(callback: CallbackQuery):
    try:
        val = callback.data.split(":", 2)[2]
        qs, qe = map(int, val.split(":"))
    except (IndexError, ValueError):
        await callback.answer()
        return
    await notif_settings_set(callback.from_user.id, quiet_start=qs, quiet_end=qe)
    label = "выключены" if qs == qe else f"{qs:02d}:00 – {qe:02d}:00 МСК"
    await callback.answer(f"✅ Тихие часы: {label}")
    s = await notif_settings_get(callback.from_user.id)
    await callback.message.edit_text(_settings_text(s), reply_markup=_settings_main_kb(s))


# --- группировка (toggle прямо из главного меню) ---
@router.callback_query(F.data == "ns:toggle_group")
async def cb_toggle_group(callback: CallbackQuery):
    s = await notif_settings_get(callback.from_user.id)
    new_val = not s["grouping_enabled"]
    await notif_settings_set(callback.from_user.id, grouping_enabled=new_val)
    s["grouping_enabled"] = new_val
    await callback.message.edit_reply_markup(reply_markup=_settings_main_kb(s))
    label = "включена" if new_val else "выключена"
    await callback.answer(f"📦 Группировка {label}")


# --- сброс ---
@router.callback_query(F.data == "ns:reset")
async def cb_reset(callback: CallbackQuery):
    await notif_settings_set(
        callback.from_user.id,
        min_discount=0,
        quiet_start=23,
        quiet_end=8,
        grouping_enabled=False,
        preferred_stores=["steam", "gog", "epic games"],
        ignored_genres=[],
    )
    s = await notif_settings_get(callback.from_user.id)
    await callback.message.edit_text(_settings_text(s), reply_markup=_settings_main_kb(s))
    await callback.answer("✅ Настройки сброшены")


# ---------------------------------------------------------------------------
# Legacy-команды (обратная совместимость)
# ---------------------------------------------------------------------------

@router.message(Command("notify_settings"))
async def cmd_notify_settings(message: Message):
    """Алиас для /settings."""
    s = await notif_settings_get(message.from_user.id)
    await message.answer(_settings_text(s), reply_markup=_settings_main_kb(s))


@router.message(Command("min_discount"))
async def cmd_min_discount(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Пример: <code>/min_discount 30</code>")
        return
    try:
        pct = int(parts[1])
        if not (0 <= pct <= 99):
            raise ValueError
    except ValueError:
        await message.answer("Введи число от 0 до 99.")
        return
    await notif_settings_set(message.from_user.id, min_discount=pct)
    label = "любой скидке" if pct == 0 else f"скидкам от {pct}%"
    await message.answer(f"✅ Буду уведомлять о {label}. Все настройки: /settings")


@router.message(Command("quiet_hours"))
async def cmd_quiet_hours(message: Message):
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Пример: <code>/quiet_hours 23 8</code>")
        return
    try:
        qs, qe = int(parts[1]), int(parts[2])
        if not (0 <= qs <= 23 and 0 <= qe <= 23):
            raise ValueError
    except ValueError:
        await message.answer("Введи два числа от 0 до 23.")
        return
    await notif_settings_set(message.from_user.id, quiet_start=qs, quiet_end=qe)
    label = "выключены" if qs == qe else f"{qs:02d}:00 – {qe:02d}:00 МСК"
    await message.answer(f"✅ Тихие часы: {label}. Все настройки: /settings")


@router.message(Command("group_notify"))
async def cmd_group_notify(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        await message.answer("Пример: <code>/group_notify on</code>")
        return
    enabled = parts[1].lower() == "on"
    await notif_settings_set(message.from_user.id, grouping_enabled=enabled)
    label = "включена" if enabled else "выключена"
    await message.answer(f"✅ Группировка {label}. Все настройки: /settings")
