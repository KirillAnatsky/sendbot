from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow():
    return datetime.utcnow()


class User(Base):
    """Пользователь админки.

    owner — владелец, может всё и раздаёт права.
    staff — сотрудник, права выдаются точечно по разделам (см. permissions)."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="staff")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # к каким ботам есть доступ; пустой список = ко всем
    bot_ids: Mapped[list] = mapped_column(JSON, default=list)
    # к каким воронкам есть доступ; пустой список = ко всем доступным ботам
    funnel_ids: Mapped[list] = mapped_column(JSON, default=list)
    # права по разделам: {"funnels": "edit", "analytics": "view", ...}
    # у владельца игнорируются — ему доступно всё
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    # привязанный телеграм-аккаунт: вход через Telegram и предпросмотр рассылок
    tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    tg_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Bot(Base):
    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    token: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    # заполняется после успешного запуска (для инфо в админке)
    tg_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # что делать с меткой deep-link при повторном переходе:
    #   last  — обновлять на новую (атрибуция последнего касания)
    #   first — навсегда сохранять самую первую
    source_policy: Mapped[str] = mapped_column(String(8), default="last")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Subscriber(Base):
    __tablename__ = "subscribers"
    __table_args__ = (UniqueConstraint("bot_id", "tg_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True, default=0)
    tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # метка последнего перехода по deep-link (/start <метка>) — она подставляется в {source}
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # метка самого первого перехода — не перезаписывается (атрибуция первого касания)
    first_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # распарсенные параметры метки
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # False = заблокировал бота
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # пауза автоматизации (воронки/автоответы) до указанного времени — для ручного диалога
    automation_paused_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # отписался от рассылок и автоматики. Не путать с is_active: там «нас
    # заблокировали», здесь — «сам попросил больше не писать»
    is_subscribed: Mapped[bool] = mapped_column(Boolean, default=True)

    tags = relationship("SubscriberTag", back_populates="subscriber", cascade="all, delete-orphan")


class Message(Base):
    """История переписки для «живого чата»."""
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscriber_id: Mapped[int] = mapped_column(ForeignKey("subscribers.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # in | out
    text: Mapped[str] = mapped_column(Text)
    is_operator: Mapped[bool] = mapped_column(Boolean, default=False)  # out от оператора вручную
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SubscriberTag(Base):
    __tablename__ = "subscriber_tags"
    __table_args__ = (UniqueConstraint("subscriber_id", "tag_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subscriber_id: Mapped[int] = mapped_column(ForeignKey("subscribers.id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)

    subscriber = relationship("Subscriber", back_populates="tags")
    tag = relationship("Tag")


class Funnel(Base):
    __tablename__ = "funnels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    # Цепочка — та же воронка, но сама не запускается: только по вызову из
    # блока «Цепочка» другой воронки. Отдельной сущности нет намеренно —
    # так у цепочки бесплатно есть свой редактор, статистика и анализ.
    is_chain: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # start | keyword | tag_added | message
    trigger_type: Mapped[str] = mapped_column(String(32), default="start")
    trigger_value: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # сырой экспорт Drawflow (для редактора)
    graph_ui: Mapped[dict] = mapped_column(JSON, default=dict)
    # скомпилированный граф для движка: {nodes: {id: {type, data, outputs: {port: [ids]}}}, start: id}
    graph: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FunnelBot(Base):
    """К каким ботам привязана воронка (many-to-many)."""
    __tablename__ = "funnel_bots"
    __table_args__ = (UniqueConstraint("funnel_id", "bot_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    funnel_id: Mapped[int] = mapped_column(ForeignKey("funnels.id", ondelete="CASCADE"), index=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True)


class FunnelRun(Base):
    __tablename__ = "funnel_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    funnel_id: Mapped[int] = mapped_column(ForeignKey("funnels.id", ondelete="CASCADE"), index=True)
    subscriber_id: Mapped[int] = mapped_column(ForeignKey("subscribers.id", ondelete="CASCADE"), index=True)
    current_node: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | done | cancelled
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Заход в цепочку — это отдельный запуск дочерней воронки. Так задержки,
    # кнопки и статистика внутри цепочки работают ровно как в обычной воронке,
    # а по её завершении родитель продолжается с узла return_node.
    parent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("funnel_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    return_node: Mapped[str | None] = mapped_column(String(32), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("funnel_runs.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(32))  # узел задержки
    execute_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending | done | cancelled


class NodeVisit(Base):
    """Прохождение узла воронки подписчиком — основа статистики по шагам."""
    __tablename__ = "node_visits"

    id: Mapped[int] = mapped_column(primary_key=True)
    funnel_id: Mapped[int] = mapped_column(Integer, index=True)
    node_id: Mapped[str] = mapped_column(String(32), index=True)
    subscriber_id: Mapped[int] = mapped_column(Integer, index=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ButtonClick(Base):
    __tablename__ = "button_clicks"

    id: Mapped[int] = mapped_column(primary_key=True)
    funnel_id: Mapped[int] = mapped_column(Integer, index=True)
    node_id: Mapped[str] = mapped_column(String(32))
    button_index: Mapped[int] = mapped_column(Integer)
    subscriber_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BroadcastRecipient(Base):
    """Кому реально ушла рассылка — для сегмента «был в рассылке»."""
    __tablename__ = "broadcast_recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(ForeignKey("broadcasts.id", ondelete="CASCADE"), index=True)
    subscriber_id: Mapped[int] = mapped_column(ForeignKey("subscribers.id", ondelete="CASCADE"), index=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SentMessage(Base):
    """Что бот отправил и каким узлом воронки — чтобы потом это удалить.

    В истории переписки лежит только текст; message_id, без которого Telegram
    не даст ничего удалить, раньше нигде не сохранялся. Альбом даёт несколько
    строк на один узел — удалять надо все.
    """
    __tablename__ = "sent_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscriber_id: Mapped[int] = mapped_column(
        ForeignKey("subscribers.id", ondelete="CASCADE"), index=True)
    funnel_id: Mapped[int] = mapped_column(Integer, index=True)
    node_id: Mapped[str] = mapped_column(String(32), index=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class MediaFileId(Base):
    """Кэш Telegram file_id по паре (бот, локальный файл), чтобы не грузить
    один и тот же файл заново на каждую отправку — экономит трафик и время."""
    __tablename__ = "media_file_ids"
    __table_args__ = (UniqueConstraint("bot_id", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(Integer, index=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    file_id: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class AIRequest(Base):
    __tablename__ = "ai_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    funnel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"), index=True, default=0)
    name: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    media: Mapped[list] = mapped_column(JSON, default=list)  # [{type, path, name}]
    # [{label, url}] — ссылка, либо {label, tag_id} — повесить тег по клику
    buttons: Mapped[list] = mapped_column(JSON, default=list)
    # True — текст уходит отдельным сообщением ПЕРЕД вложениями
    text_first: Mapped[bool] = mapped_column(Boolean, default=False)
    # {"include_tags": [ids], "exclude_tags": [ids]}
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending | running | done | failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
