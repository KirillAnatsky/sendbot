"""Нагрузочное тестирование SendBot.

Наполняет базу синтетическими данными и замеряет операции, которые реально
нагружают систему на больших базах: выборка сегмента, рассылка, аналитика,
запуск воронок, планировщик. В конце — экстраполяция на целевой объём.

Запуск (в докере, на боевой конфигурации Postgres):
    docker compose exec app python loadtest.py --subscribers 40000

Полезные ключи:
    --subscribers N   сколько подписчиков создать (по умолчанию 20000)
    --bots N          на сколько ботов их распределить (5)
    --target N        целевой объём для экстраполяции (400000)
    --keep            не удалять тестовые данные после прогона
    --skip-seed       не создавать данные (замерить на уже существующей базе)
    --cleanup-only    только стереть тестовые данные и выйти
    --database-url    адрес тестовой БД (чтобы не трогать боевую)

Тест НЕ гоняем на боевом сервере: см. НАГРУЗОЧНЫЙ-ТЕСТ.md

ВНИМАНИЕ: создаёт тестовых ботов/подписчиков с пометкой [LOADTEST] и по
умолчанию удаляет их в конце. Лучше гонять на копии базы.
"""
import argparse
import asyncio
import random
import statistics
import time
from datetime import datetime, timedelta

MARK = "[LOADTEST]"


class Timer:
    def __init__(self):
        self.results = []

    async def measure(self, name, coro_factory, runs=3):
        times = []
        result = None
        for _ in range(runs):
            t0 = time.perf_counter()
            result = await coro_factory()
            times.append((time.perf_counter() - t0) * 1000)
        best, med = min(times), statistics.median(times)
        self.results.append((name, med, best, result))
        print(f"  {name:<46} {med:8.0f} мс  (лучшее {best:.0f})")
        return med


async def seed(session, n_subs, n_bots, verbose=True):
    """Создаёт ботов, теги, подписчиков, сообщения, воронку и её прохождения."""
    from app.models import (
        Bot, ButtonClick, Funnel, FunnelBot, FunnelRun, Message,
        NodeVisit, Subscriber, SubscriberTag, Tag,
    )
    from app.graph import compile_graph

    t0 = time.perf_counter()
    bots = [Bot(name=f"{MARK} bot{i}", token=f"loadtest{i}", is_active=False) for i in range(n_bots)]
    session.add_all(bots)
    await session.flush()

    tags = [Tag(name=f"{MARK}-tag{i}") for i in range(8)]
    session.add_all(tags)
    await session.flush()

    # простая воронка из 4 узлов
    gui = {"drawflow": {"Home": {"data": {
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "message", "data": {"text": "Привет", "buttons": [{"label": "Да"}]},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": [{"node": "3", "output": "input_1"}]},
                          "output_2": {"connections": [{"node": "4", "output": "input_1"}]}}},
        "3": {"id": 3, "name": "message", "data": {"text": "Шаг 2", "buttons": []},
              "inputs": {"input_1": {"connections": []}}, "outputs": {"output_1": {"connections": []}}},
        "4": {"id": 4, "name": "message", "data": {"text": "Ветка кнопки", "buttons": []},
              "inputs": {"input_1": {"connections": []}}, "outputs": {"output_1": {"connections": []}}},
    }}}}
    funnel = Funnel(name=f"{MARK} funnel", is_active=True, trigger_type="start",
                    graph_ui=gui, graph=compile_graph(gui))
    session.add(funnel)
    await session.flush()
    for b in bots:
        session.add(FunnelBot(funnel_id=funnel.id, bot_id=b.id))

    langs = ["ru", "en", "es", "de", "pt"]
    sources = ["fb", "google", "tiktok", "blog", None]
    now = datetime.utcnow()
    BATCH = 2000
    created = 0
    for start in range(0, n_subs, BATCH):
        chunk = min(BATCH, n_subs - start)
        subs = []
        for i in range(chunk):
            idx = start + i
            subs.append(Subscriber(
                bot_id=bots[idx % n_bots].id,
                tg_id=900_000_000 + idx,
                username=f"lt{idx}",
                first_name=f"User{idx}",
                language_code=langs[idx % len(langs)],
                source=sources[idx % len(sources)],
                is_active=(idx % 11 != 0),
                created_at=now - timedelta(days=idx % 90, minutes=idx % 1440),
                last_active_at=now - timedelta(days=idx % 30),
            ))
        session.add_all(subs)
        await session.flush()

        rows_st, rows_msg, rows_run, rows_vis, rows_clk = [], [], [], [], []
        for j, s in enumerate(subs):
            idx = start + j
            # теги: у каждого 1–2
            rows_st.append(SubscriberTag(subscriber_id=s.id, tag_id=tags[idx % len(tags)].id))
            if idx % 3 == 0:
                rows_st.append(SubscriberTag(subscriber_id=s.id, tag_id=tags[(idx + 3) % len(tags)].id))
            # переписка: 2 входящих + 2 исходящих
            for k in range(2):
                rows_msg.append(Message(subscriber_id=s.id, direction="in", text="привет",
                                        created_at=now - timedelta(days=idx % 30, hours=k)))
                rows_msg.append(Message(subscriber_id=s.id, direction="out", text="ответ бота",
                                        created_at=now - timedelta(days=idx % 30, hours=k)))
        session.add_all(rows_st)
        session.add_all(rows_msg)
        await session.flush()

        # прохождение воронки: 80% дошли до узла 2, 40% до 3, 25% кликнули
        for j, s in enumerate(subs):
            idx = start + j
            if idx % 5 == 0:
                continue
            run = FunnelRun(funnel_id=funnel.id, subscriber_id=s.id, status="done")
            rows_run.append(run)
        session.add_all(rows_run)
        await session.flush()
        for run in rows_run:
            rows_vis.append(NodeVisit(funnel_id=funnel.id, node_id="2",
                                      subscriber_id=run.subscriber_id, run_id=run.id))
            if run.id % 2 == 0:
                rows_vis.append(NodeVisit(funnel_id=funnel.id, node_id="3",
                                          subscriber_id=run.subscriber_id, run_id=run.id))
            if run.id % 4 == 0:
                rows_clk.append(ButtonClick(funnel_id=funnel.id, node_id="2", button_index=0,
                                            subscriber_id=run.subscriber_id))
        session.add_all(rows_vis)
        session.add_all(rows_clk)
        await session.commit()
        created += chunk
        if verbose:
            print(f"    …создано {created}/{n_subs}", end="\r", flush=True)

    dt = time.perf_counter() - t0
    if verbose:
        print(f"    данные созданы за {dt:.1f} с ({n_subs / max(dt, .001):.0f} подписчиков/с)")
    return bots, tags, funnel


async def run_benchmarks(session, bots, tags, funnel, n_subs):
    """Замеры операций, которые нагружают систему в проде."""
    from sqlalchemy import func, select
    from app import analytics as an
    from app import segment as seg
    from app.models import Broadcast, BroadcastRecipient, Subscriber

    t = Timer()
    bot_id = bots[0].id
    tag_id = tags[0].id
    print("\n  ЗАМЕРЫ (медиана из 3 прогонов)")

    async def count_all():
        q = seg.build_query(None, {"conditions": []})
        return (await session.execute(select(func.count()).select_from(q.subquery()))).scalar()

    async def count_segment():
        f = {"match": "all", "conditions": [
            {"field": "tag", "op": "has", "value": tag_id},
            {"field": "language", "op": "equals", "value": "ru"},
            {"field": "status", "op": "equals", "value": "active"},
        ]}
        q = seg.build_query(bot_id, f)
        return (await session.execute(select(func.count()).select_from(q.subquery()))).scalar()

    async def page_subscribers():
        q = seg.build_query(bot_id, {"conditions": []}).order_by(
            Subscriber.created_at.desc()).limit(500)
        return len((await session.execute(q)).scalars().all())

    async def broadcast_audience():
        f = {"match": "all", "conditions": [{"field": "tag", "op": "has", "value": tag_id}]}
        q = seg.build_query(bot_id, f).where(Subscriber.is_active == True)  # noqa: E712
        return len((await session.execute(q)).scalars().all())

    async def dashboard_30d():
        an.invalidate_analytics_cache()  # меряем реальный запрос, а не кэш
        return await an.build_analytics(session, days=30)

    async def dashboard_365d():
        an.invalidate_analytics_cache()
        return await an.build_analytics(session, days=365)

    async def funnel_steps():
        from app.models import ButtonClick, FunnelRun, NodeVisit
        entered = (await session.execute(
            select(func.count(func.distinct(FunnelRun.subscriber_id)))
            .where(FunnelRun.funnel_id == funnel.id))).scalar()
        visits = (await session.execute(
            select(NodeVisit.node_id, func.count(func.distinct(NodeVisit.subscriber_id)))
            .where(NodeVisit.funnel_id == funnel.id).group_by(NodeVisit.node_id))).all()
        clicks = (await session.execute(
            select(ButtonClick.node_id, func.count(func.distinct(ButtonClick.subscriber_id)))
            .where(ButtonClick.funnel_id == funnel.id).group_by(ButtonClick.node_id))).all()
        return entered, len(visits), len(clicks)

    async def analysis_funnel():
        return await an.funnel_analysis(session, [
            {"type": "subscribed"},
            {"type": "node", "funnel_id": funnel.id, "node_id": "2"},
            {"type": "button", "funnel_id": funnel.id, "node_id": "2", "button": 0},
            {"type": "node", "funnel_id": funnel.id, "node_id": "3"},
        ], days=90)

    await t.measure("Всего подписчиков (COUNT)", count_all)
    total = await count_all()  # фактическое число строк, а не время замера
    await t.measure("Подсчёт сегмента (тег+язык+статус)", count_segment)
    await t.measure("Страница подписчиков (500 записей)", page_subscribers)
    audience_ms = await t.measure("Выборка аудитории рассылки", broadcast_audience)
    dash_ms = await t.measure("Дашборд, 30 дней", dashboard_30d)
    await t.measure("Дашборд, 365 дней", dashboard_365d)
    steps_ms = await t.measure("Статистика шагов воронки", funnel_steps)
    analysis_ms = await t.measure("Анализ воронки (4 шага, 90 дней)", analysis_funnel)

    # запись получателей рассылки — самая частая операция при рассылке
    bc = Broadcast(bot_id=bot_id, name=f"{MARK} bc", text="test", filters={})
    session.add(bc)
    await session.flush()

    # Берём РЕАЛЬНЫЕ id подписчиков, а не 1..1000. После любой чистки базы
    # нумерация начинается не с единицы, и вставка падала по внешнему ключу.
    sub_ids = (await session.execute(
        select(Subscriber.id)
        .where(Subscriber.bot_id.in_([b.id for b in bots]))
        .limit(1000)
    )).scalars().all()

    async def write_recipients():
        session.add_all([
            BroadcastRecipient(broadcast_id=bc.id, subscriber_id=sid, delivered=True)
            for sid in sub_ids
        ])
        await session.commit()
        return len(sub_ids)

    rec_ms = await t.measure(
        f"Запись {len(sub_ids)} получателей рассылки", write_recipients, runs=2)
    # приводим к «на 1000 штук», как ждёт экстраполяция
    if sub_ids:
        rec_ms = rec_ms * 1000 / len(sub_ids)

    return {
        "subscribers": total,
        "audience_ms": audience_ms,
        "dashboard_ms": dash_ms,
        "steps_ms": steps_ms,
        "analysis_ms": analysis_ms,
        "recipients_per_1000_ms": rec_ms,
    }


def extrapolate(stats, n_subs, target):
    k = target / max(n_subs, 1)
    print(f"\n  ЭКСТРАПОЛЯЦИЯ на {target:,} подписчиков (×{k:.0f})".replace(",", " "))
    print("  Линейные операции (растут пропорционально базе):")
    for label, key in (("Выборка аудитории рассылки", "audience_ms"),
                       ("Дашборд (30 дней)", "dashboard_ms"),
                       ("Статистика шагов воронки", "steps_ms"),
                       ("Анализ воронки", "analysis_ms")):
        v = stats[key] * k
        print(f"    {label:<34} ≈ {v/1000:6.1f} с")
    rec_total = stats["recipients_per_1000_ms"] * (target / 1000)
    print(f"    Запись получателей рассылки        ≈ {rec_total/1000/60:6.1f} мин "
          f"(фоном во время отправки)")

    tg_hours = target / 30 / 3600
    print("\n  ОГРАНИЧЕНИЕ TELEGRAM (главное узкое место):")
    print(f"    {target:,} сообщений при 30/сек на бота ≈ {tg_hours:.1f} ч одним ботом"
          .replace(",", " "))
    print(f"    на 5 ботов параллельно            ≈ {tg_hours/5:.1f} ч")
    print("    → база отдаёт данные на порядки быстрее, чем Telegram успевает принимать")


async def cleanup(session):
    from sqlalchemy import delete, select
    from app.models import (
        Bot, Broadcast, BroadcastRecipient, ButtonClick, Funnel, FunnelBot,
        FunnelRun, Message, NodeVisit, ScheduledJob, Subscriber, SubscriberTag, Tag,
    )
    bot_ids = [r[0] for r in (await session.execute(
        select(Bot.id).where(Bot.name.like(f"{MARK}%")))).all()]
    fun_ids = [r[0] for r in (await session.execute(
        select(Funnel.id).where(Funnel.name.like(f"{MARK}%")))).all()]
    sub_ids = [r[0] for r in (await session.execute(
        select(Subscriber.id).where(Subscriber.bot_id.in_(bot_ids)))).all()] if bot_ids else []
    run_ids = [r[0] for r in (await session.execute(
        select(FunnelRun.id).where(FunnelRun.funnel_id.in_(fun_ids)))).all()] if fun_ids else []
    bc_ids = [r[0] for r in (await session.execute(
        select(Broadcast.id).where(Broadcast.name.like(f"{MARK}%")))).all()]

    CH = 5000
    def chunks(xs):
        for i in range(0, len(xs), CH):
            yield xs[i:i + CH]

    for ids in chunks(run_ids):
        await session.execute(delete(ScheduledJob).where(ScheduledJob.run_id.in_(ids)))
    for ids in chunks(sub_ids):
        await session.execute(delete(Message).where(Message.subscriber_id.in_(ids)))
        await session.execute(delete(SubscriberTag).where(SubscriberTag.subscriber_id.in_(ids)))
        await session.execute(delete(BroadcastRecipient).where(BroadcastRecipient.subscriber_id.in_(ids)))
    for ids in chunks(fun_ids):
        await session.execute(delete(NodeVisit).where(NodeVisit.funnel_id.in_(ids)))
        await session.execute(delete(ButtonClick).where(ButtonClick.funnel_id.in_(ids)))
        await session.execute(delete(FunnelRun).where(FunnelRun.funnel_id.in_(ids)))
        await session.execute(delete(FunnelBot).where(FunnelBot.funnel_id.in_(ids)))
    for ids in chunks(sub_ids):
        await session.execute(delete(Subscriber).where(Subscriber.id.in_(ids)))
    if bc_ids:
        await session.execute(delete(Broadcast).where(Broadcast.id.in_(bc_ids)))
    if fun_ids:
        await session.execute(delete(Funnel).where(Funnel.id.in_(fun_ids)))
    if bot_ids:
        await session.execute(delete(Bot).where(Bot.id.in_(bot_ids)))
    await session.execute(delete(Tag).where(Tag.name.like(f"{MARK}%")))
    await session.commit()
    print("  тестовые данные удалены")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subscribers", type=int, default=20000)
    ap.add_argument("--bots", type=int, default=5)
    ap.add_argument("--target", type=int, default=400000)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--skip-seed", action="store_true")
    ap.add_argument("--cleanup-only", action="store_true",
                    help="только удалить тестовые данные и выйти")
    ap.add_argument("--database-url", default=None,
                    help="адрес тестовой БД, если гоняем не на этом сервере")
    args = ap.parse_args()

    if args.database_url:
        import os
        os.environ["DATABASE_URL"] = args.database_url

    from app.db import SessionLocal, init_db
    from app.config import settings

    print("=" * 72)
    print("  НАГРУЗОЧНОЕ ТЕСТИРОВАНИЕ SENDBOT")
    print(f"  БД: {settings.database_url.split('@')[-1]}")
    print(f"  Подписчиков: {args.subscribers:,}  |  ботов: {args.bots}".replace(",", " "))
    print("=" * 72)

    await init_db()
    async with SessionLocal() as session:
        if args.cleanup_only:
            print("\n  ОЧИСТКА ТЕСТОВЫХ ДАННЫХ")
            await cleanup(session)
            print("\n" + "=" * 72)
            return
        if args.skip_seed:
            from sqlalchemy import select
            from app.models import Bot, Funnel, Tag
            bots = (await session.execute(select(Bot).where(Bot.name.like(f"{MARK}%")))).scalars().all()
            tags = (await session.execute(select(Tag).where(Tag.name.like(f"{MARK}%")))).scalars().all()
            funnel = (await session.execute(select(Funnel).where(Funnel.name.like(f"{MARK}%")))).scalars().first()
            if not (bots and tags and funnel):
                print("  Нет тестовых данных — запусти без --skip-seed")
                return
        else:
            print("\n  НАПОЛНЕНИЕ БАЗЫ")
            bots, tags, funnel = await seed(session, args.subscribers, args.bots)

        stats = await run_benchmarks(session, bots, tags, funnel, args.subscribers)
        extrapolate(stats, stats["subscribers"] or args.subscribers, args.target)

        if not args.keep:
            print("\n  ОЧИСТКА")
            await cleanup(session)
        else:
            print("\n  Данные оставлены (--keep). Удалить: python loadtest.py --skip-seed --cleanup-only")
    print("\n" + "=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
