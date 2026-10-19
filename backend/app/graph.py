"""Компиляция экспорта Drawflow в граф для движка.

Соглашение по портам:
  start:     output_1 -> дальше
  message:   output_1 -> "далее" (сразу после отправки),
             output_{i+2} -> кнопка i (0-индекс, считая и кнопки-ссылки:
                             у ссылки ветка срабатывает, когда по ней перешли),
             output_{N+2} -> «нет реакции» (N = число кнопок; только если
                             в блоке включён таймер бездействия no_action)
  delay:     output_1 -> дальше (после паузы)
  condition: output_1 -> да (тег есть), output_2 -> нет
  action:    output_1 -> дальше
             (кроме «проверка подписки»: output_1 -> подписан, output_2 -> нет)
  language:  output_1 -> «остальные» (язык не совпал),
             output_{i+2} -> ветка языка i (0-индекс) — как кнопки у message
  filter:    output_1 -> подходит, output_2 -> не подходит
  chain:     output_1 -> дальше (после того, как цепочка пройдена целиком)
"""

NODE_TYPES = {"start", "message", "delay", "condition", "action", "note",
              "language", "filter", "chain"}

# Операции блока «Действие». Первые две — исторические (раньше блок так
# и назывался «Тег»), поэтому старые воронки продолжают работать без миграции.
ACTION_OPS = ("add_tag", "remove_tag", "unsubscribe", "check_subscription",
              "delete_message")


UNITS = ("seconds", "minutes", "hours", "days")

# Версия нумерации выходов «Сообщения». До v2 порт был только у кнопок без
# ссылки и шёл подряд (2, 3, …), а движок считал номер по ВСЕМ кнопкам —
# кнопка после ссылки уходила в пустой выход. С v2 порт есть у каждой кнопки:
# output_{i+2}, где i — её место в списке.
PORTS_VERSION = 2


class GraphError(Exception):
    pass


def no_action_port(data: dict) -> str | None:
    """Выход «нет реакции» узла «Сообщение», если таймер включён."""
    if not (data or {}).get("no_action"):
        return None
    return f"output_{len(data.get('buttons') or []) + 2}"


def _old_message_port_map(buttons: list) -> dict:
    """Старый номер выхода -> новый для узла, сохранённого до v2."""
    out, k = {}, 2
    for i, b in enumerate(buttons or []):
        if isinstance(b, dict) and b.get("url"):
            continue
        if f"output_{k}" != f"output_{i + 2}":
            out[f"output_{k}"] = f"output_{i + 2}"
        k += 1
    return out


def _port_no(port: str) -> int:
    try:
        return int(str(port).rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return 0


def upgrade_ports(drawflow_export: dict) -> bool:
    """Перевести узлы «Сообщение» на нумерацию выходов v2 (на месте).

    Связи переезжают вместе с кнопками, так что воронка ведёт туда же,
    куда вела на холсте. Узлы, уже помеченные ports_v=2, не трогаем —
    функцию безопасно звать сколько угодно раз. True — что-то поменялось.
    """
    try:
        data = drawflow_export["drawflow"]["Home"]["data"]
    except (KeyError, TypeError):
        return False
    changed, remaps = False, {}
    for nid, n in data.items():
        if not isinstance(n, dict) or n.get("name") != "message":
            continue
        d = n.get("data")
        if not isinstance(d, dict):
            d = n["data"] = {}
        if d.get("ports_v") == PORTS_VERSION:
            continue
        buttons = d.get("buttons") or []
        pm = _old_message_port_map(buttons)
        outs = {pm.get(port, port): pdata for port, pdata in (n.get("outputs") or {}).items()}
        for k in range(1, len(buttons) + 2):
            outs.setdefault(f"output_{k}", {"connections": []})
        n["outputs"] = dict(sorted(outs.items(), key=lambda kv: _port_no(kv[0])))
        d["ports_v"] = PORTS_VERSION
        if pm:
            remaps[str(nid)] = pm
        changed = True
    if remaps:
        # Drawflow хранит связь с двух концов: у входа цели тоже записан порт
        for n in data.values():
            for inp in ((n or {}).get("inputs") or {}).values():
                for c in inp.get("connections", []):
                    pm = remaps.get(str(c.get("node")))
                    if pm and c.get("input") in pm:
                        c["input"] = pm[c["input"]]
    return changed


def upgrade_compiled_ports(graph: dict) -> bool:
    """То же для уже скомпилированного графа движка."""
    changed = False
    for n in ((graph or {}).get("nodes") or {}).values():
        if n.get("type") != "message":
            continue
        d = n.setdefault("data", {})
        if d.get("ports_v") == PORTS_VERSION:
            continue
        pm = _old_message_port_map(d.get("buttons") or [])
        n["outputs"] = {pm.get(p, p): t for p, t in (n.get("outputs") or {}).items()}
        d["ports_v"] = PORTS_VERSION
        changed = True
    return changed


def compile_graph(drawflow_export: dict) -> dict:
    try:
        raw = drawflow_export["drawflow"]["Home"]["data"]
    except (KeyError, TypeError):
        raise GraphError("Пустой или некорректный граф")
    # воронки и черновики, сохранённые до v2, переводим на новую нумерацию
    upgrade_ports(drawflow_export)

    nodes = {}
    start_id = None
    for nid, n in raw.items():
        ntype = n.get("name")
        if ntype not in NODE_TYPES:
            raise GraphError(f"Неизвестный тип блока: {ntype}")
        outputs = {}
        for port, pdata in (n.get("outputs") or {}).items():
            targets = [str(c["node"]) for c in pdata.get("connections", [])]
            if targets:
                outputs[port] = targets
        nodes[str(nid)] = {
            "type": ntype,
            "data": n.get("data") or {},
            "outputs": outputs,
        }
        if ntype == "start":
            if start_id is not None:
                raise GraphError("В воронке должен быть ровно один блок «Старт»")
            start_id = str(nid)

    if start_id is None:
        raise GraphError("Добавьте блок «Старт»")

    _validate(nodes)
    return {"nodes": nodes, "start": start_id}


def _validate(nodes: dict):
    for nid, n in nodes.items():
        d = n["data"]
        if n["type"] == "note":
            continue  # заметки не исполняются
        if n["type"] == "message":
            has_text = bool((d.get("text") or "").strip())
            has_media = bool(d.get("media") or d.get("photo_url"))
            has_buttons = bool(d.get("buttons"))
            if not (has_text or has_media or has_buttons):
                raise GraphError(f"Блок «Сообщение» ({nid}): добавьте текст, вложение или кнопку")
            na = d.get("no_action")
            if na:
                try:
                    amount = float((na or {}).get("amount", 0))
                except (TypeError, ValueError, AttributeError):
                    amount = 0
                if amount <= 0:
                    raise GraphError(
                        f"Блок «Сообщение» ({nid}): укажите, сколько ждать реакции (> 0)")
                if na.get("unit") not in UNITS:
                    raise GraphError(
                        f"Блок «Сообщение» ({nid}): некорректная единица времени ожидания")
        elif n["type"] == "delay":
            try:
                amount = float(d.get("amount", 0))
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                raise GraphError(f"Блок «Задержка» ({nid}): укажите время > 0")
            if d.get("unit") not in UNITS:
                raise GraphError(f"Блок «Задержка» ({nid}): некорректная единица времени")
        elif n["type"] == "condition":
            if not d.get("tag"):
                raise GraphError(f"Блок «Условие» ({nid}): не выбран тег")
        elif n["type"] == "filter":
            f = d.get("filter") or {}
            if not f.get("conditions") and not f.get("active_24h"):
                raise GraphError(
                    f"Блок «Фильтр» ({nid}): добавьте хотя бы одно условие — "
                    "иначе он пропускает всех и ни на что не влияет")
        elif n["type"] == "language":
            langs = [str(x).strip() for x in (d.get("languages") or []) if str(x).strip()]
            if not langs:
                raise GraphError(f"Блок «Язык» ({nid}): добавьте хотя бы один язык")
        elif n["type"] == "chain":
            try:
                if int(d.get("funnel_id") or 0) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise GraphError(f"Блок «Цепочка» ({nid}): не выбрана цепочка")
        elif n["type"] == "action":
            op = d.get("op")
            if op not in ACTION_OPS:
                raise GraphError(f"Блок «Действие» ({nid}): некорректная операция")
            if op in ("add_tag", "remove_tag") and not d.get("tag"):
                raise GraphError(f"Блок «Действие» ({nid}): не выбран тег")
            if op == "check_subscription":
                channel = str(d.get("channel") or "").strip()
                if not channel:
                    raise GraphError(
                        f"Блок «Действие» ({nid}): укажите канал — @имя или числовой id")
                if not (channel.startswith("@") or channel.lstrip("-").isdigit()):
                    raise GraphError(
                        f"Блок «Действие» ({nid}): канал пишется как @имя "
                        f"или числовой id (-100…), а не «{channel}»")
            if op == "delete_message":
                target = str(d.get("target") or "").strip()
                if not target:
                    raise GraphError(
                        f"Блок «Действие» ({nid}): выберите, какое сообщение удалить")
                if target != "last" and nodes.get(target, {}).get("type") != "message":
                    raise GraphError(
                        f"Блок «Действие» ({nid}): блок, чьё сообщение нужно удалить, "
                        "больше не существует — выберите другой")


def chain_ids(graph: dict) -> list[int]:
    """Цепочки, которые вызывает этот граф (в порядке появления, без повторов)."""
    out = []
    for n in (graph.get("nodes") or {}).values():
        if n.get("type") != "chain":
            continue
        try:
            fid = int((n.get("data") or {}).get("funnel_id") or 0)
        except (TypeError, ValueError):
            continue
        if fid > 0 and fid not in out:
            out.append(fid)
    return out


def check_no_cycles(funnel_id: int, name: str, graph: dict, funnel_by_id) -> None:
    """Убедиться, что цепочки не зовут друг друга по кругу.

    Без этой проверки воронка, которая (через несколько цепочек) вызывает
    сама себя, сохранится молча, а поймает её подписчик — бесконечным потоком
    сообщений. Обычный обход в глубину: узел, который встретился повторно,
    пока мы ещё внутри него, и есть цикл. funnel_by_id(id) -> (имя, граф).
    """
    DONE, INSIDE = 1, 0
    state, path = {}, []

    def title(fid, fname):
        return f"«{fname}»" if fname else f"#{fid}"

    def visit(fid, fname, g):
        state[fid] = INSIDE
        path.append(title(fid, fname))
        for nxt in chain_ids(g):
            found = funnel_by_id(nxt)
            nxt_name = found[0] if found else None
            if state.get(nxt) == INSIDE:
                raise GraphError(
                    "Цепочки зациклены: "
                    + " → ".join(path + [title(nxt, nxt_name)])
                    + ". Такая воронка слала бы сообщения без остановки.")
            if state.get(nxt) == DONE or not found:
                state[nxt] = DONE
                continue
            visit(nxt, nxt_name, found[1])
        path.pop()
        state[fid] = DONE

    visit(funnel_id, name, graph)


def next_node(graph: dict, node_id: str, port: str = "output_1") -> str | None:
    node = graph["nodes"].get(str(node_id))
    if not node:
        return None
    targets = node["outputs"].get(port) or []
    return targets[0] if targets else None
