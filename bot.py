"""Reminder Bot — entry point.

Минимальный модуль, отвечающий только за:
- загрузку конфига (.env),
- инициализацию БД,
- создание Bot/Dispatcher с MemoryStorage для FSM,
- регистрацию роутера с хендлерами,
- запуск polling.

Вся логика команд — в ``handlers.py``. Слой данных — в ``db.py``.
Парсинг времени — в ``time_parser.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from db import init_db
from handlers import router
from scheduler import run_scheduler

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit(
        "BOT_TOKEN не найден. Проверь, что в .env есть строка "
        "BOT_TOKEN=твой_токен"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Запускаем планировщик параллельно с polling'ом.
    scheduler_task = asyncio.create_task(run_scheduler(bot))

    logger.info("Bot is starting...")
    try:
        await dp.start_polling(bot)
    finally:
        # Корректное завершение: останавливаем планировщик, закрываем сессию.
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        logger.info("Bot shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
