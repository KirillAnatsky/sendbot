"""Кнопки-ссылки со связью: засчитать переход и повести человека дальше.

Telegram не сообщает боту, что по url-кнопке нажали, — ссылка просто
открывается. Поэтому такая кнопка ведёт не на сайт, а на наш адрес
/go/<токен>: мы отмечаем клик, запускаем ветку воронки этой кнопки (и гасим
таймер бездействия) и тут же перекидываем человека на исходный сайт.

Адрес назначения в токене не хранится — он берётся из самой воронки.
Так /go/ не превращается в открытый редирект на что угодно.
"""
import asyncio
import hashlib
import hmac
import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from .config import settings
from .db import SessionLocal
from .models import Funnel, FunnelRun, Subscriber

log = logging.getLogger("sendbot.links")
router = APIRouter()

_warned = False
_tasks: set = set()   # держим ссылки на фоновые задачи, чтобы их не собрал GC


def _sig(run_id: int, node_id: str, btn: int) -> str:
    msg = f"{run_id}:{node_id}:{btn}".encode()
    return hmac.new(settings.secret_key.encode(), msg, hashlib.sha256).hexdigest()[:16]


def tracked_url(run_id: int, node_id: str, btn: int) -> str | None:
    """Ссылка через /go/ или None, если внешний адрес сервиса не задан."""
    global _warned
    if not settings.public_url:
        if not _warned:
            _warned = True
            log.warning(
                "Кнопка-ссылка со связью отправлена как обычная ссылка: не задан "
                "внешний адрес сервиса (DOMAIN или PUBLIC_URL в .env). Переход по "
                "ней не засчитается и ветку не запустит.")
        return None
    return f"{settings.public_url}/go/{run_id}.{node_id}.{btn}.{_sig(run_id, node_id, btn)}"


def parse_token(token: str):
    try:
        run_id, node_id, btn, sig = token.split(".")
        run_id, btn = int(run_id), int(btn)
    except ValueError:
        return None
    if not node_id.isdigit() or not hmac.compare_digest(sig, _sig(run_id, node_id, btn)):
        return None
    return run_id, node_id, btn


_GONE = HTMLResponse(
    "<meta charset='utf-8'><p style='font:16px sans-serif;margin:40px'>"
    "Ссылка устарела. Вернитесь в бот — там будет актуальная.</p>", status_code=404)


@router.get("/go/{token}", include_in_schema=False)
async def go(token: str):
    parsed = parse_token(token)
    if parsed is None:
        return _GONE
    run_id, node_id, btn = parsed

    from .bot.sender import personalize

    async with SessionLocal() as session:
        run = await session.get(FunnelRun, run_id)
        funnel = await session.get(Funnel, run.funnel_id) if run else None
        sub = await session.get(Subscriber, run.subscriber_id) if run else None
        node = ((funnel.graph or {}).get("nodes") or {}).get(node_id) if funnel else None
        buttons = ((node or {}).get("data") or {}).get("buttons") or []
        b = buttons[btn] if 0 <= btn < len(buttons) and isinstance(buttons[btn], dict) else {}
        url = str(b.get("url") or "").strip()
        if not url or sub is None:
            return _GONE
        url = personalize(url, sub)

    # воронку двигаем в фоне: человек не должен ждать, пока бот отправит
    # следующие сообщения, — браузер открывает сайт сразу
    task = asyncio.create_task(_on_click(run_id, node_id, btn))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return RedirectResponse(url, status_code=302)


async def _on_click(run_id: int, node_id: str, btn: int):
    from .bot import engine as fx
    from .bot.runner import manager

    try:
        async with SessionLocal() as session:
            run = await session.get(FunnelRun, run_id)
            sub = await session.get(Subscriber, run.subscriber_id) if run else None
            bot = manager.get(sub.bot_id) if sub else None
            await fx.handle_link_click(bot, session, run_id, node_id, btn)
            await session.commit()
    except Exception:  # noqa: BLE001
        log.exception("Переход по ссылке run=%s node=%s btn=%s не обработан",
                      run_id, node_id, btn)
