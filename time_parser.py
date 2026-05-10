"""Парсер пользовательского ввода времени в datetime (UTC).

Все пользователи бота интерпретируются в часовом поясе **Europe/Moscow**
(UTC+3) — это разумный дефолт для русскоязычной аудитории. Хранение
внутри (БД, планировщик) — всегда в UTC. Конвертация в Москву — только
для отображения и для парсинга ввода.

Поддерживаемые форматы (примеры):

- ``завтра в 19:00``
- ``сегодня в 23:30``
- ``через 30 минут``
- ``через 2 часа``
- ``11.05 19:00``
- ``11.05.2026 19:00``
- ``в пятницу в 18:00``

Возвращает tz-aware datetime в UTC. Если распознать не удалось —
возвращает ``None``. Если время в прошлом — тоже ``None``
(оставлено вызывающему коду решать, как сообщить пользователю).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import dateparser
import pytz

# "11.05 19:00" / "11.05.2026 19:00" → вставляет "в" перед временем,
# чтобы dateparser его понял.
_DATE_TIME_GAP_RE = re.compile(
    r"(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)\s+(\d{1,2}:\d{2})"
)
# "11.05" без года (с временем после) → "11/05".
# dateparser неплохо парсит DD/MM, но плохо парсит DD.MM без года.
_DD_MM_NO_YEAR_RE = re.compile(
    r"\b(\d{1,2})\.(\d{1,2})(?!\.\d)"
)


def _normalize_input(text: str) -> str:
    """Подправить распространённые форматы ввода под dateparser."""
    text = _DATE_TIME_GAP_RE.sub(r"\1 в \2", text)
    text = _DD_MM_NO_YEAR_RE.sub(r"\1/\2", text)
    return text

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

_PARSER_SETTINGS = {
    "TIMEZONE": "Europe/Moscow",
    "TO_TIMEZONE": "UTC",
    "RETURN_AS_TIMEZONE_AWARE": True,
    "PREFER_DATES_FROM": "future",
    "DATE_ORDER": "DMY",
}


def parse_user_time(text: str, *, now_utc: datetime | None = None) -> datetime | None:
    """Распарсить ввод пользователя в datetime UTC.

    Args:
        text: пользовательский ввод, например ``"завтра в 19:00"``.
        now_utc: точка отсчёта (для тестов). По умолчанию — текущее время UTC.

    Returns:
        tz-aware datetime в UTC или ``None`` если распознать не удалось
        / время в прошлом.
    """
    if not text or not text.strip():
        return None

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    settings = dict(_PARSER_SETTINGS)
    settings["RELATIVE_BASE"] = now_utc.astimezone(MOSCOW_TZ).replace(tzinfo=None)

    parsed = dateparser.parse(
        _normalize_input(text.strip()),
        languages=["ru", "en"],
        settings=settings,
    )
    if parsed is None:
        return None

    if parsed.tzinfo is None:
        parsed = pytz.UTC.localize(parsed)
    else:
        parsed = parsed.astimezone(timezone.utc)

    # Минимальный буфер 30 секунд — отсекает прошлое и "прямо сейчас".
    if (parsed - now_utc).total_seconds() < 30:
        return None

    return parsed


def format_for_user(dt_utc: datetime) -> str:
    """Преобразовать UTC datetime в читаемую московскую строку для пользователя."""
    moscow = dt_utc.astimezone(MOSCOW_TZ)
    return moscow.strftime("%d.%m.%Y в %H:%M МСК")


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Фиксированная точка отсчёта: 10 мая 2026, 22:00 МСК (= 19:00 UTC)
    now = datetime(2026, 5, 10, 19, 0, tzinfo=timezone.utc)
    print(f"NOW (Moscow): {now.astimezone(MOSCOW_TZ):%Y-%m-%d %H:%M %Z}")
    print()

    cases = [
        "завтра в 19:00",
        "сегодня в 23:30",
        "через 30 минут",
        "через 2 часа",
        "11.05 19:00",
        "12.05.2026 09:30",
        "в пятницу в 18:00",
        "вчера в 10:00",  # прошлое — должно вернуть None
        "абракадабра",    # мусор — должно вернуть None
    ]
    for text in cases:
        result = parse_user_time(text, now_utc=now)
        if result is None:
            print(f"  {text!r:<30} -> None")
        else:
            print(f"  {text!r:<30} -> {format_for_user(result)}")
