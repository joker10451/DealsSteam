"""
Команды управления настройками push-уведомлений вишлиста.
/notify_settings — показать текущие настройки
/min_discount N  — минимальный % скидки для уведомления (0 = все)
/quiet_hours H1 H2 — тихие часы с H1 до H2 (МСК), например /quiet_hours 23 8
/group_notify on|off — группировать уведомления вместо мгновенных
"""
import logging
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database import notif_settings_get, notif_settings_set

log = logging.getLogger(__name__)
router = Router()


def esc(text: str) -> str:
    return escape(str(text))


@router.message(Command("notify_settings"))
async def cmd_notify_settings(message: Message):
    s = await notif_settings_get(message.from_user.id)
    qs, qe = s["quiet_start"], s["quiet_end"]
    grouping = "✅ включена" if s["grouping_enabled"] else "❌ выключена"
    min_d = s["min_discount"]

    await message.answer(
        "🔔 <b>Настройки уведомлений вишлиста</b>\n\n"
        f"🏷 Минимальная скидка: <b>{min_d}%</b>\n"
        f"   (0 = уведомлять о любой скидке)\n\n"
        f"🌙 Тихие часы: <b>{qs:02d}:00 – {qe:02d}:00 МСК</b>\n"
        f"   (уведомления придут после окончания)\n\n"
        f"📦 Группировка: <b>{grouping}</b>\n"
        f"   (несколько скидок — одно сообщение)\n\n"
        "<b>Команды:</b>\n"
        "/min_discount 30 — уведомлять только от 30%\n"
        "/quiet_hours 23 8 — тихие часы с 23:00 до 08:00\n"
        "/group_notify on — включить группировку\n"
        "/group_notify off — выключить группировку"
    )


@router.message(Command("min_discount"))
async def cmd_min_discount(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Укажи минимальный процент скидки.\n"
            "Пример: <code>/min_discount 30</code>\n"
            "Значение 0 — уведомлять о любой скидке."
        )
        return

    try:
        pct = int(parts[1])
        if not (0 <= pct <= 99):
            raise ValueError
    except ValueError:
        await message.answer("Введи число от 0 до 99.")
        return

    await notif_settings_set(message.from_user.id, min_discount=pct)
    if pct == 0:
        await message.answer("✅ Буду уведомлять о любой скидке на игры из вишлиста.")
    else:
        await message.answer(f"✅ Буду уведомлять только о скидках от <b>{pct}%</b>.")


@router.message(Command("quiet_hours"))
async def cmd_quiet_hours(message: Message):
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer(
            "Укажи начало и конец тихих часов (МСК, 0–23).\n"
            "Пример: <code>/quiet_hours 23 8</code> — тихо с 23:00 до 08:00\n"
            "Чтобы отключить: <code>/quiet_hours 0 0</code>"
        )
        return

    try:
        qs = int(parts[1])
        qe = int(parts[2])
        if not (0 <= qs <= 23 and 0 <= qe <= 23):
            raise ValueError
    except ValueError:
        await message.answer("Введи два числа от 0 до 23.")
        return

    await notif_settings_set(message.from_user.id, quiet_start=qs, quiet_end=qe)

    if qs == qe:
        await message.answer("✅ Тихие часы отключены — уведомления приходят сразу.")
    else:
        await message.answer(
            f"✅ Тихие часы установлены: <b>{qs:02d}:00 – {qe:02d}:00 МСК</b>.\n"
            "Уведомления придут после окончания тихих часов."
        )


@router.message(Command("group_notify"))
async def cmd_group_notify(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        await message.answer(
            "Укажи on или off.\n"
            "Пример: <code>/group_notify on</code>"
        )
        return

    enabled = parts[1].lower() == "on"
    await notif_settings_set(message.from_user.id, grouping_enabled=enabled)

    if enabled:
        await message.answer(
            "✅ Группировка включена.\n"
            "Несколько скидок придут одним сообщением вместо нескольких."
        )
    else:
        await message.answer("✅ Группировка выключена — каждая скидка приходит сразу.")
