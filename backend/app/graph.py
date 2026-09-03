"""Компиляция экспорта Drawflow в граф для движка.

Соглашение по портам:
  start:     output_1 -> дальше
  message:   output_1 -> "далее" (сразу после отправки),
             output_{i+2} -> кнопка i (0-индекс)
  delay:     output_1 -> дальше (после паузы)
  condition: output_1 -> да (тег есть), output_2 -> нет
  action:    output_1 -> дальше
  language:  output_1 -> «остальные» (язык не совпал),
             output_{i+2} -> ветка языка i (0-индекс) — как кнопки у message
  filter:    output_1 -> подходит, output_2 -> не подходит
"""

NODE_TYPES = {"start", "message", "delay", "condition", "action", "note",
              "language", "filter"}


class GraphError(Exception):
    pass


def compile_graph(drawflow_export: dict) -> dict:
    try:
        raw = drawflow_export["drawflow"]["Home"]["data"]
    except (KeyError, TypeError):
        raise GraphError("Пустой или некорректный граф")

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
        elif n["type"] == "delay":
            try:
                amount = float(d.get("amount", 0))
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                raise GraphError(f"Блок «Задержка» ({nid}): укажите время > 0")
            if d.get("unit") not in ("seconds", "minutes", "hours", "days"):
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
        elif n["type"] == "action":
            if d.get("op") not in ("add_tag", "remove_tag"):
                raise GraphError(f"Блок «Действие» ({nid}): некорректная операция")
            if not d.get("tag"):
                raise GraphError(f"Блок «Действие» ({nid}): не выбран тег")


def next_node(graph: dict, node_id: str, port: str = "output_1") -> str | None:
    node = graph["nodes"].get(str(node_id))
    if not node:
        return None
    targets = node["outputs"].get(port) or []
    return targets[0] if targets else None
