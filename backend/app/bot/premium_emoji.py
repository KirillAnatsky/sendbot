"""Премиум-эмодзи: импорт наборов в библиотеку и страховка отправки.

В тексте эмодзи стоит тегом `<tg-emoji emoji-id="…">🔥</tg-emoji>`, на кнопке —
полем `icon_custom_emoji_id`. Telegram разрешает это ботам, у владельца
которых есть Telegram Premium (Bot API 9.4). Если Premium нет или он кончился,
неизвестно заранее, что сделает Telegram: отклонит сообщение или молча
покажет обычную эмодзи. Оба случая здесь закрыты — украшение не должно
мочь сорвать рассылку.
"""
import asyncio
import contextvars
import logging
import re
from pathlib import Path

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

log = logging.getLogger("sendbot.emoji")

TG_EMOJI_RE = re.compile(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", re.S | re.I)

# Куда складывать замечания о премиум-эмодзи во время одной отправки.
# Нужен предпросмотру: человек жмёт «Отправить себе» и должен узнать, что
# премиальные эмодзи не прошли, а не гадать, почему они обычные.
emoji_report: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "emoji_report", default=None)


def strip_tg_emoji(text):
    """<tg-emoji …>🔥</tg-emoji> → 🔥 (остаётся обычная эмодзи)."""
    if not isinstance(text, str) or "<tg-emoji" not in text.lower():
        return text
    return TG_EMOJI_RE.sub(r"\1", text)


def _has_tag(text) -> bool:
    return isinstance(text, str) and "<tg-emoji" in text.lower()


def _markup_has_icons(markup) -> bool:
    if not isinstance(markup, InlineKeyboardMarkup):
        return False
    return any(getattr(b, "icon_custom_emoji_id", None)
               for row in markup.inline_keyboard for b in row)


def _strip_markup(markup):
    if not _markup_has_icons(markup):
        return markup
    rows = [[b.model_copy(update={"icon_custom_emoji_id": None}) for b in row]
            for row in markup.inline_keyboard]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def method_uses_premium(method) -> bool:
    if _has_tag(getattr(method, "text", None)) or _has_tag(getattr(method, "caption", None)):
        return True
    for m in getattr(method, "media", None) or []:
        if isinstance(m, list):
            continue
        if _has_tag(getattr(m, "caption", None)):
            return True
    return _markup_has_icons(getattr(method, "reply_markup", None))


def method_text_uses_premium(method) -> bool:
    """Есть ли премиум-эмодзи именно в тексте (а не только на кнопках)."""
    if _has_tag(getattr(method, "text", None)) or _has_tag(getattr(method, "caption", None)):
        return True
    return any(_has_tag(getattr(m, "caption", None))
               for m in getattr(method, "media", None) or [] if not isinstance(m, list))


def strip_method(method):
    """Копия запроса без премиум-эмодзи: и в тексте, и на кнопках."""
    upd = {}
    for f in ("text", "caption"):
        v = getattr(method, f, None)
        if _has_tag(v):
            upd[f] = strip_tg_emoji(v)
    media = getattr(method, "media", None)
    if isinstance(media, list):
        new_media, changed = [], False
        for m in media:
            cap = getattr(m, "caption", None)
            if _has_tag(cap):
                m = m.model_copy(update={"caption": strip_tg_emoji(cap)})
                changed = True
            new_media.append(m)
        if changed:
            upd["media"] = new_media
    markup = getattr(method, "reply_markup", None)
    if _markup_has_icons(markup):
        upd["reply_markup"] = _strip_markup(markup)
    return method.model_copy(update=upd) if upd else method


def _result_has_custom_emoji(result) -> bool | None:
    """Пришли ли премиум-эмодзи в отправленном сообщении. None — непонятно."""
    items = result if isinstance(result, list) else [result]
    seen = False
    for msg in items:
        ents = (getattr(msg, "entities", None) or []) + (getattr(msg, "caption_entities", None) or [])
        if getattr(msg, "message_id", None) is not None:
            seen = True
        if any(getattr(e, "type", None) == "custom_emoji" for e in ents):
            return True
    return False if seen else None


def _note(kind: str, detail: str = ""):
    rep = emoji_report.get()
    if rep is not None:
        rep.append({"kind": kind, "detail": detail})


class PremiumEmojiGuard(BaseRequestMiddleware):
    """Если Telegram отверг сообщение с премиум-эмодзи — шлём его же без них.

    Повтор делается только для запросов, где премиум-эмодзи действительно
    есть, и только один раз: если и без них не прошло, ошибка была в другом,
    и её надо отдать вызывающему как есть.
    """

    # не заваливать лог одним и тем же на рассылке в 50 тысяч
    _warned = False

    async def __call__(self, make_request, bot, method):
        if not method_uses_premium(method):
            return await make_request(bot, method)
        try:
            result = await make_request(bot, method)
        except TelegramBadRequest as e:
            if not PremiumEmojiGuard._warned:
                log.warning("Премиум-эмодзи не приняты Telegram (%s) — отправляю "
                            "без них. Проверьте Telegram Premium у владельца бота.", e.message)
                PremiumEmojiGuard._warned = True
            _note("rejected", e.message)
            return await make_request(bot, strip_method(method))
        if method_text_uses_premium(method) and _result_has_custom_emoji(result) is False:
            # Telegram принял, но показал обычные эмодзи — так бывает без Premium
            _note("downgraded")
        return result


def install_guard(bot):
    """Подключить страховку к сессии бота (один раз)."""
    if not getattr(bot, "_premium_emoji_guard", False):
        bot.session.middleware(PremiumEmojiGuard())
        bot._premium_emoji_guard = True
    return bot


# ---------- импорт набора ----------

SET_LINK_RE = re.compile(
    r"(?:(?:https?://)?(?:t\.me|telegram\.me)/(?:addemoji|addstickers)/"
    r"|tg://(?:addemoji|addstickers)\?set=)?([A-Za-z0-9_]{1,64})/?$")


def parse_set_name(link: str) -> str | None:
    """Имя набора из ссылки t.me/addemoji/<имя> (или просто имени)."""
    m = SET_LINK_RE.fullmatch((link or "").strip())
    return m.group(1) if m else None


def thumb_path(media_dir, emoji_id: str) -> Path:
    return Path(media_dir) / "emoji" / f"{emoji_id}.webp"


async def fetch_set(bot, name: str, media_dir) -> dict:
    """Скачать набор: список эмодзи и миниатюры в media/emoji/.

    -> {"name", "title", "items": [{emoji_id, emoji, position, has_thumb}]}
    Бросает ValueError с понятным текстом, если набор не тот.
    """
    try:
        st = await bot.get_sticker_set(name=name)
    except TelegramBadRequest as e:
        if "STICKERSET_INVALID" in (e.message or "").upper():
            raise ValueError(f"Набор «{name}» не найден. Проверьте ссылку") from e
        raise ValueError(f"Telegram не отдал набор: {e.message}") from e
    if getattr(st, "sticker_type", None) != "custom_emoji":
        raise ValueError(
            f"«{st.title}» — набор стикеров, а не эмодзи. Нужна ссылка вида "
            "t.me/addemoji/…")

    folder = Path(media_dir) / "emoji"
    folder.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(6)

    async def grab(i, s):
        eid = s.custom_emoji_id
        if not eid:
            return None
        dest = thumb_path(media_dir, eid)
        has = dest.is_file()
        if not has:
            # миниатюра есть почти у всех; у статичных webp можно взять сам файл,
            # анимированный tgs браузер не нарисует — там остаётся обычная эмодзи
            fid = s.thumbnail.file_id if s.thumbnail else (
                s.file_id if not (s.is_animated or s.is_video) else None)
            if fid:
                async with sem:
                    try:
                        await bot.download(fid, destination=dest)
                        has = dest.is_file()
                    except Exception as e:  # noqa: BLE001 — без картинки тоже работает
                        log.warning("Миниатюра эмодзи %s не скачалась: %s", eid, e)
        return {"emoji_id": eid, "emoji": (s.emoji or "⭐")[:16],
                "position": i, "has_thumb": has}

    items = [x for x in await asyncio.gather(*(grab(i, s) for i, s in enumerate(st.stickers)))
             if x]
    return {"name": st.name, "title": st.title, "items": items}
