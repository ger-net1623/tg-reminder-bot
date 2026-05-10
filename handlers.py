"""Telegram-хендлеры для Reminder Bot.

Все обработчики команд и сообщений в одном модуле. Они регистрируются
в общем ``router``, который подключается к Dispatcher в ``bot.py``.

FSM (finite state machine) используется для пошагового сценария
``/add``: бот спрашивает время → текст → подтверждает.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from db import add_reminder, delete_reminder, list_reminders
from time_parser import format_for_user, parse_user_time

logger = logging.getLogger(__name__)
router = Router()


class AddReminder(StatesGroup):
    """Состояния для пошагового создания напоминания."""

    waiting_for_time = State()
    waiting_for_text = State()


# ---------------------------------------------------------------------------
# Универсальные команды (работают всегда, в т. ч. посреди диалога)
# ---------------------------------------------------------------------------


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Приветствие. Сбрасывает любой активный FSM."""
    await state.clear()
    await message.answer(
        "👋 Привет! Я бот-напоминалка.\n\n"
        "Я могу записать твоё напоминание и прислать его в нужное время.\n\n"
        "<b>Команды:</b>\n"
        "/add — создать напоминание\n"
        "/list — список активных\n"
        "/delete — удалить по номеру\n"
        "/help — справка\n"
        "/cancel — отменить текущий ввод",
        parse_mode="HTML",
    )
    logger.info("User %s sent /start", message.from_user.id)


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    """Справка."""
    await state.clear()
    await message.answer(
        "<b>Что я умею:</b>\n"
        "/add — создать напоминание (пошаговый диалог)\n"
        "/list — список твоих активных напоминаний\n"
        "/delete <code>N</code> — удалить напоминание по номеру (например <code>/delete 3</code>)\n"
        "/help — эта справка\n"
        "/cancel — отменить текущий ввод\n\n"
        "<b>Форматы времени, которые я понимаю:</b>\n"
        "• <code>завтра в 19:00</code>\n"
        "• <code>сегодня в 23:30</code>\n"
        "• <code>через 30 минут</code> / <code>через 2 часа</code>\n"
        "• <code>11.05 19:00</code> / <code>11.05.2026 19:00</code>\n"
        "• <code>в пятницу в 18:00</code>\n\n"
        "Все времена — по Москве (UTC+3).",
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Прервать текущий ввод."""
    current = await state.get_state()
    if current is None:
        await message.answer("Сейчас нечего отменять. Введи /add чтобы создать напоминание.")
        return
    await state.clear()
    await message.answer("Окей, отменил. Можешь начать заново через /add.")
    logger.info("User %s cancelled FSM (was in %s)", message.from_user.id, current)


# ---------------------------------------------------------------------------
# Сценарий /add — пошаговый
# ---------------------------------------------------------------------------


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    """Старт сценария создания напоминания."""
    await state.set_state(AddReminder.waiting_for_time)
    await message.answer(
        "<b>Когда напомнить?</b>\n\n"
        "Например:\n"
        "• <code>завтра в 19:00</code>\n"
        "• <code>через 30 минут</code>\n"
        "• <code>11.05 в 20:30</code>\n\n"
        "Чтобы прервать — /cancel",
        parse_mode="HTML",
    )


@router.message(StateFilter(AddReminder.waiting_for_time))
async def process_time(message: Message, state: FSMContext) -> None:
    """Обработка ввода времени."""
    if message.text is None:
        await message.answer("Жду текстовое сообщение со временем. Или /cancel.")
        return

    fire_at = parse_user_time(message.text)
    if fire_at is None:
        await message.answer(
            "❌ Не понял время или оно в прошлом.\n\n"
            "Попробуй ещё раз, например: <code>завтра в 19:00</code> или "
            "<code>через 1 час</code>.\n\n"
            "Прервать — /cancel",
            parse_mode="HTML",
        )
        return

    await state.update_data(fire_at_iso=fire_at.isoformat())
    await state.set_state(AddReminder.waiting_for_text)
    await message.answer(
        f"⏰ Понял: <b>{format_for_user(fire_at)}</b>\n\n"
        "<b>О чём напомнить?</b> (просто напиши текст)",
        parse_mode="HTML",
    )


@router.message(StateFilter(AddReminder.waiting_for_text))
async def process_text(message: Message, state: FSMContext) -> None:
    """Обработка текста напоминания и сохранение в БД."""
    if message.text is None or not message.text.strip():
        await message.answer("Текст напоминания не может быть пустым. Напиши, о чём напомнить, или /cancel.")
        return

    text = message.text.strip()
    if len(text) > 500:
        await message.answer("Слишком длинный текст (максимум 500 символов). Сократи и пришли ещё раз.")
        return

    data = await state.get_data()
    fire_at_iso = data.get("fire_at_iso")
    if fire_at_iso is None:
        # такого быть не должно — состояние повреждено, сбрасываем
        await state.clear()
        await message.answer("Что-то пошло не так со временем, начни заново через /add.")
        return

    fire_at = datetime.fromisoformat(fire_at_iso)
    reminder_id = await add_reminder(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        text=text,
        fire_at=fire_at,
    )

    await state.clear()
    await message.answer(
        f"✅ Готово! Напоминание #{reminder_id} сохранено.\n\n"
        f"⏰ Когда: <b>{format_for_user(fire_at)}</b>\n"
        f"📝 Текст: <i>{text}</i>",
        parse_mode="HTML",
    )
    logger.info(
        "User %s created reminder #%s for %s",
        message.from_user.id, reminder_id, fire_at_iso,
    )


# ---------------------------------------------------------------------------
# /list и /delete
# ---------------------------------------------------------------------------


@router.message(Command("list"))
async def cmd_list(message: Message, state: FSMContext) -> None:
    """Показать активные напоминания пользователя."""
    await state.clear()
    items = await list_reminders(user_id=message.from_user.id, only_pending=True)
    if not items:
        await message.answer(
            "У тебя нет активных напоминаний.\n\nСоздать новое: /add"
        )
        return

    lines = ["<b>📋 Твои активные напоминания:</b>", ""]
    for it in items:
        fire_at = datetime.fromisoformat(it["fire_at"])
        text = it["text"]
        # Обрежем длинные тексты, чтобы список оставался читаемым.
        if len(text) > 80:
            text = text[:77] + "..."
        lines.append(
            f"<b>#{it['id']}</b> · {format_for_user(fire_at)}\n"
            f"   <i>{text}</i>"
        )
    lines.append("")
    lines.append("Удалить: <code>/delete N</code> (например <code>/delete 3</code>)")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("delete"))
async def cmd_delete(
    message: Message,
    command: CommandObject,
    state: FSMContext,
) -> None:
    """Удалить напоминание по его номеру."""
    await state.clear()

    if not command.args or not command.args.strip():
        await message.answer(
            "Укажи номер напоминания. Например: <code>/delete 3</code>\n\n"
            "Посмотреть номера: /list",
            parse_mode="HTML",
        )
        return

    arg = command.args.strip()
    try:
        reminder_id = int(arg)
    except ValueError:
        await message.answer(
            f"<code>{arg}</code> — это не номер. Нужно число, например <code>/delete 3</code>.",
            parse_mode="HTML",
        )
        return

    deleted = await delete_reminder(
        reminder_id=reminder_id,
        user_id=message.from_user.id,
    )
    if deleted:
        await message.answer(f"🗑 Напоминание <b>#{reminder_id}</b> удалено.", parse_mode="HTML")
        logger.info(
            "User %s deleted reminder #%s",
            message.from_user.id, reminder_id,
        )
    else:
        await message.answer(
            f"Напоминание <b>#{reminder_id}</b> не найдено или уже отправлено/удалено.\n\n"
            "Список активных: /list",
            parse_mode="HTML",
        )
