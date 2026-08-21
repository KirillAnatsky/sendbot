"""Сегментация подписчиков: сборка SQLAlchemy-запроса из JSON-фильтра.

Формат фильтра (как в конструкторе админки):
{
  "match": "all" | "any",           # И / ИЛИ между условиями
  "conditions": [
     {"field": "tag",           "op": "has"|"not_has",           "value": <tag_id>},
     {"field": "name",          "op": "contains"|"equals",       "value": "текст"},
     {"field": "username",      "op": "contains"|"equals",       "value": "текст"},
     {"field": "language",      "op": "equals"|"not_equals",     "value": "ru"},
     {"field": "status",        "op": "equals",                  "value": "active"|"blocked"},
     {"field": "source",        "op": "equals"|"contains",       "value": "текст"},
     {"field": "in_funnel",     "op": "yes"|"no",                "value": <funnel_id>},
     {"field": "in_broadcast",  "op": "yes"|"no",                "value": <broadcast_id>},
     {"field": "signup",        "op": "after"|"before"|"last_days", "value": "2026-01-01"|N},
     {"field": "last_activity", "op": "after"|"before"|"last_days"|"inactive_days", "value": ...}
  ]
}
Плюс шорткат "active_24h": True (был активен за последние сутки).
"""
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select

from .models import (
    Broadcast,
    BroadcastRecipient,
    Funnel,
    FunnelRun,
    Subscriber,
    SubscriberTag,
    Tag,
)


class SegmentError(Exception):
    pass


def _parse_date(v):
    if isinstance(v, (int, float)):
        return datetime.utcfromtimestamp(v)
    try:
        return datetime.fromisoformat(str(v)[:19].replace("Z", ""))
    except ValueError:
        raise SegmentError(f"Некорректная дата: {v}")


def _cond(c):
    field = c.get("field")
    op = c.get("op")
    val = c.get("value")

    if field == "tag":
        sub = select(SubscriberTag.subscriber_id).where(SubscriberTag.tag_id == int(val))
        return Subscriber.id.in_(sub) if op == "has" else Subscriber.id.notin_(sub)

    if field == "name":
        if op == "equals":
            return (Subscriber.first_name == val) | (Subscriber.last_name == val)
        like = f"%{val}%"
        return Subscriber.first_name.ilike(like) | Subscriber.last_name.ilike(like)

    if field == "username":
        if op == "equals":
            return Subscriber.username == str(val).lstrip("@")
        return Subscriber.username.ilike(f"%{str(val).lstrip('@')}%")

    if field == "language":
        if op == "not_equals":
            return (Subscriber.language_code != val) | (Subscriber.language_code.is_(None))
        return Subscriber.language_code == val

    if field == "status":
        return Subscriber.is_active == (val == "active")

    if field == "source":
        if op == "equals":
            return Subscriber.source == val
        return Subscriber.source.ilike(f"%{val}%")

    if field == "in_funnel":
        sub = select(FunnelRun.subscriber_id).where(FunnelRun.funnel_id == int(val))
        return Subscriber.id.in_(sub) if op == "yes" else Subscriber.id.notin_(sub)

    if field == "in_broadcast":
        sub = select(BroadcastRecipient.subscriber_id).where(
            BroadcastRecipient.broadcast_id == int(val)
        )
        return Subscriber.id.in_(sub) if op == "yes" else Subscriber.id.notin_(sub)

    if field == "signup":
        col = Subscriber.created_at
        if op == "last_days":
            return col >= datetime.utcnow() - timedelta(days=int(val))
        return col >= _parse_date(val) if op == "after" else col <= _parse_date(val)

    if field == "last_activity":
        col = Subscriber.last_active_at
        if op == "last_days":
            return col >= datetime.utcnow() - timedelta(days=int(val))
        if op == "inactive_days":
            return (col < datetime.utcnow() - timedelta(days=int(val))) | (col.is_(None))
        return col >= _parse_date(val) if op == "after" else col <= _parse_date(val)

    if field == "active_24h":
        return Subscriber.last_active_at >= datetime.utcnow() - timedelta(hours=24)

    raise SegmentError(f"Неизвестное поле фильтра: {field}")


def build_query(bot_id: int | None, filt: dict):
    """Возвращает select(Subscriber) с применённым сегментом."""
    q = select(Subscriber)
    if bot_id:
        q = q.where(Subscriber.bot_id == bot_id)
    if not filt:
        return q
    conds = []
    if filt.get("active_24h"):
        conds.append(_cond({"field": "active_24h"}))
    for c in filt.get("conditions", []):
        if c.get("field") and c.get("op") is not None:
            if c.get("value") in (None, "") and c["field"] not in (
                "status", "active_24h"
            ) and c["op"] not in ("yes", "no"):
                continue  # пустое значение — пропускаем условие
            conds.append(_cond(c))
    if not conds:
        return q
    combiner = or_ if filt.get("match") == "any" else and_
    return q.where(combiner(*conds))


# описание полей и операторов для конструктора в админке
def fields_meta(tags, funnels, broadcasts):
    return [
        {"key": "tag", "label": "Тег", "type": "select", "options": tags,
         "ops": [["has", "есть"], ["not_has", "нет"]]},
        {"key": "name", "label": "Имя", "type": "text",
         "ops": [["contains", "содержит"], ["equals", "равно"]]},
        {"key": "username", "label": "@username", "type": "text",
         "ops": [["contains", "содержит"], ["equals", "равно"]]},
        {"key": "language", "label": "Язык (код, напр. ru)", "type": "text",
         "ops": [["equals", "="], ["not_equals", "≠"]]},
        {"key": "status", "label": "Статус", "type": "choice",
         "options": [{"v": "active", "l": "активен"}, {"v": "blocked", "l": "заблокировал"}],
         "ops": [["equals", "="]]},
        {"key": "source", "label": "Источник (deep-link)", "type": "text",
         "ops": [["equals", "="], ["contains", "содержит"]]},
        {"key": "in_funnel", "label": "Был в воронке", "type": "select", "options": funnels,
         "ops": [["yes", "да"], ["no", "нет"]]},
        {"key": "in_broadcast", "label": "Был в рассылке", "type": "select", "options": broadcasts,
         "ops": [["yes", "да"], ["no", "нет"]]},
        {"key": "signup", "label": "Дата подписки", "type": "date",
         "ops": [["after", "после"], ["before", "до"], ["last_days", "за последние N дней"]]},
        {"key": "last_activity", "label": "Последняя активность", "type": "date",
         "ops": [["after", "после"], ["before", "до"],
                 ["last_days", "за последние N дней"], ["inactive_days", "неактивен N дней"]]},
    ]
