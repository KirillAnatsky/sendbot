"""ИИ-сборка воронки из текстового ТЗ (Claude / OpenAI).

Модель возвращает промежуточную JSON-спеку, которую мы конвертируем
в граф Drawflow (тот же формат, что редактор в админке).
"""
import json
import re

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .graph import GraphError, compile_graph
from .models import Setting, Tag

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4o",
}

SYSTEM_PROMPT = """Ты — конструктор воронок для телеграм-бота. По ТЗ пользователя собери воронку и верни ТОЛЬКО JSON без пояснений и без markdown-ограждений, по схеме:

{
  "name": "название воронки",
  "trigger_type": "start" | "keyword" | "tag_added",
  "trigger_value": null | "ключевое слово",
  "nodes": [
    {"id": "n1", "type": "message", "text": "текст (можно HTML <b></b> и {first_name})",
     "photo_url": null | "media/файл.png" | "https://...",
     "buttons": [
        {"label": "кнопка-ветвление", "next": "id-узла"},
        {"label": "кнопка-ссылка", "url": "https://..."}
     ],
     "next": "id следующего узла или null"},
    {"id": "n2", "type": "delay", "amount": 1, "unit": "seconds|minutes|hours|days", "next": "n3"},
    {"id": "n3", "type": "condition", "tag": "имя-тега", "yes": "id или null", "no": "id или null"},
    {"id": "n4", "type": "action", "op": "add_tag|remove_tag", "tag": "имя-тега", "next": null},
    {"id": "n6", "type": "language", "languages": [{"code": "ru", "next": "id или null"}, {"code": "en", "next": "id или null"}], "other": "id или null — куда идут все остальные языки"},
    {"id": "n5", "type": "note", "text": "⚠️ предупреждение", "about": "id узла, к которому относится"}
  ]
}

Правила:
- Первый узел списка — точка входа.
- ТЕКСТЫ СООБЩЕНИЙ ПЕРЕНОСИ ИЗ ТЗ ДОСЛОВНО. Не перефразируй, не сокращай, не «улучшай». Убирай только служебные пометки самого ТЗ (номера этапов, комментарии для маркетолога).
- Сегментацию делай кнопками сообщения, каждая ветка сразу вешает тег через action.
- Задержки «на следующий день» = delay amount=1 unit=days.
- next: null — конец ветки.
- Теги называй короткими латинскими slug'ами.
- Если в ТЗ встречается маркер [КАРТИНКА: media/имя-файла] — поставь это значение в photo_url ближайшего сообщения.
- Узлы note используй для предупреждений о недостающем: в ТЗ упомянут скрин/картинка, но файла нет; нужна ссылка (регистрация, оплата, канал), а её нет; лид-магнит описан, но самого материала нет; неясное условие или сроки. Каждый note привязывай через "about". Ничего не выдумывай вместо недостающего — ставь note.
- МУЛЬТИЯЗЫЧНОСТЬ: для воронки на нескольких языках ставь узел language сразу после входа — он сам определяет язык Telegram-профиля, юзера спрашивать не надо. Каждая ветка ведёт в полную копию воронки на своём языке, "other" — в ветку языка по умолчанию (обычно английского). Коды языков — как в Telegram: ru, en, uk, de, es, pt. Кнопочный выбор языка делай только если в ТЗ это просят явно.
"""


# ---------- настройки ----------

AI_SETTINGS_KEY = "ai"


async def get_ai_settings(session: AsyncSession) -> dict:
    row = (
        await session.execute(select(Setting).where(Setting.key == AI_SETTINGS_KEY))
    ).scalar_one_or_none()
    return dict(row.value) if row else {}


async def save_ai_settings(session: AsyncSession, data: dict):
    row = (
        await session.execute(select(Setting).where(Setting.key == AI_SETTINGS_KEY))
    ).scalar_one_or_none()
    if row is None:
        row = Setting(key=AI_SETTINGS_KEY, value=data)
        session.add(row)
    else:
        row.value = data
    await session.flush()


# ---------- вызов LLM ----------

class AIError(Exception):
    pass


async def call_llm(provider: str, api_key: str, model: str, spec_text: str):
    """-> (text, input_tokens, output_tokens)"""
    timeout = aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        if provider == "anthropic":
            r = await http.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 8000,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": spec_text}],
                },
            )
            data = await r.json()
            if r.status != 200:
                raise AIError(data.get("error", {}).get("message", f"HTTP {r.status}"))
            text = "".join(b.get("text", "") for b in data.get("content", []))
            usage = data.get("usage", {})
            return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)

        elif provider == "openai":
            r = await http.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": spec_text},
                    ],
                },
            )
            data = await r.json()
            if r.status != 200:
                raise AIError(data.get("error", {}).get("message", f"HTTP {r.status}"))
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

        raise AIError(f"Неизвестный провайдер: {provider}")


def parse_llm_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(json)?\s*|\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise AIError("Модель не вернула JSON")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AIError(f"Невалидный JSON от модели: {e}")


# ---------- спека -> Drawflow ----------

async def ensure_tags(session: AsyncSession, spec: dict) -> dict:
    """Создаёт недостающие теги, возвращает {имя: id}."""
    names = set()
    for n in spec.get("nodes", []):
        if n.get("type") in ("condition", "action") and n.get("tag"):
            names.add(str(n["tag"]))
    existing = {
        t.name: t.id for t in (await session.execute(select(Tag))).scalars().all()
    }
    for name in names:
        if name not in existing:
            tag = Tag(name=name)
            session.add(tag)
            await session.flush()
            existing[name] = tag.id
    return existing


def _summary(ntype: str, d: dict, tag_names: dict) -> str:
    if ntype == "start":
        return "Точка входа"
    if ntype == "message":
        return (d.get("text") or "")[:500]
    if ntype == "delay":
        u = {"seconds": "сек", "minutes": "мин", "hours": "ч", "days": "дн"}.get(d.get("unit"), "?")
        return f"Ждать {d.get('amount')} {u}"
    if ntype == "condition":
        return f"Есть тег «{tag_names.get(str(d.get('tag')), '?')}»?"
    if ntype == "action":
        op = "Снять тег: " if d.get("op") == "remove_tag" else "Добавить тег: "
        return op + tag_names.get(str(d.get("tag")), "?")
    if ntype == "note":
        return (d.get("text") or "")[:500]
    if ntype == "language":
        return "Развилка по языку: " + (", ".join(map(str, d.get("languages") or [])) or "?")
    return ""


NODE_TITLES = {
    "start": "▶️ Старт", "message": "💬 Сообщение", "delay": "⏱ Задержка",
    "condition": "❓ Условие", "action": "⚡️ Действие", "note": "⚠️ Заметка",
    "language": "🌐 Язык", "filter": "🔎 Фильтр", "chain": "⛓ Цепочка",
}


_MEDIA_ICON = {"video": "🎬", "audio": "🎵", "voice": "🎤", "video_note": "⭕️", "document": "📎"}


def _html(ntype: str, d: dict, tag_names: dict) -> str:
    import html as h

    if ntype == "message":
        media = d.get("media") or ([{"type": "photo", "path": d["photo_url"]}] if d.get("photo_url") else [])
        media_html = ""
        if media:
            thumbs = []
            for m in media[:6]:
                if m.get("type") == "photo":
                    src = m["path"] if str(m.get("path", "")).startswith("http") else "/" + m.get("path", "")
                    thumbs.append(f'<img class="df-media-thumb" src="{h.escape(src)}" alt="">')
                else:
                    thumbs.append(f'<span class="df-media-thumb icon">{_MEDIA_ICON.get(m.get("type"), "📎")}</span>')
            media_html = '<div class="df-media">' + "".join(thumbs) + "</div>"
        text = (d.get("text") or "")[:500]
        toggle = (
            '<span class="df-toggle" onclick="toggleNodeExpand(event, this)">развернуть ▾</span>'
            if len(text) > 120 else ""
        )
        text_html = f'<div class="df-sub">{h.escape(text)}</div>{toggle}' if text else (
            "" if media else '<div class="df-sub empty">нет текста</div>')
        btns_html = ""
        buttons = d.get("buttons") or []
        if buttons:
            rows, port = [], 2
            for b in buttons:
                if b.get("url"):
                    rows.append(f'<div class="df-btn url">🔗 {h.escape(b.get("label", "ссылка"))}</div>')
                else:
                    rows.append(
                        f'<div class="df-btn"><span class="df-btn-port">{port}</span>{h.escape(b.get("label", "кнопка"))}</div>')
                    port += 1
            btns_html = '<div class="df-btns">' + "".join(rows) + "</div>"
        ports = "1: далее" if any(not b.get("url") for b in buttons) else ""
        return (
            f'<div class="df-title">{NODE_TITLES["message"]}</div>'
            f"{media_html}{text_html}{btns_html}"
            f'<div class="df-ports">{ports}</div>'
        )

    if ntype == "language":
        rows = "".join(
            f'<div class="df-btn"><span class="df-btn-port">{k + 2}</span>{h.escape(str(c))}</div>'
            for k, c in enumerate(d.get("languages") or []))
        return (
            f'<div class="df-title">{NODE_TITLES["language"]}</div>'
            f'<div class="df-btns">{rows}</div>'
            '<div class="df-ports">1: остальные</div>'
        )

    sum_ = _summary(ntype, d, tag_names)
    toggle = (
        '<span class="df-toggle" onclick="toggleNodeExpand(event, this)">развернуть ▾</span>'
        if len(sum_) > 120 else ""
    )
    return (
        f'<div class="df-title">{NODE_TITLES[ntype]}</div>'
        f'<div class="df-sub">{h.escape(sum_)}</div>{toggle}'
        '<div class="df-ports"></div>'
    )


def build_drawflow(spec: dict, tag_ids: dict) -> dict:
    """Конвертирует спеку в экспорт Drawflow (+ авторасстановка)."""
    nodes_spec = spec.get("nodes") or []
    if not nodes_spec:
        raise AIError("В спеке нет узлов")

    id_map = {}  # spec id -> numeric str
    for i, n in enumerate(nodes_spec):
        id_map[str(n["id"])] = str(i + 2)  # 1 занят под start
    tag_names = {str(v): k for k, v in tag_ids.items()}

    df = {}

    def add_conn(src, src_port, dst):
        df[src]["outputs"].setdefault(src_port, {"connections": []})
        df[src]["outputs"][src_port]["connections"].append({"node": dst, "output": "input_1"})
        df[dst]["inputs"].setdefault("input_1", {"connections": []})
        df[dst]["inputs"]["input_1"]["connections"].append({"node": src, "input": src_port})

    # стартовый узел
    df["1"] = {
        "id": 1, "name": "start", "data": {}, "class": "start",
        "html": _html("start", {}, tag_names),
        "inputs": {}, "outputs": {"output_1": {"connections": []}},
        "pos_x": 40, "pos_y": 60, "typenode": False,
    }

    for i, n in enumerate(nodes_spec):
        nid = id_map[str(n["id"])]
        ntype = n.get("type")
        if ntype not in ("message", "delay", "condition", "action", "note", "language"):
            raise AIError(f"Неизвестный тип узла: {ntype}")

        if ntype == "note":
            # about хранит ВНУТРЕННИЙ id узла (после маппинга), а не id из ТЗ
            about_raw = str(n.get("about") or "")
            data = {"text": n.get("text") or "", "about": id_map.get(about_raw, "")}
            n_out = 0
        elif ntype == "message":
            buttons = []
            for b in n.get("buttons") or []:
                btn = {"label": str(b.get("label", "Кнопка"))}
                if b.get("url"):
                    btn["url"] = b["url"]
                buttons.append(btn)
            data = {
                "text": n.get("text") or "",
                "photo_url": n.get("photo_url") or "",
                "buttons": buttons,
            }
            n_out = 1 + len([b for b in buttons if "url" not in b])
        elif ntype == "delay":
            data = {"amount": n.get("amount", 1), "unit": n.get("unit", "hours")}
            n_out = 1
        elif ntype == "condition":
            data = {"tag": str(tag_ids.get(str(n.get("tag")), ""))}
            n_out = 2
        elif ntype == "language":
            branches = n.get("languages") or []
            data = {"languages": [str(b.get("code", "")).strip() for b in branches]}
            n_out = 1 + len(branches)
        else:
            data = {
                "op": n.get("op", "add_tag"),
                "tag": str(tag_ids.get(str(n.get("tag")), "")),
            }
            n_out = 1

        df[nid] = {
            "id": int(nid), "name": ntype, "data": data, "class": ntype,
            "html": _html(ntype, data, tag_names),
            "inputs": {} if ntype == "note" else {"input_1": {"connections": []}},
            "outputs": {f"output_{k + 1}": {"connections": []} for k in range(n_out)},
            "pos_x": 0, "pos_y": 0, "typenode": False,
        }

    # связи
    add_conn("1", "output_1", id_map[str(nodes_spec[0]["id"])])
    for n in nodes_spec:
        nid = id_map[str(n["id"])]
        ntype = n["type"]

        def tgt(ref):
            if not ref:
                return None
            if str(ref) not in id_map:
                raise AIError(f"Узел «{n['id']}» ссылается на несуществующий «{ref}»")
            return id_map[str(ref)]

        if ntype == "condition":
            for port, ref in (("output_1", n.get("yes")), ("output_2", n.get("no"))):
                t = tgt(ref)
                if t:
                    add_conn(nid, port, t)
        elif ntype == "language":
            t = tgt(n.get("other"))
            if t:
                add_conn(nid, "output_1", t)
            for k, b in enumerate(n.get("languages") or []):
                t = tgt(b.get("next"))
                if t:
                    add_conn(nid, f"output_{k + 2}", t)
        else:
            t = tgt(n.get("next"))
            if t:
                add_conn(nid, "output_1", t)
            if ntype == "message":
                k = 2
                for b in n.get("buttons") or []:
                    if b.get("url"):
                        continue
                    t = tgt(b.get("next"))
                    if t:
                        add_conn(nid, f"output_{k}", t)
                    k += 1

    _layout(df)
    return {"drawflow": {"Home": {"data": df}}}


COL_W = 420   # шаг между колонками (> ширины карточки, чтобы не было наложений)


def _node_height(node: dict) -> int:
    """Оценка высоты карточки, чтобы карточки не налезали друг на друга."""
    text = ""
    if node["name"] in ("message", "note"):
        text = str(node["data"].get("text") or "")[:500]
    lines = 0
    for para in text.split("\n"):
        lines += max(1, (len(para) + 37) // 38)
    long_text = lines > 4
    lines = min(lines, 4)  # свёрнутая карточка показывает до 4 строк
    buttons = len(node["data"].get("buttons") or []) if node["name"] == "message" else 0
    h = 40 + lines * 19 + 20            # заголовок + строки текста + падинги
    if long_text:
        h += 22                        # строка «развернуть»
    h += max(buttons - 1, 0) * 15
    return h


def _layout(df: dict):
    """ВЕРТИКАЛЬНАЯ раскладка: путь идёт сверху вниз (глубина BFS -> строка),
    параллельные ветки (например, языковые) — колонками слева направо.
    Заметка встаёт в строку своего блока (data.about) справа."""
    flow = {nid: n for nid, n in df.items() if n["name"] != "note"}

    depth = {"1": 0}
    queue = ["1"]
    while queue:
        cur = queue.pop(0)
        for pdata in df[cur]["outputs"].values():
            for c in pdata["connections"]:
                t = str(c["node"])
                if t in flow and t not in depth:
                    depth[t] = depth[cur] + 1
                    queue.append(t)
    for nid in flow:
        depth.setdefault(nid, 1)

    notes_by_about: dict[str, list] = {}
    orphan_notes = []
    for nid, n in df.items():
        if n["name"] != "note":
            continue
        about = str(n["data"].get("about") or "")
        if about in flow:
            notes_by_about.setdefault(about, []).append(nid)
        else:
            orphan_notes.append(nid)

    # строки: depth -> [nid...] (заметки — сразу после своего блока в той же строке)
    rows: dict[int, list] = {}
    for nid in sorted(flow, key=lambda x: int(x)):
        rows.setdefault(depth[nid], []).append(nid)
        rows[depth[nid]].extend(notes_by_about.get(nid, []))

    X_STEP = 400   # шаг между колонками (ветками)
    Y_GAP = 70
    y = 60
    for d in sorted(rows):
        ids = rows[d]
        for i, nid in enumerate(ids):
            df[nid]["pos_x"] = 60 + i * X_STEP
            df[nid]["pos_y"] = y
        y += max(_node_height(df[n]) for n in ids) + Y_GAP

    if orphan_notes:
        for i, nid in enumerate(sorted(orphan_notes, key=lambda x: int(x))):
            df[nid]["pos_x"] = 60 + i * X_STEP
            df[nid]["pos_y"] = y


def spec_to_funnel_fields(spec: dict, tag_ids: dict) -> dict:
    """-> {name, trigger_type, trigger_value, graph_ui, graph} (с валидацией)."""
    graph_ui = build_drawflow(spec, tag_ids)
    try:
        compiled = compile_graph(graph_ui)
    except GraphError as e:
        raise AIError(f"Сгенерированный граф не прошёл валидацию: {e}")
    trigger_type = spec.get("trigger_type") or "start"
    trigger_value = spec.get("trigger_value")
    if trigger_type == "tag_added" and trigger_value:
        trigger_value = str(tag_ids.get(str(trigger_value), trigger_value))
    return {
        "name": spec.get("name") or "Воронка из ТЗ",
        "trigger_type": trigger_type,
        "trigger_value": trigger_value,
        "graph_ui": graph_ui,
        "graph": compiled,
    }


# ---------- AI-чат внутри воронки: правки и переводы ----------

def graph_to_spec(funnel) -> dict:
    """Обратная конвертация: скомпилированный граф -> спека для LLM."""
    nodes_out = []
    g = funnel.graph or {}
    nodes = g.get("nodes") or {}
    start_id = g.get("start")

    def first(node, port):
        t = (node.get("outputs") or {}).get(port) or []
        return t[0] if t else None

    # порядок: BFS от старта, потом остальные
    order, seen, queue = [], set(), [start_id] if start_id else []
    while queue:
        nid = queue.pop(0)
        if nid in seen or nid not in nodes:
            continue
        seen.add(nid)
        order.append(nid)
        for targets in (nodes[nid].get("outputs") or {}).values():
            queue.extend(targets)
    order += [n for n in nodes if n not in seen]

    entry = first(nodes.get(start_id, {}), "output_1") if start_id else None
    for nid in order:
        n = nodes[nid]
        t, d = n["type"], n.get("data") or {}
        if t == "start":
            continue
        item = {"id": nid, "type": t}
        if t == "message":
            item["text"] = d.get("text") or ""
            if d.get("media"):
                item["media"] = d["media"]
            if d.get("photo_url"):
                item["photo_url"] = d["photo_url"]
            btns = []
            for i, b in enumerate(d.get("buttons") or []):
                bb = {"label": b.get("label", "")}
                if b.get("url"):
                    bb["url"] = b["url"]
                else:
                    bb["next"] = first(n, f"output_{i + 2}")
                btns.append(bb)
            item["buttons"] = btns
            item["next"] = first(n, "output_1")
        elif t == "delay":
            item.update(amount=d.get("amount"), unit=d.get("unit"), next=first(n, "output_1"))
        elif t == "condition":
            item.update(tag=d.get("tag"), yes=first(n, "output_1"), no=first(n, "output_2"))
        elif t == "language":
            item.update(
                languages=[{"code": c, "next": first(n, f"output_{k + 2}")}
                           for k, c in enumerate(d.get("languages") or [])],
                other=first(n, "output_1"))
        elif t == "action":
            item.update(op=d.get("op"), tag=d.get("tag"), next=first(n, "output_1"))
        elif t == "note":
            item.update(text=d.get("text") or "", about=d.get("about") or "")
        nodes_out.append(item)

    # спека хранит имена тегов, а граф — id: конвертируем не здесь, а на входе LLM
    return {
        "name": funnel.name,
        "trigger_type": funnel.trigger_type,
        "trigger_value": funnel.trigger_value,
        "entry": entry,
        "nodes": nodes_out,
    }


EDIT_SYSTEM_PROMPT = """Ты — AI-редактор воронок телеграм-бота. Тебе дают ТЕКУЩУЮ воронку в JSON и просьбу пользователя (перевести, изменить, дополнить).

Отвечай ТОЛЬКО JSON без markdown-ограждений:
{"reply": "короткий ответ пользователю по-русски: что сделал/что уточнить",
 "spec": null | {"name": ..., "trigger_type": ..., "trigger_value": ..., "nodes": [...]}}

spec = null, если менять нечего (просто вопрос). Если меняешь — верни ПОЛНУЮ новую спеку всех узлов (формат узлов тот же, что во входных данных; поле "entry" не возвращай — первый узел списка станет входом).

Правила:
- Сохраняй существующие id узлов, которые не менял; новым давай id вида "n1","n2".
- Тексты, которые не просили менять, переноси ДОСЛОВНО.
- В условиях/действиях поле tag — ИМЯ тега (латинский slug). Несуществующие теги создадутся автоматически.
- МУЛЬТИЯЗЫЧНОСТЬ: если просят перевод на другие языки — поставь первым узлом language (авто-определение языка Telegram-профиля, юзера не спрашиваем): {"type": "language", "languages": [{"code": "ru", "next": ...}, ...], "other": ...}. Каждая ветка — полная копия воронки на своём языке (переведи все тексты и кнопки, ссылки не меняй), существующая ветка становится одной из языковых, "other" веди в ветку английского (или основного) языка. Кнопочный выбор языка делай, только если это просят явно.
- Задержки: unit seconds|minutes|hours|days.
- next: null — конец ветки.
"""


async def chat_edit_funnel(session, funnel, tags_list, user_messages: list, provider, api_key, model):
    """-> (reply_text, new_fields | None, in_tokens, out_tokens)"""
    import json as _json

    # Блоки, которых нет в языке спеки. Модель вернула бы воронку без них —
    # и «Цепочка» или «Фильтр» тихо исчезли бы вместе со всей веткой.
    # Лучше честно отказаться, чем молча уничтожить кусок сценария.
    UNSUPPORTED = {"chain": "Цепочка", "filter": "Фильтр"}
    present = {n.get("type") for n in (funnel.graph or {}).get("nodes", {}).values()}
    blocked = [title for t, title in UNSUPPORTED.items() if t in present]
    if blocked:
        raise AIError(
            "В воронке есть блоки, которые AI-помощник пока не умеет: "
            + ", ".join(f"«{b}»" for b in blocked)
            + ". Он переписывает воронку целиком и потерял бы их. "
            "Правьте такую воронку руками.")

    # входной контекст: текущая спека с именами тегов вместо id
    id2name = {str(t.id): t.name for t in tags_list}
    spec = graph_to_spec(funnel)
    for n in spec["nodes"]:
        if n.get("tag") is not None:
            n["tag"] = id2name.get(str(n["tag"]), str(n["tag"]))

    convo = "ТЕКУЩАЯ ВОРОНКА:\n" + _json.dumps(spec, ensure_ascii=False) + "\n\nДИАЛОГ:\n"
    for m in user_messages[-12:]:
        role = "Пользователь" if m.get("role") == "user" else "Ассистент"
        convo += f"{role}: {m.get('content','')}\n"

    text, tin, tout = await call_llm_system(provider, api_key, model, EDIT_SYSTEM_PROMPT, convo)
    data = parse_llm_json(text)
    reply = data.get("reply") or "Готово."
    new_spec = data.get("spec")
    fields = None
    if new_spec:
        tag_ids = await ensure_tags(session, new_spec)
        fields = spec_to_funnel_fields(new_spec, tag_ids)
        # имя/триггер не трогаем, если LLM их не вернул
        fields["name"] = new_spec.get("name") or funnel.name
        if not new_spec.get("trigger_type"):
            fields["trigger_type"] = funnel.trigger_type
            fields["trigger_value"] = funnel.trigger_value
    return reply, fields, tin, tout


async def call_llm_system(provider, api_key, model, system, user_text):
    """Как call_llm, но с произвольным системным промптом."""
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        if provider == "anthropic":
            r = await http.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": 16000, "system": system,
                      "messages": [{"role": "user", "content": user_text}]},
            )
            data = await r.json()
            if r.status != 200:
                raise AIError(data.get("error", {}).get("message", f"HTTP {r.status}"))
            text = "".join(b.get("text", "") for b in data.get("content", []))
            u = data.get("usage", {})
            return text, u.get("input_tokens", 0), u.get("output_tokens", 0)
        elif provider == "openai":
            r = await http.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text}]},
            )
            data = await r.json()
            if r.status != 200:
                raise AIError(data.get("error", {}).get("message", f"HTTP {r.status}"))
            u = data.get("usage", {})
            return data["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        raise AIError(f"Неизвестный провайдер: {provider}")
