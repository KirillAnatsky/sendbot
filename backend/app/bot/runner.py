"""Менеджер нескольких телеграм-ботов (aiogram 3, long polling) + воркеры.

Каждый бот — отдельная сущность со своей базой подписчиков и СВОИМ
Dispatcher (один общий диспетчер нельзя поллить параллельно на несколько
ботов). В хендлеры прокидывается db_bot_id (id бота в нашей БД).
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from ..config import settings
from ..db import SessionLocal
from ..models import Bot as BotModel
from ..models import (
    Broadcast,
    BroadcastRecipient,
    Funnel,
    FunnelBot,
    Subscriber,
    SubscriberTag,
)
from .. import segment as seg
from . import engine as fx
from .sender import build_broadcast_keyboard, send_message_content, send_to_subscriber

log = logging.getLogger("sendbot.bot")


def build_dispatcher() -> Dispatcher:
    """Новый Dispatcher с зарегистрированными хендлерами — по одному на бота."""
    d = Dispatcher()
    d.message.register(on_message, F.text)
    d.callback_query.register(on_button, F.data.startswith("f:"))
    d.callback_query.register(on_broadcast_button, F.data.startswith("b:"))
    return d


class BotManager:
    """Держит запущенные aiogram-боты и их polling-задачи по bot_id."""

    def __init__(self):
        self.bots: dict[int, Bot] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.dispatchers: dict[int, Dispatcher] = {}
        self.workers: list[asyncio.Task] = []

    def get(self, bot_id: int) -> Bot | None:
        return self.bots.get(bot_id)

    async def start_all(self):
        async with SessionLocal() as session:
            rows = (await session.execute(select(BotModel).where(BotModel.is_active == True))).scalars().all()  # noqa: E712
            for b in rows:
                await self._spawn(b.id, b.token)
            await session.commit()
        self.workers = [
            asyncio.create_task(scheduler_loop(self)),
            asyncio.create_task(broadcast_loop(self)),
            asyncio.create_task(cleanup_loop()),
            asyncio.create_task(sheets_export_loop()),
            asyncio.create_task(funnel_input_export_loop()),
        ]

    async def _spawn(self, bot_id: int, token: str):
        if bot_id in self.tasks:
            return
        try:
            bot = Bot(token=token)  # кривой формат токена кидает исключение прямо тут
        except Exception as e:  # noqa: BLE001
            log.warning("Бот #%s: некорректный токен: %s", bot_id, e)
            await self._save_error(bot_id, f"некорректный токен: {e}")
            return
        try:
            me = await bot.get_me()
            # снимаем возможный вебхук — иначе long polling молча не получает апдейты,
            # и сбрасываем «висящие» updates, чтобы бот не отвечал на старые сообщения
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:  # noqa: BLE001
            log.warning("Бот #%s не стартовал: %s", bot_id, e)
            await self._save_error(bot_id, str(e))
            await bot.session.close()
            return
        self.bots[bot_id] = bot
        d = build_dispatcher()
        self.dispatchers[bot_id] = d
        self.tasks[bot_id] = asyncio.create_task(self._run_polling(bot_id, bot, d))
        await self._save_ok(bot_id, me.username)
        log.info("Бот #%s @%s запущен", bot_id, me.username)

    async def _run_polling(self, bot_id: int, bot: Bot, d: Dispatcher):
        try:
            await d.start_polling(bot, handle_signals=False, db_bot_id=bot_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("Polling бота #%s упал: %s", bot_id, e)
            self.bots.pop(bot_id, None)
            self.dispatchers.pop(bot_id, None)
            self.tasks.pop(bot_id, None)
            # is_active оставляем True -> в списке будет «ошибка» с текстом, а не «выключен»
            async with SessionLocal() as s:
                b = await s.get(BotModel, bot_id)
                if b:
                    b.last_error = f"polling: {e}"
                    await s.commit()

    async def start_bot(self, bot_id: int):
        async with SessionLocal() as session:
            b = await session.get(BotModel, bot_id)
            if b:
                await self._spawn(bot_id, b.token)

    async def stop_bot(self, bot_id: int):
        d = self.dispatchers.pop(bot_id, None)
        if d:
            try:
                await d.stop_polling()
            except Exception:  # noqa: BLE001
                pass
        task = self.tasks.pop(bot_id, None)
        if task:
            task.cancel()
        bot = self.bots.pop(bot_id, None)
        if bot:
            try:
                await bot.session.close()
            except Exception:  # noqa: BLE001
                pass

    async def restart_bot(self, bot_id: int):
        await self.stop_bot(bot_id)
        await asyncio.sleep(0.5)
        await self.start_bot(bot_id)

    async def shutdown(self):
        for t in list(self.tasks.values()) + self.workers:
            t.cancel()

    async def _save_ok(self, bot_id, username):
        async with SessionLocal() as s:
            b = await s.get(BotModel, bot_id)
            if b:
                b.tg_username, b.last_error = username, None
                await s.commit()

    async def _save_error(self, bot_id, err):
        async with SessionLocal() as s:
            b = await s.get(BotModel, bot_id)
            if b:
                b.last_error, b.is_active = err, False
                await s.commit()


manager = BotManager()


# ---------- хендлеры ----------

async def upsert_subscriber(session, bot_id: int, tg_user, source: str | None = None) -> Subscriber:
    from datetime import datetime

    from .sender import parse_start_params

    res = await session.execute(
        select(Subscriber).where(Subscriber.bot_id == bot_id, Subscriber.tg_id == tg_user.id)
    )
    sub = res.scalar_one_or_none()
    lang = getattr(tg_user, "language_code", None)
    params = parse_start_params(source) if source else {}
    if sub is None:
        sub = Subscriber(
            bot_id=bot_id,
            tg_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            language_code=lang,
            source=source,
            first_source=source,
            params=params,
            last_active_at=datetime.utcnow(),
        )
        session.add(sub)
        await session.flush()
    else:
        sub.username = tg_user.username
        sub.first_name = tg_user.first_name
        sub.last_name = tg_user.last_name
        sub.language_code = lang or sub.language_code
        if source:
            # первую метку фиксируем один раз, последнюю — обновляем при каждом переходе
            if not sub.first_source:
                sub.first_source = source
            sub.source = source
            sub.params = params
        sub.is_active = True
        # нажал /start снова — значит вернулся; блок «Отписать» больше не в силе
        sub.is_subscribed = True
        sub.last_active_at = datetime.utcnow()
    return sub


async def _funnels_for_bot(session, bot_id: int, trigger_type: str):
    return (
        await session.execute(
            select(Funnel)
            .join(FunnelBot, FunnelBot.funnel_id == Funnel.id)
            .where(
                FunnelBot.bot_id == bot_id,
                Funnel.is_active == True,  # noqa: E712
                # цепочки сами не запускаются — только по вызову из воронки
                Funnel.is_chain == False,  # noqa: E712
                Funnel.trigger_type == trigger_type,
            )
        )
    ).scalars().all()


async def _sub_has_tag(session, sub_id: int, tag_ref) -> bool:
    try:
        tag_id = int(tag_ref)
    except (TypeError, ValueError):
        return False
    res = await session.execute(
        select(SubscriberTag.id).where(
            SubscriberTag.subscriber_id == sub_id, SubscriberTag.tag_id == tag_id
        )
    )
    return res.first() is not None


async def on_message(message: Message, db_bot_id: int):
    bot = manager.get(db_bot_id)
    if bot is None:
        return
    text = (message.text or "").strip()
    # deep-link: /start <source> — сохраняем источник подписчика
    source = None
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            source = parts[1].strip()[:128]
    async with SessionLocal() as session:
        from datetime import datetime

        from ..models import Message

        sub = await upsert_subscriber(session, db_bot_id, message.from_user, source)
        # пишем входящее в историю переписки
        session.add(Message(subscriber_id=sub.id, direction="in", text=text))
        await session.flush()

        # пауза автоматизации (оператор ведёт диалог вручную) — триггеры не запускаем
        paused = sub.automation_paused_until and sub.automation_paused_until > datetime.utcnow()
        if paused:
            await session.commit()
            return

        if text.startswith("/start"):
            for f in await _funnels_for_bot(session, db_bot_id, "start"):
                await fx.start_funnel(bot, session, f, sub)
            await session.commit()
            return

        # ключевые слова
        matched = False
        for f in await _funnels_for_bot(session, db_bot_id, "keyword"):
            kw = (f.trigger_value or "").strip().lower()
            if kw and kw == text.lower():
                await fx.start_funnel(bot, session, f, sub)
                matched = True

        # автоответ / fallback — если ничего не подошло
        if not matched:
            for f in await _funnels_for_bot(session, db_bot_id, "message"):
                # trigger_value (опц.) = id тега: не запускать, если тег уже есть
                if f.trigger_value and await _sub_has_tag(session, sub.id, f.trigger_value):
                    continue
                await fx.start_funnel(bot, session, f, sub)
        await session.commit()


async def on_button(cb: CallbackQuery, db_bot_id: int):
    bot = manager.get(db_bot_id)
    if bot is None:
        await cb.answer()
        return
    try:
        _, run_id, node_id, btn = cb.data.split(":")
        run_id, btn = int(run_id), int(btn)
    except ValueError:
        await cb.answer()
        return
    async with SessionLocal() as session:
        await upsert_subscriber(session, db_bot_id, cb.from_user)
        await fx.handle_button(bot, session, run_id, node_id, btn)
        await session.commit()
    await cb.answer()


async def on_broadcast_button(cb: CallbackQuery, db_bot_id: int):
    """Клик по кнопке рассылки: вешаем тег, если он задан у кнопки.

    Ветвиться, как в воронке, тут некуда — узлов у рассылки нет. Зато тег
    даёт главное: сегмент откликнувшихся, по которому дальше можно слать
    отдельно или запускать воронку.
    """
    try:
        _, bc_id, idx = cb.data.split(":")
        bc_id, idx = int(bc_id), int(idx)
    except ValueError:
        await cb.answer()
        return

    note = None
    async with SessionLocal() as session:
        sub = await upsert_subscriber(session, db_bot_id, cb.from_user)
        bc = await session.get(Broadcast, bc_id)
        buttons = (bc.buttons or []) if bc else []
        btn = buttons[idx] if 0 <= idx < len(buttons) else None
        if btn and btn.get("tag_id"):
            await fx._add_tag(session, sub.id, btn["tag_id"])
            note = btn.get("reply") or "Принято 👌"
            log.info("Рассылка #%s: клик по кнопке «%s» от подписчика #%s",
                     bc_id, btn.get("label"), sub.id)
        await session.commit()
    await cb.answer(note or "")


async def trigger_tag_added(session, sub: Subscriber, tag_id: int):
    """Вызывается из API при добавлении тега вручную."""
    bot = manager.get(sub.bot_id)
    if bot is None:
        return
    funnels = (
        await session.execute(
            select(Funnel)
            .join(FunnelBot, FunnelBot.funnel_id == Funnel.id)
            .where(
                FunnelBot.bot_id == sub.bot_id,
                Funnel.is_active == True,  # noqa: E712
                Funnel.is_chain == False,  # noqa: E712
                Funnel.trigger_type == "tag_added",
                Funnel.trigger_value == str(tag_id),
            )
        )
    ).scalars().all()
    for funnel in funnels:
        await fx.start_funnel(bot, session, funnel, sub)


# ---------- воркеры ----------

async def scheduler_loop(mgr: BotManager):
    while True:
        await asyncio.sleep(2)
        try:
            async with SessionLocal() as session:
                await fx.run_due_jobs(session, mgr.get)
                await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("Ошибка планировщика")


async def broadcast_loop(mgr: BotManager):
    while True:
        await asyncio.sleep(3)
        try:
            async with SessionLocal() as session:
                # «running» здесь означает рассылку, которую оборвал рестарт:
                # цикл последовательный, сам себя он обогнать не может.
                # Такие доделываем в первую очередь, с места обрыва.
                res = await session.execute(
                    select(Broadcast)
                    .where(Broadcast.status.in_(("running", "pending")))
                    .order_by(Broadcast.status != "running", Broadcast.id)
                    .limit(1)
                )
                bc = res.scalar_one_or_none()
                if bc is None:
                    continue
                bot = mgr.get(bc.bot_id)
                if bot is None:
                    bc.status = "failed"
                    await session.commit()
                    continue
                bc.status = "running"
                await session.commit()
                log.info("Старт рассылки «%s» (id=%s)", bc.name, bc.id)
                await _process_broadcast(session, bot, bc)
                await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("Ошибка рассылки")


async def sheets_export_loop():
    """Автовыгрузка статистики в Google Таблицы.

    Раз в час просыпается и решает, пора ли: при режиме «каждый час» —
    всегда, при «раз в сутки» — только в заданный час и не чаще раза в день.
    Если автовыгрузка выключена, ничего не делает.
    """
    from datetime import datetime

    from ..logging_setup import event_logger

    log = event_logger()
    await asyncio.sleep(120)  # дать приложению спокойно подняться

    while True:
        try:
            from ..api import get_sheets_cfg, run_sheets_export, save_sheets_cfg
            from ..sheets import SheetsError

            async with SessionLocal() as session:
                cfg = await get_sheets_cfg(session)
                if not cfg.get("auto") or not cfg.get("spreadsheet_id"):
                    await asyncio.sleep(600)
                    continue

                now = datetime.utcnow()
                due = True
                if cfg.get("interval") == "daily":
                    last = cfg.get("last_run") or ""
                    already_today = last[:10] == now.strftime("%Y-%m-%d")
                    due = now.hour == int(cfg.get("hour", 4)) and not already_today

                if due:
                    try:
                        counts = await run_sheets_export(session, cfg)
                        cfg["last_status"] = "ok"
                        cfg["last_error"] = ""
                        cfg["last_counts"] = counts
                        log.info("Автовыгрузка в Google Таблицы: %s", counts)
                    except SheetsError as e:
                        cfg["last_status"] = "error"
                        cfg["last_error"] = str(e)
                        log.warning("Автовыгрузка в Google Таблицы не удалась: %s", e)
                    cfg["last_run"] = now.isoformat()
                    await save_sheets_cfg(session, cfg)
                    await session.commit()
        except Exception as e:  # noqa: BLE001 — цикл не должен падать
            try:
                from ..logging_setup import event_logger as _el
                _el().warning("Сбой цикла автовыгрузки: %s", e)
            except Exception:  # noqa: BLE001
                pass
        await asyncio.sleep(600)


async def cleanup_loop():
    """Раз в сутки удаляет историю переписки и получателей рассылок старше
    HISTORY_RETENTION_DAYS (0 — не удалять)."""
    from datetime import datetime, timedelta

    from sqlalchemy import delete

    from ..models import BroadcastRecipient, Message

    while True:
        await asyncio.sleep(24 * 3600)
        days = settings.history_retention_days
        if days <= 0:
            continue
        cutoff = datetime.utcnow() - timedelta(days=days)
        try:
            async with SessionLocal() as session:
                await session.execute(delete(Message).where(Message.created_at < cutoff))
                # получатели старых рассылок
                old = select(Broadcast.id).where(Broadcast.created_at < cutoff)
                await session.execute(
                    delete(BroadcastRecipient).where(BroadcastRecipient.broadcast_id.in_(old))
                )
                await session.commit()
                log.info("Очистка истории старше %s дней выполнена", days)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка очистки истории")


async def _process_broadcast(session, bot, bc: Broadcast):
    filters = bc.filters or {}
    # новый формат — сегмент; старый — include_tags/exclude_tags (обратная совместимость)
    if filters.get("segment") is not None:
        q = seg.build_query(bc.bot_id, filters["segment"]).where(
            Subscriber.is_active == True,  # noqa: E712
            Subscriber.is_subscribed == True,  # noqa: E712
        )
    else:
        include = [int(t) for t in filters.get("include_tags") or []]
        exclude = [int(t) for t in filters.get("exclude_tags") or []]
        q = select(Subscriber).where(
            Subscriber.is_active == True,  # noqa: E712
            Subscriber.is_subscribed == True,  # noqa: E712
            Subscriber.bot_id == bc.bot_id,
        )
        if include:
            q = q.where(Subscriber.id.in_(
                select(SubscriberTag.subscriber_id).where(SubscriberTag.tag_id.in_(include))))
        if exclude:
            q = q.where(Subscriber.id.notin_(
                select(SubscriberTag.subscriber_id).where(SubscriberTag.tag_id.in_(exclude))))

    # Размер аудитории берём COUNT'ом, а не длиной списка: на 400 тысячах
    # `.all()` притащил бы в память все объекты подписчиков разом.
    bc.total = (await session.execute(
        select(func.count()).select_from(q.subquery()))).scalar() or 0
    await session.commit()

    # медиа: новый формат bc.media, иначе одиночное photo_url (обратная совместимость)
    media = list(bc.media or [])
    if not media and bc.photo_url:
        media = [{"type": "photo", "path": bc.photo_url}]

    # Если рассылку оборвал рестарт — продолжаем с места обрыва. Идём строго
    # по возрастанию id, поэтому максимальный уже записанный получатель и есть
    # граница: всё до него разослано.
    last_id = (await session.execute(
        select(func.max(BroadcastRecipient.subscriber_id))
        .where(BroadcastRecipient.broadcast_id == bc.id))).scalar() or 0
    if last_id:
        log.info("Рассылка «%s»: продолжаю после обрыва, с подписчика #%s", bc.name, last_id)

    delay = 1.0 / max(settings.broadcast_rate, 1.0)
    PAGE = 500          # сколько подписчиков достаём из базы за раз
    COMMIT_EVERY = 50   # как часто фиксируем прогресс (а не после каждого письма)
    since_commit = 0

    while True:
        chunk = (await session.execute(
            q.where(Subscriber.id > last_id).order_by(Subscriber.id).limit(PAGE)
        )).scalars().all()
        if not chunk:
            break
        for sub in chunk:
            last_id = sub.id
            kb = build_broadcast_keyboard(bc.buttons or [], bc.id, sub)
            ok = await send_message_content(
                bot, session, sub, bc.text, media, kb,
                log_history=False, text_first=bool(bc.text_first))
            bc.sent += 1 if ok else 0
            bc.failed += 0 if ok else 1
            session.add(BroadcastRecipient(
                broadcast_id=bc.id, subscriber_id=sub.id, delivered=ok))
            since_commit += 1
            if since_commit >= COMMIT_EVERY:
                await session.commit()
                since_commit = 0
            await asyncio.sleep(delay)
        await session.commit()
        since_commit = 0

    bc.status = "done"
    log.info("Рассылка «%s» завершена: отправлено %s, не доставлено %s (всего %s)",
             bc.name, bc.sent, bc.failed, bc.total)


# ---------- запуск из FastAPI ----------

async def start_bot_and_workers():
    await maybe_seed_default_bot()
    await manager.start_all()
    return []  # задачи хранит manager


async def maybe_seed_default_bot():
    """Если задан BOT_TOKEN и ботов ещё нет — создаём одного из env
    (обратная совместимость со старой однобот-версией)."""
    if not settings.bot_token:
        return
    async with SessionLocal() as session:
        existing = (await session.execute(select(BotModel))).scalars().first()
        if existing:
            return
        b = BotModel(name="Основной", token=settings.bot_token, is_active=True)
        session.add(b)
        await session.flush()
        # привяжем существующие воронки и подписчиков (миграция со старой версии)
        from sqlalchemy import update

        await session.execute(update(Subscriber).where(Subscriber.bot_id == 0).values(bot_id=b.id))
        await session.execute(update(Broadcast).where(Broadcast.bot_id == 0).values(bot_id=b.id))
        funnels = (await session.execute(select(Funnel))).scalars().all()
        for f in funnels:
            session.add(FunnelBot(funnel_id=f.id, bot_id=b.id))
        await session.commit()
        log.info("Создан бот «Основной» из BOT_TOKEN (#%s)", b.id)


async def funnel_input_export_loop():
    """Автовыгрузка недельных конверсий в лист вида 08B_FUNNEL_INPUT.

    Просыпается раз в десять минут и смотрит, пора ли:
      каждый час — в начале каждого часа, но не чаще раза в час;
      раз в сутки — в заданный час, не чаще раза в день;
      раз в неделю — в понедельник в заданный час (неделя уже закрылась,
      цифры за неё финальные), не чаще раза в день.
    """
    from datetime import datetime

    from ..logging_setup import event_logger

    log = event_logger()
    await asyncio.sleep(150)   # чуть позже обычной выгрузки, чтобы не столкнуться

    while True:
        try:
            from ..api import (
                get_fi_cfg, run_funnel_input_export, save_fi_cfg,
            )
            from ..funnel_input import monday
            from ..sheets import SheetsError

            async with SessionLocal() as session:
                cfg = await get_fi_cfg(session)
                if not cfg.get("auto") or not cfg.get("spreadsheet_id"):
                    await asyncio.sleep(600)
                    continue

                now = datetime.utcnow()
                last = cfg.get("last_run") or ""
                interval = cfg.get("interval", "daily")
                hour = int(cfg.get("hour", 4))

                if interval == "hourly":
                    due = last[:13] != now.strftime("%Y-%m-%dT%H")
                elif interval == "weekly":
                    due = (now.weekday() == 0 and now.hour == hour
                           and last[:10] != now.strftime("%Y-%m-%d"))
                else:
                    due = (now.hour == hour
                           and last[:10] != now.strftime("%Y-%m-%d"))

                if due:
                    try:
                        result = await run_funnel_input_export(session, cfg)
                        if not cfg.get("start_week"):
                            cfg["start_week"] = monday(now.date()).isoformat()
                        cfg["last_status"] = "ok"
                        cfg["last_error"] = ""
                        cfg["last_result"] = result
                        log.info("Автовыгрузка конверсий: %s", result)
                    except SheetsError as e:
                        cfg["last_status"] = "error"
                        cfg["last_error"] = str(e)
                        log.warning("Автовыгрузка конверсий не удалась: %s", e)
                    cfg["last_run"] = now.isoformat()
                    await save_fi_cfg(session, cfg)
                    await session.commit()
        except Exception as e:  # noqa: BLE001 — цикл не должен падать
            try:
                from ..logging_setup import event_logger as _el
                _el().warning("Сбой цикла выгрузки конверсий: %s", e)
            except Exception:  # noqa: BLE001
                pass
        await asyncio.sleep(600)
