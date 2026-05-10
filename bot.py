"""Reminder Bot — Telegram-бот для напоминаний.

Phase 1: минимальный hello-world. Отвечает на /start и /help.
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

# Загружаем переменные окружения из .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit(
        "BOT_TOKEN не найден. Проверь, что в .env есть строка "
        "BOT_TOKEN=твой_токен"
    )

# Логи: видим в консоли, что происходит
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    """Приветственное сообщение при старте."""
    await message.answer(
        "Привет! Я бот-напоминалка.\n\n"
        "Скоро я научусь принимать твои напоминания и присылать их "
        "в нужное время.\n\n"
        "Команды:\n"
        "/help — справка"
    )
    logger.info("User %s sent /start", message.from_user.id)


@dp.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    """Справка по боту."""
    await message.answer(
        "Я умею (пока на этапе разработки):\n"
        "/start — начать работу\n"
        "/help — эта справка\n\n"
        "Скоро добавлю:\n"
        "/add — создать напоминание\n"
        "/list — список напоминаний\n"
        "/delete — удалить напоминание"
    )


async def main() -> None:
    logger.info("Bot is starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")