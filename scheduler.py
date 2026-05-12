"""Фоновый планировщик: проверяет БД и отправляет напоминания.

Цикл работает параллельно с polling'ом. Каждые ``POLL_INTERVAL_SEC``
секунд:

1. Запрашивает у БД все pending-напоминания, у которых ``fire_at <= now``.
2. Для каждого: отправляет сообщение в чат через Bot API.
3. Помечает напоминание как ``sent`` (даже если отправка упала — чтобы
   не зацикливаться на одном плохом адресате).

Точность срабатывания: ±``POLL_INTERVAL_SEC`` секунд. Для
напоминалки 15 секунд более чем достаточно (для секундной точности
нужен был бы APScheduler с per-reminder задачами, что для портфолио-
демо избыточно).
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from db import get_due_reminders, mark_sent
from time_parser import format_for_user
from datetime import datetime

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 15


async def _send_reminder(bot: Bot, reminder: dict) -> None:
    """Отправить одно напоминание. Не должно падать наружу — все ошибки
    логируем, но reminder помечаем как sent в любом случае."""
    chat_id = reminder["chat_id"]
    text = reminder["text"]
    fire_at = datetime.fromisoformat(reminder["fire_at"])

    body = (
        f"⏰ <b>Напоминание</b>\n\n"
        f"{text}\n\n"
        f"<i>Запланировано на {format_for_user(fire_at)}</i>"
    )
    try:
        await bot.send_message(chat_id, body)
        logger.info("Sent reminder #%s to chat %s", reminder["id"], chat_id)
    except TelegramAPIError as exc:
        # Юзер мог заблокировать бота / удалить чат / и т.п.
        logger.warning(
            "Failed to send reminder #%s to chat %s: %s",
            reminder["id"], chat_id, exc,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error sending reminder #%s: %s", reminder["id"], exc)
    finally:
        # Всегда помечаем sent — иначе зациклимся на проблемных записях.
        await mark_sent(reminder_id=reminder["id"])


async def _tick(bot: Bot) -> None:
    """Один цикл: достать просроченные и разослать."""
    due = await get_due_reminders()
    if not due:
        return
    logger.info("Found %s due reminders, dispatching...", len(due))
    for reminder in due:
        await _send_reminder(bot, reminder)


async def run_scheduler(bot: Bot) -> None:
    """Бесконечный цикл планировщика. Останавливается через
    ``asyncio.CancelledError`` (вызов task.cancel())."""
    logger.info("Scheduler started, poll interval = %ss", POLL_INTERVAL_SEC)
    try:
        while True:
            try:
                await _tick(bot)
            except Exception as exc:  # noqa: BLE001
                # Никогда не даём scheduler'у умереть из-за одной ошибки в БД/сети.
                logger.exception("Scheduler tick failed: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_SEC)
    except asyncio.CancelledError:
        logger.info("Scheduler cancelled, exiting cleanly")
        raise
