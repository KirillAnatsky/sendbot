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
