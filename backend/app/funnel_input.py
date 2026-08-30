"""Недельные конверсии по шагам — выгрузка в чужой лист (08B_FUNNEL_INPUT).

Чем это отличается от обычной выгрузки (exports.py + sheets.py): там лист
целиком наш, и мы каждый раз затираем его заново. Здесь лист чужой — в нём
есть шапка, выпадающие списки, колонки, которые заполняются руками
(Site transitions, Custom metrics) и формулы, которые на всё это смотрят.

Поэтому пишем точечно:
  * колонки ищем по названиям в шапке, а не по буквам — если таблицу
    перекроят, выгрузка не начнёт писать мимо;
  * строку ищем по ключу «неделя + бот/воронка»: повторный запуск обновляет
    ту же строку, а не плодит дубли;
  * трогаем только свои ячейки — Site transitions и Custom metrics остаются
    как есть.

Одна строка = неделя (с понедельника, UTC) × бот × воронка.
Шагами считаются узлы «Сообщение» по порядку обхода воронки от старта.
"""
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote as _urlquote

import aiohttp
from sqlalchemy import func, select

from . import exports
from . import sheets as gs
from .logging_setup import event_logger
from .models import Bot, Funnel, FunnelBot, NodeVisit, Subscriber
from .sheets import SheetsError

log = event_logger()

# в листе колонки Step 1 users … Step 25 users
MAX_STEPS = 25

# сколько строк листа читаем, чтобы найти существующие записи
SCAN_ROWS = 5000


# ---------- недели ----------

def monday(d: date) -> date:
    """Понедельник той недели, в которую попадает дата."""
    return d - timedelta(days=d.weekday())


def week_bounds(start: date) -> tuple[datetime, datetime]:
    s = datetime(start.year, start.month, start.day)
    return s, s + timedelta(days=7)


def weeks_since(start_week: str | None) -> list[tuple[datetime, datetime]]:
    """Недели от start_week до текущей включительно.

    start_week пустой — значит выгрузку ещё ни разу не запускали: берём
    только текущую неделю. Историю не восстанавливаем задним числом.
    """
    cur = monday(datetime.utcnow().date())
    first = cur
    if start_week:
        try:
            first = monday(date.fromisoformat(str(start_week)[:10]))
        except ValueError:
            first = cur
    if first > cur:
        first = cur
    out, d = [], first
    while d <= cur:
        out.append(week_bounds(d))
        d += timedelta(days=7)
    return out


# ---------- сбор данных ----------

def funnel_steps(graph: dict) -> list[tuple[str, dict]]:
    """Шаги воронки: узлы «Сообщение» по порядку обхода, не больше 25."""
    return [(nid, n) for nid, n in exports._ordered_nodes(graph or {})
            if n.get("type") == "message"][:MAX_STEPS]


async def collect_rows(session, weeks, allowed_bots=None) -> list[dict]:
    """Строки для листа: [{week, label, steps: [n, n, ...], bot, funnel}]."""
    bq = select(Bot).order_by(Bot.id)
    if allowed_bots is not None:
        bq = bq.where(Bot.id.in_(allowed_bots or [-1]))
    bots = (await session.execute(bq)).scalars().all()

    links = (await session.execute(select(FunnelBot.bot_id, FunnelBot.funnel_id))).all()
    funnels_of: dict[int, list[int]] = {}
    for bid, fid in links:
        funnels_of.setdefault(bid, []).append(fid)
    funnels = {f.id: f for f in
               (await session.execute(select(Funnel))).scalars().all()}

    out: list[dict] = []
    for b in bots:
        fids = [f for f in funnels_of.get(b.id, []) if f in funnels]
        if not fids:
            continue
        # На бота приходится одна воронка — так устроена работа, и так же
        # устроена таблица: в колонке «Funnel / Bot» стоит юзернейм бота,
        # по нему же строки сходятся с рекламной частью (08_CAMPAIGNS).
        # Если воронок вдруг окажется несколько, берём одну — включённую и
        # самую свежую, — иначе две строки за неделю легли бы в одну ячейку.
        label = f"@{b.tg_username}" if b.tg_username else b.name

        chosen = sorted(
            (funnels[fid] for fid in fids),
            key=lambda f: (f.is_active, f.updated_at or f.created_at),
            reverse=True)
        if len(chosen) > 1:
            log.warning(
                "У бота %s несколько воронок (%s) — в таблицу пойдёт «%s»",
                label, len(chosen), chosen[0].name)

        for f in chosen[:1]:
            steps = funnel_steps(f.graph)
            if not steps:
                continue

            for ws, we in weeks:
                counts = dict((await session.execute(
                    select(NodeVisit.node_id,
                           func.count(func.distinct(NodeVisit.subscriber_id)))
                    .join(Subscriber, Subscriber.id == NodeVisit.subscriber_id)
                    .where(NodeVisit.funnel_id == f.id,
                           Subscriber.bot_id == b.id,
                           NodeVisit.created_at >= ws,
                           NodeVisit.created_at < we)
                    .group_by(NodeVisit.node_id)
                )).all())
                values = [int(counts.get(nid, 0)) for nid, _ in steps]
                if not any(values):
                    continue          # пустую неделю в таблицу не пишем
                out.append({
                    "week": ws.strftime("%Y-%m-%d"),
                    "label": label,
                    "steps": values,
                    "bot": label,
                    "funnel": f.name,
                })
    return out


# ---------- адреса ячеек ----------

def _col_letter(i: int) -> str:
    """0 → A, 25 → Z, 26 → AA."""
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _quote_title(name: str) -> str:
    return "'" + str(name).replace("'", "''") + "'"


def _norm(s) -> str:
    """«Funnel / Bot» → funnelbot; сравнивать заголовки как есть ненадёжно."""
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


_EPOCH = date(1899, 12, 30)   # нулевой день в Google Таблицах


def _as_date_str(v) -> str:
    """Значение ячейки с датой → YYYY-MM-DD.

    Даты приходят числом (порядковый день), если в ячейке настоящая дата,
    и строкой, если её вбили текстом — второй случай тоже надо понимать,
    иначе ключ не совпадёт и мы задублируем строку.
    """
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)):
        try:
            return (_EPOCH + timedelta(days=int(v))).strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            return ""
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})", s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return s


def _runs(cells: dict) -> list[tuple[int, list]]:
    """Соседние колонки — одним диапазоном, чтобы не слать по ячейке."""
    out: list[list] = []
    for c in sorted(cells):
        if out and c == out[-1][0] + len(out[-1][1]):
            out[-1][1].append(cells[c])
        else:
            out.append([c, [cells[c]]])
    return [(c, v) for c, v in out]


# ---------- запись в лист ----------

async def write_rows(info: dict, spreadsheet_id: str, sheet_name: str,
                     rows: list[dict]) -> dict:
    """Пишет строки в лист, обновляя существующие. Возвращает статистику."""
    if not spreadsheet_id:
        raise SheetsError("Не указана таблица")
    if not sheet_name:
        raise SheetsError("Не указано название листа")

    token = await gs.get_access_token(info)
    email = info.get("client_email", "")
    read_range = _urlquote(f"{_quote_title(sheet_name)}!A1:BZ{SCAN_ROWS}", safe="")

    async with aiohttp.ClientSession() as s:
        status, body = await gs._request(
            s, "GET",
            f"{gs.API}/{spreadsheet_id}/values/{read_range}"
            "?valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption=SERIAL_NUMBER",
            token)
        if status == 400:
            raise SheetsError(
                f"В таблице нет листа «{sheet_name}». Проверьте название — "
                "оно должно совпадать с ярлыком внизу таблицы буква в букву.")
        if status != 200:
            raise SheetsError(gs._explain(status, body, spreadsheet_id, email))

        values = body.get("values") or []

        # 1. шапка: ищем строку, в которой есть «Week start»
        hdr_i = None
        for i, row in enumerate(values[:20]):
            if any(_norm(c) == "weekstart" for c in row):
                hdr_i = i
                break
        if hdr_i is None:
            raise SheetsError(
                f"В листе «{sheet_name}» не нашлась строка заголовков — "
                "нужна колонка «Week start».")

        idx = {}
        for j, c in enumerate(values[hdr_i]):
            key = _norm(c)
            if key and key not in idx:
                idx[key] = j

        c_week = idx.get("weekstart")
        c_label = idx.get("funnelbot")
        if c_label is None:
            raise SheetsError("Не нашлась колонка «Funnel / Bot».")

        step_cols = []
        for n in range(1, MAX_STEPS + 1):
            j = idx.get(f"step{n}users")
            if j is None:
                break
            step_cols.append(j)
        if not step_cols:
            raise SheetsError("Не нашлись колонки «Step 1 users», «Step 2 users» …")

        # 2. что в листе уже есть
        existing: dict[tuple, int] = {}
        blanks: dict[str, list[int]] = {}   # заготовленные строки без недели
        last_used = hdr_i
        for i in range(hdr_i + 1, len(values)):
            row = values[i]
            if not any(str(c).strip() for c in row):
                continue
            last_used = i
            label = str(row[c_label]).strip() if c_label < len(row) else ""
            if not label:
                continue
            week = _as_date_str(row[c_week]) if (
                c_week is not None and c_week < len(row)) else ""
            if week:
                existing.setdefault((week, label), i)
            else:
                blanks.setdefault(label, []).append(i)

        # 3. каждой строке — своё место
        plan: list[tuple[int, dict]] = []
        next_free = last_used + 1
        updated = appended = 0
        for r in rows:
            key = (r["week"], r["label"])
            i = existing.get(key)
            if i is not None:
                updated += 1
            else:
                pool = blanks.get(r["label"])
                if pool:
                    i = pool.pop(0)     # занимаем заготовленную пустую строку
                    updated += 1
                else:
                    i = next_free
                    next_free += 1
                    appended += 1
                existing[key] = i

            cells = {c_label: r["label"]}
            if c_week is not None:
                cells[c_week] = r["week"]
            for col, val in zip(step_cols, r["steps"]):
                cells[col] = val
            plan.append((i, cells))

        if not plan:
            return {"rows": 0, "updated": 0, "appended": 0,
                    "sheet": sheet_name, "steps": len(step_cols)}

        # 4. одна пачка на всё
        data = []
        for i, cells in plan:
            for start, vals in _runs(cells):
                a1 = (f"{_quote_title(sheet_name)}!"
                      f"{_col_letter(start)}{i + 1}:"
                      f"{_col_letter(start + len(vals) - 1)}{i + 1}")
                data.append({"range": a1, "values": [vals]})

        status, body = await gs._request(
            s, "POST", f"{gs.API}/{spreadsheet_id}/values:batchUpdate", token,
            json={"valueInputOption": "USER_ENTERED", "data": data})
        if status != 200:
            raise SheetsError(gs._explain(status, body, spreadsheet_id, email))

    return {"rows": len(plan), "updated": updated, "appended": appended,
            "sheet": sheet_name, "steps": len(step_cols)}
