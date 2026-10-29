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


def test_action_node_validation():
    """Блок «Действие»: каждая операция требует своих полей."""
    from app.graph import GraphError, compile_graph

    def gui(data):
        return _gui({
            "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
                  "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
            "2": {"id": 2, "name": "action", "data": data,
                  "inputs": {"input_1": {"connections": []}},
                  "outputs": {"output_1": {"connections": []}}},
            "3": {"id": 3, "name": "message", "data": {"text": "Привет"},
                  "inputs": {"input_1": {"connections": []}},
                  "outputs": {"output_1": {"connections": []}}},
        })

    # старый формат «Тег» продолжает компилироваться
    compile_graph(gui({"op": "add_tag", "tag": "1"}))
    compile_graph(gui({"op": "unsubscribe"}))
    compile_graph(gui({"op": "check_subscription", "channel": "@ch"}))
    compile_graph(gui({"op": "check_subscription", "channel": "-1001234567890"}))
    compile_graph(gui({"op": "delete_message", "target": "last"}))
    compile_graph(gui({"op": "delete_message", "target": "3"}))

    for bad, hint in [
        ({"op": "whatever"}, "операция"),
        ({"op": "add_tag"}, "тег"),
        ({"op": "check_subscription"}, "канал"),
        ({"op": "check_subscription", "channel": "t.me/ch"}, "@имя"),
        ({"op": "delete_message"}, "какое сообщение"),
        ({"op": "delete_message", "target": "99"}, "не существует"),
        # нельзя удалять по ссылке на блок, который сообщений не шлёт
        ({"op": "delete_message", "target": "1"}, "не существует"),
    ]:
        with pytest.raises(GraphError) as e:
            compile_graph(gui(bad))
        assert hint in str(e.value), (bad, str(e.value))


@pytest.mark.asyncio
async def test_unsubscribe_stops_delays_and_broadcasts(session):
    """Отписка гасит отложенные шаги и убирает человека из аудитории рассылок."""
    from sqlalchemy import select

    from app.bot import engine as fx
    from app.models import Bot, Funnel, FunnelRun, ScheduledJob, Subscriber
    from app import segment as seg

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    f = Funnel(name="F", is_active=True, trigger_type="start", graph_ui={}, graph={})
    session.add(f)
    sub = Subscriber(bot_id=b.id, tg_id=10, first_name="X", is_active=True)
    session.add(sub)
    await session.flush()
    run = FunnelRun(funnel_id=f.id, subscriber_id=sub.id, current_node="1", status="active")
    session.add(run)
    await session.flush()
    session.add(ScheduledJob(run_id=run.id, node_id="2",
                             execute_at=datetime.utcnow() + timedelta(hours=1)))
    await session.flush()

    await fx._unsubscribe(session, sub)

    assert sub.is_subscribed is False
    job = (await session.execute(select(ScheduledJob))).scalar_one()
    assert job.status == "cancelled"

    # в сегментах отписавшегося не видно, а «отписался» — видно
    q = seg.build_query(b.id, {"conditions": [
        {"field": "subscribed", "op": "equals", "value": "yes"}]})
    assert (await session.execute(q)).scalars().all() == []
    q = seg.build_query(b.id, {"conditions": [
        {"field": "subscribed", "op": "equals", "value": "no"}]})
    assert [s.id for s in (await session.execute(q)).scalars()] == [sub.id]


@pytest.mark.asyncio
async def test_action_node_runs_in_funnel(session):
    """Прогон через движок: развилка по подписке и остановка воронки отпиской."""
    from sqlalchemy import select

    from app.bot import engine as fx
    from app.bot import runner
    from app.graph import compile_graph
    from app.models import Bot, Funnel, FunnelBot, FunnelRun, Subscriber

    class MockBot:
        def __init__(self, member=True):
            self.sent = []
            self.member = member

        async def send_message(self, cid, text, **kw):
            self.sent.append(text)
            return type("M", (), {"photo": [], "message_id": 100 + len(self.sent)})()

        async def get_chat_member(self, chat, uid):
            if self.member is None:
                raise RuntimeError("бот не админ канала")
            return type("CM", (), {"status": "member" if self.member else "left"})()

    # старт -> проверка подписки -> (да: «Спасибо», нет: «Подпишись» -> отписать)
    gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "action",
              "data": {"op": "check_subscription", "channel": "@ch"},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": [{"node": "3", "output": "input_1"}]},
                          "output_2": {"connections": [{"node": "4", "output": "input_1"}]}}},
        "3": {"id": 3, "name": "message", "data": {"text": "Спасибо"},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": []}}},
        "4": {"id": 4, "name": "message", "data": {"text": "Подпишись"},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": [{"node": "5", "output": "input_1"}]}}},
        "5": {"id": 5, "name": "action", "data": {"op": "unsubscribe"},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": [{"node": "6", "output": "input_1"}]}}},
        "6": {"id": 6, "name": "message", "data": {"text": "Это уже не должно уйти"},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": []}}},
    })
    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    f = Funnel(name="F", is_active=True, trigger_type="start",
               graph_ui=gui, graph=compile_graph(gui))
    session.add(f)
    await session.flush()
    session.add(FunnelBot(funnel_id=f.id, bot_id=b.id))

    # подписан на канал -> первая ветка
    bot = MockBot(member=True)
    runner.manager.bots = {b.id: bot}
    sub = Subscriber(bot_id=b.id, tg_id=10, first_name="A", is_active=True)
    session.add(sub)
    await session.flush()
    await fx.start_funnel(bot, session, f, sub)
    assert bot.sent == ["Спасибо"]
    assert sub.is_subscribed is not False

    # не подписан -> вторая ветка, и отписка обрывает воронку
    bot2 = MockBot(member=False)
    sub2 = Subscriber(bot_id=b.id, tg_id=11, first_name="B", is_active=True)
    session.add(sub2)
    await session.flush()
    await fx.start_funnel(bot2, session, f, sub2)
    assert bot2.sent == ["Подпишись"]          # «Это уже не должно уйти» не отправлено
    assert sub2.is_subscribed is False
    run2 = (await session.execute(select(FunnelRun).where(
        FunnelRun.subscriber_id == sub2.id))).scalar_one()
    assert run2.status == "done"

    # бот не админ канала -> ошибка = «не подписан», воронка не падает
    bot3 = MockBot(member=None)
    sub3 = Subscriber(bot_id=b.id, tg_id=12, first_name="C", is_active=True)
    session.add(sub3)
    await session.flush()
    await fx.start_funnel(bot3, session, f, sub3)
    assert bot3.sent == ["Подпишись"]


@pytest.mark.asyncio
async def test_deliverable_count_matches_who_actually_gets_it(session):
    """Число «Получателей» = то, что реально уйдёт: без блока и без отписавшихся."""
    from sqlalchemy import func, select

    from app import segment as seg
    from app.models import Bot, Subscriber

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    for tg, active, subscribed in [(1, True, True), (2, True, False), (3, False, True)]:
        s_ = Subscriber(bot_id=b.id, tg_id=tg, first_name="X", is_active=active)
        s_.is_subscribed = subscribed
        session.add(s_)
    await session.flush()

    q = seg.build_query(b.id, {})
    assert (await session.execute(
        select(func.count()).select_from(q.subquery()))).scalar() == 3

    # та же отсечка, что в api.subscribers_search(deliverable=True) и в рассыльщике
    q = q.where(Subscriber.is_active == True,  # noqa: E712
                Subscriber.is_subscribed.isnot(False))
    assert (await session.execute(
        select(func.count()).select_from(q.subquery()))).scalar() == 1


@pytest.mark.asyncio
async def test_delete_message_removes_what_the_node_sent(session):
    """Блок «Удалить сообщение» удаляет ровно то, что отправил указанный блок."""
    from sqlalchemy import select

    from app.bot import engine as fx
    from app.models import Bot, Funnel, SentMessage, Subscriber

    deleted = []

    class MockBot:
        async def delete_message(self, cid, mid):
            deleted.append(mid)

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    f = Funnel(name="F", is_active=True, trigger_type="start", graph_ui={}, graph={})
    session.add(f)
    sub = Subscriber(bot_id=b.id, tg_id=10, first_name="X", is_active=True)
    session.add(sub)
    await session.flush()
    # узел «2» отправил альбом из двух сообщений, узел «3» — одно
    for node_id, mid in [("2", 100), ("2", 101), ("3", 200)]:
        session.add(SentMessage(subscriber_id=sub.id, funnel_id=f.id,
                                node_id=node_id, message_id=mid))
    await session.flush()

    await fx._delete_message(MockBot(), session, f, sub, "2", "9")
    assert deleted == [100, 101]

    # «last» берёт последний блок целиком, а не одно сообщение
    deleted.clear()
    await fx._delete_message(MockBot(), session, f, sub, "last", "9")
    assert deleted == [200]

    # ничего не осталось — блок отрабатывает молча, воронка не падает
    deleted.clear()
    await fx._delete_message(MockBot(), session, f, sub, "last", "9")
    assert deleted == []
    assert (await session.execute(select(SentMessage))).scalars().all() == []


def test_chain_cycles_are_caught_on_save():
    """Цепочка, которая через другие цепочки зовёт себя, не должна сохраниться."""
    from app.graph import GraphError, check_no_cycles, chain_ids, compile_graph

    def gui(calls):
        nodes = {"1": {"id": 1, "name": "start", "data": {}, "inputs": {},
                       "outputs": {"output_1": {"connections": []}}}}
        for i, fid in enumerate(calls):
            nid = str(i + 2)
            nodes[nid] = {"id": i + 2, "name": "chain", "data": {"funnel_id": fid},
                          "inputs": {"input_1": {"connections": []}},
                          "outputs": {"output_1": {"connections": []}}}
        return compile_graph(_gui(nodes))

    a, b, c = gui([2]), gui([3]), gui([1])
    assert chain_ids(a) == [2]

    book = {1: ("A", a), 2: ("B", b), 3: ("C", c)}
    with pytest.raises(GraphError) as e:
        check_no_cycles(1, "A", a, book.get)
    assert "зациклены" in str(e.value)
    assert "«A»" in str(e.value) and "«C»" in str(e.value)

    # ромб — не цикл: две воронки зовут одну и ту же цепочку
    d = gui([])
    book = {1: ("A", gui([2, 3])), 2: ("B", gui([4])), 3: ("C", gui([4])), 4: ("D", d)}
    check_no_cycles(1, "A", book[1][1], book.get)

    # цепочка сама на себя
    book = {1: ("A", gui([1]))}
    with pytest.raises(GraphError):
        check_no_cycles(1, "A", book[1][1], book.get)


@pytest.mark.asyncio
async def test_chain_runs_and_returns(session):
    """Заход в цепочку и возврат наружу — в том числе через задержку и кнопку."""
    from sqlalchemy import select

    from app.bot import engine as fx
    from app.graph import compile_graph
    from app.models import Bot, Funnel, FunnelRun, ScheduledJob, Subscriber

    class MockBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, cid, text, **kw):
            self.sent.append(text)
            return type("M", (), {"photo": [], "message_id": len(self.sent)})()

    def msg(nid, text, nxt=None, buttons=None):
        outs = {"output_1": {"connections": [{"node": nxt, "output": "input_1"}] if nxt else []}}
        if buttons:
            outs["output_2"] = {"connections": [{"node": buttons, "output": "input_1"}]}
        return {"id": int(nid), "name": "message",
                "data": {"text": text, "buttons": [{"label": "Да"}] if buttons else []},
                "inputs": {"input_1": {"connections": []}}, "outputs": outs}

    bot = MockBot()
    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()

    # цепочка: одно сообщение
    chain_gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": msg("2", "из цепочки"),
    })
    chain = Funnel(name="Цепочка", is_chain=True, is_active=False,
                   graph_ui=chain_gui, graph=compile_graph(chain_gui))
    session.add(chain)
    await session.flush()

    # воронка: до цепочки -> цепочка -> после цепочки
    main_gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": msg("2", "до", nxt="3"),
        "3": {"id": 3, "name": "chain", "data": {"funnel_id": chain.id},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": [{"node": "4", "output": "input_1"}]}}},
        "4": msg("4", "после"),
    })
    main = Funnel(name="Воронка", is_active=True, trigger_type="start",
                  graph_ui=main_gui, graph=compile_graph(main_gui))
    session.add(main)
    sub = Subscriber(bot_id=b.id, tg_id=10, first_name="X", is_active=True)
    session.add(sub)
    await session.flush()

    await fx.start_funnel(bot, session, main, sub)
    assert bot.sent == ["до", "из цепочки", "после"]

    runs = (await session.execute(select(FunnelRun))).scalars().all()
    parent = [r for r in runs if r.funnel_id == main.id][0]
    child = [r for r in runs if r.funnel_id == chain.id][0]
    assert child.parent_run_id == parent.id and child.return_node == "3"
    assert child.depth == 1 and parent.depth == 0
    assert parent.status == "done" and child.status == "done"

    # задержка внутри цепочки: воронка ждёт, пока цепочка не дойдёт до конца
    chain2_gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "delay", "data": {"amount": 1, "unit": "hours"},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": [{"node": "3", "output": "input_1"}]}}},
        "3": msg("3", "после паузы"),
    })
    chain2 = Funnel(name="С паузой", is_chain=True, is_active=False,
                    graph_ui=chain2_gui, graph=compile_graph(chain2_gui))
    session.add(chain2)
    await session.flush()
    main2_gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "chain", "data": {"funnel_id": chain2.id},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": [{"node": "3", "output": "input_1"}]}}},
        "3": msg("3", "хвост воронки"),
    })
    main2 = Funnel(name="Воронка 2", is_active=True, trigger_type="start",
                   graph_ui=main2_gui, graph=compile_graph(main2_gui))
    session.add(main2)
    sub2 = Subscriber(bot_id=b.id, tg_id=11, first_name="Y", is_active=True)
    session.add(sub2)
    await session.flush()

    bot.sent.clear()
    await fx.start_funnel(bot, session, main2, sub2)
    assert bot.sent == []                      # ждём паузу внутри цепочки

    job = (await session.execute(select(ScheduledJob).where(
        ScheduledJob.status == "pending"))).scalars().one()
    job.execute_at = datetime.utcnow() - timedelta(minutes=1)
    await session.flush()
    await fx.run_due_jobs(session, lambda bot_id: bot)
    # цепочка досталась до конца и вернула человека в воронку
    assert bot.sent == ["после паузы", "хвост воронки"]


@pytest.mark.asyncio
async def test_chain_with_button_returns_after_click(session):
    """Цепочка ждёт нажатия — воронка продолжается только после него."""
    from sqlalchemy import select

    from app.bot import engine as fx
    from app.graph import compile_graph
    from app.models import Bot, Funnel, FunnelRun, Subscriber

    class MockBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, cid, text, **kw):
            self.sent.append(text)
            return type("M", (), {"photo": [], "message_id": len(self.sent)})()

    bot = MockBot()
    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()

    # цепочка: вопрос с кнопкой -> ответ
    chain_gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "message",
              "data": {"text": "вопрос", "buttons": [{"label": "Да"}]},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": []},
                          "output_2": {"connections": [{"node": "3", "output": "input_1"}]}}},
        "3": {"id": 3, "name": "message", "data": {"text": "ответ", "buttons": []},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": []}}},
    })
    chain = Funnel(name="Опрос", is_chain=True, graph_ui=chain_gui,
                   graph=compile_graph(chain_gui))
    session.add(chain)
    await session.flush()
    main_gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "chain", "data": {"funnel_id": chain.id},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": [{"node": "3", "output": "input_1"}]}}},
        "3": {"id": 3, "name": "message", "data": {"text": "хвост", "buttons": []},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": []}}},
    })
    main = Funnel(name="Воронка", is_active=True, trigger_type="start",
                  graph_ui=main_gui, graph=compile_graph(main_gui))
    session.add(main)
    sub = Subscriber(bot_id=b.id, tg_id=10, first_name="X", is_active=True)
    session.add(sub)
    await session.flush()

    await fx.start_funnel(bot, session, main, sub)
    assert bot.sent == ["вопрос"]          # воронка стоит и ждёт кнопку в цепочке

    child = (await session.execute(select(FunnelRun).where(
        FunnelRun.funnel_id == chain.id))).scalar_one()
    await fx.handle_button(bot, session, child.id, "2", 0)
    assert bot.sent == ["вопрос", "ответ", "хвост"]

    parent = (await session.execute(select(FunnelRun).where(
        FunnelRun.funnel_id == main.id))).scalar_one()
    assert parent.status == "done"


@pytest.mark.asyncio
async def test_chain_restart_cancels_nested_run(session):
    """Перезапуск воронки гасит и запущенную из неё цепочку."""
    from sqlalchemy import func, select

    from app.bot import engine as fx
    from app.graph import compile_graph
    from app.models import Bot, Funnel, FunnelRun, ScheduledJob, Subscriber

    class MockBot:
        async def send_message(self, cid, text, **kw):
            return type("M", (), {"photo": [], "message_id": 1})()

    bot = MockBot()
    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()

    chain_gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "delay", "data": {"amount": 2, "unit": "days"},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": []}}},
    })
    chain = Funnel(name="Долгая", is_chain=True, graph_ui=chain_gui,
                   graph=compile_graph(chain_gui))
    session.add(chain)
    await session.flush()
    main_gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "chain", "data": {"funnel_id": chain.id},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": []}}},
    })
    main = Funnel(name="Воронка", is_active=True, trigger_type="start",
                  graph_ui=main_gui, graph=compile_graph(main_gui))
    session.add(main)
    sub = Subscriber(bot_id=b.id, tg_id=10, first_name="X", is_active=True)
    session.add(sub)
    await session.flush()

    await fx.start_funnel(bot, session, main, sub)
    assert (await session.execute(select(func.count()).select_from(
        ScheduledJob.__table__).where(ScheduledJob.status == "pending"))).scalar() == 1

    # человек снова нажал /start — прошлый проход целиком отменяется
    await fx.start_funnel(bot, session, main, sub)
    runs = (await session.execute(select(FunnelRun).order_by(FunnelRun.id))).scalars().all()
    assert [r.status for r in runs[:2]] == ["cancelled", "cancelled"]
    jobs = (await session.execute(select(ScheduledJob).order_by(ScheduledJob.id))).scalars().all()
    assert jobs[0].status == "cancelled"


@pytest.mark.asyncio
async def test_chain_never_starts_on_its_own(session):
    """Цепочка не должна запускаться триггером, даже если включена."""
    from app.bot import runner
    from app.models import Bot, Funnel, FunnelBot

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    chain = Funnel(name="Цепочка", is_chain=True, is_active=True,
                   trigger_type="start", graph_ui={}, graph={})
    normal = Funnel(name="Воронка", is_chain=False, is_active=True,
                    trigger_type="start", graph_ui={}, graph={})
    session.add_all([chain, normal])
    await session.flush()
    session.add_all([FunnelBot(funnel_id=chain.id, bot_id=b.id),
                     FunnelBot(funnel_id=normal.id, bot_id=b.id)])
    await session.flush()

    found = await runner._funnels_for_bot(session, b.id, "start")
    assert [f.name for f in found] == ["Воронка"]


@pytest.mark.asyncio
async def test_text_first_puts_caption_above_media(session):
    """«Сначала текст» — одно сообщение с подписью сверху, а не два сообщения."""
    from app.bot import sender
    from app.models import Bot, Subscriber

    class MockBot:
        def __init__(self, fail_above=False):
            self.calls = []
            self.fail_above = fail_above

        def _rec(self, name, **kw):
            self.calls.append((name, kw))
            if self.fail_above and kw.get("show_caption_above_media"):
                raise RuntimeError("Bad Request: unknown parameter")
            return type("M", (), {"message_id": len(self.calls), "photo": [],
                                  "video": None, "audio": None, "voice": None,
                                  "document": None, "video_note": None})()

        async def send_message(self, cid, text, **kw):
            return self._rec("message", text=text, **kw)

        async def send_photo(self, cid, arg, **kw):
            return self._rec("photo", **kw)

        async def send_video(self, cid, arg, **kw):
            return self._rec("video", **kw)

        async def send_audio(self, cid, arg, **kw):
            return self._rec("audio", **kw)

        async def send_media_group(self, cid, media):
            self.calls.append(("group", {"media": media}))
            return [type("M", (), {"message_id": 1, "photo": []})() for _ in media]

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    sub = Subscriber(bot_id=b.id, tg_id=10, first_name="X", is_active=True)
    session.add(sub)
    await session.flush()

    photo = [{"type": "photo", "path": "http://x/1.jpg"}]

    # фото + текст сверху = ОДНО сообщение с флагом
    bot = MockBot()
    await sender.send_message_content(bot, session, sub, "привет", photo, None,
                                      text_first=True, log_history=False)
    assert [c[0] for c in bot.calls] == ["photo"]
    assert bot.calls[0][1]["caption"] == "привет"
    assert bot.calls[0][1]["show_caption_above_media"] is True

    # без флага параметр не передаём вообще
    bot = MockBot()
    await sender.send_message_content(bot, session, sub, "привет", photo, None,
                                      log_history=False)
    assert "show_caption_above_media" not in bot.calls[0][1]

    # кнопки остаются в том же сообщении, под фото
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Да", callback_data="x")]])
    bot = MockBot()
    await sender.send_message_content(bot, session, sub, "привет", photo, kb,
                                      text_first=True, log_history=False)
    assert [c[0] for c in bot.calls] == ["photo"]
    assert bot.calls[0][1]["reply_markup"] is kb
    assert bot.calls[0][1]["show_caption_above_media"] is True

    # альбом: подпись сверху ставится на первое вложение
    bot = MockBot()
    await sender.send_message_content(
        bot, session, sub, "привет",
        [{"type": "photo", "path": "http://x/1.jpg"}, {"type": "photo", "path": "http://x/2.jpg"}],
        None, text_first=True, log_history=False)
    assert [c[0] for c in bot.calls] == ["group"]
    first = bot.calls[0][1]["media"][0]
    assert first.caption == "привет" and first.show_caption_above_media is True
    assert bot.calls[0][1]["media"][1].caption is None

    # аудио так не умеет — остаётся честное отдельное сообщение перед вложением
    bot = MockBot()
    await sender.send_message_content(bot, session, sub, "привет",
                                      [{"type": "audio", "path": "http://x/1.mp3"}],
                                      None, text_first=True, log_history=False)
    assert [c[0] for c in bot.calls] == ["message", "audio"]
    assert bot.calls[0][1]["text"] == "привет"
    assert bot.calls[1][1]["caption"] is None      # текст не продублирован

    # текст не дублируется и когда кнопки уезжают отдельным сообщением
    bot = MockBot()
    await sender.send_message_content(
        bot, session, sub, "привет",
        [{"type": "audio", "path": "http://x/1.mp3"}, {"type": "audio", "path": "http://x/2.mp3"}],
        kb, text_first=True, log_history=False)
    texts = [c[1].get("text") for c in bot.calls if c[0] == "message"]
    assert texts.count("привет") == 1

    # если Telegram вдруг не примет флаг, сообщение всё равно уходит
    bot = MockBot(fail_above=True)
    ok = await sender.send_message_content(bot, session, sub, "привет", photo, None,
                                           text_first=True, log_history=False)
    assert ok is True
    assert [c[0] for c in bot.calls] == ["photo", "photo"]
    assert "show_caption_above_media" not in bot.calls[1][1]


@pytest.mark.asyncio
async def test_run_moment_conditions(session):
    """День недели, дата и время срабатывания — считаются от «сейчас» в поясе проекта."""
    from datetime import date, time as dtime
    from zoneinfo import ZoneInfo

    from app import segment as seg
    from app import tz
    from app.models import Bot, Subscriber

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    sub = Subscriber(bot_id=b.id, tg_id=10, first_name="X", is_active=True)
    session.add(sub)
    await session.flush()

    tz.set_timezone("Europe/Kyiv")

    def at(moment, conditions):
        """Проверить фильтр так, будто «сейчас» — это moment."""
        real = tz.now
        tz.now = lambda: moment
        try:
            return seg.build_query(None, {"conditions": conditions}, allow_now=True)
        finally:
            tz.now = real

    kyiv = ZoneInfo("Europe/Kyiv")
    # среда, 15 октября 2026, 14:30
    wed = datetime.combine(date(2026, 10, 15), dtime(14, 30), kyiv)
    assert wed.isoweekday() == 4  # проверяем сам ориентир: это четверг

    async def passes(moment, cond):
        q = at(moment, [cond]).where(Subscriber.id == sub.id)
        return (await session.execute(q)).first() is not None

    # --- день недели ---
    assert await passes(wed, {"field": "weekday", "op": "in", "value": "4"})
    assert not await passes(wed, {"field": "weekday", "op": "in", "value": "1,2,3"})
    assert await passes(wed, {"field": "weekday", "op": "not_in", "value": "6,7"})
    assert not await passes(wed, {"field": "weekday", "op": "not_in", "value": "4"})

    # --- дата срабатывания ---
    assert await passes(wed, {"field": "run_date", "op": "on", "value": "2026-10-15"})
    assert not await passes(wed, {"field": "run_date", "op": "on", "value": "2026-10-16"})
    assert await passes(wed, {"field": "run_date", "op": "after", "value": "2026-10-01"})
    assert not await passes(wed, {"field": "run_date", "op": "after", "value": "2026-11-01"})
    assert await passes(wed, {"field": "run_date", "op": "before", "value": "2026-12-31"})

    # --- время срабатывания ---
    assert await passes(wed, {"field": "run_time", "op": "between", "value": "10:00-18:00"})
    assert not await passes(wed, {"field": "run_time", "op": "between", "value": "18:00-22:00"})
    assert await passes(wed, {"field": "run_time", "op": "after", "value": "14:00"})
    assert await passes(wed, {"field": "run_time", "op": "before", "value": "15:00"})

    # окно через полночь: 22:00–06:00 должно ловить и вечер, и ночь
    night = {"field": "run_time", "op": "between", "value": "22:00-06:00"}
    assert await passes(datetime.combine(date(2026, 10, 15), dtime(23, 0), kyiv), night)
    assert await passes(datetime.combine(date(2026, 10, 15), dtime(3, 0), kyiv), night)
    assert not await passes(wed, night)   # 14:30 — не ночь

    # --- пояс реально влияет ---
    # 23:30 по Киеву — это уже следующий день по UTC+5, и наоборот
    late = datetime.combine(date(2026, 10, 15), dtime(23, 30), kyiv)
    assert await passes(late, {"field": "run_date", "op": "on", "value": "2026-10-15"})
    tz.set_timezone("Asia/Almaty")
    almaty = late.astimezone(ZoneInfo("Asia/Almaty"))
    assert almaty.date() == date(2026, 10, 16)
    assert await passes(almaty, {"field": "run_date", "op": "on", "value": "2026-10-16"})
    tz.set_timezone("Europe/Kyiv")


def test_run_moment_conditions_are_rejected_in_broadcasts():
    """В рассылке эти условия запрещены: она идёт долго и перевернулась бы."""
    from app import segment as seg

    for field, op, value in [("weekday", "in", "1"),
                             ("run_date", "on", "2026-10-15"),
                             ("run_time", "between", "10:00-18:00")]:
        with pytest.raises(seg.SegmentError) as e:
            seg.build_query(1, {"conditions": [{"field": field, "op": op, "value": value}]})
        assert "только в блоке «Фильтр»" in str(e.value)

    # а в ноде «Фильтр» те же условия проходят
    seg.build_query(1, {"conditions": [
        {"field": "weekday", "op": "in", "value": "1"}]}, allow_now=True)

    # в конструктор рассылки эти поля вообще не отдаются как обычные
    meta = {f["key"]: f for f in seg.fields_meta([], [], [])}
    assert all(meta[k].get("node_only") for k in ("weekday", "run_date", "run_time"))


def test_run_moment_values_are_validated():
    """Кривое значение — понятная ошибка, а не молча пропущенное условие."""
    from app import segment as seg

    def check(cond, hint):
        with pytest.raises(seg.SegmentError) as e:
            seg.build_query(None, {"conditions": [cond]}, allow_now=True)
        assert hint in str(e.value), (cond, str(e.value))

    check({"field": "weekday", "op": "in", "value": "8"}, "день недели")
    check({"field": "weekday", "op": "in", "value": ""}, "не заполнено")
    check({"field": "run_time", "op": "after", "value": "25:00"}, "время")
    check({"field": "run_time", "op": "between", "value": "10:00-"}, "время")
    check({"field": "run_time", "op": "between", "value": ""}, "не заполнено")
    check({"field": "run_date", "op": "on", "value": "не дата"}, "дата")


def test_timezone_setting_survives_nonsense():
    """Пояс из настроек: неизвестный не должен ломать фильтры молча."""
    from app import tz

    assert tz.is_valid("Europe/Kyiv")
    assert not tz.is_valid("Марс/Олимп")
    assert tz.set_timezone("Europe/Warsaw") == "Europe/Warsaw"
    assert tz.set_timezone("Марс/Олимп") == "Europe/Warsaw"   # оставили прежний
    assert tz.now().tzinfo is not None
    assert all(tz.is_valid(v) for v, _ in tz.COMMON)
    tz.set_timezone(tz.DEFAULT_TZ)


def test_ai_screenshot_request_shape():
    """Скриншоты уходят модели картинками, а не описанием картинок."""
    from app import ai

    images = [{"media_type": "image/png", "data": "AAAA"},
              {"media_type": "image/jpeg", "data": "BBBB"}]

    a = ai._anthropic_content("собери воронку", images)
    assert [p["type"] for p in a] == ["image", "image", "text"]
    assert a[0]["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}
    assert a[-1]["text"] == "собери воронку"      # текст идёт последним

    o = ai._openai_content("собери воронку", images)
    assert [p["type"] for p in o] == ["image_url", "image_url", "text"]
    assert o[0]["image_url"]["url"] == "data:image/png;base64,AAAA"

    # без картинок формат запроса не меняется — обычный текст
    assert ai._anthropic_content("текст", None) == "текст"
    assert ai._openai_content("текст", []) == "текст"


def test_ai_screenshot_prompt_forbids_guessing():
    """Промпт для скриншотов должен запрещать выдумывать, а не поощрять «похоже»."""
    from app import ai

    p = ai.SCREENSHOT_PROMPT
    for must in ["НЕ ГАДАЙ", "note", "ДОСЛОВНО", ai.PLACEHOLDER_PATH]:
        assert must in p, must
    # заглушка — реальный файл в репозитории, иначе воронка соберётся с битой картинкой
    from pathlib import Path
    assert (Path(ai.__file__).parent / "assets" / "ai-placeholder.png").is_file()


@pytest.mark.asyncio
async def test_ai_screens_upload_and_load(tmp_path, monkeypatch):
    """Загрузка скриншотов: чужие форматы и пути наружу из media не проходят."""
    from fastapi import HTTPException

    from app import api as api_mod
    from app.config import settings

    monkeypatch.setattr(settings, "media_dir", str(tmp_path))
    (tmp_path / "screen_ok.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    loaded = api_mod._load_screens(["media/screen_ok.png"])
    assert loaded[0]["media_type"] == "image/png"
    assert loaded[0]["data"]                     # base64 непустой

    # выход за media — отказ, а не чтение чужого файла
    for bad in ["../../etc/passwd", "media/../../etc/passwd", "media/нет.png"]:
        with pytest.raises(HTTPException) as e:
            api_mod._load_screens([bad])
        assert e.value.status_code == 400

    # чужое расширение внутри media тоже не читаем
    (tmp_path / "doc.pdf").write_bytes(b"%PDF")
    with pytest.raises(HTTPException):
        api_mod._load_screens(["media/doc.pdf"])


def test_ai_placeholder_lands_in_media(tmp_path, monkeypatch):
    """Заглушка появляется в media сама — до того, как её попросит воронка."""
    from pathlib import Path

    from app import ai
    from app.config import settings

    monkeypatch.setattr(settings, "media_dir", str(tmp_path))
    path = ai.ensure_placeholder()
    assert path == ai.PLACEHOLDER_PATH
    assert (Path(tmp_path) / "ai-placeholder.png").is_file()

    # повторный вызов ничего не ломает и не перезаписывает
    mine = "уже заменили".encode()
    (Path(tmp_path) / "ai-placeholder.png").write_bytes(mine)
    ai.ensure_placeholder()
    assert (Path(tmp_path) / "ai-placeholder.png").read_bytes() == mine


@pytest.mark.asyncio
async def test_ai_spec_supports_every_block(session):
    """Язык ТЗ покрывает все блоки конструктора — и туда, и обратно."""
    from app import ai
    from app.models import Funnel, Tag

    session.add(Tag(name="vip"))
    chain = Funnel(name="Прогрев", is_chain=True, graph_ui={}, graph={})
    session.add(chain)
    await session.flush()

    spec = {
        "name": "Полная воронка",
        "trigger_type": "start",
        "nodes": [
            {"id": "m1", "type": "message", "text": "Привет", "text_first": True,
             "photo_url": "media/x.png",
             "buttons": [{"label": "Да", "style": "success", "next": "f1"},
                         {"label": "Сайт", "url": "https://ya.ru"}],
             "next": "f1"},
            {"id": "f1", "type": "filter", "match": "any",
             "conditions": [{"field": "tag", "op": "has", "value": "vip"},
                            {"field": "run_time", "op": "between", "value": "10:00-18:00"}],
             "yes": "a1", "no": "a2"},
            {"id": "a1", "type": "action", "op": "check_subscription",
             "channel": "@news", "yes": "ch1", "no": "a3"},
            {"id": "a2", "type": "action", "op": "unsubscribe", "next": None},
            {"id": "a3", "type": "action", "op": "delete_message", "target": "m1", "next": "ch1"},
            {"id": "ch1", "type": "chain", "chain": "Прогрев", "next": "d1"},
            {"id": "d1", "type": "delay", "amount": 2, "unit": "hours", "next": None},
        ],
    }

    tag_ids = await ai.ensure_tags(session, spec)
    chain_ids = await ai.resolve_chains(session, spec)
    fields = ai.spec_to_funnel_fields(spec, tag_ids, chain_ids)   # тут же compile_graph
    nodes = fields["graph"]["nodes"]
    by_type = {}
    for nid, n in nodes.items():
        by_type.setdefault(n["type"], []).append((nid, n))

    # все типы доехали до графа
    assert set(by_type) == {"start", "message", "filter", "action", "chain", "delay"}

    msg_id, msg = by_type["message"][0]
    assert msg["data"]["text_first"] is True
    assert msg["data"]["buttons"][0]["style"] == "success"

    _, filt = by_type["filter"][0]
    conds = filt["data"]["filter"]["conditions"]
    assert filt["data"]["filter"]["match"] == "any"
    # тег превратился из имени в id, время осталось как есть
    assert conds[0]["value"] == str(tag_ids["vip"])
    assert conds[1] == {"field": "run_time", "op": "between", "value": "10:00-18:00"}
    assert len(filt["outputs"]) == 2

    acts = {n["data"]["op"]: (nid, n) for nid, n in by_type["action"]}
    assert acts["check_subscription"][1]["data"]["channel"] == "@news"
    assert len(acts["check_subscription"][1]["outputs"]) == 2
    # цель удаления — внутренний id блока, а не id из ТЗ
    assert acts["delete_message"][1]["data"]["target"] == msg_id

    _, ch = by_type["chain"][0]
    assert ch["data"]["funnel_id"] == str(chain.id)
    # карточки рисуются в graph_ui — там же, где их видит редактор
    cards = fields["graph_ui"]["drawflow"]["Home"]["data"]
    assert "Прогрев" in cards[by_type["chain"][0][0]]["html"]   # имя, а не номер
    assert "подходит" in cards[by_type["filter"][0][0]]["html"]
    assert "подписан" in cards[acts["check_subscription"][0]]["html"]

    # обратная разборка: то же самое читается назад без потерь
    f = Funnel(name=fields["name"], graph=fields["graph"], graph_ui=fields["graph_ui"],
               trigger_type="start")
    back = ai.graph_to_spec(f)
    types = {n["type"] for n in back["nodes"]}
    assert types == {"message", "filter", "action", "chain", "delay"}
    b_filter = next(n for n in back["nodes"] if n["type"] == "filter")
    assert b_filter["match"] == "any" and len(b_filter["conditions"]) == 2
    b_check = next(n for n in back["nodes"]
                   if n["type"] == "action" and n["op"] == "check_subscription")
    assert b_check["channel"] == "@news" and b_check["yes"] and b_check["no"]
    b_chain = next(n for n in back["nodes"] if n["type"] == "chain")
    assert str(b_chain["chain_id"]) == str(chain.id)
    b_msg = next(n for n in back["nodes"] if n["type"] == "message")
    assert b_msg["text_first"] is True and b_msg["buttons"][0]["style"] == "success"


@pytest.mark.asyncio
async def test_ai_spec_rejects_invented_things(session):
    """Придуманное поле фильтра, действие или цепочка — ошибка, а не тихий пропуск."""
    from app import ai
    from app.models import Funnel

    session.add(Funnel(name="Прогрев", is_chain=True, graph_ui={}, graph={}))
    await session.flush()

    def build(nodes):
        return ai.spec_to_funnel_fields({"nodes": nodes}, {}, {"Прогрев": 1})

    # несуществующее поле фильтра
    with pytest.raises(ai.AIError) as e:
        build([{"id": "f", "type": "filter",
                "conditions": [{"field": "оплатил", "op": "yes", "value": "1"}]}])
    assert "нет поля" in str(e.value)

    # поле есть, операции нет
    with pytest.raises(ai.AIError) as e:
        build([{"id": "f", "type": "filter",
                "conditions": [{"field": "language", "op": "contains", "value": "ru"}]}])
    assert "нет операции" in str(e.value)

    # фильтр без условий пропускал бы всех
    with pytest.raises(ai.AIError) as e:
        build([{"id": "f", "type": "filter", "conditions": []}])
    assert "пропускает всех" in str(e.value)

    # выдуманное действие
    with pytest.raises(ai.AIError) as e:
        build([{"id": "a", "type": "action", "op": "выдать_деньги"}])
    assert "неизвестное действие" in str(e.value)

    # удаление сообщения из несуществующего блока
    with pytest.raises(ai.AIError) as e:
        build([{"id": "a", "type": "action", "op": "delete_message", "target": "нетакого"}])
    assert "удалять нечего" in str(e.value)

    # цепочка с придуманным названием: в ошибке видно, что есть на самом деле
    with pytest.raises(ai.AIError) as e:
        await ai.resolve_chains(session, {"nodes": [{"type": "chain", "chain": "Выдумка"}]})
    assert "Выдумка" in str(e.value) and "Прогрев" in str(e.value)

    # существующая — находится
    assert await ai.resolve_chains(
        session, {"nodes": [{"type": "chain", "chain": "Прогрев"}]}) == {"Прогрев": 1}


@pytest.mark.asyncio
async def test_ai_chat_no_longer_refuses_new_blocks(session):
    """Раньше чат отказывался править воронки с «Фильтром» и «Цепочкой»."""
    from app import ai
    from app.models import Funnel, Tag

    tag = Tag(name="vip")
    session.add(tag)
    chain = Funnel(name="Прогрев", is_chain=True, graph_ui={}, graph={})
    session.add(chain)
    await session.flush()

    graph = {"start": "1", "nodes": {
        "1": {"type": "start", "data": {}, "outputs": {"output_1": ["2"]}},
        "2": {"type": "filter", "data": {"filter": {"match": "all", "conditions": [
            {"field": "tag", "op": "has", "value": str(tag.id)}]}},
            "outputs": {"output_1": ["3"]}},
        "3": {"type": "chain", "data": {"funnel_id": str(chain.id)}, "outputs": {}},
    }}
    f = Funnel(name="F", graph=graph, graph_ui={}, trigger_type="start")

    spec = ai.graph_to_spec(f)
    # id тега и цепочки заменяются на имена — так же, как это делает чат
    id2name = {str(tag.id): tag.name}
    for n in spec["nodes"]:
        for c in n.get("conditions") or []:
            if c.get("field") == "tag":
                c["value"] = id2name.get(str(c["value"]), c["value"])
    assert next(n for n in spec["nodes"] if n["type"] == "filter")["conditions"][0]["value"] == "vip"
    assert next(n for n in spec["nodes"] if n["type"] == "chain")["chain_id"] == str(chain.id)


def test_step_numbers_are_one_source_of_truth():
    """Номер шага одинаков везде: на плитке, в выгрузке конверсий и в отчёте."""
    from app import exports
    from app.funnel_input import funnel_steps
    from app.graph import compile_graph

    def msg(nid, nxt=None):
        return {"id": int(nid), "name": "message", "data": {"text": "т", "buttons": []},
                "inputs": {"input_1": {"connections": []}},
                "outputs": {"output_1": {"connections":
                    [{"node": str(nxt), "output": "input_1"}] if nxt else []}}}

    gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": msg("2", "3"),
        # задержка между сообщениями не должна занимать номер шага
        "3": {"id": 3, "name": "delay", "data": {"amount": 1, "unit": "hours"},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": [{"node": "4", "output": "input_1"}]}}},
        "4": msg("4", "5"),
        "5": msg("5"),
        "9": msg("9"),   # оторванный — уходит в конец
    })
    graph = compile_graph(gui)

    nums = exports.step_numbers(graph)
    assert nums == {"2": 1, "4": 2, "5": 3, "9": 4}
    # блоки, которых нет в выгрузке конверсий, номеров не получают
    assert "3" not in nums and "1" not in nums

    # выгрузка конверсий нумерует ровно так же
    from_export = {nid: i + 1 for i, (nid, _n) in enumerate(funnel_steps(graph))}
    assert from_export == nums


@pytest.mark.asyncio
async def test_funnels_report_shows_step_numbers(session):
    """В отчёте «Воронки» у сообщений виден тот же номер шага."""
    from app import exports
    from app.graph import compile_graph
    from app.models import Bot, Funnel, FunnelBot

    gui = _gui({
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "message", "data": {"text": "Привет", "buttons": []},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": [{"node": "3", "output": "input_1"}]}}},
        "3": {"id": 3, "name": "delay", "data": {"amount": 1, "unit": "hours"},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": [{"node": "4", "output": "input_1"}]}}},
        "4": {"id": 4, "name": "message", "data": {"text": "Оффер", "buttons": []},
              "inputs": {"input_1": {"connections": []}},
              "outputs": {"output_1": {"connections": []}}},
    })
    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    f = Funnel(name="F", is_active=True, trigger_type="start",
               graph_ui=gui, graph=compile_graph(gui))
    session.add(f)
    await session.flush()
    session.add(FunnelBot(funnel_id=f.id, bot_id=b.id))
    await session.flush()

    rows = await exports.funnels_rows(session)
    header = rows[0]
    # колонка со сквозным номером строки больше не зовётся «№»: её путали
    # со Step N, а это разные числа
    assert header[3] == "Порядок" and header[4] == "Шаг"

    labels = [r[4] for r in rows[1:]]
    assert any(l.startswith("Шаг 1 —") for l in labels)
    assert any(l.startswith("Шаг 2 —") for l in labels)
    # у задержки номера шага нет — в выгрузку конверсий она не попадает
    delay_row = next(r for r in rows[1:] if r[5] == "delay")
    assert not delay_row[4].startswith("Шаг")
    # сквозной номер и номер шага — разные: у второго сообщения строка 3, шаг 2
    offer = next(r for r in rows[1:] if r[4].startswith("Шаг 2"))
    assert offer[3] == 3


@pytest.mark.asyncio
async def test_language_filter_picks_regional_variants(session):
    """Выбрал «Португальский» — попадают и pt, и pt-br. Иначе полбазы мимо."""
    from sqlalchemy import select

    from app import segment as seg
    from app.models import Bot, Subscriber

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    codes = ["pt", "pt-br", "es", "es-419", "en", "ru", None]
    for i, code in enumerate(codes):
        session.add(Subscriber(bot_id=b.id, tg_id=100 + i, first_name="X",
                               is_active=True, language_code=code))
    await session.flush()

    async def found(value, op="equals"):
        q = seg.build_query(b.id, {"conditions": [
            {"field": "language", "op": op, "value": value}]})
        return sorted(s.language_code or "—" for s in (await session.execute(q)).scalars())

    # код без региона забирает и региональные варианты
    assert await found("pt") == ["pt", "pt-br"]
    assert await found("es") == ["es", "es-419"]
    # код с регионом — только он сам
    assert await found("pt-br") == ["pt-br"]
    # ничего лишнего не прилипает
    assert await found("en") == ["en"]
    assert await found("ru") == ["ru"]

    # «не равно» — это ровно отрицание, и неизвестный язык туда попадает
    assert await found("pt", "not_equals") == ["en", "es", "es-419", "ru", "—"]


def test_language_list_is_one_for_filter_and_node():
    """Список языков — один на весь сервис, и коды в нём настоящие."""
    from app import segment as seg

    meta = {f["key"]: f for f in seg.fields_meta([], [], [])}
    lang = meta["language"]
    assert lang["type"] == "choice"          # выпадающий список, а не поле ввода
    codes = [o["v"] for o in lang["options"]]

    # то, чем реально пользуются, — на месте
    for must in ("ru", "en", "uk", "es", "pt", "pt-br", "de", "pl", "tr", "ar"):
        assert must in codes, must
    assert len(codes) == len(set(codes)), "коды не должны повторяться"
    # коды в том виде, в каком их присылает Telegram: нижний регистр, дефис
    for c in codes:
        assert c == c.lower() and "_" not in c, c

    assert seg.language_label("pt-br") == "Португальский (Бразилия) (pt-br)"
    assert seg.language_label("xx") == "xx"          # незнакомый — как есть

    # человекочитаемое описание фильтра показывает язык словами
    out = seg.describe({"conditions": [
        {"field": "language", "op": "equals", "value": "ar"}]}, {}, {}, {})
    assert out == ["язык: = Арабский (ar)"]


SENDPULSE_CSV = (
    "telegram_id,id,username,full_name,status,last activity at,subscribed at,"
    "tags,operator,campaign\n"
    "6255797660,69d4f4f6de814a6fa80793ae,vadiml_smm,Vadim // Head Of SMM,active,"
    "19/08/2026 14:51:24,07/04/2026 12:13:42,Английский,,promo\n"
    "8352435188,69d66d7558338d761d0aa433,KseniaS,Ksenia S.,active,"
    "08/09/2026 15:05:03,08/04/2026 15:00:05,\"Английский,Арабский\",operator1,\n"
    "5921763711,69d81c42d8465320de0222f0,,Facundo Quintero,active,"
    "09/04/2026 21:38:13,09/04/2026 21:38:10,Испанский,,\n"
    "8209702571,69d66c2c2bc8717ef505a63e,smmadmin,smm_admin,blocked,"
    "08/04/2026 14:54:36,08/04/2026 14:54:36,,,\n"
)


def test_import_reads_sendpulse_export():
    """Выгрузка из SendPulse разбирается как есть, без ручной подготовки."""
    from app import subscriber_import as imp

    rows, problems, cols = imp.parse_rows(SENDPULSE_CSV.encode())
    assert problems == []
    assert len(rows) == 4
    # колонка telegram_id выиграла у общей колонки «id» — это чужой id сервиса
    assert rows[0]["tg_id"] == 6255797660

    first = rows[0]
    assert first["username"] == "vadiml_smm"
    # должность и слэши в имени не режем: «фамилия» тут была бы мусором
    assert first["first_name"] == "Vadim // Head Of SMM" and first["last_name"] is None
    assert first["is_active"] is True
    assert first["created_at"] == datetime(2026, 4, 7, 12, 13, 42)
    assert first["last_active_at"] == datetime(2026, 8, 19, 14, 51, 24)
    assert first["tags"] == ["Английский"]
    # чужие колонки не выбрасываем, а складываем следом в params
    assert first["params"]["import_id"] == "69d4f4f6de814a6fa80793ae"
    assert first["params"]["import_campaign"] == "promo"
    # пустая ячейка ключа не создаёт — незачем хранить пустоту
    assert "import_operator" not in first["params"]
    ksenia = next(r for r in rows if r["tg_id"] == 8352435188)
    assert ksenia["params"]["import_operator"] == "operator1"

    # «Имя Фамилия» из двух простых слов всё-таки разделяем
    facundo = next(r for r in rows if r["tg_id"] == 5921763711)
    assert (facundo["first_name"], facundo["last_name"]) == ("Facundo", "Quintero")

    assert next(r for r in rows if r["tg_id"] == 8352435188)["tags"] == ["Английский", "Арабский"]
    assert next(r for r in rows if r["tg_id"] == 8209702571)["is_active"] is False


def test_import_survives_messy_files():
    """Кривые строки не рушат импорт, а попадают в список проблем."""
    from app import subscriber_import as imp

    csv_text = (
        "Telegram ID;Username;Subscribed at\n"
        "123;alice;2026-04-07\n"
        "не число;bob;2026-04-07\n"
        "123;дубль;2026-04-08\n"
        "\n"
        "456;;непонятная дата\n"
    )
    rows, problems, _ = imp.parse_rows(csv_text.encode())
    assert [r["tg_id"] for r in rows] == [123, 456]
    assert any("не число" in p for p in problems)
    assert any("уже был в файле" in p for p in problems)
    # дату не разобрали — оставляем пустой, а не выдумываем
    assert rows[1]["created_at"] is None
    # пустая строка — не проблема, её просто нет
    assert len(problems) == 2

    # файл вообще без Telegram ID — понятный отказ
    with pytest.raises(ValueError) as e:
        imp.parse_rows(b"name,email\nVasya,v@mail.ru\n")
    assert "Telegram ID" in str(e.value)

    # Excel любит дописывать «.0» к числам
    rows, _, _ = imp.parse_rows(b"telegram_id\n6255797660.0\n")
    assert rows[0]["tg_id"] == 6255797660

    # кириллица в cp1251 тоже читается
    rows, _, _ = imp.parse_rows("telegram_id,tags\n777,Английский\n".encode("cp1251"))
    assert rows[0]["tags"] == ["Английский"]


@pytest.mark.asyncio
async def test_import_writes_and_repeats_safely(session):
    """Повторный импорт того же файла не плодит дублей и не портит живые данные."""
    from sqlalchemy import func, select

    from app import subscriber_import as imp
    from app.models import Bot, Subscriber, SubscriberTag, Tag

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()

    rows, _, _ = imp.parse_rows(SENDPULSE_CSV.encode())
    res = await imp.apply_rows(session, b.id, rows, update_existing=True)
    assert res["added"] == 4 and res["updated"] == 0

    subs = (await session.execute(select(Subscriber))).scalars().all()
    assert len(subs) == 4
    vadim = next(s for s in subs if s.tg_id == 6255797660)
    assert vadim.is_subscribed is True          # импортированные подписаны
    assert vadim.created_at == datetime(2026, 4, 7, 12, 13, 42)

    # теги созданы и привязаны
    assert {t.name for t in (await session.execute(select(Tag))).scalars()} == {
        "Английский", "Арабский", "Испанский"}
    links = (await session.execute(select(func.count()).select_from(SubscriberTag))).scalar()
    assert links == 4        # 1 + 2 + 1 + 0

    # бот успел узнать настоящее имя — выгрузка не должна его затирать
    vadim.first_name = "Вадим"
    vadim.language_code = "ru"
    await session.flush()

    res2 = await imp.apply_rows(session, b.id, rows, update_existing=True)
    assert res2["added"] == 0 and res2["updated"] == 4
    assert (await session.execute(select(func.count()).select_from(Subscriber))).scalar() == 4
    await session.refresh(vadim)
    assert vadim.first_name == "Вадим"          # своё не перезаписано
    assert vadim.language_code == "ru"
    # теги тоже не задвоились
    assert (await session.execute(select(func.count()).select_from(SubscriberTag))).scalar() == 4

    # режим «не трогать существующих»
    res3 = await imp.apply_rows(session, b.id, rows, update_existing=False)
    assert res3 == {"added": 0, "updated": 0, "skipped": 4,
                    "tags": ["Английский", "Арабский", "Испанский"]}


@pytest.mark.asyncio
async def test_import_survives_collisions(session):
    """Тот самый обвал на 34 тысячах строк.

    Импорт большой базы идёт частями, и части перекрываются: повторный запрос
    браузера, второй клик, залитый заново файл. Раньше код сначала спрашивал
    «а нет ли уже такого тега», потом вставлял — и падал на уникальном индексе
    («duplicate key ... tags_name_key», тег «Австрийский» уже был), унося с
    собой весь импорт. Вставка должна пропускать существующее, а не падать.
    """
    from sqlalchemy import func, select

    from app import subscriber_import as imp
    from app.models import Bot, Subscriber, SubscriberTag, Tag

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()

    # Тег уже существует — создан руками в интерфейсе или прошлой частью
    session.add(Tag(name="Австрийский"))
    await session.flush()

    # 1. Ровно то место, где всё рвалось: вставка строки, которая уже есть.
    # С обычным INSERT это IntegrityError и конец импорта.
    await session.execute(
        imp.insert_ignore(session, Tag, ["name"]),
        [{"name": "Австрийский", "created_at": datetime(2026, 9, 16)}])
    await session.flush()

    # и страховка от тихого отката: если session.bind однажды перестанет
    # отдавать диалект, insert_ignore выродится в обычный INSERT незаметно
    assert "ON CONFLICT" in str(imp.insert_ignore(session, Tag, ["name"]).compile()).upper(), (
        "insert_ignore откатился в обычный INSERT — импорт снова будет падать")

    # 2. Теперь целиком: файл с этим тегом заезжает в базу

    csv = ("telegram_id,tags\n"
           "111,\"Австрийский,Немецкий\"\n"
           "222,Австрийский\n").encode()
    rows, _, _ = imp.parse_rows(csv)
    res = await imp.apply_rows(session, b.id, rows, update_existing=True)
    assert res["added"] == 2

    # существующий тег переиспользован, а не задвоен
    names = sorted(t.name for t in (await session.execute(select(Tag))).scalars())
    assert names == ["Австрийский", "Немецкий"]

    # 3. Та же часть приезжает второй раз — ни падения, ни дублей
    await imp.apply_rows(session, b.id, rows, update_existing=True)
    assert (await session.execute(
        select(func.count()).select_from(Subscriber))).scalar() == 2
    assert (await session.execute(
        select(func.count()).select_from(Tag))).scalar() == 2
    assert (await session.execute(
        select(func.count()).select_from(SubscriberTag))).scalar() == 3   # 2 + 1


@pytest.mark.asyncio
async def test_import_is_per_bot(session):
    """База привязана к боту: у другого бота те же люди — отдельные записи."""
    from sqlalchemy import func, select

    from app import subscriber_import as imp
    from app.models import Bot, Subscriber

    b1 = Bot(name="Первый", token="t1", is_active=True)
    b2 = Bot(name="Второй", token="t2", is_active=True)
    session.add_all([b1, b2])
    await session.flush()

    rows, _, _ = imp.parse_rows(b"telegram_id\n111\n222\n")
    await imp.apply_rows(session, b1.id, rows, True)
    await imp.apply_rows(session, b2.id, rows, True)

    assert (await session.execute(select(func.count()).select_from(Subscriber))).scalar() == 4
    for bot in (b1, b2):
        got = (await session.execute(select(Subscriber).where(
            Subscriber.bot_id == bot.id))).scalars().all()
        assert sorted(s.tg_id for s in got) == [111, 222]


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

    owner = _user("owner")
    d = await broadcast_detail(bc.id, owner, session)
    assert d["text"] == "Привет!"
    assert len(d["media"]) == 2
    assert d["bot"] == "Мой бот"
    assert d["sent"] == 1 and d["failed"] == 1
    assert d["delivered"] == 1 and d["not_delivered"] == 1
    # условия сегмента расшифрованы словами, а не id
    assert d["audience_kind"] == "Сегмент"
    assert "тег: есть VIP" in d["audience"]
    # язык в описании теперь словами, а не голым кодом
    assert "язык: = Русский (ru)" in d["audience"]
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

    d = await broadcast_detail(bc.id, _user("owner"), session)
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
        "/api/auth/telegram",   # вход через Telegram: подпись проверяется внутри
        "/api/auth/telegram/config",  # какой бот на кнопке входа — видно и так на странице
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


# ---------- изоляция по ботам и воронкам ----------

@pytest_asyncio.fixture
async def two_bots(session):
    """Два бота, у каждого свой подписчик и своя воронка."""
    from app.models import Bot, Funnel, FunnelBot, Subscriber

    b1 = Bot(name="Мой", token="t1")
    b2 = Bot(name="Чужой", token="t2")
    session.add_all([b1, b2])
    await session.flush()

    s1 = Subscriber(bot_id=b1.id, tg_id=11, first_name="Свой")
    s2 = Subscriber(bot_id=b2.id, tg_id=22, first_name="Чужой")
    f1 = Funnel(name="Воронка 1", trigger_type="start", graph={}, graph_ui={})
    f2 = Funnel(name="Воронка 2", trigger_type="start", graph={}, graph_ui={})
    session.add_all([s1, s2, f1, f2])
    await session.flush()
    session.add_all([FunnelBot(funnel_id=f1.id, bot_id=b1.id),
                     FunnelBot(funnel_id=f2.id, bot_id=b2.id)])
    await session.commit()
    return {"b1": b1, "b2": b2, "s1": s1, "s2": s2, "f1": f1, "f2": f2}


@pytest.mark.asyncio
async def test_subscribers_limited_to_own_bots(session, two_bots):
    """Сотрудник с доступом к одному боту не видит чужих подписчиков."""
    from app.api import list_subscribers, subscribers_search, SegmentSearchIn

    staff = _user(perms={"subscribers": "view"})
    staff.bot_ids = [two_bots["b1"].id]

    rows = await list_subscribers(user=staff, session=session)
    assert [r["first_name"] for r in rows] == ["Свой"]

    res = await subscribers_search(SegmentSearchIn(filter={}), user=staff, session=session)
    assert res["total"] == 1
    assert res["subscribers"][0]["first_name"] == "Свой"

    # владелец видит обоих
    owner_rows = await list_subscribers(user=_user("owner"), session=session)
    assert len(owner_rows) == 2


@pytest.mark.asyncio
async def test_cannot_open_foreign_subscriber(session, two_bots):
    """Прямой запрос чужого подписчика по id — 403, а не выдача данных."""
    from fastapi import HTTPException
    from app.api import get_subscriber, get_messages

    staff = _user(perms={"subscribers": "view", "chat": "view"})
    staff.bot_ids = [two_bots["b1"].id]

    ok = await get_subscriber(two_bots["s1"].id, user=staff, session=session)
    assert ok["id"] == two_bots["s1"].id

    for fn in (get_subscriber, get_messages):
        with pytest.raises(HTTPException) as e:
            await fn(two_bots["s2"].id, user=staff, session=session)
        assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_funnels_limited_by_bots_and_explicit_list(session, two_bots):
    """Воронки фильтруются и по доступным ботам, и по явному списку."""
    from fastapi import HTTPException
    from app.api import get_funnel, list_funnels

    staff = _user(perms={"funnels": "view"})
    staff.bot_ids = [two_bots["b1"].id]

    got = await list_funnels(user=staff, session=session)
    assert [f["name"] for f in got] == ["Воронка 1"]

    with pytest.raises(HTTPException) as e:
        await get_funnel(two_bots["f2"].id, user=staff, session=session)
    assert e.value.status_code == 403

    # явный список воронок сужает ещё сильнее
    staff2 = _user(perms={"funnels": "view"})
    staff2.funnel_ids = [two_bots["f2"].id]      # доступ ко всем ботам, но одна воронка
    got2 = await list_funnels(user=staff2, session=session)
    assert [f["name"] for f in got2] == ["Воронка 2"]


@pytest.mark.asyncio
async def test_broadcasts_limited_to_own_bots(session, two_bots):
    from fastapi import HTTPException
    from app.api import broadcast_detail, list_broadcasts
    from app.models import Broadcast

    bc1 = Broadcast(bot_id=two_bots["b1"].id, name="Своя", text="a", filters={})
    bc2 = Broadcast(bot_id=two_bots["b2"].id, name="Чужая", text="b", filters={})
    session.add_all([bc1, bc2])
    await session.commit()

    staff = _user(perms={"broadcasts": "view"})
    staff.bot_ids = [two_bots["b1"].id]

    got = await list_broadcasts(user=staff, session=session)
    assert [b["name"] for b in got] == ["Своя"]

    with pytest.raises(HTTPException) as e:
        await broadcast_detail(bc2.id, staff, session)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_analytics_counts_only_own_bots(session, two_bots):
    from app import analytics as an
    from app.auth import allowed_bot_ids

    staff = _user(perms={"analytics": "view"})
    staff.bot_ids = [two_bots["b1"].id]

    an.invalidate_analytics_cache()
    mine = await an.build_analytics(
        session, days=30, allowed_bots=await allowed_bot_ids(staff, session))
    assert mine["totals"]["subscribers"] == 1

    an.invalidate_analytics_cache()
    all_ = await an.build_analytics(session, days=30, allowed_bots=None)
    assert all_["totals"]["subscribers"] == 2


@pytest.mark.asyncio
async def test_delete_needs_separate_permission():
    """Права «изменять» мало — на удаление нужна отдельная галка."""
    from fastapi import HTTPException
    from app.auth import can_delete, require_delete

    editor = _user(perms={"subscribers": "edit", "funnels": "edit"})
    assert not can_delete(editor)
    with pytest.raises(HTTPException) as e:
        await require_delete(user=editor)
    assert e.value.status_code == 403
    assert "Удаление" in e.value.detail

    with_delete = _user(perms={"subscribers": "edit", "delete": "edit"})
    assert can_delete(with_delete)
    assert await require_delete(user=with_delete) is with_delete

    assert can_delete(_user("owner"))


# ---------- выгрузка статистики ----------

@pytest.mark.asyncio
async def test_export_funnel_steps_numbers(session):
    """Лист «Шаги воронок»: порядок шагов и проценты считаются верно."""
    from app import exports
    from app.models import Bot, Funnel, FunnelBot, FunnelRun, NodeVisit, Subscriber

    bot = Bot(name="Бот", token="t")
    session.add(bot)
    await session.flush()

    # старт -> сообщение А -> сообщение Б
    graph = {
        "start": "1",
        "nodes": {
            "1": {"type": "start", "data": {}, "outputs": {"output_1": ["2"]}},
            "2": {"type": "message", "data": {"text": "Привет"}, "outputs": {"output_1": ["3"]}},
            "3": {"type": "message", "data": {"text": "Оффер"}, "outputs": {}},
        },
    }
    f = Funnel(name="Прогрев", trigger_type="start", graph=graph, graph_ui={})
    session.add(f)
    await session.flush()
    session.add(FunnelBot(funnel_id=f.id, bot_id=bot.id))

    # 10 вошли, 10 увидели первое сообщение, 4 дошли до второго
    for i in range(10):
        sub = Subscriber(bot_id=bot.id, tg_id=1000 + i)
        session.add(sub)
        await session.flush()
        run = FunnelRun(funnel_id=f.id, subscriber_id=sub.id, status="active")
        session.add(run)
        await session.flush()
        session.add(NodeVisit(funnel_id=f.id, node_id="2", subscriber_id=sub.id, run_id=run.id))
        if i < 4:
            session.add(NodeVisit(funnel_id=f.id, node_id="3", subscriber_id=sub.id, run_id=run.id))
    await session.commit()

    rows = await exports.funnels_rows(session)
    header, *data = rows
    assert header[:3] == ["Воронка", "Боты", "Статус"]

    # строка входа + два шага, в порядке прохождения; у сообщений в названии
    # стоит номер шага — тот же, что на плитке и в колонке «Step N users»
    assert [r[4] for r in data] == ["Вошли в воронку", "Шаг 1 — Привет", "Шаг 2 — Оффер"]
    entered, step1, step2 = data
    assert entered[6] == 10
    assert step1[6] == 10 and step1[7] == 100.0     # 100% от входа
    assert step2[6] == 4
    assert step2[7] == 40.0                          # 40% от входа
    assert step2[8] == 40.0                          # и 40% от предыдущего шага


@pytest.mark.asyncio
async def test_export_respects_bot_access(session):
    """Сотрудник выгружает только свои воронки и своих ботов."""
    from app import exports
    from app.models import Bot, Funnel, FunnelBot

    b1, b2 = Bot(name="Мой", token="t1"), Bot(name="Чужой", token="t2")
    session.add_all([b1, b2])
    await session.flush()
    f1 = Funnel(name="Своя", trigger_type="start", graph={"nodes": {}}, graph_ui={})
    f2 = Funnel(name="Чужая", trigger_type="start", graph={"nodes": {}}, graph_ui={})
    session.add_all([f1, f2])
    await session.flush()
    session.add_all([FunnelBot(funnel_id=f1.id, bot_id=b1.id),
                     FunnelBot(funnel_id=f2.id, bot_id=b2.id)])
    await session.commit()

    rows = await exports.funnels_rows(session, allowed_bots=[b1.id])
    names = {r[0] for r in rows[1:]}
    assert names == {"Своя"}

    bots = await exports.bots_rows(session, days=30, allowed_bots=[b1.id])
    assert [r[0] for r in bots[1:]] == ["Мой"]


@pytest.mark.asyncio
async def test_export_build_tabs_selection(session):
    """Выгружаются только отмеченные листы, плюс служебный «Обновлено»."""
    from app import exports

    tabs = await exports.build_tabs(session, {"funnels": True, "bots": False}, days=7)
    assert set(tabs) == {"Шаги воронок", "Обновлено"}

    tabs2 = await exports.build_tabs(session, {"bots": True, "broadcasts": True}, days=7)
    assert set(tabs2) == {"Боты", "Рассылки", "Обновлено"}


@pytest.mark.asyncio
async def test_sheets_rejects_bad_key():
    """Понятная ошибка вместо технической, если вставили не тот файл."""
    from app.sheets import SheetsError, get_access_token

    with pytest.raises(SheetsError) as e:
        await get_access_token({"foo": "bar"})
    assert "client_email" in str(e.value)
    assert "сервисного аккаунта" in str(e.value)


# ---------- премиум-эмодзи ----------

def test_emoji_set_link_parsing():
    from app.bot.premium_emoji import parse_set_name

    assert parse_set_name("https://t.me/addemoji/RestrictedEmoji") == "RestrictedEmoji"
    assert parse_set_name("t.me/addemoji/Some_Set/") == "Some_Set"
    assert parse_set_name("tg://addemoji?set=abc") == "abc"
    assert parse_set_name("  plain_name ") == "plain_name"
    assert parse_set_name("https://t.me/addstickers/Pack") == "Pack"  # тип проверит Telegram
    assert parse_set_name("https://example.com/addemoji/x") is None
    assert parse_set_name("") is None


def test_strip_tg_emoji():
    from app.bot.premium_emoji import strip_tg_emoji

    html = 'Привет <tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji> и <b><tg-emoji emoji-id="1">🎁</tg-emoji></b>'
    assert strip_tg_emoji(html) == "Привет 🔥 и <b>🎁</b>"
    assert strip_tg_emoji(None) is None


def _premium_msg(**kw):
    from aiogram.methods import SendMessage
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="Жми", url="https://x.y", icon_custom_emoji_id="555")]])
    return SendMessage(chat_id=1, text='Акция <tg-emoji emoji-id="777">🎁</tg-emoji>',
                       reply_markup=kb, **kw)


async def test_emoji_guard_retries_without_premium():
    """Telegram отверг премиум-эмодзи — то же сообщение уходит без них."""
    from aiogram.exceptions import TelegramBadRequest

    from app.bot.premium_emoji import PremiumEmojiGuard, emoji_report

    sent = []

    async def make_request(bot, method):
        sent.append(method)
        if "<tg-emoji" in method.text or method.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id:
            raise TelegramBadRequest(method=method, message="Bad Request: custom emoji not allowed")
        return "ok"

    report = []
    tok = emoji_report.set(report)
    try:
        res = await PremiumEmojiGuard()(make_request, None, _premium_msg())
    finally:
        emoji_report.reset(tok)
    assert res == "ok"
    assert len(sent) == 2
    assert sent[1].text == "Акция 🎁"
    assert sent[1].reply_markup.inline_keyboard[0][0].icon_custom_emoji_id is None
    assert sent[1].reply_markup.inline_keyboard[0][0].url == "https://x.y"
    assert report and report[0]["kind"] == "rejected"


async def test_emoji_guard_other_errors_are_not_masked():
    """Ошибка не из-за эмодзи — после одного повтора отдаётся как есть."""
    import pytest
    from aiogram.exceptions import TelegramBadRequest

    from app.bot.premium_emoji import PremiumEmojiGuard

    calls = []

    async def make_request(bot, method):
        calls.append(method)
        raise TelegramBadRequest(method=method, message="Bad Request: chat not found")

    with pytest.raises(TelegramBadRequest):
        await PremiumEmojiGuard()(make_request, None, _premium_msg())
    assert len(calls) == 2


async def test_emoji_guard_plain_messages_untouched_and_downgrade_noticed():
    from aiogram.methods import SendMessage
    from aiogram.types import MessageEntity

    from app.bot.premium_emoji import PremiumEmojiGuard, emoji_report

    class Msg:
        message_id = 5
        entities = []
        caption_entities = None

    calls = []

    async def make_request(bot, method):
        calls.append(method)
        return Msg()

    plain = SendMessage(chat_id=1, text="<b>просто</b> текст 🎁")
    assert isinstance(await PremiumEmojiGuard()(make_request, None, plain), Msg)
    assert calls == [plain]          # без премиум-эмодзи — ни копий, ни проверок

    report = []
    tok = emoji_report.set(report)
    try:
        await PremiumEmojiGuard()(make_request, None, _premium_msg())
    finally:
        emoji_report.reset(tok)
    assert [r["kind"] for r in report] == ["downgraded"]

    Msg.entities = [MessageEntity(type="custom_emoji", offset=6, length=2, custom_emoji_id="777")]
    report = []
    tok = emoji_report.set(report)
    try:
        await PremiumEmojiGuard()(make_request, None, _premium_msg())
    finally:
        emoji_report.reset(tok)
    assert report == []


async def test_emoji_guard_media_group_captions():
    from aiogram.methods import SendMediaGroup
    from aiogram.types import InputMediaPhoto

    from app.bot.premium_emoji import method_uses_premium, strip_method

    m = SendMediaGroup(chat_id=1, media=[
        InputMediaPhoto(media="a", caption='x <tg-emoji emoji-id="9">⭐</tg-emoji>'),
        InputMediaPhoto(media="b"),
    ])
    assert method_uses_premium(m)
    s = strip_method(m)
    assert s.media[0].caption == "x ⭐" and s.media[1].media == "b"
    assert not method_uses_premium(s)


async def test_emoji_set_import(tmp_path):
    """Набор забирается целиком, миниатюры ложатся в media/emoji/."""
    import pytest
    from types import SimpleNamespace as NS

    from app.bot.premium_emoji import fetch_set

    class FakeBot:
        def __init__(self, sticker_type="custom_emoji"):
            self.sticker_type = sticker_type
            self.downloaded = []

        async def get_sticker_set(self, name):
            return NS(name=name, title="Подарки", sticker_type=self.sticker_type, stickers=[
                NS(custom_emoji_id="1", emoji="🎁", file_id="f1", is_animated=True,
                   is_video=False, thumbnail=NS(file_id="t1")),
                NS(custom_emoji_id="2", emoji="🔥", file_id="f2", is_animated=False,
                   is_video=False, thumbnail=None),        # статичная: берём сам файл
                NS(custom_emoji_id="3", emoji="✨", file_id="f3", is_animated=True,
                   is_video=False, thumbnail=None),        # tgs без миниатюры: без картинки
            ])

        async def download(self, file_id, destination):
            self.downloaded.append(file_id)
            destination.write_bytes(b"RIFF....WEBP")

    bot = FakeBot()
    data = await fetch_set(bot, "Gifts", tmp_path)
    assert data["title"] == "Подарки"
    assert [(i["emoji_id"], i["emoji"], i["has_thumb"]) for i in data["items"]] == [
        ("1", "🎁", True), ("2", "🔥", True), ("3", "✨", False)]
    assert sorted(bot.downloaded) == ["f2", "t1"]
    assert (tmp_path / "emoji" / "1.webp").is_file()

    # второй раз уже скачанное не тянем
    bot.downloaded.clear()
    await fetch_set(bot, "Gifts", tmp_path)
    assert bot.downloaded == []

    with pytest.raises(ValueError, match="набор стикеров"):
        await fetch_set(FakeBot("regular"), "Pack", tmp_path)


async def test_broadcast_buttons_keep_only_numeric_icon():
    from app.api import _clean_broadcast_buttons

    out = _clean_broadcast_buttons([
        {"label": "a", "url": "https://x", "icon_custom_emoji_id": "5368324170671202286"},
        {"label": "b", "url": "https://x", "icon_custom_emoji_id": "<script>"},
    ])
    assert out[0]["icon_custom_emoji_id"] == "5368324170671202286"
    assert "icon_custom_emoji_id" not in out[1]


# ---------- причины недоставки рассылки ----------

def test_delivery_error_classification():
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
    from aiogram.methods import SendMessage

    from app.bot.delivery import classify

    m = SendMessage(chat_id=1, text="x")
    cases = [
        (TelegramForbiddenError(m, "Forbidden: bot was blocked by the user"), "blocked"),
        (TelegramForbiddenError(m, "Forbidden: user is deactivated"), "deactivated"),
        (TelegramForbiddenError(m, "Forbidden: bot can't initiate conversation with a user"), "chat_not_found"),
        (TelegramBadRequest(m, "Bad Request: chat not found"), "chat_not_found"),
        (TelegramBadRequest(m, "Bad Request: PEER_ID_INVALID"), "chat_not_found"),
        (TelegramBadRequest(m, "Bad Request: wrong file identifier/HTTP URL specified"), "media"),
        (TelegramBadRequest(m, "Bad Request: can't parse entities: unclosed tag"), "bad_request"),
        (TelegramNetworkError(m, "Request timeout error"), "network"),
        (RuntimeError("что-то странное"), "other"),
    ]
    for exc, code in cases:
        assert classify(exc)[0] == code, (exc, code)


async def test_broadcast_stores_failure_reasons(session, monkeypatch):
    """Рассылка сохраняет причину у каждого недошедшего, недостижимых
    помечает неактивными, а разбивка видна в карточке."""
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
    from aiogram.methods import SendMessage
    from sqlalchemy import select

    from app.api import broadcast_detail
    from app.bot import runner
    from app.models import Bot, Broadcast, BroadcastRecipient, Subscriber

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    subs = [Subscriber(bot_id=b.id, tg_id=tg, first_name=f"S{tg}", is_active=True) for tg in range(1, 7)]
    session.add_all(subs)
    bc = Broadcast(bot_id=b.id, name="Акция", text="hi", filters={"segment": {}}, status="running")
    session.add(bc)
    await session.commit()

    m = SendMessage(chat_id=1, text="x")
    errors = {
        2: TelegramForbiddenError(m, "Forbidden: bot was blocked by the user"),
        3: TelegramForbiddenError(m, "Forbidden: bot was blocked by the user"),
        4: TelegramBadRequest(m, "Bad Request: chat not found"),
        5: TelegramBadRequest(m, "Bad Request: message is too long"),
    }

    class FakeBot:
        async def send_message(self, chat_id, *a, **k):
            if chat_id in errors:
                raise errors[chat_id]
            return type("M", (), {"message_id": 1})()

    monkeypatch.setattr(runner.settings, "broadcast_rate", 1000.0)
    await runner._process_broadcast(session, FakeBot(), bc)
    await session.commit()

    rows = {r.subscriber_id: r for r in (await session.execute(select(BroadcastRecipient))).scalars()}
    by_tg = {s.tg_id: s for s in subs}
    assert rows[by_tg[1].id].delivered and rows[by_tg[1].id].error_code is None
    assert rows[by_tg[2].id].error_code == "blocked"
    assert rows[by_tg[4].id].error_code == "chat_not_found"
    assert rows[by_tg[5].id].error_code == "bad_request"
    assert "too long" in rows[by_tg[5].id].error_text
    assert rows[by_tg[2].id].error_text is None      # понятным причинам текст не нужен
    for s in subs:
        await session.refresh(s)
    # недостижимые больше не получают рассылки; «плохое сообщение» — не вина человека
    assert [s.is_active for s in subs] == [True, False, False, False, True, True]
    assert (bc.sent, bc.failed, bc.total) == (2, 4, 6)

    d = await broadcast_detail(bc.id, _user("owner"), session)
    got = {r["code"]: (r["count"], r["pct"]) for r in d["reasons"]}
    assert got == {"blocked": (2, 50.0), "chat_not_found": (1, 25.0), "bad_request": (1, 25.0)}
    assert d["reasons"][0]["code"] == "blocked"       # крупные сверху
    assert any("too long" in t for t in d["error_samples"])

    only = await broadcast_detail(bc.id, _user("owner"), session, only_failed=True)
    assert len(only["recipients"]) == 4 and all(not r["delivered"] for r in only["recipients"])
    assert {r["reason"] for r in only["recipients"]} >= {"заблокировал бота"}


async def test_broadcast_reasons_for_old_broadcasts(session):
    """У рассылок до учёта причин разбивка строится по статусу подписчика,
    а аудитория не бывает меньше, чем отправлено + не дошло."""
    from app.api import broadcast_detail
    from app.models import Bot, Broadcast, BroadcastRecipient, Subscriber

    b = Bot(name="B", token="t")
    session.add(b)
    await session.flush()
    s1 = Subscriber(bot_id=b.id, tg_id=1, is_active=False)
    s2 = Subscriber(bot_id=b.id, tg_id=2, is_active=True)
    s3 = Subscriber(bot_id=b.id, tg_id=3, is_active=True)
    session.add_all([s1, s2, s3])
    bc = Broadcast(bot_id=b.id, name="Old", text="x", status="done", total=2, sent=1, failed=2)
    session.add(bc)
    await session.flush()
    session.add_all([
        BroadcastRecipient(broadcast_id=bc.id, subscriber_id=s1.id, delivered=False),
        BroadcastRecipient(broadcast_id=bc.id, subscriber_id=s2.id, delivered=False),
        BroadcastRecipient(broadcast_id=bc.id, subscriber_id=s3.id, delivered=True),
    ])
    await session.commit()

    d = await broadcast_detail(bc.id, _user("owner"), session)
    assert {r["code"]: r["count"] for r in d["reasons"]} == {"legacy_inactive": 1, "legacy_unknown": 1}
    assert d["total"] == 3


async def test_broadcast_resume_keeps_audience(session, monkeypatch):
    """После обрыва аудитория = уже обработанные + оставшиеся, даже если
    часть обработанных за это время стала неактивной."""
    from sqlalchemy import select

    from app.bot import runner
    from app.models import Bot, Broadcast, BroadcastRecipient, Subscriber

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    subs = [Subscriber(bot_id=b.id, tg_id=tg, is_active=True) for tg in range(1, 5)]
    session.add_all(subs)
    bc = Broadcast(bot_id=b.id, name="R", text="hi", filters={"segment": {}}, status="running",
                   sent=1, failed=1)
    session.add(bc)
    await session.flush()
    # первые двое уже обработаны до обрыва, второй заблокировал бота
    subs[1].is_active = False
    session.add_all([
        BroadcastRecipient(broadcast_id=bc.id, subscriber_id=subs[0].id, delivered=True),
        BroadcastRecipient(broadcast_id=bc.id, subscriber_id=subs[1].id, delivered=False,
                           error_code="blocked"),
    ])
    await session.commit()

    class FakeBot:
        async def send_message(self, *a, **k):
            return type("M", (), {"message_id": 1})()

    monkeypatch.setattr(runner.settings, "broadcast_rate", 1000.0)
    await runner._process_broadcast(session, FakeBot(), bc)
    assert (bc.total, bc.sent, bc.failed) == (4, 3, 1)
    n = len((await session.execute(select(BroadcastRecipient))).scalars().all())
    assert n == 4


# ---------- AI-чат воронки: раскладка и скриншоты ----------

def test_ai_layout_command_is_sanitized():
    from app.ai import clean_layout

    assert clean_layout({"direction": "horizontal", "spacing": "wide"}) == \
        {"direction": "horizontal", "spacing": "wide"}
    assert clean_layout({"direction": "VERTICAL"}) == {"direction": "vertical", "spacing": "normal"}
    assert clean_layout({"direction": "vertical", "spacing": "huge"})["spacing"] == "normal"
    assert clean_layout({"direction": "diagonal"}) is None
    assert clean_layout(None) is None
    assert clean_layout("horizontal") is None


async def test_ai_chat_layout_only_does_not_rebuild_funnel(session, monkeypatch):
    """Просьба «разложи удобно» не пересобирает воронку: модель отдаёт
    только команду раскладки, а тексты и связи остаются как были."""
    import json

    from app import ai
    from app.graph import compile_graph
    from app.models import Funnel

    graph_ui = {"drawflow": {"Home": {"data": {
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}},
              "pos_x": 0, "pos_y": 0},
        "2": {"id": 2, "name": "message", "data": {"text": "Привет"},
              "inputs": {"input_1": {"connections": [{"node": "1", "input": "output_1"}]}},
              "outputs": {"output_1": {"connections": []}}, "pos_x": 0, "pos_y": 0},
    }}}}
    f = Funnel(name="F", trigger_type="start", graph_ui=graph_ui, graph=compile_graph(graph_ui))
    session.add(f)
    await session.commit()

    seen = {}

    async def fake_llm(provider, key, model, system, text, images=None):
        seen.update(system=system, text=text, images=images)
        return json.dumps({"reply": "Разложил строками", "spec": None,
                           "layout": {"direction": "horizontal", "spacing": "wide"}}), 10, 5

    monkeypatch.setattr(ai, "call_llm_system", fake_llm)
    shots = [{"media_type": "image/png", "data": "AAAA"}]
    reply, fields, layout, tin, tout = await ai.chat_edit_funnel(
        session, f, [], [{"role": "user", "content": "разложи удобно, связи путаются"}],
        "anthropic", "k", "m", images=shots)
    assert fields is None                      # воронку не трогали
    assert layout == {"direction": "horizontal", "spacing": "wide"}
    assert reply == "Разложил строками"
    assert seen["images"] == shots             # скриншот дошёл до модели
    assert "скриншотов: 1" in seen["text"]
    assert "РАСКЛАДКА" in seen["system"] and "layout" in seen["system"]


async def test_ai_chat_sees_unsaved_canvas(session, monkeypatch):
    """Модель видит то, что сейчас на холсте, а не последнее сохранение."""
    import json

    from app import ai
    from app.graph import compile_graph
    from app.models import Funnel

    def gui(text):
        return {"drawflow": {"Home": {"data": {
            "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
                  "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
            "2": {"id": 2, "name": "message", "data": {"text": text},
                  "inputs": {"input_1": {"connections": [{"node": "1", "input": "output_1"}]}},
                  "outputs": {"output_1": {"connections": []}}},
        }}}}

    f = Funnel(name="F", trigger_type="start", graph_ui=gui("старый текст"),
               graph=compile_graph(gui("старый текст")))
    session.add(f)
    await session.commit()
    seen = {}

    async def fake_llm(provider, key, model, system, text, images=None):
        seen["text"] = text
        return json.dumps({"reply": "ок", "spec": None, "layout": None}), 1, 1

    monkeypatch.setattr(ai, "call_llm_system", fake_llm)
    await ai.chat_edit_funnel(session, f, [], [{"role": "user", "content": "что тут?"}],
                              "anthropic", "k", "m", graph=compile_graph(gui("несохранённая правка")))
    assert "несохранённая правка" in seen["text"] and "старый текст" not in seen["text"]


def test_llm_content_puts_images_before_text():
    from app.ai import _anthropic_content, _openai_content

    im = [{"media_type": "image/png", "data": "QQ=="}]
    a = _anthropic_content("сделай так", im)
    assert a[0]["type"] == "image" and a[-1] == {"type": "text", "text": "сделай так"}
    o = _openai_content("сделай так", im)
    assert o[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert _anthropic_content("без картинок", None) == "без картинок"


# ---------- кнопки: выход у каждой, таймер бездействия, ссылки со связью ----------

def _msg(nid, data, outs, inputs=True):
    return {"id": int(nid), "name": "message", "data": data,
            "inputs": {"input_1": {"connections": []}} if inputs else {},
            "outputs": {p: {"connections": [{"node": t, "output": "input_1"}] if t else []}
                        for p, t in outs.items()}}


def _start(to="2"):
    return {"id": 1, "name": "start", "data": {}, "inputs": {},
            "outputs": {"output_1": {"connections": [{"node": to, "output": "input_1"}]}}}


class _KbBot:
    def __init__(self):
        self.sent, self.kbs = [], []

    async def send_message(self, cid, text, **kw):
        self.sent.append(text)
        self.kbs.append(kw.get("reply_markup"))
        return type("M", (), {"photo": [], "message_id": len(self.sent)})()


async def _setup(session, gui):
    from app.bot import runner
    from app.graph import compile_graph
    from app.models import Bot, Funnel, FunnelBot, Subscriber

    bot = _KbBot()
    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    runner.manager.bots = {b.id: bot}
    f = Funnel(name="F", is_active=True, trigger_type="start",
               graph_ui=gui, graph=compile_graph(gui))
    session.add(f)
    await session.flush()
    session.add(FunnelBot(funnel_id=f.id, bot_id=b.id))
    sub = Subscriber(bot_id=b.id, tg_id=10, first_name="Ann", is_active=True)
    session.add(sub)
    await session.flush()
    return bot, f, sub


def test_old_ports_are_upgraded_with_their_links():
    """До v2 кнопка после ссылки сидела на output_2, а движок слал её в
    output_3 — в пустоту. Перевод сохраняет то, что нарисовано на холсте."""
    from app.graph import compile_graph, upgrade_ports

    gui = _gui({
        "1": _start(),
        "2": _msg("2", {"text": "x", "buttons": [{"label": "Сайт", "url": "https://a"},
                                                  {"label": "Да"}]},
                  {"output_1": None, "output_2": "3"}),
        "3": _msg("3", {"text": "после Да"}, {"output_1": None}),
    })
    gui["drawflow"]["Home"]["data"]["3"]["inputs"]["input_1"]["connections"] = [
        {"node": "2", "input": "output_2"}]
    g = compile_graph(gui)
    assert g["nodes"]["2"]["outputs"] == {"output_3": ["3"]}
    n2 = gui["drawflow"]["Home"]["data"]["2"]
    assert list(n2["outputs"]) == ["output_1", "output_2", "output_3"]
    assert n2["data"]["ports_v"] == 2
    assert gui["drawflow"]["Home"]["data"]["3"]["inputs"]["input_1"]["connections"][0]["input"] == "output_3"
    assert upgrade_ports(gui) is False            # второй раз ничего не делает


async def test_old_funnels_upgraded_on_start(session):
    import copy

    from sqlalchemy import select

    from app.db import _migrate_data
    from app.models import Funnel

    gui = _gui({
        "1": _start(),
        "2": _msg("2", {"text": "x", "buttons": [{"label": "Сайт", "url": "https://a"},
                                                  {"label": "Да"}]},
                  {"output_1": None, "output_2": "3"}),
        "3": _msg("3", {"text": "после Да"}, {"output_1": None}),
    })
    old_graph = {"start": "1", "nodes": {
        "1": {"type": "start", "data": {}, "outputs": {"output_1": ["2"]}},
        "2": {"type": "message", "data": copy.deepcopy(gui["drawflow"]["Home"]["data"]["2"]["data"]),
              "outputs": {"output_2": ["3"]}},
        "3": {"type": "message", "data": {"text": "после Да"}, "outputs": {}}}}
    session.add(Funnel(name="old", graph_ui=gui, graph=old_graph))
    await session.commit()
    await _migrate_data()
    session.expire_all()
    f = (await session.execute(select(Funnel))).scalar_one()
    assert f.graph["nodes"]["2"]["outputs"] == {"output_3": ["3"]}
    assert f.graph_ui["drawflow"]["Home"]["data"]["2"]["data"]["ports_v"] == 2


def test_no_action_validation():
    from app.graph import GraphError, compile_graph

    def g(na):
        return _gui({"1": _start(),
                     "2": _msg("2", {"text": "x", "no_action": na}, {"output_1": None})})
    compile_graph(g({"amount": 2, "unit": "hours"}))
    with pytest.raises(GraphError):
        compile_graph(g({"amount": 0, "unit": "hours"}))
    with pytest.raises(GraphError):
        compile_graph(g({"amount": 1, "unit": "weeks"}))


def _no_action_gui():
    # кнопка «Да» -> 3, «нет реакции» (output_3) -> 4
    return _gui({
        "1": _start(),
        "2": _msg("2", {"text": "Вопрос", "buttons": [{"label": "Да"}],
                        "no_action": {"amount": 1, "unit": "hours"}, "ports_v": 2},
                  {"output_1": None, "output_2": "3", "output_3": "4"}),
        "3": _msg("3", {"text": "Нажал"}, {"output_1": None}),
        "4": _msg("4", {"text": "Напоминание"}, {"output_1": None}),
    })


async def _expire_jobs(session):
    from sqlalchemy import select

    from app.models import ScheduledJob
    for j in (await session.execute(select(ScheduledJob))).scalars():
        j.execute_at = datetime.utcnow() - timedelta(seconds=1)
    await session.flush()


async def test_no_action_fires_when_nothing_happens(session):
    from sqlalchemy import select

    from app.bot import engine as fx
    from app.models import FunnelRun, ScheduledJob

    bot, f, sub = await _setup(session, _no_action_gui())
    await fx.start_funnel(bot, session, f, sub)
    job = (await session.execute(select(ScheduledJob))).scalar_one()
    assert (job.kind, job.port, job.status) == ("no_action", "output_3", "pending")
    run = (await session.execute(select(FunnelRun))).scalar_one()
    assert run.status == "active"

    await fx.run_due_jobs(session, lambda _: bot)   # ещё рано
    assert bot.sent == ["Вопрос"]
    await _expire_jobs(session)
    await fx.run_due_jobs(session, lambda _: bot)
    assert bot.sent == ["Вопрос", "Напоминание"]


async def test_no_action_cancelled_by_click_or_text(session):
    from sqlalchemy import select

    from app.bot import engine as fx
    from app.models import FunnelRun, ScheduledJob

    bot, f, sub = await _setup(session, _no_action_gui())
    await fx.start_funnel(bot, session, f, sub)
    run = (await session.execute(select(FunnelRun))).scalar_one()
    await fx.handle_button(bot, session, run.id, "2", 0)
    assert bot.sent == ["Вопрос", "Нажал"]
    job = (await session.execute(select(ScheduledJob))).scalar_one()
    assert job.status == "cancelled"
    await _expire_jobs(session)
    await fx.run_due_jobs(session, lambda _: bot)
    assert "Напоминание" not in bot.sent

    # перезапуск и ответ текстом — тоже реакция
    await fx.start_funnel(bot, session, f, sub)
    await fx.cancel_no_action(session, subscriber_id=sub.id)
    await _expire_jobs(session)
    await fx.run_due_jobs(session, lambda _: bot)
    assert "Напоминание" not in bot.sent


async def test_no_action_without_link_does_not_schedule(session):
    from sqlalchemy import select

    from app.bot import engine as fx
    from app.models import ScheduledJob

    gui = _gui({"1": _start(),
                "2": _msg("2", {"text": "x", "no_action": {"amount": 1, "unit": "hours"}},
                          {"output_1": None, "output_2": None})})
    bot, f, sub = await _setup(session, gui)
    await fx.start_funnel(bot, session, f, sub)
    assert (await session.execute(select(ScheduledJob))).first() is None


def _link_gui():
    # ссылка (output_2) -> 3, обычная кнопка (output_3) -> 4
    return _gui({
        "1": _start(),
        "2": _msg("2", {"text": "Жми", "ports_v": 2, "buttons": [
                    {"label": "Сайт", "url": "https://site.test/?n={first_name}"},
                    {"label": "Ок"}]},
                  {"output_1": None, "output_2": "3", "output_3": "4"}),
        "3": _msg("3", {"text": "Перешёл"}, {"output_1": None}),
        "4": _msg("4", {"text": "Ок нажат"}, {"output_1": None}),
    })


async def test_url_button_with_link_goes_through_tracker(session):
    import asyncio
    from urllib.parse import urlparse

    from sqlalchemy import func, select

    from app import links
    from app.bot import engine as fx
    from app.config import settings
    from app.models import ButtonClick, FunnelRun

    settings.public_url = "https://bot.test"
    bot, f, sub = await _setup(session, _link_gui())
    await fx.start_funnel(bot, session, f, sub)
    await session.commit()
    kb = bot.kbs[0].inline_keyboard
    url = kb[0][0].url
    assert url.startswith("https://bot.test/go/")
    assert kb[1][0].callback_data.endswith(":2:1")   # обычная кнопка — индекс 1

    token = urlparse(url).path.rsplit("/", 1)[1]
    assert (await links.go(token[:-1] + "0")).status_code == 404   # подделка
    resp = await links.go(token)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://site.test/?n=Ann"
    await asyncio.gather(*list(links._tasks))
    assert bot.sent == ["Жми", "Перешёл"]

    # повторный переход открывает сайт, но ветку второй раз не шлёт
    await links.go(token)
    await asyncio.gather(*list(links._tasks))
    assert bot.sent == ["Жми", "Перешёл"]
    session.expire_all()
    n = (await session.execute(select(func.count(ButtonClick.id)))).scalar()
    assert n == 1

    # обычная кнопка после ссылки уходит в свой выход
    run = (await session.execute(select(FunnelRun))).scalar_one()
    await fx.handle_button(bot, session, run.id, "2", 1)
    assert bot.sent[-1] == "Ок нажат"


async def test_url_button_plain_without_public_url_or_link(session):
    from app.bot import engine as fx
    from app.config import settings

    settings.public_url = ""
    bot, f, sub = await _setup(session, _link_gui())
    await fx.start_funnel(bot, session, f, sub)
    assert bot.kbs[0].inline_keyboard[0][0].url == "https://site.test/?n=Ann"

    settings.public_url = "https://bot.test"
    gui = _gui({"1": _start(),
                "2": _msg("2", {"text": "x", "ports_v": 2,
                                "buttons": [{"label": "Сайт", "url": "https://a.test"}]},
                          {"output_1": None, "output_2": None})})
    bot2, f2, sub2 = await _setup(session, gui)
    await fx.start_funnel(bot2, session, f2, sub2)
    assert bot2.kbs[0].inline_keyboard[0][0].url == "https://a.test"   # связи нет — как было


async def test_ai_spec_url_next_and_no_action(session):
    from app import ai
    from app.models import Funnel

    spec = {"name": "T", "trigger_type": "start", "trigger_value": None, "nodes": [
        {"id": "n1", "type": "message", "text": "Привет",
         "buttons": [{"label": "Сайт", "url": "https://a.test", "next": "n2"},
                     {"label": "Дальше", "next": "n3"}],
         "no_action": {"amount": 2, "unit": "hours", "next": "n4"}, "next": None},
        {"id": "n2", "type": "message", "text": "после ссылки", "buttons": [], "next": None},
        {"id": "n3", "type": "message", "text": "после кнопки", "buttons": [], "next": None},
        {"id": "n4", "type": "message", "text": "напоминание", "buttons": [], "next": None},
    ]}
    fields = ai.spec_to_funnel_fields(spec, await ai.ensure_tags(session, spec))
    g = fields["graph"]
    first = g["nodes"][g["nodes"][g["start"]]["outputs"]["output_1"][0]]
    texts = {nid: n["data"].get("text") for nid, n in g["nodes"].items()}
    assert {p: texts[t[0]] for p, t in first["outputs"].items()} == {
        "output_2": "после ссылки", "output_3": "после кнопки", "output_4": "напоминание"}
    f = Funnel(is_active=False, **fields)
    session.add(f)
    await session.flush()
    back = ai.graph_to_spec(f)
    m = next(n for n in back["nodes"] if n.get("text") == "Привет")
    assert m["no_action"]["unit"] == "hours" and m["buttons"][0]["next"]


# ---------- AI-чат: история в базе, вложения не теряются, откат правки ----------

def _media_gui():
    return {"drawflow": {"Home": {"data": {
        "1": {"id": 1, "name": "start", "data": {}, "inputs": {},
              "outputs": {"output_1": {"connections": [{"node": "5", "output": "input_1"}]}}},
        "5": {"id": 5, "name": "message", "data": {
                  "text": "Привет", "ports_v": 2,
                  "media": [{"type": "photo", "path": "media/hello.png", "name": "hello.png"}],
                  "buttons": [{"label": "Далее", "icon_custom_emoji_id": "777"}]},
              "inputs": {"input_1": {"connections": [{"node": "1", "input": "output_1"}]}},
              "outputs": {"output_1": {"connections": []}, "output_2": {"connections": []}}},
    }}}}


async def _ai_setup(session, monkeypatch, replies):
    import json

    from app import ai
    from app.graph import compile_graph
    from app.models import Funnel

    f = Funnel(name="F", trigger_type="start", graph_ui=_media_gui(), graph=compile_graph(_media_gui()))
    session.add(f)
    await session.commit()
    seen = []

    async def fake_llm(provider, key, model, system, text, images=None):
        seen.append(text)
        return json.dumps(replies.pop(0), ensure_ascii=False), 1, 1

    async def fake_settings(_s):
        return {"api_key": "k", "provider": "anthropic", "model": "m"}

    monkeypatch.setattr(ai, "call_llm_system", fake_llm)
    monkeypatch.setattr(ai, "get_ai_settings", fake_settings)
    return f, seen


def _translate_spec(drop_media_on_original=True):
    orig = {"id": "5", "type": "message", "text": "Привет",
            "buttons": [{"label": "Далее", "next": None}], "next": None}
    if not drop_media_on_original:
        orig["media"] = [{"type": "photo", "path": "media/hello.png", "name": "hello.png"}]
    return {"nodes": [
        {"id": "n1", "type": "language", "languages": [{"code": "ru", "next": "5"},
                                                        {"code": "en", "next": "n2"}], "other": "n2"},
        orig,
        {"id": "n2", "type": "message", "text": "Hello",
         "media": [{"type": "photo", "path": "media/hello.png", "name": "hello.png"}],
         "buttons": [{"label": "Next", "next": None}], "next": None},
    ]}


async def test_ai_edit_keeps_media_ids_and_button_icons(session, monkeypatch):
    from app import api
    from app.models import Funnel

    f, _ = await _ai_setup(session, monkeypatch,
                           [{"reply": "Перевёл", "spec": _translate_spec(), "layout": None}])
    r = await api.ai_edit(api.AIEditIn(funnel_id=f.id, messages=[{"role": "user", "content": "переведи на en"}]),
                          user=_user("owner"), session=session)
    assert r["updated"] and r["version_id"]
    assert "Вложений стало меньше" not in r["reply"]
    await session.refresh(f)
    nodes = f.graph["nodes"]
    assert "5" in nodes                                 # старый блок сохранил свой номер
    assert nodes["5"]["data"]["media"][0]["path"] == "media/hello.png"   # модель потеряла — вернули
    assert nodes["5"]["data"]["buttons"][0]["icon_custom_emoji_id"] == "777"
    en = next(n for n in nodes.values() if n["data"].get("text") == "Hello")
    assert en["data"]["media"][0]["path"] == "media/hello.png"   # копия с картинкой
    assert isinstance(f, Funnel)


async def test_ai_edit_warns_when_media_removed_on_request(session, monkeypatch):
    from app import api

    spec = {"nodes": [{"id": "5", "type": "message", "text": "Привет",
                       "buttons": [{"label": "Далее", "next": None}], "next": None}]}
    f, _ = await _ai_setup(session, monkeypatch, [{"reply": "Убрал", "spec": spec, "layout": None}])
    r = await api.ai_edit(api.AIEditIn(funnel_id=f.id, messages=[
        {"role": "user", "content": "убери картинки"}]), user=_user("owner"), session=session)
    await session.refresh(f)
    assert not f.graph["nodes"]["5"]["data"].get("media")     # просили — убрали
    assert "Вложений стало меньше: было 1, стало 0" in r["reply"]


async def test_ai_chat_history_lives_in_db_and_edit_can_be_undone(session, monkeypatch):
    from app import api

    f, seen = await _ai_setup(session, monkeypatch, [
        {"reply": "Перевёл", "spec": _translate_spec(), "layout": None},
        {"reply": "Да, две ветки", "spec": None, "layout": None},
    ])
    owner = _user("owner")
    r1 = await api.ai_edit(api.AIEditIn(funnel_id=f.id, messages=[
        {"role": "user", "content": "переведи на английский"}]), user=owner, session=session)
    # вторая реплика — с «чистого» клиента: прежней истории он не присылает
    await api.ai_edit(api.AIEditIn(funnel_id=f.id, messages=[
        {"role": "user", "content": "сколько веток?"}]), user=owner, session=session)
    assert "переведи на английский" in seen[1] and "Перевёл" in seen[1]

    hist = await api.ai_chat_history(f.id, user=owner, session=session)
    assert [m["role"] for m in hist] == ["user", "assistant", "user", "assistant"]
    assert hist[1]["version_id"] == r1["version_id"]

    restored = await api.restore_version(f.id, r1["version_id"], user=owner, session=session)
    nodes = restored["graph_ui"]["drawflow"]["Home"]["data"]
    assert sorted(n["name"] for n in nodes.values()) == ["message", "start"]   # до перевода
    vers = await api.list_versions(f.id, user=owner, session=session)
    assert [v["source"] for v in vers][:2] == ["restore", "ai"]
    assert vers[0]["media"] >= 2                     # снимок до отката — с переводом

    await api.ai_chat_clear(f.id, user=owner, session=session)
    assert await api.ai_chat_history(f.id, user=owner, session=session) == []


async def test_save_snapshots_are_throttled(session):
    from app import api
    from app.models import Funnel, FunnelVersion
    from sqlalchemy import func, select

    from app.graph import compile_graph
    f = Funnel(name="F", trigger_type="start", graph_ui=_media_gui(), graph=compile_graph(_media_gui()))
    session.add(f)
    await session.commit()
    body = api.FunnelIn(name="F", graph_ui=_media_gui())
    await api.update_funnel(f.id, body, user=_user("owner"), session=session)
    await api.update_funnel(f.id, api.FunnelIn(name="F", graph_ui=_media_gui()),
                            user=_user("owner"), session=session)
    n = (await session.execute(select(func.count(FunnelVersion.id)))).scalar()
    assert n == 1        # автосохранение не плодит одинаковые копии


async def test_restore_from_backup_reads_pg_dump(session, tmp_path):
    import gzip
    import json
    import sys as _sys

    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import restore_from_backup as rb
    from app.graph import compile_graph
    from app.models import Funnel

    graph = compile_graph(_media_gui())
    gui_txt = json.dumps(_media_gui(), ensure_ascii=False).replace("\\", "\\\\")
    g_txt = json.dumps(graph, ensure_ascii=False).replace("\\", "\\\\")
    dump = ("SET x;\nCOPY public.funnels (id, name, is_active, trigger_type, trigger_value, "
            "graph_ui, graph, updated_at) FROM stdin;\n"
            f"7\tВоронка\\tс табом\tf\tstart\t\\N\t{gui_txt}\t{g_txt}\t2026-10-21 10:00:00\n\\.\n")
    p = tmp_path / "d.sql.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write(dump)
    with rb._open(str(p)) as fh:
        rows = rb.read_funnels(fh)
    assert rows[0]["name"] == "Воронка\tс табом" and rows[0]["trigger_value"] is None
    assert rb.media_total(rows[0]["graph"]) == 1

    session.add(Funnel(id=7, name="Воронка", graph_ui={}, graph={}))
    await session.commit()
    vid = await rb.add_version(rows[0], "из бэкапа")
    from app.models import FunnelVersion
    v = await session.get(FunnelVersion, vid)
    assert v.funnel_id == 7 and v.source == "backup" and rb.media_total(v.graph) == 1


# ---------- AI-чат: правка операциями и порциями ----------

def _lang_spec():
    """language -> en (20 -> 21 -> 22), es (40 -> 41) — старая испанская ветка."""
    return {"nodes": [
        {"id": "10", "type": "language", "languages": [{"code": "en", "next": "20"},
                                                        {"code": "es", "next": "40"}], "other": "20"},
        {"id": "20", "type": "message", "text": "Hello",
         "media": [{"type": "photo", "path": "media/a.png", "name": "a.png"}],
         "buttons": [{"label": "Go", "next": "21", "icon_custom_emoji_id": "5"},
                     {"label": "Site", "url": "https://x.test"}], "next": None},
        {"id": "21", "type": "action", "op": "add_tag", "tag": "en_lead", "next": "22"},
        {"id": "22", "type": "condition", "tag": "en_lead", "yes": "99", "no": None},
        {"id": "40", "type": "message", "text": "Hola viejo", "buttons": [], "next": "99"},
        {"id": "41", "type": "note", "text": "старое", "about": "40"},
        {"id": "99", "type": "message", "text": "общий хвост", "buttons": [], "next": None},
    ]}


def test_ai_ops_copy_delete_patch():
    from app import ai_ops

    spec = _lang_spec()
    log = ai_ops.apply_ops(spec, [
        {"op": "delete_branch", "root": "40"},
        {"op": "copy_branch", "root": "20", "prefix": "es", "tag_map": {"en_lead": "es_lead"},
         "stop_at": ["99"]},
        {"op": "patch", "id": "10", "fields": {"languages": [{"code": "en", "next": "20"},
                                                             {"code": "es", "next": "es_20"}]}},
        {"op": "patch", "id": "es_20", "fields": {"text": "Hola", "buttons": [{"label": "Vamos"}]}},
        {"op": "patch", "id": "nope", "fields": {}},
    ])
    idx = {n["id"]: n for n in spec["nodes"]}
    assert "40" not in idx and "41" not in idx and "99" in idx    # общий хвост цел
    assert set(log["created"]) == {"es_20", "es_21", "es_22"}
    es = idx["es_20"]
    assert es["text"] == "Hola" and es["media"][0]["path"] == "media/a.png"
    assert es["buttons"][0] == {"label": "Vamos", "next": "es_21", "icon_custom_emoji_id": "5"}
    assert es["buttons"][1]["url"] == "https://x.test"
    assert idx["es_21"]["tag"] == "es_lead" and idx["es_22"]["tag"] == "es_lead"
    assert idx["es_22"]["yes"] == "99"                 # стоп-узел — ссылка на оригинал
    assert idx["21"]["tag"] == "en_lead"                # оригинал не тронут
    assert log["errors"] and "nope" in log["errors"][0]
    assert ai_ops.drop_dangling(spec) == []


async def test_ai_edit_in_portions_translates_big_funnel(session, monkeypatch):
    """Модель шлёт операции порциями ("more"), сервер сам зовёт её дальше."""
    import json

    from app import ai, api
    from app.graph import compile_graph
    from app.models import Funnel

    spec = _lang_spec()
    tag_ids = await ai.ensure_tags(session, spec)
    fields = ai.spec_to_funnel_fields(spec, tag_ids)
    f = Funnel(is_active=False, **fields)
    session.add(f)
    await session.commit()

    prompts = []
    replies = [
        {"reply": "Скопировал ветку, перевожу", "more": True, "ops": [
            {"op": "delete_branch", "root": "40"},
            {"op": "copy_branch", "root": "20", "prefix": "es", "tag_prefix": "es_",
             "tag_map": {"en_lead": "es_lead"}, "stop_at": ["99"]},
            {"op": "patch", "id": "10", "fields": {"languages": [
                {"code": "en", "next": "20"}, {"code": "es", "next": "es_20"}]}}]},
        {"reply": "Перевёл", "more": False, "ops": [
            {"op": "patch", "id": "es_20", "fields": {"text": "Hola", "buttons": [{"label": "Vamos"}]}}]},
    ]

    async def fake_llm(provider, key, model, system, text, images=None):
        prompts.append(text)
        return json.dumps(replies.pop(0), ensure_ascii=False), 10, 10

    async def fake_settings(_s):
        return {"api_key": "k", "provider": "anthropic", "model": "m"}

    monkeypatch.setattr(ai, "call_llm_system", fake_llm)
    monkeypatch.setattr(ai, "get_ai_settings", fake_settings)
    r = await api.ai_edit(api.AIEditIn(funnel_id=f.id, messages=[
        {"role": "user", "content": "перенеси английскую ветку на испанский"}]),
        user=_user("owner"), session=session)

    assert len(prompts) == 2 and "ПРОДОЛЖЕНИЕ" in prompts[1] and '"es_20"' in prompts[1]
    assert "✅ Применено: добавлено блоков: 3" in r["reply"]
    await session.refresh(f)
    g = f.graph
    texts = {n["data"].get("text"): nid for nid, n in g["nodes"].items()}
    assert "Hola viejo" not in texts and "Hola" in texts and "Hello" in texts
    lang = next(n for n in g["nodes"].values() if n["type"] == "language")
    es_root = lang["outputs"]["output_3"][0]
    assert g["nodes"][es_root]["data"]["text"] == "Hola"
    assert g["nodes"][es_root]["data"]["media"][0]["path"] == "media/a.png"
    assert "20" in g["nodes"] and "99" in g["nodes"]     # старые блоки — со старыми номерами
    from app.models import Tag
    from sqlalchemy import select
    names = set((await session.execute(select(Tag.name))).scalars())
    assert "es_lead" in names
    compile_graph(f.graph_ui)


async def test_ai_edit_asks_for_smaller_portion_when_cut(session, monkeypatch):
    import json

    from app import ai

    f, _ = await _ai_setup(session, monkeypatch, [])
    calls = []

    async def fake_llm(provider, key, model, system, text, images=None):
        calls.append(text)
        if len(calls) == 1:
            raise ai._truncated(5, 32000)
        return json.dumps({"reply": "ок", "more": False, "ops": [
            {"op": "patch", "id": "5", "fields": {"text": "Привет!"}}]}), 1, 1

    monkeypatch.setattr(ai, "call_llm_system", fake_llm)
    reply, fields, _, tin, tout = await ai.chat_edit_funnel(
        session, f, [], [{"role": "user", "content": "поправь"}], "anthropic", "k", "m")
    assert "ОБОРВАЛСЯ" in calls[1] and tout == 32001
    assert fields["graph"]["nodes"]["5"]["data"]["text"] == "Привет!"
    assert fields["graph"]["nodes"]["5"]["data"]["media"]      # картинка на месте


async def test_ai_edit_question_changes_nothing(session, monkeypatch):
    from app import ai

    f, _ = await _ai_setup(session, monkeypatch, [
        {"reply": "Тут один шаг", "ops": None, "more": True}])
    reply, fields, *_ = await ai.chat_edit_funnel(
        session, f, [], [{"role": "user", "content": "что тут?"}], "anthropic", "k", "m")
    assert fields is None and reply == "Тут один шаг"


# ---------- AI-чат после «Разложить»: видит холст, не сбивает раскладку ----------

def _arranged_gui(direction="horizontal"):
    """start -> 2 (Шаг 1) -> 3 (Шаг 2); плитки стоят строкой, как после «Разложить»."""
    return {"drawflow": {"Home": {"data": {
        "1": {"id": 1, "name": "start", "data": {"layout": {"direction": direction, "spacing": "wide"}},
              "inputs": {}, "pos_x": 60, "pos_y": 60,
              "outputs": {"output_1": {"connections": [{"node": "2", "output": "input_1"}]}}},
        "2": {"id": 2, "name": "message", "data": {"text": "Первое", "ports_v": 2},
              "pos_x": 900, "pos_y": 60,
              "inputs": {"input_1": {"connections": [{"node": "1", "input": "output_1"}]}},
              "outputs": {"output_1": {"connections": [{"node": "3", "output": "input_1"}]}}},
        "3": {"id": 3, "name": "message", "data": {"text": "Второе", "ports_v": 2},
              "pos_x": 1700, "pos_y": 60,
              "inputs": {"input_1": {"connections": [{"node": "2", "input": "output_1"}]}},
              "outputs": {"output_1": {"connections": []}}},
    }}}}


async def _arranged_funnel(session):
    from app.graph import compile_graph
    from app.models import Funnel

    gui = _arranged_gui()
    f = Funnel(name="F", trigger_type="start", graph_ui=gui, graph=compile_graph(_arranged_gui()))
    session.add(f)
    await session.commit()
    return f


async def test_ai_sees_step_numbers_and_canvas(session, monkeypatch):
    import json

    from app import ai

    f = await _arranged_funnel(session)
    seen = []

    async def fake_llm(provider, key, model, system, text, images=None):
        seen.append(text)
        return json.dumps({"reply": "ок", "ops": None}), 1, 1

    monkeypatch.setattr(ai, "call_llm_system", fake_llm)
    await ai.chat_edit_funnel(session, f, [], [{"role": "user", "content": "что в шаге 2?"}],
                              "anthropic", "k", "m")
    spec = json.loads(seen[0].split("ТЕКУЩАЯ ВОРОНКА:\n", 1)[1].split("\n\nХОЛСТ", 1)[0])
    n3 = next(n for n in spec["nodes"] if n["id"] == "3")
    assert n3["step"] == 2 and n3["pos"] == [1700, 60]
    assert "СТРОКАМИ" in seen[0]


async def test_ai_patch_keeps_arranged_positions(session, monkeypatch):
    import json

    from app import ai, api

    f = await _arranged_funnel(session)
    replies = [{"reply": "Поправил", "more": False, "ops": [
        {"op": "patch", "id": "3", "fields": {"text": "Второе!"}}]}]

    async def fake_llm(*a, **kw):
        return json.dumps(replies.pop(0), ensure_ascii=False), 1, 1

    async def fake_settings(_s):
        return {"api_key": "k", "provider": "anthropic", "model": "m"}

    monkeypatch.setattr(ai, "call_llm_system", fake_llm)
    monkeypatch.setattr(ai, "get_ai_settings", fake_settings)
    r = await api.ai_edit(api.AIEditIn(funnel_id=f.id, messages=[
        {"role": "user", "content": "поправь шаг 2"}], graph_ui=_arranged_gui()),
        user=_user("owner"), session=session)
    assert r["layout"] is None                       # раскладывать заново не нужно
    data = r["funnel"]["graph_ui"]["drawflow"]["Home"]["data"]
    assert (data["2"]["pos_x"], data["3"]["pos_x"]) == (900, 1700)
    start = next(n for n in data.values() if n["name"] == "start")
    assert start["data"]["layout"]["direction"] == "horizontal"
    assert data["3"]["data"]["text"] == "Второе!"


async def test_ai_new_blocks_relayout_like_user_chose(session, monkeypatch):
    import json

    from app import ai, api

    f = await _arranged_funnel(session)
    replies = [{"reply": "Добавил", "more": False, "ops": [
        {"op": "set", "node": {"id": "n1", "type": "message", "text": "Третье", "next": None}},
        {"op": "patch", "id": "3", "fields": {"next": "n1"}}]}]

    async def fake_llm(*a, **kw):
        return json.dumps(replies.pop(0), ensure_ascii=False), 1, 1

    async def fake_settings(_s):
        return {"api_key": "k", "provider": "anthropic", "model": "m"}

    monkeypatch.setattr(ai, "call_llm_system", fake_llm)
    monkeypatch.setattr(ai, "get_ai_settings", fake_settings)
    r = await api.ai_edit(api.AIEditIn(funnel_id=f.id, messages=[
        {"role": "user", "content": "добавь шаг в конец"}], graph_ui=_arranged_gui()),
        user=_user("owner"), session=session)
    assert r["layout"] == {"direction": "horizontal", "spacing": "wide"}


# ---------- превью картинок для холста ----------

async def test_thumbs_are_small_cached_and_safe(tmp_path, monkeypatch):
    from PIL import Image

    from app import thumbs
    from app.config import settings

    monkeypatch.setattr(settings, "media_dir", str(tmp_path))
    Image.new("RGB", (2000, 1500), (200, 10, 10)).save(tmp_path / "big.png")
    (tmp_path / "anim.gif").write_bytes(b"GIF89a")
    (tmp_path.parent / "secret.txt").write_text("x")

    r = await thumbs.thumb(500, "big.png")
    assert r.media_type == "image/jpeg"
    with Image.open(r.path) as im:
        assert im.size == (480, 360)               # ближайший размер из SIZES
    assert ".thumbs" in str(r.path)
    again = await thumbs.thumb(480, "media/big.png")
    assert str(again.path) == str(r.path)          # второй раз — из кэша
    assert str((await thumbs.thumb(240, "anim.gif")).path).endswith("anim.gif")   # как есть
    assert (await thumbs.thumb(240, "../secret.txt")).status_code == 404
    assert (await thumbs.thumb(240, "nope.png")).status_code == 404


# ---------- копия воронки ----------

async def test_duplicate_funnel_is_full_and_off(session):
    from sqlalchemy import select

    from app import api
    from app.graph import compile_graph
    from app.models import Bot, Funnel, FunnelBot, User

    b = Bot(name="B", token="t", is_active=True)
    session.add(b)
    await session.flush()
    gui = _media_gui()
    f = Funnel(name="Прогрев", is_active=True, trigger_type="keyword", trigger_value="go",
               graph_ui=gui, graph=compile_graph(_media_gui()))
    session.add(f)
    await session.flush()
    session.add(FunnelBot(funnel_id=f.id, bot_id=b.id))
    staff = User(login="s", name="S", password_hash="x", role="staff", bot_ids=[],
                 funnel_ids=[f.id], permissions={"funnels": "edit"})
    session.add(staff)
    await session.commit()

    r1 = await api.duplicate_funnel(f.id, user=_user("owner"), session=session)
    r2 = await api.duplicate_funnel(f.id, user=staff, session=session)
    assert (r1["name"], r2["name"]) == ("Прогрев (копия)", "Прогрев (копия 2)")

    c = await session.get(Funnel, r1["id"])
    assert c.is_active is False                       # не отвечает вместе с оригиналом
    assert (c.trigger_type, c.trigger_value) == ("keyword", "go")
    assert c.graph == f.graph and c.graph_ui == f.graph_ui
    assert c.graph["nodes"]["5"]["data"]["media"][0]["path"] == "media/hello.png"
    c.graph_ui["drawflow"]["Home"]["data"]["5"]["data"]["text"] = "правка копии"
    await session.refresh(f)
    assert f.graph_ui["drawflow"]["Home"]["data"]["5"]["data"]["text"] == "Привет"   # не общий объект
    bots = (await session.execute(select(FunnelBot.bot_id).where(
        FunnelBot.funnel_id == c.id))).scalars().all()
    assert bots == [b.id]
    await session.refresh(staff)
    assert r2["id"] in staff.funnel_ids              # сотрудник видит свою копию
