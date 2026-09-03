"""Отправка сообщений с обработкой блокировок и подстановкой переменных."""
import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Subscriber

log = logging.getLogger("sendbot.sender")


def _resolve_media(path: str):
    """http(s)-ссылка — как есть; иначе локальный файл из media/ (защита от ../)."""
    path = (path or "").strip()
    if path.startswith(("http://", "https://")):
        return path
    from pathlib import Path

    from aiogram.types import FSInputFile

    from ..config import settings

    media = Path(settings.media_dir).resolve()
    rel = path[len("media/"):] if path.startswith("media/") else path
    p = (media / rel).resolve()
    if media not in p.parents and p != media:
        return None
    return FSInputFile(p) if p.is_file() else None


# обратная совместимость
_resolve_photo = _resolve_media


import re as _re


def parse_start_params(payload: str) -> dict:
    """Разбор deep-link кода в параметры.
    Формат: пары через «__», ключ-значение через «-». Пример:
        s1-fb__s2-google  ->  {"s1": "fb", "s2": "google"}
    Также поддерживается «~» между парами и «.» как разделитель ключ/значение."""
    params = {}
    if not payload:
        return params
    for sep in ("__", "~"):
        payload = payload.replace(sep, "\x00")
    for pair in payload.split("\x00"):
        if not pair:
            continue
        m = _re.match(r"^([A-Za-z0-9]+)[-.](.+)$", pair)
        if m:
            params[m.group(1)] = m.group(2)
    return params


def personalize(text: str, sub: Subscriber) -> str:
    text = (
        text.replace("{first_name}", sub.first_name or "")
        .replace("{last_name}", sub.last_name or "")
        .replace("{username}", f"@{sub.username}" if sub.username else "")
        .replace("{source}", sub.source or "")
        .replace("{first_source}", getattr(sub, "first_source", None) or sub.source or "")
    )
    # параметры deep-link: {p:ключ}
    params = sub.params or {}
    text = _re.sub(r"\{p:([A-Za-z0-9]+)\}", lambda m: str(params.get(m.group(1), "")), text)
    return text


# Оформление кнопок появилось в Bot API 9.4: не произвольный цвет, а три
# готовых стиля. Клиенты, которые их не знают, рисуют обычную кнопку —
# поэтому поле безопасно отправлять всегда.
BUTTON_STYLES = ("primary", "success", "danger")


def _button_extras(b: dict) -> dict:
    """Необязательное оформление кнопки, если задано и допустимо."""
    extra = {}
    style = (b.get("style") or "").strip().lower()
    if style in BUTTON_STYLES:
        extra["style"] = style
    emoji_id = (b.get("icon_custom_emoji_id") or "").strip()
    if emoji_id:
        extra["icon_custom_emoji_id"] = emoji_id
    if b.get("disabled"):
        extra["disabled"] = True
    return extra


def build_keyboard(buttons: list, run_id: int, node_id: str, sub: Subscriber | None = None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    rows = []
    for i, b in enumerate(buttons):
        if not isinstance(b, dict):
            b = {"label": str(b)}
        label = b.get("label")
        url = b.get("url")
        if sub is not None:
            label = personalize(label or "", sub)
        extra = _button_extras(b)
        if url:
            if sub is not None:
                url = personalize(url, sub)  # подставляем {p:...}, {source} в ссылку
            rows.append([InlineKeyboardButton(text=label, url=url, **extra)])
        else:
            rows.append([
                InlineKeyboardButton(text=label, callback_data=f"f:{run_id}:{node_id}:{i}",
                                     **extra)
            ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _log_out(session, sub, text, is_operator):
    from ..models import Message

    session.add(Message(
        subscriber_id=sub.id, direction="out", text=text, is_operator=is_operator
    ))
    await session.flush()


async def send_to_subscriber(
    bot: Bot,
    session: AsyncSession,
    sub: Subscriber,
    text: str,
    photo_url: str | None = None,
    keyboard: InlineKeyboardMarkup | None = None,
    is_operator: bool = False,
) -> bool:
    """True = доставлено, False = не доставлено (блок и т.п.).
    Сохраняет исходящее сообщение в историю переписки."""
    import asyncio

    text = personalize(text, sub)
    try:
        if photo_url:
            photo = _resolve_photo(photo_url)
            if photo is None:
                log.warning("Картинка не найдена: %s — отправляю без неё", photo_url)
                await bot.send_message(
                    sub.tg_id, text, reply_markup=keyboard, parse_mode=ParseMode.HTML
                )
            else:
                await bot.send_photo(
                    sub.tg_id, photo, caption=text,
                    reply_markup=keyboard, parse_mode=ParseMode.HTML,
                )
        else:
            await bot.send_message(
                sub.tg_id, text,
                reply_markup=keyboard, parse_mode=ParseMode.HTML,
            )
        await _log_out(session, sub, ("🖼 " + text) if photo_url else text, is_operator)
        return True
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await send_to_subscriber(bot, session, sub, text, photo_url, keyboard, is_operator)
    except TelegramForbiddenError:
        sub.is_active = False
        await session.flush()
        return False
    except Exception as e:  # noqa: BLE001
        log.warning("Не отправилось tg_id=%s: %s", sub.tg_id, e)
        return False


# ---------- мультимедиа-сообщение (альбомы, видео/аудио/кружок/файл) ----------

CAPTION_CAPABLE = {"photo", "video", "audio", "voice", "document"}
GROUP_FAMILY = {"photo": "pv", "video": "pv", "audio": "audio", "document": "document"}
KIND_LABEL = {
    "photo": "🖼 фото", "video": "🎬 видео", "audio": "🎵 аудио",
    "voice": "🎤 голосовое", "video_note": "⭕️ кружок", "document": "📎 файл",
}


async def _deliver(coro_factory, bot, session, sub) -> bool:
    """Выполнить отправку с обработкой RetryAfter/Forbidden."""
    import asyncio

    try:
        await coro_factory()
        return True
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await _deliver(coro_factory, bot, session, sub)
    except TelegramForbiddenError:
        sub.is_active = False
        await session.flush()
        return False
    except Exception as e:  # noqa: BLE001
        log.warning("Не отправилось tg_id=%s: %s", sub.tg_id, e)
        return False


def _plan_sends(media: list) -> list:
    """Группирует вложения в отправки по правилам Telegram.
    Возвращает список ('group'|'single', family_or_type, [items])."""
    sends, buf, buf_fam = [], [], None

    def flush():
        nonlocal buf, buf_fam
        for i in range(0, len(buf), 10):  # медиагруппа — до 10
            sends.append(("group", buf_fam, buf[i:i + 10]))
        buf, buf_fam = [], None

    for m in media:
        fam = GROUP_FAMILY.get(m.get("type"))
        if fam:
            if buf_fam and buf_fam != fam:
                flush()
            buf_fam = fam
            buf.append(m)
        else:
            flush()
            sends.append(("single", m.get("type"), [m]))
    flush()
    return sends


async def _deliver_result(coro_factory, bot, session, sub):
    """Как _deliver, но возвращает результат отправки (Message/список) или None."""
    import asyncio

    try:
        return await coro_factory()
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await _deliver_result(coro_factory, bot, session, sub)
    except TelegramForbiddenError:
        sub.is_active = False
        await session.flush()
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("Не отправилось tg_id=%s: %s", sub.tg_id, e)
        return None


# ---------- кэш file_id ----------

async def _cached_file_id(session, bot_id: int, path: str) -> str | None:
    from sqlalchemy import select

    from ..models import MediaFileId

    r = await session.execute(
        select(MediaFileId.file_id).where(
            MediaFileId.bot_id == bot_id, MediaFileId.path == path
        )
    )
    row = r.first()
    return row[0] if row else None


async def _store_file_id(session, bot_id: int, path: str, file_id: str):
    from ..models import MediaFileId

    if await _cached_file_id(session, bot_id, path):
        return
    session.add(MediaFileId(bot_id=bot_id, path=path, file_id=file_id))
    try:
        await session.flush()
    except Exception:  # noqa: BLE001 — гонка по уникальности, не критично
        pass


def _extract_file_id(msg, mtype: str) -> str | None:
    try:
        if mtype == "photo":
            return msg.photo[-1].file_id
        return getattr(getattr(msg, mtype, None), "file_id", None)
    except Exception:  # noqa: BLE001
        return None


async def _media_arg(session, bot_id: int, m: dict):
    """-> (arg, cache_path). arg: file_id-строка / URL / FSInputFile / None.
    cache_path задан, только если это локальный файл, который стоит закэшировать."""
    path = (m.get("path") or "").strip()
    if path.startswith(("http://", "https://")):
        return path, None
    cached = await _cached_file_id(session, bot_id, path)
    if cached:
        return cached, None
    f = _resolve_media(path)  # FSInputFile или None
    return f, (path if f is not None else None)


def _input_media(mtype: str, arg, caption):
    from aiogram.types import (
        InputMediaAudio,
        InputMediaDocument,
        InputMediaPhoto,
        InputMediaVideo,
    )

    cls = {
        "photo": InputMediaPhoto, "video": InputMediaVideo,
        "audio": InputMediaAudio, "document": InputMediaDocument,
    }[mtype]
    if caption:
        return cls(media=arg, caption=caption, parse_mode=ParseMode.HTML)
    return cls(media=arg)


async def _send_one(bot, session, sub, kind, items, caption, markup) -> bool:
    """Отправить одну единицу плана. Кэширует полученные file_id по (бот, файл)."""
    bot_id = sub.bot_id

    # медиагруппа только если реально >1 (у групп нельзя кнопки)
    if kind == "group" and len(items) > 1:
        group, cache_map, first = [], [], True
        for m in items:
            arg, cache_path = await _media_arg(session, bot_id, m)
            if arg is None:
                continue
            group.append(_input_media(m["type"], arg, caption if first else None))
            cache_map.append((len(group) - 1, cache_path, m["type"]))
            first = False
        if not group:
            return False
        res = await _deliver_result(
            lambda: bot.send_media_group(sub.tg_id, media=group), bot, session, sub
        )
        if not res:
            return False
        for gi, cache_path, mtype in cache_map:
            if cache_path and gi < len(res):
                fid = _extract_file_id(res[gi], mtype)
                if fid:
                    await _store_file_id(session, bot_id, cache_path, fid)
        return True

    m = items[0]
    t = m["type"]
    arg, cache_path = await _media_arg(session, bot_id, m)
    if arg is None:
        log.warning("Файл вложения не найден: %s", m.get("path"))
        return False
    html = ParseMode.HTML
    senders = {
        "photo": lambda: bot.send_photo(sub.tg_id, arg, caption=caption, reply_markup=markup, parse_mode=html),
        "video": lambda: bot.send_video(sub.tg_id, arg, caption=caption, reply_markup=markup, parse_mode=html),
        "audio": lambda: bot.send_audio(sub.tg_id, arg, caption=caption, reply_markup=markup, parse_mode=html),
        "voice": lambda: bot.send_voice(sub.tg_id, arg, caption=caption, reply_markup=markup, parse_mode=html),
        "document": lambda: bot.send_document(sub.tg_id, arg, caption=caption, reply_markup=markup, parse_mode=html),
        "video_note": lambda: bot.send_video_note(sub.tg_id, arg, reply_markup=markup),  # без caption
    }
    fn = senders.get(t)
    if fn is None:
        return False
    res = await _deliver_result(fn, bot, session, sub)
    if not res:
        return False
    if cache_path:
        fid = _extract_file_id(res, t)
        if fid:
            await _store_file_id(session, bot_id, cache_path, fid)
    return True


async def send_message_content(
    bot: Bot, session: AsyncSession, sub: Subscriber,
    text: str, media: list | None, keyboard: InlineKeyboardMarkup | None,
    is_operator: bool = False,
    log_history: bool = True,
) -> bool:
    """Отправка сообщения воронки: текст + любое число вложений + кнопки.
    Правила: одиночное вложение несёт подпись+кнопки; при нескольких —
    альбомы без кнопок, а текст/кнопки уходят отдельным сообщением.

    log_history=False — не писать копию в переписку. Так уходят массовые
    рассылки: их текст уже лежит один раз в таблице broadcasts, а кто его
    получил — в broadcast_recipients. Писать одну и ту же строку в историю
    каждому из 400 тысяч человек незачем, в чате она собирается на лету."""
    text = personalize(text or "", sub)
    media = [m for m in (media or []) if m.get("path")]

    if not media:
        if not text and keyboard is None:
            return True
        ok = await _deliver(
            lambda: bot.send_message(sub.tg_id, text or "", reply_markup=keyboard, parse_mode=ParseMode.HTML),
            bot, session, sub,
        )
        if ok and log_history:
            await _log_out(session, sub, text, is_operator)
        return ok

    sends = _plan_sends(media)
    single_total = len(media) == 1
    caption_used = False
    kb_used = False
    delivered = False

    for kind, fam, items in sends:
        cap, markup = None, None
        real_single = kind == "single" or (kind == "group" and len(items) == 1)
        if single_total and real_single:
            t = items[0]["type"]
            if t in CAPTION_CAPABLE:
                cap, caption_used = text or None, True
            markup, kb_used = keyboard, True  # кнопки на единственном вложении (в т.ч. кружке)
        elif keyboard is None and not caption_used and items[0]["type"] in CAPTION_CAPABLE:
            # нет кнопок — вешаем подпись на первое подходящее вложение
            cap, caption_used = text or None, True
        ok = await _send_one(bot, session, sub, kind, items, cap, markup)
        delivered = delivered or ok

    # финальное текстовое сообщение: если кнопки ещё не прикреплены или текст не отправлен
    need_final = (keyboard is not None and not kb_used) or (text and not caption_used)
    if need_final:
        body = text or "⠀"  # send_message требует непустой текст
        kb_final = None if kb_used else keyboard
        ok = await _deliver(
            lambda: bot.send_message(sub.tg_id, body, reply_markup=kb_final, parse_mode=ParseMode.HTML),
            bot, session, sub,
        )
        delivered = delivered or ok

    if log_history:
        summary = text or ("📎 " + ", ".join(KIND_LABEL.get(m["type"], m["type"]) for m in media))
        await _log_out(session, sub, summary, is_operator)
    return delivered
