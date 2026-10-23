"""Точечные правки воронки от AI-помощника (операции над спекой).

Раньше модель на любую правку возвращала ВСЮ воронку заново. На большой
воронке ответ упирался в лимит длины: модель либо обрезала его (и шаги
пропадали), либо — что хуже — отказывалась работать и просила «режим
точечной правки». Здесь этот режим: модель шлёт список операций, сервер
применяет их к текущей спеке. Тяжёлую механику (скопировать ветку целиком,
переименовать теги, удалить ветку) делает сервер, а модель пишет только то,
что требует головы, — переведённые тексты.

Спека — то, что отдаёт ai.graph_to_spec: {"nodes": [{id, type, ...}]},
первый узел — вход воронки. Ссылки на другие узлы лежат в next / yes / no /
other / buttons[].next / languages[].next / no_action.next.
"""
import copy

# поля, в которых лежат переходы (target у delete_message и about у заметки —
# тоже ссылки на узлы, но не переходы: по ним ветку не обходим)
_JUMP_KEYS = ("next", "yes", "no", "other")


def _refs(node: dict):
    """Все переходы узла: (контейнер, ключ)."""
    for k in _JUMP_KEYS:
        if k in node:
            yield node, k
    for b in node.get("buttons") or []:
        if isinstance(b, dict) and "next" in b:
            yield b, "next"
    for lang in node.get("languages") or []:
        if isinstance(lang, dict) and "next" in lang:
            yield lang, "next"
    na = node.get("no_action")
    if isinstance(na, dict) and "next" in na:
        yield na, "next"


def _map_refs(node: dict, fn):
    for holder, key in _refs(node):
        if holder.get(key) is not None:
            holder[key] = fn(str(holder[key]))
    if node.get("type") == "action" and node.get("target") not in (None, "", "last"):
        node["target"] = fn(str(node["target"]))
    if node.get("type") == "note" and node.get("about"):
        node["about"] = fn(str(node["about"]))


def _successors(node: dict) -> list[str]:
    return [str(h[k]) for h, k in _refs(node) if h.get(k)]


def _index(spec: dict) -> dict:
    return {str(n.get("id")): n for n in spec.get("nodes") or []}


def reach(spec: dict, root: str, skip: str | None = None) -> list[str]:
    """Узлы, достижимые из root (по порядку обхода). skip — не заходить в него."""
    idx = _index(spec)
    out, seen, queue = [], set(), [str(root)]
    while queue:
        nid = queue.pop(0)
        if nid in seen or nid not in idx or nid == skip:
            continue
        seen.add(nid)
        out.append(nid)
        queue.extend(_successors(idx[nid]))
    return out


def _tags_of(node: dict) -> list:
    """Где в узле лежат имена тегов: (контейнер, ключ)."""
    found = []
    if node.get("type") in ("condition", "action") and node.get("tag"):
        found.append((node, "tag"))
    for c in node.get("conditions") or []:
        if isinstance(c, dict) and c.get("field") == "tag" and c.get("value"):
            found.append((c, "value"))
    return found


def _retag(node: dict, tag_map: dict, tag_prefix: str = "") -> int:
    n = 0
    for holder, key in _tags_of(node):
        old = str(holder[key])
        new = tag_map.get(old)
        if new is None and tag_prefix and not old.startswith(tag_prefix):
            new = tag_prefix + old
        if new and new != old:
            holder[key] = new
            n += 1
    return n


def drop_dangling(spec: dict) -> list[str]:
    """Ссылки на несуществующие узлы -> null. Возвращает, что пришлось обнулить."""
    ids = set(_index(spec))
    broken = []

    def fix(ref):
        if ref in ids:
            return ref
        broken.append(ref)
        return None
    for n in spec.get("nodes") or []:
        _map_refs(n, fix)
    return broken


def _merge_patch(node: dict, fields: dict):
    for k, v in (fields or {}).items():
        if k in ("id", "type"):
            continue
        if k == "buttons" and isinstance(v, list):
            # кнопки сливаем по порядку: перевод подписи не должен стирать
            # переход, ссылку или оформление кнопки
            old = node.setdefault("buttons", [])
            for i, b in enumerate(v):
                if not isinstance(b, dict):
                    b = {"label": str(b)}
                if i < len(old) and isinstance(old[i], dict):
                    old[i].update(b)
                else:
                    old.append(dict(b))
        elif isinstance(v, dict) and isinstance(node.get(k), dict) and k == "no_action":
            node[k].update(v)
        else:
            node[k] = v


def apply_ops(spec: dict, ops: list) -> dict:
    """Применить операции к спеке (на месте).

    -> {"created": [...], "changed": [...], "deleted": [...], "errors": [...]}
    Ошибочная операция пропускается и попадает в errors — остальные
    применяются: полработы лучше, чем ничего, а модель увидит, что не вышло.
    """
    log = {"created": [], "changed": [], "deleted": [], "errors": []}
    nodes = spec.setdefault("nodes", [])
    spec.pop("entry", None)   # вход — первый узел списка

    def idx():
        return _index(spec)

    def delete(ids):
        ids = {str(i) for i in ids}
        before = len(nodes)
        nodes[:] = [n for n in nodes if str(n.get("id")) not in ids]
        if len(nodes) != before:
            log["deleted"].extend(sorted(ids))

    for op in ops or []:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        try:
            if kind == "set":
                node = op.get("node") or {}
                nid = str(node.get("id") or "")
                if not nid or not node.get("type"):
                    raise ValueError("set: у узла нужны id и type")
                cur = idx().get(nid)
                if cur is None:
                    nodes.append(copy.deepcopy(node))
                    log["created"].append(nid)
                else:
                    nodes[nodes.index(cur)] = copy.deepcopy(node)
                    log["changed"].append(nid)

            elif kind == "patch":
                nid = str(op.get("id") or "")
                cur = idx().get(nid)
                if cur is None:
                    raise ValueError(f"patch: узла «{nid}» нет")
                _merge_patch(cur, op.get("fields") or {})
                log["changed"].append(nid)

            elif kind == "delete":
                ids = op.get("ids") or ([op["id"]] if op.get("id") else [])
                delete(ids)

            elif kind == "delete_branch":
                root = str(op.get("root") or "")
                if root not in idx():
                    raise ValueError(f"delete_branch: узла «{root}» нет")
                entry = str(nodes[0].get("id")) if nodes else None
                # удаляем только то, до чего нельзя дойти в обход этой ветки
                keep = set(reach(spec, entry, skip=root)) if entry and entry != root else set()
                doomed = [n for n in reach(spec, root) if n not in keep]
                doomed += [str(n.get("id")) for n in nodes
                           if n.get("type") == "note" and str(n.get("about")) in doomed]
                delete(doomed)

            elif kind == "copy_branch":
                root = str(op.get("root") or "")
                prefix = str(op.get("prefix") or "").strip()
                if root not in idx() or not prefix:
                    raise ValueError("copy_branch: нужны существующий root и prefix")
                stop = {str(s) for s in op.get("stop_at") or []}
                src_ids = [n for n in reach(spec, root) if n not in stop or n == root]
                # дальше стоп-узлов не идём: ссылки на них остаются на оригинал
                if stop:
                    allowed, queue = set(), [root]
                    index = idx()
                    while queue:
                        cur = queue.pop(0)
                        if cur in allowed or cur not in index:
                            continue
                        allowed.add(cur)
                        if cur in stop and cur != root:
                            continue
                        queue.extend(s for s in _successors(index[cur]) if s not in stop)
                    src_ids = [n for n in src_ids if n in allowed]
                index = idx()
                src_ids += [str(n.get("id")) for n in nodes
                            if n.get("type") == "note" and str(n.get("about")) in src_ids]
                new_id = {s: f"{prefix}_{s}" for s in src_ids}
                tag_map = {str(k): str(v) for k, v in (op.get("tag_map") or {}).items()}
                tag_prefix = str(op.get("tag_prefix") or "")
                for s in src_ids:
                    clone = copy.deepcopy(index[s])
                    clone["id"] = new_id[s]
                    _map_refs(clone, lambda r: new_id.get(r, r))
                    if tag_map or tag_prefix:
                        _retag(clone, tag_map, tag_prefix)
                    old = idx().get(clone["id"])
                    if old is not None:          # копия уже была — обновляем
                        nodes[nodes.index(old)] = clone
                        log["changed"].append(clone["id"])
                    else:
                        nodes.append(clone)
                        log["created"].append(clone["id"])

            elif kind == "retag":
                tag_map = {str(k): str(v) for k, v in (op.get("map") or {}).items()}
                if op.get("from"):
                    tag_map[str(op["from"])] = str(op.get("to") or "")
                tag_prefix = str(op.get("prefix") or "")
                scope = op.get("ids")
                if op.get("root"):
                    scope = reach(spec, str(op["root"]))
                scope = {str(s) for s in scope} if scope else None
                for n in nodes:
                    if scope is None or str(n.get("id")) in scope:
                        if _retag(n, tag_map, tag_prefix):
                            log["changed"].append(str(n.get("id")))

            elif kind == "entry":
                cur = idx().get(str(op.get("id") or ""))
                if cur is None:
                    raise ValueError("entry: такого узла нет")
                nodes.remove(cur)
                nodes.insert(0, cur)

            elif kind == "meta":
                for k in ("name", "trigger_type", "trigger_value"):
                    if k in op:
                        spec[k] = op[k]
            else:
                raise ValueError(f"неизвестная операция «{kind}»")
        except (ValueError, KeyError, TypeError) as e:
            log["errors"].append(str(e))

    for k in ("created", "changed", "deleted"):
        log[k] = list(dict.fromkeys(log[k]))
    log["changed"] = [c for c in log["changed"] if c not in log["created"]]
    return log


def summary(log: dict) -> str:
    """Что реально поменялось — пишется в ответ чата сервером, а не моделью."""
    parts = []
    if log.get("created"):
        parts.append(f"добавлено блоков: {len(log['created'])}")
    if log.get("changed"):
        parts.append(f"изменено: {len(log['changed'])}")
    if log.get("deleted"):
        parts.append(f"удалено: {len(log['deleted'])}")
    return ", ".join(parts)
