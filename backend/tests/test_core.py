"""Автотесты ядра. Гоняются в CI при каждом изменении кода.

Локально:  cd backend && pytest -q
"""
import os
import sys
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_ci.db")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("ADMIN_PASSWORD", "test-pass")


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    """Чистая БД на каждый тест."""
    db_file = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from app.db import SessionLocal, init_db

    await init_db()
    async with SessionLocal() as s:
        yield s


# ---------- граф воронки ----------

def _gui(nodes):
    return {"drawflow": {"Home": {"data": nodes}}}


def test_graph_compiles_and_validates():
    from app.graph import GraphError, compile_graph

    ok = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "message", "data": {"text": "Привет", "buttons": []},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": []}}},
    })
    g = compile_graph(ok)
    assert g["start"] == "1" and len(g["nodes"]) == 2

    # пустое сообщение без медиа и кнопок — ошибка
    bad = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "message", "data": {"text": "", "buttons": []},
              "inputs": {"input_1": {"connections": []}}, "outputs": {"output_1": {"connections": []}}},
    })
    with pytest.raises(GraphError):
        compile_graph(bad)


def test_delay_units_include_seconds():
    from app.bot.engine import UNIT_SECONDS

    assert UNIT_SECONDS["seconds"] == 1
    assert UNIT_SECONDS["days"] == 86400


# ---------- пароли и токены ----------

def test_password_hash_and_verify():
    from app.auth import hash_password, verify_password

    h = hash_password("Секрет123")
    assert h != "Секрет123"
    assert verify_password("Секрет123", h)
    assert not verify_password("другой", h)


def test_token_roundtrip_and_tamper():
    from app.auth import make_token, parse_token

    class U:
        id, role = 7, "owner"

    t = make_token(U())
    data = parse_token(t)
    assert data["uid"] == 7 and data["role"] == "owner"
    assert parse_token(t[:-3] + "aaa") is None  # подделка подписи не проходит


# ---------- deep-link метки ----------

def test_personalize_substitutions():
    from app.bot.sender import personalize

    class S:
        first_name, last_name, username = "Кирилл", None, "k"
        source, first_source, params = "google", "fb", {}

    assert personalize("{first_name} {source} {first_source}", S()) == "Кирилл google fb"


# ---------- мультимедиа ----------

def test_media_grouping_rules():
    from app.bot.sender import _plan_sends

    plan = _plan_sends([
        {"type": "photo", "path": "a"}, {"type": "photo", "path": "b"},
        {"type": "video_note", "path": "c"},
    ])
    kinds = [p[0] for p in plan]
    assert kinds == ["group", "single"]      # фото в альбом, кружок отдельно
    assert len(plan[0][2]) == 2


# ---------- сегменты ----------

@pytest.mark.asyncio
async def test_segment_filters(session):
    from sqlalchemy import func, select

    from app import segment as seg
    from app.models import Bot, Subscriber, SubscriberTag, Tag

    b = Bot(name="B", token="t")
    session.add(b)
    await session.flush()
    tag = Tag(name="vip")
    session.add(tag)
    await session.flush()
    now = datetime.utcnow()
    subs = [
        Subscriber(bot_id=b.id, tg_id=1, first_name="A", language_code="ru",
                   is_active=True, created_at=now, last_active_at=now),
        Subscriber(bot_id=b.id, tg_id=2, first_name="B", language_code="en",
                   is_active=True, created_at=now - timedelta(days=40),
                   last_active_at=now - timedelta(days=40)),
        Subscriber(bot_id=b.id, tg_id=3, first_name="C", language_code="ru",
                   is_active=False, created_at=now, last_active_at=now),
    ]
    session.add_all(subs)
    await session.flush()
    session.add(SubscriberTag(subscriber_id=subs[0].id, tag_id=tag.id))
    await session.flush()

    async def count(f):
        q = seg.build_query(b.id, f)
        return (await session.execute(select(func.count()).select_from(q.subquery()))).scalar()

    assert await count({"conditions": []}) == 3
    assert await count({"conditions": [{"field": "language", "op": "equals", "value": "ru"}]}) == 2
    assert await count({"conditions": [{"field": "status", "op": "equals", "value": "active"}]}) == 2
    assert await count({"conditions": [{"field": "tag", "op": "has", "value": tag.id}]}) == 1
    assert await count({"match": "any", "conditions": [
        {"field": "tag", "op": "has", "value": tag.id},
        {"field": "language", "op": "equals", "value": "en"}]}) == 2


# ---------- движок воронок ----------

@pytest.mark.asyncio
async def test_funnel_engine_flow_and_tracking(session):
    from sqlalchemy import func, select

    from app.bot import engine as fx
    from app.bot import runner
    from app.graph import compile_graph
    from app.models import Bot, Funnel, FunnelBot, FunnelRun, NodeVisit, Subscriber

    class MockBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, cid, text, **kw):
            self.sent.append(text)
            return type("M", (), {"photo": []})()

    bot = MockBot()
    runner.manager.bots = {1: bot}

    gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "message", "data": {"text": "Привет", "buttons": [{"label": "Да"}]},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": []},
                          "output_2": {"connections": [{"node": "3", "output": "input_1"}]}}},
        "3": {"id": 3, "name": "message", "data": {"text": "После клика", "buttons": []},
              "inputs": {"input_1": {"connections": []}}, "outputs": {"output_1": {"connections": []}}},
    })
    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    f = Funnel(name="F", is_active=True, trigger_type="start",
               graph_ui=gui, graph=compile_graph(gui))
    session.add(f)
    await session.flush()
    session.add(FunnelBot(funnel_id=f.id, bot_id=b.id))
    sub = Subscriber(bot_id=b.id, tg_id=10, first_name="X", is_active=True)
    session.add(sub)
    await session.flush()

    await fx.start_funnel(bot, session, f, sub)
    assert "Привет" in bot.sent

    run = (await session.execute(select(FunnelRun))).scalar_one()
    await fx.handle_button(bot, session, run.id, "2", 0)
    assert "После клика" in bot.sent

    visits = (await session.execute(
        select(NodeVisit.node_id, func.count()).group_by(NodeVisit.node_id))).all()
    assert dict(visits) == {"2": 1, "3": 1}


@pytest.mark.asyncio
async def test_subscribers_isolated_per_bot(session):
    from app.bot import runner
    from app.models import Bot

    b1, b2 = Bot(name="1", token="a"), Bot(name="2", token="b")
    session.add_all([b1, b2])
    await session.flush()
    user = type("U", (), {"id": 555, "username": "u", "first_name": "N",
                          "last_name": None, "language_code": "ru"})()
    s1 = await runner.upsert_subscriber(session, b1.id, user, "fb")
    s2 = await runner.upsert_subscriber(session, b2.id, user, "google")
    assert s1.id != s2.id                    # один человек — разные записи у разных ботов
    s1b = await runner.upsert_subscriber(session, b1.id, user, "tiktok")
    assert s1b.source == "tiktok" and s1b.first_source == "fb"  # метки


# ---------- AI: конвертация графа ----------

@pytest.mark.asyncio
async def test_ai_spec_roundtrip(session):
    from app import ai
    from app.models import Funnel

    spec = {"name": "T", "trigger_type": "start", "trigger_value": None, "nodes": [
        {"id": "n1", "type": "message", "text": "Привет",
         "buttons": [{"label": "Дальше", "next": "n2"}], "next": None},
        {"id": "n2", "type": "message", "text": "Конец", "buttons": [], "next": None},
    ]}
    tag_ids = await ai.ensure_tags(session, spec)
    fields = ai.spec_to_funnel_fields(spec, tag_ids)
    f = Funnel(is_active=False, **fields)
    session.add(f)
    await session.flush()

    back = ai.graph_to_spec(f)
    texts = [n.get("text") for n in back["nodes"] if n["type"] == "message"]
    assert "Привет" in texts and "Конец" in texts


# ---------- аналитика: считается в БД, а не в памяти приложения ----------

@pytest.mark.asyncio
async def test_analytics_ltv_matches_reference(session):
    """LTV и распределение по «возрасту» должны совпадать с прямым расчётом.

    Раньше это считалось выгрузкой всей таблицы подписчиков в питон; теперь
    агрегирует БД — цифры обязаны остаться теми же.
    """
    from app import analytics as an
    from app.models import Bot, Subscriber

    bot = Bot(name="b", token="t")
    session.add(bot)
    await session.flush()

    now = datetime.utcnow()
    plan = [(0.5, True), (3, True), (15, False), (45, False), (200, True)]
    expected = []
    for i, (age, active) in enumerate(plan * 4):
        created = now - timedelta(days=age)
        last = created + timedelta(days=age / 2)
        session.add(Subscriber(bot_id=bot.id, tg_id=500 + i, created_at=created,
                               last_active_at=last, is_active=active))
        expected.append(((now if active else last) - created).total_seconds() / 86400.0)
    await session.commit()

    res = await an.build_analytics(session, days=365)
    t = res["totals"]

    assert t["subscribers"] == len(expected)
    assert abs(t["lifetime_avg_days"] - sum(expected) / len(expected)) < 0.2

    s = sorted(expected)
    n = len(s)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    assert abs(t["lifetime_median_days"] - median) < 0.2

    buckets = [(0, 1), (1, 7), (7, 30), (30, 90), (90, 10 ** 9)]
    got = [d["v"] for d in res["breakdowns"]["lifetime"]]
    assert got == [sum(1 for x in expected if lo <= x < hi) for lo, hi in buckets]


@pytest.mark.asyncio
async def test_funnel_analysis_respects_event_order(session):
    """Шаг засчитывается только если событие произошло ПОСЛЕ предыдущего шага."""
    from app import analytics as an
    from app.models import Bot, ButtonClick, NodeVisit, Subscriber

    bot = Bot(name="b", token="t")
    session.add(bot)
    await session.flush()

    now = datetime.utcnow()
    subs = []
    for i in range(20):
        s = Subscriber(bot_id=bot.id, tg_id=900 + i, created_at=now - timedelta(days=5))
        session.add(s)
        subs.append(s)
    await session.flush()

    for i, s in enumerate(subs):
        if i % 2 == 0:  # 10 дошли до узла
            session.add(NodeVisit(funnel_id=1, node_id="n1", subscriber_id=s.id,
                                  run_id=1, created_at=now - timedelta(days=4)))
        if i % 4 == 0:  # 5 из них кликнули ПОСЛЕ
            session.add(ButtonClick(funnel_id=1, node_id="n1", button_index=0,
                                    subscriber_id=s.id, created_at=now - timedelta(days=3)))
        if i % 2 == 0:  # клик по n2 — РАНЬШЕ визита, засчитываться не должен
            session.add(ButtonClick(funnel_id=1, node_id="n2", button_index=0,
                                    subscriber_id=s.id, created_at=now - timedelta(days=6)))
    await session.commit()

    steps = [{"type": "subscribed"},
             {"type": "node", "funnel_id": 1, "node_id": "n1"},
             {"type": "button", "funnel_id": 1, "node_id": "n1", "button": 0}]
    res = await an.funnel_analysis(session, steps, days=365)
    assert [x["count"] for x in res["steps"]] == [20, 10, 5]
    assert res["steps"][1]["from_prev"] == 50.0

    # событие раньше предыдущего шага не проходит
    res2 = await an.funnel_analysis(session, [
        {"type": "node", "funnel_id": 1, "node_id": "n1"},
        {"type": "button", "funnel_id": 1, "node_id": "n2", "button": 0},
    ], days=365)
    assert [x["count"] for x in res2["steps"]] == [10, 0]


@pytest.mark.asyncio
async def test_delete_subscribers_removes_all_traces(session):
    """Удаление подписчика подчищает всё: переписку, теги, запуски, визиты."""
    from app.api import _delete_subscribers
    from app.models import (Bot, ButtonClick, FunnelRun, Message, NodeVisit,
                            ScheduledJob, Subscriber, SubscriberTag, Tag)
    from sqlalchemy import func, select

    bot = Bot(name="b", token="t")
    tag = Tag(name="vip")
    session.add_all([bot, tag])
    await session.flush()

    keep = Subscriber(bot_id=bot.id, tg_id=1)      # этот должен остаться
    gone = Subscriber(bot_id=bot.id, tg_id=2)      # этого удаляем
    session.add_all([keep, gone])
    await session.flush()

    for s in (keep, gone):
        session.add(Message(subscriber_id=s.id, direction="in", text="hi"))
        session.add(SubscriberTag(subscriber_id=s.id, tag_id=tag.id))
        run = FunnelRun(funnel_id=1, subscriber_id=s.id, status="active")
        session.add(run)
        await session.flush()
        session.add(ScheduledJob(run_id=run.id, node_id="n1",
                                 execute_at=datetime.utcnow()))
        session.add(NodeVisit(funnel_id=1, node_id="n1", subscriber_id=s.id, run_id=run.id))
        session.add(ButtonClick(funnel_id=1, node_id="n1", button_index=0, subscriber_id=s.id))
    await session.commit()

    n = await _delete_subscribers(session, [gone.id])
    await session.commit()
    assert n == 1

    async def count(model, *cond):
        return (await session.execute(select(func.count()).select_from(model).where(*cond))).scalar()

    assert await count(Subscriber, Subscriber.id == gone.id) == 0
    assert await count(Subscriber, Subscriber.id == keep.id) == 1
    for model in (Message, SubscriberTag, FunnelRun, NodeVisit, ButtonClick):
        assert await count(model, model.subscriber_id == gone.id) == 0, model.__name__
        assert await count(model, model.subscriber_id == keep.id) == 1, model.__name__
    # отложенные задачи удалённого исчезли, у оставшегося — на месте
    assert await count(ScheduledJob) == 1


@pytest.mark.asyncio
async def test_broadcast_detail(session):
    """Карточка рассылки: содержимое, расшифровка аудитории, факт доставки."""
    from app.api import broadcast_detail
    from app.models import Bot, Broadcast, BroadcastRecipient, Subscriber, Tag

    bot = Bot(name="Мой бот", token="t")
    tag = Tag(name="VIP")
    session.add_all([bot, tag])
    await session.flush()

    bc = Broadcast(
        bot_id=bot.id, name="Акция", text="Привет!",
        media=[{"type": "photo", "path": "media/a.png", "name": "a.png"},
               {"type": "video", "path": "media/b.mp4", "name": "b.mp4"}],
        filters={"segment": {"match": "all", "conditions": [
            {"field": "tag", "op": "has", "value": str(tag.id)},
            {"field": "language", "op": "equals", "value": "ru"},
        ]}},
        status="done", total=2, sent=1, failed=1,
    )
    session.add(bc)
    s1 = Subscriber(bot_id=bot.id, tg_id=1, first_name="Иван", username="ivan")
    s2 = Subscriber(bot_id=bot.id, tg_id=2, first_name="Пётр")
    session.add_all([s1, s2])
    await session.flush()
    session.add_all([
        BroadcastRecipient(broadcast_id=bc.id, subscriber_id=s1.id, delivered=True),
        BroadcastRecipient(broadcast_id=bc.id, subscriber_id=s2.id, delivered=False),
    ])
    await session.commit()

    d = await broadcast_detail(bc.id, session)
    assert d["text"] == "Привет!"
    assert len(d["media"]) == 2
    assert d["bot"] == "Мой бот"
    assert d["sent"] == 1 and d["failed"] == 1
    assert d["delivered"] == 1 and d["not_delivered"] == 1
    # условия сегмента расшифрованы словами, а не id
    assert d["audience_kind"] == "Сегмент"
    assert "тег: есть VIP" in d["audience"]
    assert "язык: = ru" in d["audience"]
    names = {r["name"] for r in d["recipients"]}
    assert names == {"Иван", "Пётр"}


@pytest.mark.asyncio
async def test_broadcast_detail_tag_filters(session):
    """Старый формат фильтра (include/exclude теги) тоже читается словами."""
    from app.api import broadcast_detail
    from app.models import Bot, Broadcast, Tag

    bot = Bot(name="b", token="t")
    t1, t2 = Tag(name="лиды"), Tag(name="отписка")
    session.add_all([bot, t1, t2])
    await session.flush()
    bc = Broadcast(bot_id=bot.id, name="R", text="t",
                   filters={"include_tags": [t1.id], "exclude_tags": [t2.id]})
    session.add(bc)
    await session.commit()

    d = await broadcast_detail(bc.id, session)
    assert d["audience_kind"] == "Теги"
    assert "есть тег: лиды" in d["audience"]
    assert "нет тега: отписка" in d["audience"]


# ---------- права доступа ----------

def _user(role="staff", perms=None):
    from app.models import User
    u = User(login="u", name="U", password_hash="x", role=role,
             bot_ids=[], permissions=perms or {})
    return u


def test_permissions_owner_can_everything():
    from app.auth import FEATURE_KEYS, has_perm, user_permissions
    owner = _user("owner")
    for f in FEATURE_KEYS:
        assert has_perm(owner, f, "edit"), f
    # владельцу «изменять» показывается везде, кроме разделов только для чтения
    perms = user_permissions(owner)
    assert perms["analytics"] == "view"     # смотреть нечего менять
    assert perms["funnels"] == "edit"


def test_permissions_levels():
    from app.auth import has_perm
    u = _user(perms={"funnels": "view", "broadcasts": "edit"})

    assert has_perm(u, "funnels", "view")
    assert not has_perm(u, "funnels", "edit")     # смотреть можно, менять нельзя
    assert has_perm(u, "broadcasts", "view")      # «изменять» включает «смотреть»
    assert has_perm(u, "broadcasts", "edit")
    assert not has_perm(u, "subscribers", "view")  # раздел не выдан вообще
    assert not has_perm(u, "logs", "view")


def test_permissions_normalize():
    from app.auth import normalize_permissions
    got = normalize_permissions({
        "funnels": "edit",
        "analytics": "edit",      # раздел только для чтения → понижаем
        "logs": "none",           # «нет» не храним
        "выдуманное": "edit",     # неизвестный раздел выкидываем
        "tags": "хакер",          # неизвестный уровень выкидываем
    })
    assert got == {"funnels": "edit", "analytics": "view"}


@pytest.mark.asyncio
async def test_require_dependency_blocks_and_allows():
    from fastapi import HTTPException
    from app.auth import require

    dep = require("funnels", "edit")

    allowed = _user(perms={"funnels": "edit"})
    assert await dep(user=allowed) is allowed

    viewer = _user(perms={"funnels": "view"})
    with pytest.raises(HTTPException) as e:
        await dep(user=viewer)
    assert e.value.status_code == 403
    assert "Воронки" in e.value.detail      # ошибка объясняет, чего не хватает


def test_every_endpoint_is_protected():
    """Ни один маршрут не должен остаться без проверки прав.

    Исключения перечислены явно: health — публичная проверка живости,
    login — вход, остальные проверяют пользователя внутри себя.
    """
    import inspect

    from app.api import router

    ALLOWED_OPEN = {
        "/api/health",          # публичная проверка живости (нужна деплою)
        "/api/auth/login",      # вход
        "/api/logs/ws",         # сокет: право проверяется внутри по токену
    }
    unprotected = []
    for r in router.routes:
        path = getattr(r, "path", "")
        if path in ALLOWED_OPEN:
            continue
        if getattr(r, "dependencies", None):
            continue
        # без dependencies допустимо, только если пользователь берётся аргументом
        endpoint = getattr(r, "endpoint", None)
        params = inspect.signature(endpoint).parameters if endpoint else {}
        if "user" in params:
            continue
        unprotected.append(f"{sorted(getattr(r, 'methods', ['WS']))} {path}")

    assert not unprotected, "Маршруты без проверки прав: " + ", ".join(unprotected)


@pytest.mark.asyncio
async def test_login_response_includes_permissions(session):
    """Вход должен отдавать те же поля, что и /auth/me.

    Иначе сразу после логина интерфейс не знает о правах и прячет всё подряд.
    """
    from app.api import LoginIn, auth_me, login
    from app.auth import hash_password
    from app.models import User

    u = User(login="vadim", name="Vadim", password_hash=hash_password("secret123"),
             role="staff", bot_ids=[1], permissions={"bots": "view", "funnels": "edit"})
    session.add(u)
    await session.commit()

    res = await login(LoginIn(login="vadim", password="secret123"), session)
    me = await auth_me(user=u)

    assert res["user"] == me, "состав /auth/login и /auth/me разошёлся"
    assert res["user"]["permissions"] == {"bots": "view", "funnels": "edit"}
    assert res["user"]["bot_ids"] == [1]
