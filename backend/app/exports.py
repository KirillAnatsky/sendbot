"""Сбор данных для выгрузки в таблицу.

Каждая функция возвращает список строк: первая — заголовки, дальше данные.
Такой формат одинаково годится и для Google Таблиц, и для CSV.
"""
from datetime import datetime, timedelta

from sqlalchemy import func, select

from .models import (
    Bot,
    Broadcast,
    ButtonClick,
    Funnel,
    FunnelBot,
    FunnelRun,
    Message,
    NodeVisit,
    Subscriber,
)

# что можно выгружать; порядок = порядок листов в таблице
SHEETS = [
    {"key": "funnels", "title": "Шаги воронок",
     "label": "Шаги воронок",
     "hint": "по каждому шагу: сколько дошло, доля от входа и от предыдущего шага"},
    {"key": "buttons", "title": "Клики по кнопкам",
     "label": "Клики по кнопкам",
     "hint": "сколько раз нажали каждую кнопку в каждом сообщении"},
    {"key": "bots", "title": "Боты",
     "label": "Сводка по ботам",
     "hint": "подписчики, активные, заблокировавшие, прирост за период"},
    {"key": "broadcasts", "title": "Рассылки",
     "label": "Рассылки",
     "hint": "аудитория, отправлено, не дошло, доставляемость"},
    {"key": "daily", "title": "По дням",
     "label": "Прирост по дням",
     "hint": "новые подписчики, входящие сообщения и клики за каждый день"},
]
SHEET_KEYS = [s["key"] for s in SHEETS]
SHEET_TITLE = {s["key"]: s["title"] for s in SHEETS}


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0


def _node_label(ntype: str, data: dict) -> str:
    if ntype == "message":
        t = (data.get("text") or "").strip().replace("\n", " ")
        return t[:60] or "сообщение без текста"
    if ntype == "delay":
        unit = {"seconds": "сек", "minutes": "мин", "hours": "ч", "days": "дн"}.get(
            data.get("unit"), "")
        return f"задержка {data.get('amount')} {unit}"
    if ntype == "condition":
        return "условие по тегу"
    if ntype == "filter":
        n = len((data.get("filter") or {}).get("conditions") or [])
        return f"фильтр по {n} услови{'ю' if n == 1 else 'ям'}"
    if ntype == "language":
        return "развилка по языку: " + (", ".join(map(str, data.get("languages") or [])) or "?")
    if ntype == "action":
        return "проставить/снять тег"
    return ntype


def _ordered_nodes(graph: dict):
    """Шаги воронки по порядку обхода от старта — как их проходит подписчик.

    Граф хранится в скомпилированном виде: у каждого узла outputs = {порт: [id, ...]}.
    """
    nodes = (graph or {}).get("nodes") or {}
    start = graph.get("start") or next(
        (nid for nid, n in nodes.items() if n.get("type") == "start"), None)

    order, seen = [], set()
    queue = [str(start)] if start else list(nodes)
    while queue:
        nid = str(queue.pop(0))
        if nid in seen or nid not in nodes:
            continue
        seen.add(nid)
        n = nodes[nid]
        if n.get("type") not in ("start", "note"):
            order.append((nid, n))
        for targets in (n.get("outputs") or {}).values():
            queue.extend(str(t) for t in targets)

    # узлы, до которых обход не дошёл (не подключены) — в конец списка
    for nid, n in nodes.items():
        if nid not in seen and n.get("type") not in ("start", "note"):
            order.append((nid, n))
    return order


async def funnels_rows(session, allowed_bots=None) -> list[list]:
    fq = select(Funnel).order_by(Funnel.name)
    if allowed_bots is not None:
        fq = fq.where(Funnel.id.in_(
            select(FunnelBot.funnel_id).where(FunnelBot.bot_id.in_(allowed_bots or [-1]))))
    funnels = (await session.execute(fq)).scalars().all()

    bot_names = dict((await session.execute(select(Bot.id, Bot.name))).all())
    fb = (await session.execute(select(FunnelBot.funnel_id, FunnelBot.bot_id))).all()
    bots_of: dict[int, list] = {}
    for fid, bid in fb:
        bots_of.setdefault(fid, []).append(bot_names.get(bid, str(bid)))

    rows = [["Воронка", "Боты", "Статус", "№", "Шаг", "Тип",
             "Дошло человек", "% от входа", "% от предыдущего"]]
    for f in funnels:
        entered = (await session.execute(
            select(func.count(func.distinct(FunnelRun.subscriber_id)))
            .where(FunnelRun.funnel_id == f.id)
        )).scalar() or 0
        visits = dict((await session.execute(
            select(NodeVisit.node_id, func.count(func.distinct(NodeVisit.subscriber_id)))
            .where(NodeVisit.funnel_id == f.id).group_by(NodeVisit.node_id)
        )).all())

        bots = ", ".join(bots_of.get(f.id, [])) or "не назначена"
        status = "включена" if f.is_active else "выключена"
        rows.append([f.name, bots, status, 0, "Вошли в воронку", "вход",
                     entered, 100 if entered else 0, ""])
        prev = entered
        for i, (nid, n) in enumerate(_ordered_nodes(f.graph), start=1):
            count = int(visits.get(nid, 0))
            rows.append([
                f.name, bots, status, i,
                _node_label(n.get("type", ""), n.get("data") or {}),
                n.get("type", ""),
                count, _pct(count, entered), _pct(count, prev),
            ])
            prev = count or prev
    return rows


async def buttons_rows(session, allowed_bots=None) -> list[list]:
    fq = select(Funnel).order_by(Funnel.name)
    if allowed_bots is not None:
        fq = fq.where(Funnel.id.in_(
            select(FunnelBot.funnel_id).where(FunnelBot.bot_id.in_(allowed_bots or [-1]))))
    funnels = (await session.execute(fq)).scalars().all()

    rows = [["Воронка", "Сообщение", "Кнопка", "Кликов", "Дошло до сообщения", "CTR %"]]
    for f in funnels:
        clicks = (await session.execute(
            select(ButtonClick.node_id, ButtonClick.button_index, func.count(ButtonClick.id))
            .where(ButtonClick.funnel_id == f.id)
            .group_by(ButtonClick.node_id, ButtonClick.button_index)
        )).all()
        if not clicks:
            continue
        visits = dict((await session.execute(
            select(NodeVisit.node_id, func.count(func.distinct(NodeVisit.subscriber_id)))
            .where(NodeVisit.funnel_id == f.id).group_by(NodeVisit.node_id)
        )).all())
        nodes = (f.graph or {}).get("nodes") or {}
        for node_id, idx, n_clicks in sorted(clicks, key=lambda x: (str(x[0]), x[1])):
            node = nodes.get(str(node_id), {})
            data = node.get("data") or {}
            buttons = data.get("buttons") or []
            label = (buttons[idx].get("label") if idx < len(buttons) else None) or f"кнопка {idx + 1}"
            seen = int(visits.get(str(node_id), 0))
            rows.append([f.name, _node_label(node.get("type", "message"), data),
                         label, n_clicks, seen, _pct(n_clicks, seen)])
    return rows


async def bots_rows(session, days: int, allowed_bots=None) -> list[list]:
    since = datetime.utcnow() - timedelta(days=days)
    bq = select(Bot).order_by(Bot.name)
    if allowed_bots is not None:
        bq = bq.where(Bot.id.in_(allowed_bots or [-1]))
    bots = (await session.execute(bq)).scalars().all()

    rows = [["Бот", "Всего подписчиков", "Активных", "Заблокировали",
             f"Новых за {days} дн", "Активны за 7 дней"]]
    week = datetime.utcnow() - timedelta(days=7)
    for b in bots:
        def q(*cond):
            return select(func.count(Subscriber.id)).where(Subscriber.bot_id == b.id, *cond)

        total = (await session.execute(q())).scalar() or 0
        active = (await session.execute(q(Subscriber.is_active == True))).scalar() or 0  # noqa: E712
        new = (await session.execute(q(Subscriber.created_at >= since))).scalar() or 0
        alive = (await session.execute(q(Subscriber.last_active_at >= week))).scalar() or 0
        rows.append([b.name, total, active, total - active, new, alive])
    return rows


async def broadcasts_rows(session, allowed_bots=None) -> list[list]:
    q = select(Broadcast).order_by(Broadcast.created_at.desc())
    if allowed_bots is not None:
        q = q.where(Broadcast.bot_id.in_(allowed_bots or [-1]))
    bcs = (await session.execute(q)).scalars().all()
    bot_names = dict((await session.execute(select(Bot.id, Bot.name))).all())

    rows = [["Дата", "Название", "Бот", "Статус", "Аудитория",
             "Отправлено", "Не дошло", "Доставляемость %", "Текст"]]
    for b in bcs:
        rows.append([
            b.created_at.strftime("%Y-%m-%d %H:%M"), b.name,
            bot_names.get(b.bot_id, "—"), b.status,
            b.total, b.sent, b.failed, _pct(b.sent, b.total),
            (b.text or "")[:200],
        ])
    return rows


async def daily_rows(session, days: int, allowed_bots=None) -> list[list]:
    from . import analytics as an

    data = await an.build_analytics(session, days=days, allowed_bots=allowed_bots)
    rows = [["Дата", "Новых подписчиков", "Всего накопительно",
             "Входящих сообщений", "Кликов по кнопкам"]]
    s = data["series"]
    for i, day in enumerate(data["days"]):
        rows.append([day, s["new_subscribers"][i], s["cumulative_subscribers"][i],
                     s["incoming_messages"][i], s["button_clicks"][i]])
    return rows


async def build_tabs(session, selected: dict, days: int = 30, allowed_bots=None) -> dict:
    """Собирает выбранные листы. selected: {"funnels": True, ...}"""
    out = {}
    if selected.get("funnels"):
        out[SHEET_TITLE["funnels"]] = await funnels_rows(session, allowed_bots)
    if selected.get("buttons"):
        out[SHEET_TITLE["buttons"]] = await buttons_rows(session, allowed_bots)
    if selected.get("bots"):
        out[SHEET_TITLE["bots"]] = await bots_rows(session, days, allowed_bots)
    if selected.get("broadcasts"):
        out[SHEET_TITLE["broadcasts"]] = await broadcasts_rows(session, allowed_bots)
    if selected.get("daily"):
        out[SHEET_TITLE["daily"]] = await daily_rows(session, days, allowed_bots)

    # служебный лист: когда обновляли — чтобы было видно свежесть цифр
    out["Обновлено"] = [
        ["Последнее обновление", datetime.utcnow().strftime("%Y-%m-%d %H:%M") + " UTC"],
        ["Период отчётов", f"{days} дней"],
    ]
    return out
