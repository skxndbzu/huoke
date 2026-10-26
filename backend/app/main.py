import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, text
from .config import settings
from .db import Session, Control, Lead, Contact, Message, Suppression, Event, ChannelRun, AnalysisCall, init_db, locked_control, event, now
from .schemas import ContactInput, DraftInput, MessageEdit, Approval, QueueInput, Feedback, Strict
from .delivery import content_hash, eligibility, TERMINAL
from .analysis import analyze, configured


@asynccontextmanager
async def lifespan(app):
    cfg = settings()
    if not cfg.operator_token or not cfg.reviewer_token or cfg.operator_token == cfg.reviewer_token:
        raise RuntimeError('必须配置不同的运维和人工审核令牌')
    yield


app = FastAPI(title='青鸟获客 · 试运行', lifespan=lifespan, docs_url=None, redoc_url=None)
auth = HTTPBearer(auto_error=False)


def role(credentials: HTTPAuthorizationCredentials | None = Depends(auth)):
    token = credentials.credentials if credentials else ''
    cfg = settings()
    if token and cfg.reviewer_token and secrets.compare_digest(token, cfg.reviewer_token):
        return 'reviewer'
    if token and cfg.operator_token and secrets.compare_digest(token, cfg.operator_token):
        return 'operator'
    raise HTTPException(401, '需要登录')


def reviewer(actor=Depends(role)):
    if actor != 'reviewer':
        raise HTTPException(403, '仅项目所有者可以核实联系方式及批准消息')
    return actor


def obj(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def get(s, model, key):
    row = s.get(model, key)
    if not row:
        raise HTTPException(404, '记录不存在')
    return row


def message_view(s, m):
    return {**obj(m), 'content_hash': content_hash(m), 'block_reason': eligibility(s, m)}


@app.middleware('http')
async def headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/healthz')
def health():
    try:
        with Session() as s:
            s.execute(text('SELECT 1'))
        return {'status': 'ok', 'environment': 'staging', 'real_send_enabled': settings().real_send_enabled}
    except Exception:
        raise HTTPException(503, '数据库不可用') from None


@app.get('/api/overview')
def overview(actor=Depends(role)):
    with Session() as s:
        control = get(s, Control, 1)
        calls = list(s.scalars(select(AnalysisCall)))
        return {
            'role': actor, 'paused': control.paused, 'product': control.product,
            'real_send_enabled': settings().real_send_enabled,
            'counts': {table.__tablename__: s.scalar(select(func.count()).select_from(table)) for table in (Lead, Contact, Message)},
            'message_statuses': dict(s.execute(select(Message.status, func.count()).group_by(Message.status)).all()),
            'cost': {'estimated_usd': sum(c.estimated_usd or 0 for c in calls),
                'unresolved_reserved_usd': sum(c.reserved_usd for c in calls if c.estimated_usd is None),
                'model_calls': len(calls), 'development_cost': '未知：未取得 Codex 计费数据',
                'daily_budget': settings().ai_daily_budget, 'monthly_budget': settings().ai_monthly_budget},
            'integrations': {'ai': 'configured_unverified' if configured() else 'not_configured',
                'smtp': 'configured_unverified' if settings().smtp_host else 'not_configured',
                'inbox': 'not_connected', 'conversion': 'not_connected'},
        }


@app.get('/api/leads')
def leads(q: str = '', actor=Depends(role)):
    with Session() as s:
        query = select(Lead).order_by(Lead.id.desc()).limit(200)
        if q:
            query = query.where(Lead.title.icontains(q, autoescape=True) | Lead.text.icontains(q, autoescape=True))
        return [obj(l) for l in s.scalars(query)]


@app.get('/api/leads/{lead_id}')
def lead_detail(lead_id: int, actor=Depends(role)):
    with Session() as s:
        lead = get(s, Lead, lead_id)
        return {**obj(lead), 'contacts': [obj(c) for c in s.scalars(select(Contact).where(Contact.lead_id == lead_id))],
            'messages': [message_view(s, m) for m in s.scalars(select(Message).where(Message.lead_id == lead_id))]}


@app.post('/api/leads/{lead_id}/feedback')
def feedback(lead_id: int, data: Feedback, actor=Depends(role)):
    with Session.begin() as s:
        locked_control(s)
        lead = get(s, Lead, lead_id)
        if lead.human_takeover and not data.human_takeover and actor != 'reviewer':
            raise HTTPException(403, '结束人工接管需项目所有者操作')
        lead.feedback, lead.human_takeover, lead.status = data.text, data.human_takeover, data.status
        event(s, 'human_feedback', lead_id, **data.model_dump())
    return {'ok': True}


@app.post('/api/leads/{lead_id}/contacts')
def add_contact(lead_id: int, data: ContactInput, actor=Depends(role)):
    with Session.begin() as s:
        locked_control(s)
        get(s, Lead, lead_id)
        contact = Contact(lead_id=lead_id, **data.model_dump())
        s.add(contact)
        s.flush()
        event(s, 'contact_added_unverified', contact.id)
        return obj(contact)


@app.post('/api/contacts/{contact_id}/verify')
def verify_contact(contact_id: int, actor=Depends(reviewer)):
    with Session.begin() as s:
        locked_control(s)
        contact = get(s, Contact, contact_id)
        contact.verified = True
        event(s, 'contact_verified_by_owner', contact_id, evidence=contact.source_url)
    return {'ok': True}


@app.post('/api/leads/{lead_id}/analyze')
def analyze_lead(lead_id: int, actor=Depends(role)):
    try:
        return analyze(lead_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post('/api/leads/{lead_id}/draft')
def draft(lead_id: int, data: DraftInput, actor=Depends(role)):
    with Session.begin() as s:
        control = locked_control(s)
        lead = get(s, Lead, lead_id)
        contact = get(s, Contact, data.contact_id)
        if contact.lead_id != lead_id:
            raise HTTPException(409, '联系人不属于该客户')
        # Deterministic drafting uses only owner-confirmed product facts; source stays a quotation.
        product = control.product
        if not product.get('confirmed'):
            raise HTTPException(409, '产品资料未确认')
        m = Message(lead_id=lead_id, contact_id=contact.id, recipient=contact.address,
            channel=data.channel, account='simulation' if data.channel == 'simulation' else settings().smtp_from,
            subject='关于您提到的模型 API 需求',
            body=f'你好，我看到你发布的“{lead.title[:120]}”。\n\n我们{product["description"]}\n如果你仍在评估模型接口，愿意交流一下需要接入的模型和使用场景吗？\n\n如果不希望收到后续联系，请直接回复告知。')
        s.add(m)
        s.flush()
        event(s, 'draft_created', m.id, method='confirmed_facts_template', product_version=product['version'])
        return message_view(s, m)


@app.get('/api/messages')
def messages(actor=Depends(role)):
    with Session() as s:
        return [message_view(s, m) for m in s.scalars(select(Message).order_by(Message.id.desc()).limit(200))]


@app.put('/api/messages/{message_id}')
def edit_message(message_id: int, data: MessageEdit, actor=Depends(role)):
    with Session.begin() as s:
        locked_control(s)
        m = get(s, Message, message_id)
        if m.status in TERMINAL or m.version != data.version:
            raise HTTPException(409, '状态或版本已变化；终态消息不能修改或重发')
        contact = get(s, Contact, data.contact_id)
        if contact.lead_id != m.lead_id:
            raise HTTPException(409, '联系人不属于该客户')
        for field in ('subject', 'body', 'contact_id', 'channel', 'attachments'):
            setattr(m, field, getattr(data, field))
        m.recipient = contact.address
        m.account = 'simulation' if data.channel == 'simulation' else settings().smtp_from
        m.version += 1
        m.status, m.approval_hash, m.approved_at, m.not_before = 'pending_review', '', '', ''
        event(s, 'draft_edited_approval_revoked', m.id, version=m.version)
        return message_view(s, m)


@app.post('/api/messages/{message_id}/approve')
def approve(message_id: int, data: Approval, actor=Depends(reviewer)):
    with Session.begin() as s:
        locked_control(s)
        m = get(s, Message, message_id)
        if m.status != 'pending_review' or m.version != data.version or data.content_hash != content_hash(m):
            raise HTTPException(409, '审核版本已变化，请重新查看完整内容')
        reason = eligibility(s, m)
        if reason:
            raise HTTPException(409, reason)
        m.approval_hash, m.approved_at, m.status = content_hash(m), now(), 'approved'
        event(s, 'owner_approved', m.id, version=m.version, content_hash=m.approval_hash, actor=actor)
        return message_view(s, m)


@app.post('/api/messages/{message_id}/queue')
def queue(message_id: int, data: QueueInput, actor=Depends(role)):
    with Session.begin() as s:
        control = locked_control(s)
        m = get(s, Message, message_id)
        if m.status == 'queued' and m.approval_hash == content_hash(m):
            return message_view(s, m)
        if control.paused:
            raise HTTPException(409, '全局发送已暂停')
        if m.status != 'approved' or m.approval_hash != content_hash(m):
            raise HTTPException(409, '需要有效的人工批准')
        reason = eligibility(s, m)
        if reason:
            raise HTTPException(409, reason)
        if data.not_before:
            try:
                date = datetime.fromisoformat(data.not_before)
                if date.tzinfo is None:
                    raise ValueError()
                m.not_before = date.astimezone(timezone.utc).isoformat()
            except ValueError:
                raise HTTPException(422, '排期须包含时区') from None
        else:
            m.not_before = now()
        m.status = 'queued'
        event(s, 'queued', m.id, not_before=m.not_before)
        return message_view(s, m)


class Reason(Strict):
    reason: str = ''


@app.post('/api/messages/{message_id}/cancel')
def cancel(message_id: int, data: Reason, actor=Depends(role)):
    with Session.begin() as s:
        locked_control(s)
        m = get(s, Message, message_id)
        if m.status in TERMINAL:
            raise HTTPException(409, '消息已开始发送或处于终态，不能撤回')
        m.status, m.approval_hash, m.result = 'cancelled', '', data.reason[:1000]
        event(s, 'message_cancelled', m.id, reason=data.reason[:1000])
    return {'ok': True}


class Pause(Strict):
    paused: bool


@app.post('/api/control/pause')
def pause(data: Pause, actor=Depends(role)):
    if not data.paused and actor != 'reviewer':
        raise HTTPException(403, '恢复发送需项目所有者操作')
    with Session.begin() as s:
        control = locked_control(s)
        control.paused = data.paused
        event(s, 'pause_changed', paused=data.paused, actor=actor)
    return {'paused': data.paused}


class SuppressInput(Strict):
    address: str
    reason: str


@app.post('/api/suppressions')
def suppress(data: SuppressInput, actor=Depends(role)):
    with Session.begin() as s:
        locked_control(s)
        address = data.address.strip().lower()
        if not s.get(Suppression, address):
            s.add(Suppression(address=address, reason=data.reason[:1000]))
        event(s, 'suppressed', address, reason=data.reason[:1000])
    return {'ok': True}


@app.post('/api/sources/hackernews/sync')
def sync(actor=Depends(role)):
    from .tasks import sync_source
    try:
        task = sync_source.delay()
        return {'task_id': task.id, 'status': 'scheduled'}
    except Exception:
        raise HTTPException(503, '任务队列不可用') from None


@app.get('/api/activity')
def activity(actor=Depends(role)):
    with Session() as s:
        return {'events': [obj(e) for e in s.scalars(select(Event).order_by(Event.id.desc()).limit(60))],
            'runs': [obj(r) for r in s.scalars(select(ChannelRun).order_by(ChannelRun.id.desc()).limit(20))]}


static = Path(__file__).parent / 'static'
if static.exists():
    app.mount('/', StaticFiles(directory=static, html=True), name='ui')
