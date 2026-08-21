"""Аналитика для дашборда: серии по дням, разбивки, retention, LTV."""
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select
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

    total = (await session.execute(subs_q())).scalar() or 0
    active = (await session.execute(subs_q(Subscriber.is_active == True))).scalar() or 0  # noqa: E712
    new_period = (
        await session.execute(subs_q(Subscriber.created_at >= start_dt))
    ).scalar() or 0
    week_ago = datetime.utcnow() - timedelta(days=7)
    active_7d = (
        await session.execute(subs_q(Subscriber.last_active_at >= week_ago))
    ).scalar() or 0

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

    # LTV по времени: сколько подписчик «живёт» с нами
    lt_q = select(Subscriber.created_at, Subscriber.last_active_at, Subscriber.is_active)
    if conds:
        lt_q = lt_q.where(and_(*conds))
    rows_lt = (await session.execute(lt_q)).all()
    now = datetime.utcnow()
    lifetimes, lifetimes_churned = [], []
    for created, last_active, is_act in rows_lt:
        if not created:
            continue
        end = now if is_act else (last_active or created)
        d = max((end - created).total_seconds() / 86400.0, 0.0)
        lifetimes.append(d)
        if not is_act:
            lifetimes_churned.append(d)

    def _avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else 0

    def _median(xs):
        if not xs:
            return 0
        s = sorted(xs)
        n = len(s)
        return round((s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2), 1)

    # распределение по «возрасту» подписки
    buckets = [("0–1 дн", 0, 1), ("1–7 дн", 1, 7), ("7–30 дн", 7, 30),
               ("30–90 дн", 30, 90), ("90+ дн", 90, 10**9)]
    lifetime_dist = [
        {"k": label, "v": sum(1 for x in lifetimes if lo <= x < hi)}
        for label, lo, hi in buckets
    ]

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
            "lifetime_avg_days": _avg(lifetimes),
            "lifetime_median_days": _median(lifetimes),
            "churned_lifetime_avg_days": _avg(lifetimes_churned),
        },
        "breakdowns": {
            "languages": langs,
            "sources": sources,
            "bots": [{"k": k, "v": v} for k, v in bots_rows],
            "lifetime": lifetime_dist,
        },
    }


# ---------- анализ воронок (в стиле Amplitude) ----------

async def _step_events(session, step: dict, start_dt, bot_id=None):
    """-> список (subscriber_id, timestamp) для события-шага."""
    t = step.get("type")
    if t == "subscribed":
        q = select(Subscriber.id, Subscriber.created_at).where(
            Subscriber.created_at >= start_dt
        )
        if bot_id:
            q = q.where(Subscriber.bot_id == bot_id)
        return (await session.execute(q)).all()

    if t == "node":
        q = select(NodeVisit.subscriber_id, NodeVisit.created_at).where(
            NodeVisit.funnel_id == int(step["funnel_id"]),
            NodeVisit.node_id == str(step["node_id"]),
            NodeVisit.created_at >= start_dt,
        )
        return (await session.execute(q)).all()

    if t == "button":
        q = select(ButtonClick.subscriber_id, ButtonClick.created_at).where(
            ButtonClick.funnel_id == int(step["funnel_id"]),
            ButtonClick.node_id == str(step["node_id"]),
            ButtonClick.created_at >= start_dt,
        )
        if step.get("button") is not None and step.get("button") != "":
            q = q.where(ButtonClick.button_index == int(step["button"]))
        return (await session.execute(q)).all()

    if t == "broadcast":
        q = (
            select(
                BroadcastRecipient.subscriber_id,
                func.coalesce(BroadcastRecipient.created_at, Broadcast.created_at),
            )
            .join(Broadcast, Broadcast.id == BroadcastRecipient.broadcast_id)
            .where(
                BroadcastRecipient.broadcast_id == int(step["broadcast_id"]),
                BroadcastRecipient.delivered == True,  # noqa: E712
            )
        )
        return (await session.execute(q)).all()

    if t == "message_in":
        q = select(Message.subscriber_id, Message.created_at).where(
            Message.direction == "in", Message.created_at >= start_dt
        )
        if bot_id:
            q = q.join(Subscriber, Subscriber.id == Message.subscriber_id).where(
                Subscriber.bot_id == bot_id
            )
        return (await session.execute(q)).all()

    return []


async def funnel_analysis(session, steps: list, days: int = 30, bot_id=None) -> dict:
    """Последовательная воронка: на каждом шаге остаются только те, кто сделал
    событие ПОСЛЕ своего события предыдущего шага (как в Amplitude)."""
    days = max(1, min(days or 30, 365))
    start_dt = datetime.utcnow() - timedelta(days=days)
    prev: dict | None = None
    out = []
    for step in steps[:10]:
        rows = await _step_events(session, step, start_dt, bot_id)
        cur: dict = {}
        for sub, ts in rows:
            if ts is None:
                continue
            if prev is None:
                if sub not in cur or ts < cur[sub]:
                    cur[sub] = ts
            else:
                base = prev.get(sub)
                if base is not None and ts >= base and (sub not in cur or ts < cur[sub]):
                    cur[sub] = ts
        out.append(len(cur))
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
