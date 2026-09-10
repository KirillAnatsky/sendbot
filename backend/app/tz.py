"""Часовой пояс проекта.

Сервер живёт по UTC. Условия «день недели», «дата» и «время срабатывания»
считаются от «сейчас», поэтому без явного пояса окно «с 10:00 до 18:00»
молча уезжало бы на несколько часов — и заметили бы это по недошедшим
сообщениям, а не по ошибке.

Пояс лежит в таблице settings и читается в память один раз на старте:
условия сегмента собираются в обычной синхронной функции, которой ходить
в базу неоткуда. Меняется — сразу обновляем и память.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("sendbot.tz")

SETTING_KEY = "timezone"
DEFAULT_TZ = "Europe/Kyiv"

# Список для выпадающего меню в админке. Не весь мир: только то, чем реально
# пользуются, — длинный список из 600 зон выбирать невозможно.
COMMON = [
    ("Europe/Kyiv", "Киев (UTC+3 / зимой +2)"),
    ("Europe/Moscow", "Москва (UTC+3)"),
    ("Europe/Warsaw", "Варшава (UTC+2 / зимой +1)"),
    ("Europe/Berlin", "Берлин (UTC+2 / зимой +1)"),
    ("Europe/London", "Лондон (UTC+1 / зимой 0)"),
    ("Europe/Lisbon", "Лиссабон (UTC+1 / зимой 0)"),
    ("Europe/Istanbul", "Стамбул (UTC+3)"),
    ("Asia/Tbilisi", "Тбилиси (UTC+4)"),
    ("Asia/Yerevan", "Ереван (UTC+4)"),
    ("Asia/Dubai", "Дубай (UTC+4)"),
    ("Asia/Almaty", "Алматы (UTC+5)"),
    ("Asia/Bangkok", "Бангкок (UTC+7)"),
    ("Asia/Tokyo", "Токио (UTC+9)"),
    ("America/New_York", "Нью-Йорк (UTC-4 / зимой -5)"),
    ("America/Los_Angeles", "Лос-Анджелес (UTC-7 / зимой -8)"),
    ("UTC", "UTC (время сервера)"),
]

_current = DEFAULT_TZ


def is_valid(name: str) -> bool:
    try:
        ZoneInfo(str(name))
        return True
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False


def set_timezone(name: str) -> str:
    """Запомнить пояс в памяти. Возвращает то, что реально установилось."""
    global _current
    if is_valid(name):
        _current = str(name)
    else:
        log.warning("Неизвестный часовой пояс %r — оставляю %s", name, _current)
    return _current


def get_timezone() -> str:
    return _current


def now() -> datetime:
    """«Сейчас» в поясе проекта. Именно от него считаются условия фильтра."""
    try:
        return datetime.now(ZoneInfo(_current))
    except Exception:  # noqa: BLE001 — пояс мог исчезнуть вместе с tzdata
        log.warning("Пояс %s недоступен — считаю по UTC", _current)
        return datetime.now(timezone.utc)


async def load_from_db(session) -> str:
    """Поднять сохранённый пояс из settings (зовём на старте приложения)."""
    from sqlalchemy import select

    from .models import Setting

    row = (await session.execute(
        select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    name = (row.value or {}).get("tz") if row else None
    return set_timezone(name or DEFAULT_TZ)


async def save_to_db(session, name: str) -> str:
    from sqlalchemy import select

    from .models import Setting

    applied = set_timezone(name)
    row = (await session.execute(
        select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    if row:
        row.value = {"tz": applied}
    else:
        session.add(Setting(key=SETTING_KEY, value={"tz": applied}))
    await session.flush()
    log.info("Часовой пояс проекта: %s", applied)
    return applied
