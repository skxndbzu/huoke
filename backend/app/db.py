from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Text, Integer, Float, Boolean, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings


def now():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Control(Base):
    __tablename__ = 'control'
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    paused: Mapped[bool] = mapped_column(default=True)
    last_send: Mapped[str] = mapped_column(default='')
    product: Mapped[dict] = mapped_column(JSON, default=lambda: {
        'version': 1, 'confirmed': True,
        'description': '为国内开发者和企业提供统一的模型 API 接口和按量计费服务。',
        'evidence': '项目所有者 2026-10-27 初始需求',
        'unknown': ['产品名称', '官网', '模型清单', '价格', '试用政策', '服务保证'],
    })


class Lead(Base):
    __tablename__ = 'leads'
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    source_key: Mapped[str] = mapped_column(String(200), unique=True)
    url: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(200), default='未知')
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    published_at: Mapped[str] = mapped_column(default='')
    collected_at: Mapped[str] = mapped_column(default=now)
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(default='待核实')
    owner: Mapped[str] = mapped_column(default='未分配')
    human_takeover: Mapped[bool] = mapped_column(default=False)
    feedback: Mapped[str] = mapped_column(Text, default='')


class Contact(Base):
    __tablename__ = 'contacts'
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(index=True)
    address: Mapped[str] = mapped_column(String(320))
    source_url: Mapped[str] = mapped_column(Text)
    basis: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(default=False)
    collected_at: Mapped[str] = mapped_column(default=now)


class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(index=True)
    contact_id: Mapped[int]
    recipient: Mapped[str] = mapped_column(String(320))
    channel: Mapped[str] = mapped_column(default='simulation')
    account: Mapped[str] = mapped_column(default='simulation')
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    attachments: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(default='pending_review', index=True)
    approval_hash: Mapped[str] = mapped_column(default='')
    approved_at: Mapped[str] = mapped_column(default='')
    not_before: Mapped[str] = mapped_column(default='')
    attempted_at: Mapped[str] = mapped_column(default='')
    provider_id: Mapped[str] = mapped_column(default='')
    result: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[str] = mapped_column(default=now)


class Suppression(Base):
    __tablename__ = 'suppressions'
    address: Mapped[str] = mapped_column(String(320), primary_key=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(default=now)


class DeliveryAttempt(Base):
    __tablename__ = 'delivery_attempts'
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[int]
    created_at: Mapped[str] = mapped_column(default=now)


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]
    entity: Mapped[str] = mapped_column(default='')
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(default=now, index=True)


class SourceItem(Base):
    __tablename__ = 'source_items'
    key: Mapped[str] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(default='')
    checked_at: Mapped[str] = mapped_column(default=now)


class ChannelRun(Base):
    __tablename__ = 'channel_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str]
    status: Mapped[str] = mapped_column(default='running')
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(default='')
    started_at: Mapped[str] = mapped_column(default=now)
    finished_at: Mapped[str] = mapped_column(default='')


class AnalysisCall(Base):
    __tablename__ = 'analysis_calls'
    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True)
    lead_id: Mapped[int]
    model: Mapped[str]
    status: Mapped[str] = mapped_column(default='reserved')
    reserved_usd: Mapped[float] = mapped_column(Float)
    estimated_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(default=now)


engine = create_engine(settings().database_url, **({'connect_args': {'check_same_thread': False}} if settings().database_url.startswith('sqlite') else {'pool_pre_ping': True}))
Session = sessionmaker(engine, expire_on_commit=False)


def init_db():
    Base.metadata.create_all(engine)
    with Session.begin() as s:
        if not s.get(Control, 1):
            s.add(Control(id=1))


def locked_control(s):
    from sqlalchemy import select
    return s.scalar(select(Control).where(Control.id == 1).with_for_update())


def event(s, kind, entity='', **detail):
    s.add(Event(kind=kind, entity=str(entity), detail=detail))


if __name__ == '__main__':
    init_db()
