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
     {"field": "subscribed",    "op": "equals",                  "value": "yes"|"no"},
     {"field": "source",        "op": "equals"|"contains",       "value": "текст"},
     {"field": "in_funnel",     "op": "yes"|"no",                "value": <funnel_id>},
     {"field": "in_broadcast",  "op": "yes"|"no",                "value": <broadcast_id>},
     {"field": "signup",        "op": "after"|"before"|"last_days", "value": "2026-01-01"|N},
     {"field": "last_activity", "op": "after"|"before"|"last_days"|"inactive_days", "value": ...},
     # Три условия про момент срабатывания, а не про подписчика: они одинаково
     # верны или неверны для всех сразу. Живут только в ноде «Фильтр» —
     # в рассылке их смысл разъезжается (см. NODE_ONLY ниже).
     {"field": "weekday",   "op": "in"|"not_in",              "value": "1,2,3"},
     {"field": "run_date",  "op": "after"|"before"|"on",      "value": "2026-10-15"},
     {"field": "run_time",  "op": "between"|"after"|"before", "value": "10:00-18:00"}
  ]
}
Плюс шорткат "active_24h": True (был активен за последние сутки).
"""
from datetime import datetime, timedelta

from sqlalchemy import and_, false, or_, select, true

from .models import (
    Broadcast,
    BroadcastRecipient,
    Funnel,
    FunnelRun,
    Subscriber,
    SubscriberTag,
    Tag,
)


# Языки для выпадающего списка. Коды — те, что реально присылает Telegram
# в language_code (IETF: «ru», «pt-br», «zh-hans»). Сначала то, чем пользуются
# чаще всего, дальше по алфавиту: список из семидесяти строк иначе
# невозможно глазами разобрать.
LANGUAGES = [
    ("ru", "Русский"), ("en", "Английский"), ("uk", "Украинский"),
    ("es", "Испанский"), ("pt", "Португальский"), ("pt-br", "Португальский (Бразилия)"),
    ("de", "Немецкий"), ("fr", "Французский"), ("it", "Итальянский"),
    ("pl", "Польский"), ("tr", "Турецкий"), ("ar", "Арабский"),
    ("az", "Азербайджанский"), ("sq", "Албанский"), ("am", "Амхарский"),
    ("hy", "Армянский"), ("eu", "Баскский"), ("be", "Белорусский"),
    ("bn", "Бенгальский"), ("my", "Бирманский"), ("bg", "Болгарский"),
    ("bs", "Боснийский"), ("cy", "Валлийский"), ("hu", "Венгерский"),
    ("vi", "Вьетнамский"), ("el", "Греческий"), ("ka", "Грузинский"),
    ("gu", "Гуджарати"), ("da", "Датский"), ("he", "Иврит"),
    ("id", "Индонезийский"), ("ga", "Ирландский"), ("is", "Исландский"),
    ("kk", "Казахский"), ("kn", "Каннада"), ("ca", "Каталанский"),
    ("ky", "Киргизский"), ("zh-hans", "Китайский (упрощённый)"),
    ("zh-hant", "Китайский (традиционный)"), ("ko", "Корейский"),
    ("km", "Кхмерский"), ("lv", "Латышский"), ("lt", "Литовский"),
    ("mk", "Македонский"), ("ms", "Малайский"), ("ml", "Малаялам"),
    ("mr", "Маратхи"), ("mn", "Монгольский"), ("ne", "Непальский"),
    ("nl", "Нидерландский"), ("no", "Норвежский"), ("pa", "Панджаби"),
    ("fa", "Персидский"), ("ro", "Румынский"), ("sr", "Сербский"),
    ("si", "Сингальский"), ("sk", "Словацкий"), ("sl", "Словенский"),
    ("sw", "Суахили"), ("ta", "Тамильский"), ("te", "Телугу"),
    ("th", "Тайский"), ("ur", "Урду"), ("uz", "Узбекский"),
    ("fil", "Филиппинский"), ("fi", "Финский"), ("hr", "Хорватский"),
    ("hi", "Хинди"), ("cs", "Чешский"), ("sv", "Шведский"),
    ("et", "Эстонский"), ("af", "Африкаанс"), ("ja", "Японский"),
]
LANGUAGE_NAME = dict(LANGUAGES)


def language_label(code) -> str:
    """«ru» -> «Русский (ru)». Незнакомый код показываем как есть."""
    c = str(code or "").strip()
    name = LANGUAGE_NAME.get(c.lower())
    return f"{name} ({c})" if name else c


class SegmentError(Exception):
    pass


# Поля про «сейчас». В сегменте рассылки им не место: аудитория считается
# один раз, а рассылка на 400 тысяч идёт около часа и запрашивает базу
# страницами. Условие «время 10:00–18:00» посреди отправки перевернулось бы,
# и часть людей молча осталась бы без письма — а рассылка отчиталась бы
# «готово». В ноде «Фильтр» проверка мгновенная, поэтому там всё честно.
NODE_ONLY = {"weekday", "run_date", "run_time"}

WEEKDAY_NAMES = {1: "понедельник", 2: "вторник", 3: "среда", 4: "четверг",
                 5: "пятница", 6: "суббота", 7: "воскресенье"}


def _parse_time(v) -> int:
    """«14:30» -> минуты от полуночи."""
    txt = str(v or "").strip()
    try:
        hh, mm = txt.split(":")
        h, m = int(hh), int(mm)
    except ValueError:
        raise SegmentError(f"Некорректное время: {v} (нужно ЧЧ:ММ)")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise SegmentError(f"Некорректное время: {v}")
    return h * 60 + m


def _weekday_list(v) -> list[int]:
    if isinstance(v, (list, tuple)):
        raw = list(v)
    else:
        raw = [x for x in str(v or "").split(",")]
    days = []
    for x in raw:
        x = str(x).strip()
        if not x:
            continue
        try:
            d = int(x)
        except ValueError:
            raise SegmentError(f"Некорректный день недели: {x}")
        if not 1 <= d <= 7:
            raise SegmentError(f"Некорректный день недели: {x}")
        days.append(d)
    if not days:
        raise SegmentError("Выберите хотя бы один день недели")
    return days


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
        code = str(val or "").strip().lower()
        if "-" in code:
            # код с регионом — только точное совпадение: «pt-br» это именно
            # бразильский, а не любой португальский
            match = Subscriber.language_code == code
        else:
            # код без региона — он сам и все его региональные варианты.
            # Выбрали «Португальский» — подходят и «pt», и «pt-br»; иначе
            # фильтр молча пропускал бы половину аудитории. Так же
            # сопоставляет языки и развилка в блоке «Язык».
            match = ((Subscriber.language_code == code)
                     | (Subscriber.language_code.like(code + "-%")))
        if op == "not_equals":
            return ~match | (Subscriber.language_code.is_(None))
        return match

    if field == "status":
        return Subscriber.is_active == (val == "active")

    if field == "subscribed":
        # у старых записей колонки не было -> NULL, и это «подписан»
        if val == "no":
            return Subscriber.is_subscribed.is_(False)
        return (Subscriber.is_subscribed.is_(True)) | (Subscriber.is_subscribed.is_(None))

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

    # ---- условия про момент срабатывания ----
    # Они не про подписчика: либо верны для всех сразу, либо ни для кого.
    # Поэтому и превращаются в true()/false() — в SQL идти не за чем.
    if field in NODE_ONLY:
        from . import tz

        now = tz.now()
        if field == "weekday":
            days = _weekday_list(val)
            hit = now.isoweekday() in days
            return true() if (hit if op != "not_in" else not hit) else false()

        if field == "run_date":
            today = now.date()
            other = _parse_date(val).date()
            hit = today == other if op == "on" else (
                today >= other if op == "after" else today <= other)
            return true() if hit else false()

        if field == "run_time":
            mins = now.hour * 60 + now.minute
            if op == "between":
                a, _, b = str(val or "").partition("-")
                start, end = _parse_time(a), _parse_time(b)
                # окно через полночь («22:00-06:00») — это два куска суток
                hit = start <= mins <= end if start <= end else (mins >= start or mins <= end)
            else:
                point = _parse_time(val)
                hit = mins >= point if op == "after" else mins <= point
            return true() if hit else false()

    raise SegmentError(f"Неизвестное поле фильтра: {field}")


async def matches(session, sub_id: int, filt: dict) -> bool:
    """Подходит ли конкретный подписчик под фильтр.

    Тот же движок, что в рассылках и в разделе подписчиков: условия в ноде
    «Фильтр» и в сегменте рассылки значат одно и то же, и добавленное поле
    сразу доступно везде. Разрешены и условия про момент срабатывания —
    здесь это ровно текущая секунда, а не растянутая на час рассылка.
    """
    if not (filt or {}).get("conditions") and not (filt or {}).get("active_24h"):
        return True   # пустой фильтр никого не отсеивает
    q = build_query(None, filt, allow_now=True).where(Subscriber.id == sub_id).limit(1)
    return (await session.execute(q)).first() is not None


def build_query(bot_id: int | None, filt: dict, allowed_bot_ids: list[int] | None = None,
                allow_now: bool = False):
    """Возвращает select(Subscriber) с применённым сегментом.

    allowed_bot_ids — жёсткое ограничение по правам пользователя: даже если
    в запросе просят другого бота, чужие подписчики не попадут в выборку.

    allow_now — пускать ли условия про момент срабатывания. По умолчанию нет:
    их зовёт нода «Фильтр», а в рассылке они врали бы (см. NODE_ONLY).
    """
    q = select(Subscriber)
    if allowed_bot_ids is not None:
        q = q.where(Subscriber.bot_id.in_([int(x) for x in allowed_bot_ids] or [-1]))
    if bot_id:
        q = q.where(Subscriber.bot_id == bot_id)
    if not filt:
        return q
    conds = []
    if filt.get("active_24h"):
        conds.append(_cond({"field": "active_24h"}))
    for c in filt.get("conditions", []):
        if c.get("field") in NODE_ONLY and not allow_now:
            raise SegmentError(
                f"Условие «{_FIELD_LABEL.get(c['field'], c['field'])}» работает "
                "только в блоке «Фильтр» внутри воронки: рассылка идёт долго, "
                "и посреди неё такое условие перевернулось бы.")
        if c.get("field") and c.get("op") is not None:
            if c.get("value") in (None, "") and c["field"] not in (
                "status", "subscribed", "active_24h"
            ) and c["op"] not in ("yes", "no"):
                if c["field"] in NODE_ONLY:
                    raise SegmentError(
                        f"Условие «{_FIELD_LABEL.get(c['field'], c['field'])}»: "
                        "не заполнено значение")
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
        {"key": "language", "label": "Язык", "type": "choice",
         "options": [{"v": code, "l": name} for code, name in LANGUAGES],
         "ops": [["equals", "="], ["not_equals", "≠"]]},
        {"key": "status", "label": "Статус", "type": "choice",
         "options": [{"v": "active", "l": "активен"}, {"v": "blocked", "l": "заблокировал"}],
         "ops": [["equals", "="]]},
        {"key": "subscribed", "label": "Подписан на рассылки", "type": "choice",
         "options": [{"v": "yes", "l": "да"}, {"v": "no", "l": "отписался"}],
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
        # node_only — условия про момент срабатывания; в сегменте рассылки
        # они не показываются, там их смысл разъезжается (см. NODE_ONLY)
        {"key": "weekday", "label": "День недели", "type": "weekdays", "node_only": True,
         "options": [{"v": d, "l": WEEKDAY_NAMES[d][:2].capitalize()} for d in range(1, 8)],
         "ops": [["in", "один из"], ["not_in", "кроме"]]},
        {"key": "run_date", "label": "Дата срабатывания", "type": "date", "node_only": True,
         "ops": [["on", "в этот день"], ["after", "начиная с"], ["before", "по"]]},
        {"key": "run_time", "label": "Время срабатывания", "type": "time", "node_only": True,
         "ops": [["between", "в промежутке"], ["after", "после"], ["before", "до"]]},
    ]


# ---------- человекочитаемое описание фильтра ----------

_FIELD_LABEL = {
    "tag": "тег", "name": "имя", "username": "@username", "language": "язык",
    "status": "статус", "subscribed": "подписан на рассылки",
    "source": "источник (deep-link)",
    "in_funnel": "был в воронке", "in_broadcast": "был в рассылке",
    "signup": "дата подписки", "last_activity": "последняя активность",
    "weekday": "день недели", "run_date": "дата срабатывания",
    "run_time": "время срабатывания",
}
_OP_LABEL = {
    "has": "есть", "not_has": "нет", "contains": "содержит", "equals": "=",
    "not_equals": "≠", "yes": "да", "no": "нет", "after": "после",
    "before": "до", "last_days": "за последние N дней",
    "inactive_days": "неактивен N дней",
    "in": "один из", "not_in": "кроме", "on": "в этот день",
    "between": "в промежутке",
}
_STATUS_LABEL = {"active": "активен", "blocked": "заблокировал"}


def describe(filt: dict, tag_names: dict, funnel_names: dict, bc_names: dict) -> list[str]:
    """Разбирает фильтр сегмента в список строк вида «тег: есть VIP»."""
    if not filt:
        return []
    out = []
    for c in filt.get("conditions") or []:
        field = c.get("field")
        op = c.get("op")
        val = c.get("value")
        if field == "tag":
            val = tag_names.get(int(val), f"#{val}") if str(val).isdigit() else val
        elif field == "in_funnel":
            val = funnel_names.get(int(val), f"#{val}") if str(val).isdigit() else val
        elif field == "in_broadcast":
            val = bc_names.get(int(val), f"#{val}") if str(val).isdigit() else val
        elif field == "status":
            val = _STATUS_LABEL.get(val, val)
        elif field == "subscribed":
            val = {"yes": "да", "no": "отписался"}.get(val, val)
        elif field == "language":
            val = language_label(val)
        elif field == "weekday":
            try:
                val = ", ".join(WEEKDAY_NAMES[d] for d in _weekday_list(val))
            except SegmentError:
                pass
        elif field == "run_time" and op == "between":
            val = str(val).replace("-", " – ")
        label = _FIELD_LABEL.get(field, field)
        op_l = _OP_LABEL.get(op, op)
        out.append(f"{label}: {op_l} {val}".strip() if val not in (None, "") else f"{label}: {op_l}")
    if filt.get("active_24h"):
        out.append("активен за последние 24 часа")
    return out
