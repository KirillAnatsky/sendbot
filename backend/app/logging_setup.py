"""Логи приложения: файл с ротацией + вывод в консоль.

Файлы лежат в LOG_DIR (по умолчанию ./logs):
    sendbot.log        — всё (INFO и выше), 10 МБ × 5 файлов
    sendbot-error.log  — только ошибки, 5 МБ × 5 файлов

Старые файлы автоматически удаляются, диск не забьётся.
"""
import logging
import logging.handlers
import os
from pathlib import Path

_configured = False


def setup_logging():
    global _configured
    if _configured:
        return
    _configured = True

    from .config import settings

    log_dir = Path(getattr(settings, "log_dir", "./logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    # консоль (её собирает docker)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    # основной файл с ротацией
    main_file = logging.handlers.RotatingFileHandler(
        log_dir / "sendbot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    main_file.setFormatter(fmt)
    root.addHandler(main_file)

    # отдельный файл только с ошибками — чтобы быстро находить проблемы
    err_file = logging.handlers.RotatingFileHandler(
        log_dir / "sendbot-error.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    err_file.setFormatter(fmt)
    err_file.setLevel(logging.WARNING)
    root.addHandler(err_file)

    # библиотеки не должны засорять лог
    for noisy in ("aiogram.event", "aiohttp.access", "sqlalchemy.engine.Engine",
                  "uvicorn.access", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("sendbot").info("Логирование настроено, файлы: %s", log_dir.resolve())


# единая точка получения логгера события
def event_logger():
    """Логгер бизнес-событий: входы, боты, рассылки, воронки."""
    return logging.getLogger("sendbot.events")
