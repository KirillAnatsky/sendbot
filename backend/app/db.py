from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs() -> dict:
    """Пул соединений: аналитика раскладывает независимые запросы параллельно,
    каждому нужна своя коннекция. Для SQLite пул не настраивается."""
    if settings.database_url.startswith("sqlite"):
        return {}
    return {
        "pool_size": 20,
        "max_overflow": 20,
        "pool_pre_ping": True,   # база на другом сервере — проверяем живость
        "pool_recycle": 1800,
    }


engine = create_async_engine(settings.database_url, echo=False, **_engine_kwargs())
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    from . import models  # noqa

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate)


def _migrate(conn):
    """Мягкая авто-миграция для тех, кто поднимал старую версию (без alembic):
    добавляем недостающие колонки и чиним уникальность подписчиков."""
    from sqlalchemy import inspect, text

    insp = inspect(conn)
    tables = insp.get_table_names()
    existing = {t: {c["name"] for c in insp.get_columns(t)} for t in tables}

    def ex(sql):
        conn.execute(text(sql))

    def add(table, col, ddl):
        if table in existing and col not in existing[table]:
            ex(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    add("subscribers", "bot_id", "bot_id INTEGER DEFAULT 0")
    add("subscribers", "language_code", "language_code VARCHAR(16)")
    add("subscribers", "source", "source VARCHAR(128)")
    add("subscribers", "params", "params JSON")
    add("subscribers", "first_source", "first_source VARCHAR(128)")
    add("bots", "source_policy", "source_policy VARCHAR(8) DEFAULT 'last'")
    add("users", "permissions", "permissions JSON")
    add("users", "funnel_ids", "funnel_ids JSON")

    add("subscribers", "last_active_at", "last_active_at TIMESTAMP")
    add("subscribers", "automation_paused_until", "automation_paused_until TIMESTAMP")
    add("broadcasts", "bot_id", "bot_id INTEGER DEFAULT 0")
    add("broadcasts", "media", "media JSON")
    add("broadcast_recipients", "created_at", "created_at TIMESTAMP")

    if "subscribers" not in tables:
        return

    # В старой версии tg_id был ГЛОБАЛЬНО уникальным (индекс ix_subscribers_tg_id
    # с unique=True). Теперь уникальна пара (bot_id, tg_id): один человек может
    # быть подписчиком разных ботов. Удаляем старый уникальный индекс.
    indexes = insp.get_indexes("subscribers")
    old_unique = any(
        ix.get("name") == "ix_subscribers_tg_id" and ix.get("unique") for ix in indexes
    )
    if old_unique:
        ex("DROP INDEX IF EXISTS ix_subscribers_tg_id")
        ex("CREATE INDEX IF NOT EXISTS ix_subscribers_tg_id ON subscribers (tg_id)")

    # гарантируем составную уникальность (bot_id, tg_id)
    names = {ix.get("name") for ix in insp.get_indexes("subscribers")}
    names |= {u.get("name") for u in insp.get_unique_constraints("subscribers")}
    if "uq_subscribers_bot_tg" not in names:
        try:
            ex("CREATE UNIQUE INDEX IF NOT EXISTS uq_subscribers_bot_tg "
               "ON subscribers (bot_id, tg_id)")
        except Exception:  # noqa: BLE001
            pass

    # индексы под аналитику/сегменты и статистику воронок (ускоряют на больших базах)
    for ddl in (
        "CREATE INDEX IF NOT EXISTS ix_subscribers_created_at ON subscribers (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_subscribers_last_active_at ON subscribers (last_active_at)",
        "CREATE INDEX IF NOT EXISTS ix_subscribers_bot_created ON subscribers (bot_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_messages_sub_created ON messages (subscriber_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_node_visits_funnel_node ON node_visits (funnel_id, node_id)",
        "CREATE INDEX IF NOT EXISTS ix_node_visits_funnel_sub ON node_visits (funnel_id, subscriber_id)",
        "CREATE INDEX IF NOT EXISTS ix_button_clicks_funnel_node ON button_clicks (funnel_id, node_id)",
        # под «Анализ воронки»: шаги фильтруются по (воронка, узел, период)
        "CREATE INDEX IF NOT EXISTS ix_node_visits_fnc ON node_visits (funnel_id, node_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_button_clicks_fnc ON button_clicks (funnel_id, node_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_button_clicks_sub_created ON button_clicks (subscriber_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_messages_dir_created ON messages (direction, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_bc_recipients_bc ON broadcast_recipients (broadcast_id, delivered)",
        "CREATE INDEX IF NOT EXISTS ix_scheduled_jobs_status_at ON scheduled_jobs (status, execute_at)",
        "CREATE INDEX IF NOT EXISTS ix_runs_funnel_sub ON funnel_runs (funnel_id, subscriber_id)",
    ):
        try:
            ex(ddl)
        except Exception:  # noqa: BLE001 — таблицы может не быть на первом старте
            pass
