"""Импорт базы подписчиков из CSV.

Зачем это вообще нужно. Telegram не даёт боту список тех, кто на него подписан:
бот узнаёт о человеке только когда тот сам пишет или нажимает кнопку. Метода
«отдай всех подписчиков» в Bot API нет и никогда не было. Поэтому при переезде
с другого сервиса единственный способ не потерять базу — перенести её файлом.

Главное, что переносится, — telegram_id. Всё остальное (имя, юзернейм, теги,
даты) полезно, но без id человек для бота не существует.

ВАЖНО про доставку. Импорт кладёт людей в нашу базу, но не создаёт отношений
с ботом. Писать можно только тем, кто когда-то сам начал диалог именно с ЭТИМ
ботом. Переносите базу вместе с токеном того же бота — тогда всё дойдёт.
Если бот новый, Telegram ответит «Forbidden» на каждого, и рассылка честно
пометит их заблокировавшими.
"""
import csv
import io
import logging
from datetime import datetime

from datetime import datetime as _dt

from sqlalchemy import insert, select

from .models import Subscriber, SubscriberTag, Tag

log = logging.getLogger("sendbot.import")

# Сколько строк держим в памяти между сбросами в базу. На 400 тысячах строк
# построчная запись заняла бы часы, а разом всё не влезет.
CHUNK = 1000

# Заголовки колонок. Ключ — наше поле, значения — как эту колонку могут
# назвать. Сравнение идёт по «сплющенному» имени: без пробелов, подчёркиваний
# и регистра, поэтому «telegram_id», «Telegram ID» и «TelegramId» — одно и то же.
COLUMNS = {
    "tg_id": ["telegramid", "tgid", "userid", "chatid", "id"],
    "username": ["username", "login", "nickname"],
    "full_name": ["fullname", "name"],
    "first_name": ["firstname"],
    "last_name": ["lastname", "surname"],
    "language_code": ["language", "languagecode", "lang", "язык"],
    "status": ["status", "статус"],
    "last_active_at": ["lastactivityat", "lastactivity", "lastseen"],
    "created_at": ["subscribedat", "subscribed", "createdat", "signupdate"],
    "tags": ["tags", "теги"],
    "source": ["source", "campaign", "utmsource", "источник"],
}

# Колонки, которые не ложатся в наши поля, но выбрасывать их жалко:
# складываем в params, чтобы остались следом от прежнего сервиса.
KEEP_AS_PARAMS = ["operator", "campaign", "id", "bot", "phone", "email"]


def _flat(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def map_columns(header: list) -> dict:
    """{наше поле: номер колонки}. Неизвестные колонки просто игнорируются."""
    flat = [_flat(h) for h in header]
    out = {}
    for field, aliases in COLUMNS.items():
        for alias in aliases:
            if alias in flat:
                idx = flat.index(alias)
                # «id» — самый общий псевдоним: отдаём его под tg_id только
                # если отдельной колонки telegram_id в файле нет
                if field == "tg_id" and alias == "id" and any(
                        a in flat for a in ("telegramid", "tgid", "userid", "chatid")):
                    continue
                out[field] = idx
                break
    return out


DATE_FORMATS = [
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y",
]


def parse_date(value):
    """Дата в любом из ходовых форматов. Не разобралась — None, не выдумываем."""
    txt = str(value or "").strip().replace("Z", "")
    if not txt:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(txt[:19], fmt)
        except ValueError:
            continue
    return None


def split_name(full: str):
    """«Vadim // Head Of SMM» -> («Vadim // Head Of SMM», None).

    Резать по первому пробелу нельзя: в выгрузках в этом поле лежит что угодно —
    должность, эмодзи, два имени. Имя оставляем целиком, фамилию берём только
    когда это явно «Имя Фамилия» из двух простых слов.
    """
    txt = " ".join(str(full or "").split())
    if not txt:
        return None, None
    parts = txt.split(" ")
    if len(parts) == 2 and all(p.isalpha() for p in parts):
        return parts[0], parts[1]
    return txt, None


def parse_rows(blob: bytes) -> tuple[list, list, dict]:
    """-> (строки, проблемы, карта колонок). Ничего не пишет в базу."""
    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = blob.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Не удалось прочитать файл: неизвестная кодировка")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel   # одна колонка или необычный файл — читаем как есть

    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("Файл пустой")

    cols = map_columns(header)
    if "tg_id" not in cols:
        raise ValueError(
            "В файле нет колонки с Telegram ID. Нужна колонка «telegram_id» "
            "(или «tg_id», «user_id», «chat_id») — без неё бот не знает, "
            "кому писать.")

    extra = {_flat(h): i for i, h in enumerate(header) if _flat(h) in KEEP_AS_PARAMS}
    rows, problems, seen = [], [], set()

    for n, raw in enumerate(reader, start=2):
        if not any(str(c).strip() for c in raw):
            continue
        def cell(field):
            i = cols.get(field)
            return str(raw[i]).strip() if i is not None and i < len(raw) else ""

        tg_raw = cell("tg_id")
        try:
            tg_id = int(float(tg_raw))          # бывает «6255797660.0» из Excel
        except (TypeError, ValueError):
            problems.append(f"строка {n}: «{tg_raw[:40]}» — это не Telegram ID")
            continue
        if tg_id <= 0:
            problems.append(f"строка {n}: некорректный Telegram ID {tg_id}")
            continue
        if tg_id in seen:
            problems.append(f"строка {n}: Telegram ID {tg_id} уже был в файле")
            continue
        seen.add(tg_id)

        first, last = None, None
        if cols.get("first_name") is not None:
            first, last = cell("first_name") or None, cell("last_name") or None
        if not first:
            first, last2 = split_name(cell("full_name"))
            last = last or last2

        status = cell("status").lower()
        params = {}
        for key, i in extra.items():
            val = str(raw[i]).strip() if i < len(raw) else ""
            if val:
                params["import_" + key] = val[:200]

        rows.append({
            "tg_id": tg_id,
            "username": (cell("username") or "").lstrip("@")[:64] or None,
            "first_name": (first or "")[:128] or None,
            "last_name": (last or "")[:128] or None,
            "language_code": (cell("language_code") or "").lower()[:16] or None,
            # пусто в колонке статуса считаем «активен»: в выгрузках её часто
            # просто нет, и объявлять всех заблокировавшими нельзя
            "is_active": status in ("", "active", "активен", "1", "true", "subscribed"),
            "last_active_at": parse_date(cell("last_active_at")),
            "created_at": parse_date(cell("created_at")),
            "tags": [t.strip()[:64] for t in cell("tags").split(",") if t.strip()],
            "source": (cell("source") or "")[:128] or None,
            "params": params,
        })

    return rows, problems, cols


async def apply_rows(session, bot_id: int, rows: list, update_existing: bool) -> dict:
    """Записать разобранные строки. -> {added, updated, skipped, tags}."""
    added = updated = skipped = 0

    # теги создаём заранее, одним заходом: иначе на каждой строке был бы
    # отдельный поход в базу
    wanted = {t for r in rows for t in r["tags"]}
    tag_ids = {}
    if wanted:
        have = {t.name: t.id for t in (await session.execute(
            select(Tag).where(Tag.name.in_(wanted)))).scalars()}
        for name in sorted(wanted):
            if name not in have:
                tag = Tag(name=name)
                session.add(tag)
                await session.flush()
                have[name] = tag.id
        tag_ids = have

    for start in range(0, len(rows), CHUNK):
        chunk = rows[start:start + CHUNK]
        ids = [r["tg_id"] for r in chunk]
        existing = {s.tg_id: s for s in (await session.execute(
            select(Subscriber).where(
                Subscriber.bot_id == bot_id, Subscriber.tg_id.in_(ids))
        )).scalars()}

        # Новых вставляем пакетом, а не объектами по одному: на 400 тысячах
        # разница между «минуты» и «часы». ORM оставляем только для обновления
        # существующих — там нужна поштучная логика «не затирать живое».
        to_insert, touched = [], []
        now = _dt.utcnow()
        for r in chunk:
            sub = existing.get(r["tg_id"])
            if sub is None:
                to_insert.append({
                    "bot_id": bot_id, "tg_id": r["tg_id"],
                    "username": r["username"], "first_name": r["first_name"],
                    "last_name": r["last_name"], "language_code": r["language_code"],
                    "is_active": r["is_active"], "is_subscribed": True,
                    "last_active_at": r["last_active_at"],
                    "source": r["source"], "first_source": r["source"],
                    "params": r["params"], "created_at": r["created_at"] or now,
                })
                added += 1
                continue

            if not update_existing:
                skipped += 1
                continue
            # Живую запись не портим: дозаполняем пустое, но не затираем то,
            # что бот узнал сам. Его данные свежее любой выгрузки.
            for field in ("username", "first_name", "last_name", "language_code"):
                if not getattr(sub, field) and r[field]:
                    setattr(sub, field, r[field])
            if r["last_active_at"] and (
                    not sub.last_active_at or sub.last_active_at < r["last_active_at"]):
                sub.last_active_at = r["last_active_at"]
            if r["params"]:
                sub.params = {**(sub.params or {}), **r["params"]}
            updated += 1
            touched.append((sub.id, r))

        if to_insert:
            await session.execute(insert(Subscriber), to_insert)
        await session.flush()

        # теги вешаем только тем строкам, которые реально трогали
        if tag_ids:
            if to_insert:
                fresh = dict((tg, sid) for tg, sid in (await session.execute(
                    select(Subscriber.tg_id, Subscriber.id).where(
                        Subscriber.bot_id == bot_id,
                        Subscriber.tg_id.in_([d["tg_id"] for d in to_insert]))
                )).all())
                by_tg = {r["tg_id"]: r for r in chunk}
                touched += [(sid, by_tg[tg]) for tg, sid in fresh.items() if tg in by_tg]

            sub_ids = [sid for sid, _ in touched]
            links = []
            if sub_ids:
                already = {(st.subscriber_id, st.tag_id) for st in (await session.execute(
                    select(SubscriberTag).where(SubscriberTag.subscriber_id.in_(sub_ids))
                )).scalars()}
                for sid, r in touched:
                    for name in r["tags"]:
                        tid = tag_ids.get(name)
                        if tid and (sid, tid) not in already:
                            links.append({"subscriber_id": sid, "tag_id": tid})
                            already.add((sid, tid))
            if links:
                await session.execute(insert(SubscriberTag), links)
                await session.flush()

    log.info("Импорт в бота #%s: добавлено %s, обновлено %s, пропущено %s",
             bot_id, added, updated, skipped)
    return {"added": added, "updated": updated, "skipped": skipped,
            "tags": sorted(tag_ids)}
