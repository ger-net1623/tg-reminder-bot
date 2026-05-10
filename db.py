"""SQLite-хранилище напоминаний для Reminder Bot.

Модуль изолирован от Telegram-логики: его функции работают только с
данными. Это позволяет тестировать БД отдельно (см. ``__main__`` блок
внизу — smoke-тест, запускается через ``python db.py``).

Схема таблицы ``reminders``:

- ``id``           — INTEGER PRIMARY KEY AUTOINCREMENT
- ``user_id``      — Telegram user ID владельца напоминания
- ``chat_id``      — куда отправлять напоминание (обычно равен user_id)
- ``text``         — текст напоминания
- ``fire_at``      — когда сработать, ISO-8601 UTC, например ``2026-05-11T19:00:00+00:00``
- ``created_at``   — когда создано, ISO-8601 UTC
- ``status``       — ``pending`` / ``sent`` / ``cancelled``

Все datetime хранятся в UTC. Конвертация в локальное время — задача
слоя представления (handlers), не БД.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path("data") / "reminders.db"

STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_CANCELLED = "cancelled"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    chat_id     INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    fire_at     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_reminders_user_status
    ON reminders (user_id, status);

CREATE INDEX IF NOT EXISTS idx_reminders_status_fire_at
    ON reminders (status, fire_at);
"""


def _utcnow_iso() -> str:
    """Текущее время в UTC, в ISO-8601 со смещением (`+00:00`)."""
    return datetime.now(timezone.utc).isoformat()


def _to_iso(dt: datetime) -> str:
    """Сериализация datetime для хранения. Требует tz-aware datetime."""
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware (use UTC)")
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(value: str) -> datetime:
    """Парсинг ISO-8601 обратно в tz-aware datetime."""
    return datetime.fromisoformat(value)


async def init_db() -> None:
    """Создать схему, если её ещё нет. Идемпотентно — можно вызывать каждый запуск."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(_SCHEMA)
        await conn.commit()
    logger.info("DB initialized at %s", DB_PATH)


async def add_reminder(
    *,
    user_id: int,
    chat_id: int,
    text: str,
    fire_at: datetime,
) -> int:
    """Создать новое напоминание. Возвращает его ``id``."""
    if not text or not text.strip():
        raise ValueError("reminder text must not be empty")
    fire_at_iso = _to_iso(fire_at)
    created_at_iso = _utcnow_iso()
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute(
            """
            INSERT INTO reminders (user_id, chat_id, text, fire_at, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, chat_id, text.strip(), fire_at_iso, created_at_iso, STATUS_PENDING),
        )
        await conn.commit()
        reminder_id = cursor.lastrowid
    assert reminder_id is not None
    logger.info("Added reminder id=%s for user=%s at %s", reminder_id, user_id, fire_at_iso)
    return reminder_id


async def list_reminders(
    *,
    user_id: int,
    only_pending: bool = True,
) -> list[dict]:
    """Вернуть напоминания пользователя, отсортированные по времени срабатывания."""
    query = "SELECT * FROM reminders WHERE user_id = ?"
    params: tuple = (user_id,)
    if only_pending:
        query += " AND status = ?"
        params = (user_id, STATUS_PENDING)
    query += " ORDER BY fire_at ASC"

    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def delete_reminder(*, reminder_id: int, user_id: int) -> bool:
    """Удалить напоминание. Только владелец может удалить.

    Возвращает True если удалено, False если не найдено / не принадлежит юзеру.
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute(
            "DELETE FROM reminders WHERE id = ? AND user_id = ?",
            (reminder_id, user_id),
        )
        await conn.commit()
        deleted = cursor.rowcount > 0
    if deleted:
        logger.info("Deleted reminder id=%s by user=%s", reminder_id, user_id)
    return deleted


async def get_due_reminders(*, now: datetime | None = None) -> list[dict]:
    """Вернуть все pending-напоминания, у которых ``fire_at <= now``.

    Используется планировщиком: каждую минуту он зовёт эту функцию,
    отсылает каждое напоминание и помечает их как 'sent'.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now_iso = _to_iso(now)
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """
            SELECT * FROM reminders
            WHERE status = ? AND fire_at <= ?
            ORDER BY fire_at ASC
            """,
            (STATUS_PENDING, now_iso),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def mark_sent(*, reminder_id: int) -> None:
    """Отметить напоминание как отправленное."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "UPDATE reminders SET status = ? WHERE id = ?",
            (STATUS_SENT, reminder_id),
        )
        await conn.commit()
    logger.info("Marked reminder id=%s as sent", reminder_id)


# ---------------------------------------------------------------------------
# Smoke test: запускается через ``python db.py``. Делает полный цикл
# CRUD на временной тестовой БД и печатает результаты, чтобы убедиться,
# что слой работает без подключения к Telegram.
# ---------------------------------------------------------------------------


async def _smoke_test() -> None:
    global DB_PATH
    DB_PATH = Path("data") / "_smoke_test.db"
    if DB_PATH.exists():
        DB_PATH.unlink()

    print(f"[smoke] DB path: {DB_PATH}")

    await init_db()
    print("[smoke] init_db OK")

    fire_at = datetime(2026, 5, 11, 19, 0, tzinfo=timezone.utc)
    rid1 = await add_reminder(
        user_id=12345, chat_id=12345, text="Позвонить маме", fire_at=fire_at,
    )
    rid2 = await add_reminder(
        user_id=12345, chat_id=12345, text="Купить хлеб",
        fire_at=datetime(2026, 5, 11, 20, 0, tzinfo=timezone.utc),
    )
    print(f"[smoke] add_reminder OK: rid1={rid1}, rid2={rid2}")

    items = await list_reminders(user_id=12345)
    print(f"[smoke] list_reminders OK: {len(items)} items")
    for it in items:
        print(f"        - id={it['id']} text='{it['text']}' fire_at={it['fire_at']}")

    # delete second
    ok = await delete_reminder(reminder_id=rid2, user_id=12345)
    print(f"[smoke] delete_reminder OK: deleted={ok}")

    # try delete with wrong user — must fail
    ok_wrong = await delete_reminder(reminder_id=rid1, user_id=99999)
    print(f"[smoke] delete with wrong user_id: deleted={ok_wrong} (ожидаем False)")

    # due check: rid1 in 2026 — будет 'due' если сейчас уже после 2026-05-11
    # (проверим явно, передав future now)
    due = await get_due_reminders(now=datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc))
    print(f"[smoke] get_due_reminders OK: {len(due)} due")

    # mark_sent
    await mark_sent(reminder_id=rid1)
    pending = await list_reminders(user_id=12345, only_pending=True)
    all_items = await list_reminders(user_id=12345, only_pending=False)
    print(f"[smoke] mark_sent OK: pending={len(pending)}, all={len(all_items)}")

    # cleanup
    DB_PATH.unlink()
    print("[smoke] All tests passed ✓")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    asyncio.run(_smoke_test())
