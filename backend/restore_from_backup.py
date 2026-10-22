"""Достать воронку из бэкапа базы и положить её в «🕘 История» редактора.

Ничего не перезаписывает: из бэкапа создаётся ВЕРСИЯ воронки. Дальше в
редакторе открываешь «🕘 История» → строка «🗄 из бэкапа» → «Вернуть».

На сервере (из /opt/sendbot):

  # какие воронки есть в бэкапе и сколько в них вложений
  gunzip -c backups/predeploy-20261021-1530.sql.gz | \\
    docker compose -f docker-compose.prod.yml exec -T app python restore_from_backup.py -

  # положить воронку №5 из этого бэкапа в историю версий
  gunzip -c backups/predeploy-20261021-1530.sql.gz | \\
    docker compose -f docker-compose.prod.yml exec -T app python restore_from_backup.py - 5

Чтобы найти бэкап, где картинки ещё были, смотри колонку «вложений»:
список бэкапов — `ls -lt backups/`.
"""
import asyncio
import gzip
import json
import re
import sys

_ESC = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\"}


def _unescape(field: str):
    """Поле формата COPY ... FROM stdin (текстовый формат pg_dump)."""
    if field == r"\N":
        return None
    out, i = [], 0
    while i < len(field):
        c = field[i]
        if c != "\\" or i + 1 >= len(field):
            out.append(c)
            i += 1
            continue
        nxt = field[i + 1]
        if nxt in _ESC:
            out.append(_ESC[nxt])
            i += 2
        elif nxt.isdigit():          # восьмеричный код \NNN
            m = re.match(r"[0-7]{1,3}", field[i + 1:])
            out.append(chr(int(m.group(0), 8)))
            i += 1 + len(m.group(0))
        else:
            out.append(nxt)
            i += 2
    return "".join(out)


def read_funnels(lines) -> list[dict]:
    """Строки таблицы funnels из дампа."""
    cols, rows, inside = None, [], False
    for line in lines:
        line = line.rstrip("\n")
        if not inside:
            m = re.match(r"COPY (?:public\.)?funnels \((.+)\) FROM stdin;", line)
            if m:
                cols = [c.strip().strip('"') for c in m.group(1).split(",")]
                inside = True
            continue
        if line == r"\.":
            break
        vals = [_unescape(v) for v in line.split("\t")]
        rows.append(dict(zip(cols, vals)))
    for r in rows:
        for k in ("graph_ui", "graph"):
            try:
                r[k] = json.loads(r.get(k) or "{}")
            except ValueError:
                r[k] = {}
    return rows


def media_total(graph: dict) -> int:
    n = 0
    for node in (graph.get("nodes") or {}).values():
        if node.get("type") == "message":
            d = node.get("data") or {}
            n += len(d.get("media") or []) + (1 if d.get("photo_url") else 0)
    return n


def _open(path: str):
    if path == "-":
        return sys.stdin
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


async def add_version(row: dict, label: str) -> int:
    from app.db import SessionLocal, init_db
    from app.models import Funnel, FunnelVersion

    await init_db()
    async with SessionLocal() as s:
        fid = int(row["id"])
        if await s.get(Funnel, fid) is None:
            raise SystemExit(f"Воронки #{fid} в текущей базе нет — восстанавливать некуда.")
        v = FunnelVersion(
            funnel_id=fid, name=row.get("name") or "", graph_ui=row["graph_ui"], graph=row["graph"],
            trigger_type=row.get("trigger_type") or "start", trigger_value=row.get("trigger_value"),
            source="backup", note=label[:200])
        s.add(v)
        await s.commit()
        return v.id


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    src = sys.argv[1]
    with _open(src) as fh:
        rows = read_funnels(fh)
    if not rows:
        raise SystemExit("В дампе нет таблицы funnels")
    if len(sys.argv) == 2:
        print(f"{'id':>4}  {'вложений':>8}  {'блоков':>6}  изменена             название")
        for r in sorted(rows, key=lambda r: int(r["id"])):
            blocks = max(len(r["graph"].get("nodes") or {}) - 1, 0)
            print(f"{r['id']:>4}  {media_total(r['graph']):>8}  {blocks:>6}  "
                  f"{(r.get('updated_at') or '')[:19]:<19}  {r.get('name')}")
        return
    fid = sys.argv[2]
    row = next((r for r in rows if str(r["id"]) == str(fid)), None)
    if row is None:
        raise SystemExit(f"Воронки #{fid} в этом бэкапе нет")
    label = f"из бэкапа ({(row.get('updated_at') or '')[:16]}), вложений: {media_total(row['graph'])}"
    vid = asyncio.run(add_version(row, label))
    print(f"Готово: версия #{vid} — {label}. Редактор → «🕘 История» → «Вернуть».")


if __name__ == "__main__":
    main()
