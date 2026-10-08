"""Почему сообщение не дошло — человеческим языком.

Telegram отвечает на неудачную отправку текстом ошибки. Здесь он сводится к
короткому коду: его сохраняем у получателя рассылки и по нему строим разбивку
в карточке («заблокировали 18%, чата нет 5%…»). Код ставится в contextvar
прямо там, где ловится исключение, — отправка уходит вглубь (альбомы,
подписи, кнопки), и протаскивать причину через все возвраты незачем.
"""
import contextvars

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
)

# (код, текст ошибки) последней неудачи в текущей отправке
last_error: contextvars.ContextVar[tuple | None] = contextvars.ContextVar(
    "last_send_error", default=None)

REASONS = {
    "blocked": "заблокировал бота",
    "deactivated": "аккаунт удалён",
    "chat_not_found": "чата нет (не запускал бота)",
    "bad_request": "Telegram отклонил сообщение",
    "media": "вложение не отправилось",
    "network": "сбой связи с Telegram",
    "other": "другое",
    # у рассылок до учёта причин — восстанавливаем по статусу подписчика
    "legacy_inactive": "заблокировал или удалён (по статусу)",
    "legacy_unknown": "неизвестно (старая рассылка)",
}

# после этих ошибок писать человеку бессмысленно, пока он сам не напишет боту
UNREACHABLE = {"blocked", "deactivated", "chat_not_found"}


def classify(exc: Exception) -> tuple[str, str]:
    msg = str(getattr(exc, "message", None) or exc)
    low = msg.lower()
    if isinstance(exc, TelegramForbiddenError):
        if "deactivated" in low:
            return "deactivated", msg
        if "blocked" in low or "kicked" in low:
            return "blocked", msg
        # «bot can't initiate conversation» — человек ни разу не писал боту
        return "chat_not_found", msg
    if isinstance(exc, TelegramBadRequest):
        if "chat not found" in low or "peer_id_invalid" in low or "user not found" in low:
            return "chat_not_found", msg
        if any(w in low for w in ("file", "photo", "video", "media", "wrong type")):
            return "media", msg
        return "bad_request", msg
    if isinstance(exc, TelegramNetworkError):
        return "network", msg
    return "other", msg


def note(exc: Exception) -> str:
    code, msg = classify(exc)
    last_error.set((code, msg[:200]))
    return code


def note_code(code: str, msg: str = ""):
    last_error.set((code, msg[:200]))
