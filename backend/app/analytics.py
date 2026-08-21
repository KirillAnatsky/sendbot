"""Аналитика для дашборда: серии по дням, разбивки, retention, LTV."""
from datetime import datetime, timedelta

from sqlalchemy import DateTime, and_, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Bot,
    Broadcast,
    BroadcastRecipient,
    ButtonClick,
    Message,
    NodeVisit,
    Subscriber,
    SubscriberTag,
)


def _sub_conditions(bot_id, language, source, tag_id):
    conds = []
    if bot_id:
        conds.append(Subscriber.bot_id == bot_id)
    if language:
        conds.append(Subscriber.language_code == language)
    if source:
        conds.append(Subscriber.source.ilike(f"%{source}%"))
    if tag_id:
        conds.append(
            Subscriber.id.in_(
                select(SubscriberTag.subscriber_id).where(SubscriberTag.tag_id == tag_id)
            )
        )
    return conds


LIFETIME_BUCKETS = [
    ("0–1 дн", 0, 1),
    ("1–7 дн", 1, 7),
    ("7–30 дн", 7, 30),
    ("30–90 дн", 30, 90),
    ("90+ дн", 90, 10 ** 9),
]


def _is_postgres(session) -> bool:
    try:
        return session.get_bind().dialect.name == "postgresql"
    except Exception:  # noqa: BLE001
        try:
            return session.sync_session.get_bind().dialect.name == "postgresql"
        except Exception:  # noqa: BLE001
            return False


def _lifetime_days_expr(session, now):
    """Сколько дней подписчик «живёт» с нами — считается на стороне БД."""
    end = case(
        (Subscriber.is_active == True, literal(now, DateTime)),  # noqa: E712
        else_=func.coalesce(Subscriber.last_active_at, Subscriber.created_at),
    )
    if _is_postgres(session):
        return func.extract("epoch", end - Subscriber.created_at) / 86400.0
    return func.julianday(end) - func.julianday(Subscriber.created_at)


def _day_range(days: int):
    end = datetime.utcnow().date()
    start = end - timedelta(days=days - 1)
    return [start + timedelta(days=i) for i in range(days)]


def _fill(rows, days_list):
    """rows: [(date_str|date, count)] -> список значений по дням."""
    m = {}
    for d, c in rows:
        key = str(d)[:10]
        m[key] = (m.get(key) or 0) + int(c or 0)
    return [m.get(str(d), 0) for d in days_list]


async def build_analytics(
    session: AsyncSession,
    days: int = 30,
    bot_id: int | None = None,
    language: str | None = None,
    source: str | None = None,
    tag_id: int | None = None,
) -> dict:
    days = max(2, min(days, 365))
    day_list = _day_range(days)
    start_dt = datetime.combine(day_list[0], datetime.min.time())
    conds = _sub_conditions(bot_id, language, source, tag_id)

    def subs_q(*extra):
        q = select(func.count(Subscriber.id))
        all_c = list(conds) + list(extra)
        return q.where(and_(*all_c)) if all_c else q

    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    # Все счётчики + LTV одним запросом: раньше это были 4 отдельных COUNT
    # плюс выгрузка всей таблицы подписчиков в память приложения.
    lt = _lifetime_days_expr(session, now)

    def _cnt(cond):
        return func.sum(case((cond, 1), else_=0))

    def _bucket(lo, hi):
        return func.sum(case((and_(lt >= lo, lt < hi), 1), else_=0))

    agg_cols = [
        func.count(Subscriber.id),
        _cnt(Subscriber.is_active == True),  # noqa: E712
        _cnt(Subscriber.created_at >= start_dt),
        _cnt(Subscriber.last_active_at >= week_ago),
        func.avg(lt),
        func.avg(case((Subscriber.is_active == False, lt))),  # noqa: E712
    ] + [_bucket(lo, hi) for _, lo, hi in LIFETIME_BUCKETS]

    agg_q = select(*agg_cols)
    if conds:
        agg_q = agg_q.where(and_(*conds))
    agg = (await session.execute(agg_q)).one()

    total = int(agg[0] or 0)
    active = int(agg[1] or 0)
    new_period = int(agg[2] or 0)
    active_7d = int(agg[3] or 0)
    lifetime_avg = round(float(agg[4] or 0), 1)
    churned_avg = round(float(agg[5] or 0), 1)
    lifetime_dist = [
        {"k": label, "v": int(agg[6 + i] or 0)}
        for i, (label, _, _) in enumerate(LIFETIME_BUCKETS)
    ]

    # медиана — отдельным запросом, на Postgres считает сама БД
    if _is_postgres(session):
        med_q = select(func.percentile_cont(0.5).within_group(lt.asc()))
        if conds:
            med_q = med_q.where(and_(*conds))
        lifetime_median = round(float((await session.execute(med_q)).scalar() or 0), 1)
    else:
        med_q = select(lt).order_by(lt)
        if conds:
            med_q = med_q.where(and_(*conds))
        vals = [float(v or 0) for (v,) in (await session.execute(med_q)).all()]
        n = len(vals)
        lifetime_median = round(
            (vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2), 1
        ) if n else 0

    # прирост по дням + накопительно
    rows = (
        await session.execute(
            select(func.date(Subscriber.created_at), func.count(Subscriber.id))
            .where(and_(Subscriber.created_at >= start_dt, *conds) if conds else Subscriber.created_at >= start_dt)
            .group_by(func.date(Subscriber.created_at))
        )
    ).all()
    new_daily = _fill(rows, day_list)
    before = total - sum(new_daily)
    cumulative = []
    acc = before
    for v in new_daily:
        acc += v
        cumulative.append(acc)

    # активность: входящие сообщения и клики по дням (в рамках фильтра подписчиков)
    sub_ids_sq = select(Subscriber.id).where(and_(*conds)) if conds else None

    msg_q = (
        select(func.date(Message.created_at), func.count(Message.id))
        .where(Message.created_at >= start_dt, Message.direction == "in")
        .group_by(func.date(Message.created_at))
    )
    if sub_ids_sq is not None:
        msg_q = msg_q.where(Message.subscriber_id.in_(sub_ids_sq))
    incoming_daily = _fill((await session.execute(msg_q)).all(), day_list)

    click_q = (
        select(func.date(ButtonClick.created_at), func.count(ButtonClick.id))
        .where(ButtonClick.created_at >= start_dt)
        .group_by(func.date(ButtonClick.created_at))
    )
    if sub_ids_sq is not None:
        click_q = click_q.where(ButtonClick.subscriber_id.in_(sub_ids_sq))
    clicks_daily = _fill((await session.execute(click_q)).all(), day_list)

    # разбивки
    async def breakdown(col, limit=8):
        q = (
            select(func.coalesce(col, "—"), func.count(Subscriber.id))
            .group_by(col)
            .order_by(func.count(Subscriber.id).desc())
            .limit(limit)
        )
        if conds:
            q = q.where(and_(*conds))
        return [{"k": str(k), "v": v} for k, v in (await session.execute(q)).all()]

    langs = await breakdown(Subscriber.language_code)
    sources = await breakdown(Subscriber.source)

    bots_rows = (
        await session.execute(
            select(Bot.name, func.count(Subscriber.id))
            .join(Subscriber, Subscriber.bot_id == Bot.id)
            .group_by(Bot.id)
            .order_by(func.count(Subscriber.id).desc())
        )
    ).all()

    return {
        "days": [str(d) for d in day_list],
        "series": {
            "new_subscribers": new_daily,
            "cumulative_subscribers": cumulative,
            "incoming_messages": incoming_daily,
            "button_clicks": clicks_daily,
        },
        "totals": {
            "subscribers": total,
            "active": active,
            "blocked": total - active,
            "new_period": new_period,
            "active_7d": active_7d,
            "retention_7d": round(100 * active_7d / total, 1) if total else 0,
            # LTV по времени (в днях)
            "lifetime_avg_days": lifetime_avg,
            "lifetime_median_days": lifetime_median,
            "churned_lifetime_avg_days": churned_avg,
        },
        "breakdowns": {
            "languages": langs,
            "sources": sources,
            "bots": [{"k": k, "v": v} for k, v in bots_rows],
            "lifetime": lifetime_dist,
        },
    }


# ---------- анализ воронок (в стиле Amplitude) ----------

def _step_query(step: dict, start_dt, bot_id=None):
    """SELECT (subscriber_id, момент события) для одного шага воронки.

    Возвращается именно запрос, а не строки: дальше шаги склеиваются в один
    SQL-запрос и считаются внутри БД. Раньше каждый шаг выгружал все свои
    события в память приложения — на 120к подписчиков это было ~1,4 с.
    """
    t = step.get("type")
    if t == "subscribed":
        q = select(
            Subscriber.id.label("sid"), Subscriber.created_at.label("ts")
        ).where(Subscriber.created_at >= start_dt)
        if bot_id:
            q = q.where(Subscriber.bot_id == bot_id)
        return q

    if t == "node":
        return select(
            NodeVisit.subscriber_id.label("sid"), NodeVisit.created_at.label("ts")
        ).where(
            NodeVisit.funnel_id == int(step["funnel_id"]),
            NodeVisit.node_id == str(step["node_id"]),
            NodeVisit.created_at >= start_dt,
        )

    if t == "button":
        q = select(
            ButtonClick.subscriber_id.label("sid"), ButtonClick.created_at.label("ts")
        ).where(
            ButtonClick.funnel_id == int(step["funnel_id"]),
            ButtonClick.node_id == str(step["node_id"]),
            ButtonClick.created_at >= start_dt,
        )
        if step.get("button") not in (None, ""):
            q = q.where(ButtonClick.button_index == int(step["button"]))
        return q

    if t == "broadcast":
        return (
            select(
                BroadcastRecipient.subscriber_id.label("sid"),
                func.coalesce(
                    BroadcastRecipient.created_at, Broadcast.created_at
                ).label("ts"),
            )
            .join(Broadcast, Broadcast.id == BroadcastRecipient.broadcast_id)
            .where(
                BroadcastRecipient.broadcast_id == int(step["broadcast_id"]),
                BroadcastRecipient.delivered == True,  # noqa: E712
            )
        )

    if t == "message_in":
        q = select(
            Message.subscriber_id.label("sid"), Message.created_at.label("ts")
        ).where(Message.direction == "in", Message.created_at >= start_dt)
        if bot_id:
            q = q.join(Subscriber, Subscriber.id == Message.subscriber_id).where(
                Subscriber.bot_id == bot_id
            )
        return q

    return None


async def funnel_analysis(session, steps: list, days: int = 30, bot_id=None) -> dict:
    """Последовательная воронка: на каждом шаге остаются только те, кто сделал
    событие ПОСЛЕ своего события предыдущего шага (как в Amplitude).

    Считается целиком в БД: каждый шаг — подзапрос «подписчик + первый момент
    события», следующий джойнится к предыдущему с условием ts >= предыдущего.
    """
    days = max(1, min(days or 30, 365))
    start_dt = datetime.utcnow() - timedelta(days=days)

    out = []
    prev = None
    for step in steps[:10]:
        base = _step_query(step, start_dt, bot_id)
        if base is None:
            out.append(0)
            prev = None
            continue
        b = base.subquery()
        if prev is None:
            cur = (
                select(b.c.sid.label("sid"), func.min(b.c.ts).label("ts"))
                .where(b.c.ts.isnot(None))
                .group_by(b.c.sid)
            ).subquery()
        else:
            cur = (
                select(b.c.sid.label("sid"), func.min(b.c.ts).label("ts"))
                .join(prev, prev.c.sid == b.c.sid)
                .where(b.c.ts.isnot(None), b.c.ts >= prev.c.ts)
                .group_by(b.c.sid)
            ).subquery()
        n = (await session.execute(select(func.count()).select_from(cur))).scalar() or 0
        out.append(int(n))
        prev = cur

    result = []
    first = out[0] if out else 0
    for i, n in enumerate(out):
        prev_n = out[i - 1] if i else n
        result.append({
            "count": n,
            "from_prev": round(100 * n / prev_n, 1) if prev_n else 0,
            "from_first": round(100 * n / first, 1) if first else 0,
        })
    return {"steps": result, "days": days}
