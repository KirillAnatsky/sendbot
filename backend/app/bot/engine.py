"""Движок воронок: исполняет скомпилированный граф по подписчику."""
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..graph import next_node
from ..models import (
    ButtonClick,
    Funnel,
    FunnelRun,
    NodeVisit,
    ScheduledJob,
    Subscriber,
    SubscriberTag,
    Tag,
)
from .sender import build_keyboard, send_message_content, send_to_subscriber

log = logging.getLogger("sendbot.engine")

MAX_STEPS = 100  # защита от циклов

UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}


async def start_funnel(bot: Bot, session: AsyncSession, funnel: Funnel, sub: Subscriber):
    """Запустить воронку для подписчика (перезапуск, если уже была активна)."""
    graph = funnel.graph
    if not graph or "start" not in graph:
        return

    # отменяем прежний активный запуск этой воронки
    old = await session.execute(
        select(FunnelRun).where(
            FunnelRun.funnel_id == funnel.id,
            FunnelRun.subscriber_id == sub.id,
            FunnelRun.status == "active",
        )
    )
    for r in old.scalars():
        r.status = "cancelled"
        jobs = await session.execute(
            select(ScheduledJob).where(
                ScheduledJob.run_id == r.id, ScheduledJob.status == "pending"
            )
        )
        for j in jobs.scalars():
            j.status = "cancelled"

    run = FunnelRun(funnel_id=funnel.id, subscriber_id=sub.id, current_node=graph["start"])
    session.add(run)
    await session.flush()
    await advance(bot, session, run, funnel, sub, graph["start"], "output_1")


async def advance(
    bot: Bot,
    session: AsyncSession,
    run: FunnelRun,
    funnel: Funnel,
    sub: Subscriber,
    from_node: str,
    port: str,
):
    """Идти по графу от узла from_node через порт port, пока не упрёмся в паузу/конец."""
    graph = funnel.graph
    node_id = next_node(graph, from_node, port)
    steps = 0
    waiting_buttons = False

    while node_id and steps < MAX_STEPS:
        steps += 1
        node = graph["nodes"].get(node_id)
        if node is None:
            break
        ntype, data = node["type"], node["data"]
        run.current_node = node_id
        # трекинг шага для статистики воронки
        session.add(NodeVisit(
            funnel_id=funnel.id, node_id=node_id,
            subscriber_id=sub.id, run_id=run.id,
        ))

        if ntype == "message":
            buttons = data.get("buttons") or []
            kb = build_keyboard(buttons, run.id, node_id, sub)
            media = list(data.get("media") or [])
            if not media and data.get("photo_url"):  # обратная совместимость
                media = [{"type": "photo", "path": data["photo_url"]}]
            await send_message_content(bot, session, sub, data.get("text", ""), media, kb)
            has_branchy_buttons = any(
                node["outputs"].get(f"output_{i + 2}") for i in range(len(buttons))
            )
            waiting_buttons = waiting_buttons or has_branchy_buttons
            node_id = next_node(graph, node_id, "output_1")

        elif ntype == "delay":
            seconds = float(data["amount"]) * UNIT_SECONDS[data["unit"]]
            session.add(
                ScheduledJob(
                    run_id=run.id,
                    node_id=node_id,
                    execute_at=datetime.utcnow() + timedelta(seconds=seconds),
                )
            )
            await session.flush()
            return  # продолжит планировщик

        elif ntype == "condition":
            has = await _has_tag(session, sub.id, data["tag"])
            node_id = next_node(graph, node_id, "output_1" if has else "output_2")

        elif ntype == "action":
            if data["op"] == "add_tag":
                await _add_tag(session, sub.id, data["tag"])
            else:
                await _remove_tag(session, sub.id, data["tag"])
            node_id = next_node(graph, node_id, "output_1")

        else:  # start
            node_id = next_node(graph, node_id, "output_1")

    # дошли до конца ветки; если нет живых кнопок и отложенных шагов — воронка пройдена
    if not waiting_buttons:
        pending = await session.execute(
            select(ScheduledJob.id).where(
                ScheduledJob.run_id == run.id, ScheduledJob.status == "pending"
            )
        )
        if pending.first() is None:
            run.status = "done"
    await session.flush()


async def handle_button(bot: Bot, session: AsyncSession, run_id: int, node_id: str, btn: int):
    run = await session.get(FunnelRun, run_id)
    if run is None or run.status == "cancelled":
        return
    funnel = await session.get(Funnel, run.funnel_id)
    sub = await session.get(Subscriber, run.subscriber_id)
    if funnel is None or sub is None:
        return

    session.add(
        ButtonClick(
            funnel_id=funnel.id, node_id=node_id, button_index=btn, subscriber_id=sub.id
        )
    )
    run.status = "active"
    await advance(bot, session, run, funnel, sub, node_id, f"output_{btn + 2}")


async def run_due_jobs(session: AsyncSession, get_bot):
    """get_bot(bot_id) -> aiogram Bot | None. Бот выбирается по подписчику."""
    jobs = await session.execute(
        select(ScheduledJob).where(
            ScheduledJob.status == "pending",
            ScheduledJob.execute_at <= datetime.utcnow(),
        )
    )
    for job in jobs.scalars().all():
        job.status = "done"
        run = await session.get(FunnelRun, job.run_id)
        if run is None or run.status == "cancelled":
            continue
        funnel = await session.get(Funnel, run.funnel_id)
        sub = await session.get(Subscriber, run.subscriber_id)
        if funnel is None or sub is None or not funnel.is_active or not sub.is_active:
            continue
        # пауза автоматизации — переносим шаг: вернём job в pending на минуту вперёд
        if sub.automation_paused_until and sub.automation_paused_until > datetime.utcnow():
            job.status = "pending"
            job.execute_at = sub.automation_paused_until
            continue
        bot = get_bot(sub.bot_id)
        if bot is None:
            continue
        try:
            await advance(bot, session, run, funnel, sub, job.node_id, "output_1")
        except Exception:  # noqa: BLE001
            log.exception("Ошибка выполнения отложенного шага job=%s", job.id)


async def _tag_id_by_ref(session: AsyncSession, tag_ref) -> int | None:
    """tag в данных узла — это id тега (строкой или числом)."""
    try:
        return int(tag_ref)
    except (TypeError, ValueError):
        res = await session.execute(select(Tag.id).where(Tag.name == str(tag_ref)))
        row = res.first()
        return row[0] if row else None


async def _has_tag(session: AsyncSession, sub_id: int, tag_ref) -> bool:
    tag_id = await _tag_id_by_ref(session, tag_ref)
    if tag_id is None:
        return False
    res = await session.execute(
        select(SubscriberTag.id).where(
            SubscriberTag.subscriber_id == sub_id, SubscriberTag.tag_id == tag_id
        )
    )
    return res.first() is not None


async def _add_tag(session: AsyncSession, sub_id: int, tag_ref):
    tag_id = await _tag_id_by_ref(session, tag_ref)
    if tag_id is None or await _has_tag(session, sub_id, tag_id):
        return
    session.add(SubscriberTag(subscriber_id=sub_id, tag_id=tag_id))
    await session.flush()


async def _remove_tag(session: AsyncSession, sub_id: int, tag_ref):
    tag_id = await _tag_id_by_ref(session, tag_ref)
    if tag_id is None:
        return
    res = await session.execute(
        select(SubscriberTag).where(
            SubscriberTag.subscriber_id == sub_id, SubscriberTag.tag_id == tag_id
        )
    )
    for st in res.scalars():
        await session.delete(st)
    await session.flush()
