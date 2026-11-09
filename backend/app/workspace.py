"""Human relationship work: never approves, queues, or sends a message."""
from datetime import datetime, timezone
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select, func
from .db import (Session, Lead, LeadWorkspace, Touchpoint, ContactPoint, Contact,
                 Control, AnalysisCall, locked_control, event, now)
from .schemas import Strict, safe_url, ContactInput

STAGES = {'new': '待联系', 'contacted': '已联系', 'conversation': '沟通中',
          'trial': '试用中', 'paid': '已付费', 'retained': '持续使用',
          'declined': '已拒绝', 'closed': '已结束'}
QUALIFICATIONS = {'pending': '待核实', 'valid': '有效需求', 'invalid': '不匹配'}


def utc(value):
    if not value:
        return ''
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if d.tzinfo is None:
            raise ValueError()
        return d.astimezone(timezone.utc).isoformat()
    except (ValueError, AttributeError):
        raise ValueError('时间必须是包含时区的日期时间') from None


def row(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def workspace(s, lead_id):
    if not s.get(Lead, lead_id):
        raise HTTPException(404, '客户不存在')
    w = s.get(LeadWorkspace, lead_id)
    if not w:
        w = LeadWorkspace(lead_id=lead_id)
        s.add(w)
        s.flush()
    return w


def view(s, lead):
    w = s.get(LeadWorkspace, lead.id)
    return {**row(lead), 'work': row(w) if w else {
        'version': 0, 'qualification': 'pending', 'reason': '', 'stage': 'new',
        'next_followup': '', 'unread_reply': False, 'review_seconds': 0}}


class WorkEdit(Strict):
    version: int = Field(ge=0)
    qualification: Literal['pending', 'valid', 'invalid']
    reason: str = Field(default='', max_length=2000)
    stage: Literal['new', 'contacted', 'conversation', 'trial', 'paid', 'retained', 'declined', 'closed']
    next_followup: str = ''
    review_seconds: int = Field(default=0, ge=0, le=3600)
    _time = field_validator('next_followup')(utc)


class TouchInput(Strict):
    request_id: str = Field(min_length=16, max_length=64)
    kind: Literal['note', 'outbound', 'inbound', 'conversation', 'registered', 'first_call', 'paid', 'retained']
    channel: Literal['wechat', 'qq', 'email', 'github', 'community', 'phone', 'other']
    body: str = Field(min_length=1, max_length=5000)
    occurred_at: str
    _time = field_validator('occurred_at')(utc)


class PointInput(Strict):
    kind: Literal['email', 'wechat', 'qq', 'url']
    value: str = Field(min_length=1, max_length=500)
    source_url: str = Field(max_length=2000)
    evidence: str = Field(min_length=5, max_length=2000)
    _url = field_validator('source_url')(safe_url)

    @field_validator('value')
    @classmethod
    def one_line(cls, value):
        if any(c in value for c in '\r\n\x00'):
            raise ValueError('联系方式必须是单行')
        return value.strip()


def register_workspace(app, role, reviewer):
    @app.get('/api/workspace/leads')
    def lead_list(q: str = '', source: str = '', qualification: str = '', stage: str = '',
                  bucket: str = '', offset: int = 0, limit: int = 50, actor=Depends(role)):
        limit, offset = max(1, min(limit, 100)), max(0, offset)
        with Session() as s:
            stmt = select(Lead).outerjoin(LeadWorkspace, LeadWorkspace.lead_id == Lead.id)
            if q:
                stmt = stmt.where(Lead.title.icontains(q, autoescape=True) | Lead.text.icontains(q, autoescape=True) | Lead.author.icontains(q, autoescape=True))
            if source:
                stmt = stmt.where(Lead.source == source)
            if qualification:
                stmt = stmt.where(func.coalesce(LeadWorkspace.qualification, 'pending') == qualification)
            if stage:
                stmt = stmt.where(func.coalesce(LeadWorkspace.stage, 'new') == stage)
            if bucket == 'review':
                stmt = stmt.where(func.coalesce(LeadWorkspace.qualification, 'pending') == 'pending')
            elif bucket == 'contact':
                stmt = stmt.where(LeadWorkspace.qualification == 'valid', LeadWorkspace.stage == 'new')
            elif bucket == 'reply':
                stmt = stmt.where(LeadWorkspace.unread_reply.is_(True))
            elif bucket == 'followup':
                stmt = stmt.where(LeadWorkspace.next_followup != '', LeadWorkspace.next_followup <= now(),
                                  LeadWorkspace.stage.not_in(['closed', 'declined']), LeadWorkspace.qualification != 'invalid')
            total = s.scalar(select(func.count()).select_from(stmt.subquery()))
            items = list(s.scalars(stmt.order_by(Lead.id.desc()).offset(offset).limit(limit)))
            return {'total': total, 'items': [view(s, l) for l in items],
                    'sources': list(s.scalars(select(Lead.source).distinct()))}

    @app.get('/api/workspace/leads/{lead_id}')
    def detail(lead_id: int, actor=Depends(role)):
        with Session() as s:
            l = s.get(Lead, lead_id)
            if not l:
                raise HTTPException(404, '客户不存在')
            return {**view(s, l), 'points': [row(p) for p in s.scalars(select(ContactPoint).where(ContactPoint.lead_id == lead_id))],
                    'email_contacts': [row(c) for c in s.scalars(select(Contact).where(Contact.lead_id == lead_id))],
                    'timeline': [row(t) for t in s.scalars(select(Touchpoint).where(Touchpoint.lead_id == lead_id).order_by(Touchpoint.occurred_at.desc()).limit(100))]}

    @app.put('/api/workspace/leads/{lead_id}')
    def update(lead_id: int, data: WorkEdit, actor=Depends(role)):
        if data.qualification != 'pending' and not data.reason.strip():
            raise HTTPException(422, '请记录判断依据或不匹配原因')
        with Session.begin() as s:
            locked_control(s)
            old = s.get(LeadWorkspace, lead_id)
            if (old.version if old else 0) != data.version:
                raise HTTPException(409, '客户状态已更新，请刷新后重试')
            w = workspace(s, lead_id)
            for k in ('qualification', 'reason', 'stage', 'next_followup'):
                setattr(w, k, getattr(data, k))
            w.review_seconds += data.review_seconds
            w.version, w.updated_at = data.version + 1, now()
            if data.stage in ('declined', 'closed') or data.qualification == 'invalid':
                w.next_followup, w.unread_reply = '', False
                # Stop pending automatic contact too, without changing any approval.
                s.get(Lead, lead_id).human_takeover = True
            event(s, 'workspace_updated', lead_id, **data.model_dump())
            return row(w)

    @app.post('/api/workspace/leads/{lead_id}/touchpoints')
    def touch(lead_id: int, data: TouchInput, actor=Depends(role)):
        if not data.occurred_at or data.occurred_at > now():
            raise HTTPException(422, '沟通记录需填写实际发生时间，不能是未来时间')
        with Session.begin() as s:
            locked_control(s)
            existing = s.scalar(select(Touchpoint).where(Touchpoint.request_id == data.request_id))
            if existing:
                if existing.lead_id != lead_id or any(getattr(existing, k) != v for k, v in data.model_dump().items()):
                    raise HTTPException(409, '重复请求的内容不同')
                return row(existing)
            w = workspace(s, lead_id)
            t = Touchpoint(lead_id=lead_id, **data.model_dump())
            s.add(t)
            if data.kind == 'inbound':
                w.unread_reply = True
            elif data.kind == 'outbound':
                w.unread_reply = False
                if w.stage == 'new':
                    w.stage = 'contacted'
            if data.kind == 'conversation' and w.stage not in ('trial', 'paid', 'retained', 'closed', 'declined'):
                w.stage = 'conversation'
            w.version += 1
            w.updated_at = now()
            s.flush()
            event(s, 'manual_touchpoint', lead_id, kind_recorded=data.kind, record_id=t.id)
            return row(t)

    @app.post('/api/workspace/leads/{lead_id}/read')
    def read_reply(lead_id: int, actor=Depends(role)):
        with Session.begin() as s:
            locked_control(s)
            w = workspace(s, lead_id)
            w.unread_reply = False
            w.version += 1
            return {'ok': True}

    @app.post('/api/workspace/leads/{lead_id}/points')
    def add_point(lead_id: int, data: PointInput, actor=Depends(role)):
        if data.kind == 'email':
            try:
                data.value = ContactInput(address=data.value, source_url=data.source_url, basis=data.evidence).address
            except ValueError:
                raise HTTPException(422, '请填写单个有效邮箱地址') from None
        if data.kind == 'url':
            try:
                safe_url(data.value)
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
        with Session.begin() as s:
            locked_control(s)
            workspace(s, lead_id)
            existing = s.scalar(select(ContactPoint).where(ContactPoint.lead_id == lead_id, ContactPoint.kind == data.kind, ContactPoint.value == data.value))
            if existing:
                return row(existing)
            p = ContactPoint(lead_id=lead_id, **data.model_dump())
            s.add(p)
            s.flush()
            event(s, 'contact_point_added', lead_id, point_id=p.id)
            return row(p)

    @app.post('/api/workspace/points/{point_id}/verify')
    def verify_point(point_id: int, actor=Depends(reviewer)):
        with Session.begin() as s:
            locked_control(s)
            p = s.get(ContactPoint, point_id)
            if not p:
                raise HTTPException(404, '联系方式不存在')
            p.verified = True
            if p.kind == 'email':
                c = s.scalar(select(Contact).where(Contact.lead_id == p.lead_id, Contact.address == p.value))
                if not c:
                    s.add(Contact(lead_id=p.lead_id, address=p.value, source_url=p.source_url, basis=p.evidence, verified=True))
                else:
                    c.verified = True
            event(s, 'contact_point_verified', p.lead_id, point_id=p.id)
            return row(p)

    @app.get('/api/workspace/leads/{lead_id}/suggestion')
    def suggestion(lead_id: int, actor=Depends(role)):
        with Session() as s:
            l = s.get(Lead, lead_id)
            if not l:
                raise HTTPException(404, '客户不存在')
            p = s.get(Control, 1).product
            w = s.get(LeadWorkspace, lead_id)
            if w and (w.stage in ('declined', 'closed') or w.qualification == 'invalid'):
                raise HTTPException(409, '该客户已停止跟进或标记为不匹配')
            if not p.get('confirmed'):
                raise HTTPException(409, '产品资料尚未确认')
            return {'method': 'confirmed_facts_template', 'body': f'你好，看到你发布的「{l.title[:100]}」。我们{p["description"]}\n想了解一下，你目前还在评估模型接口吗？主要的模型和使用场景是什么？如果不方便联系，请告诉我。',
                    'evidence_url': l.url, 'authorization': 'none', 'product_version': p.get('version')}

    @app.get('/api/workspace/metrics')
    def metrics(actor=Depends(role)):
        with Session() as s:
            rows = list(s.execute(select(Lead, LeadWorkspace).outerjoin(LeadWorkspace, LeadWorkspace.lead_id == Lead.id)))
            points = set(s.scalars(select(ContactPoint.lead_id))) | set(s.scalars(select(Contact.lead_id)))
            events = list(s.scalars(select(Touchpoint)))
            calls = list(s.scalars(select(AnalysisCall)))
            grouped = {}
            for l, w in rows:
                g = grouped.setdefault(l.source, {'source': l.source, 'candidates': 0, 'reviewed': 0, 'valid': 0, 'invalid': 0,
                    'with_contact': 0, 'review_seconds': 0, 'conversation': set(), 'registered': set(), 'first_call': set(), 'paid': set(), 'retained': set(),
                    'estimated_ai_usd': 0, 'unresolved_calls': 0})
                g['candidates'] += 1
                if w:
                    g['reviewed'] += int(w.qualification != 'pending')
                    g['valid'] += int(w.qualification == 'valid')
                    g['invalid'] += int(w.qualification == 'invalid')
                    g['review_seconds'] += w.review_seconds
                g['with_contact'] += int(l.id in points)
            source_by_id = {l.id: l.source for l, _ in rows}
            for t in events:
                if t.lead_id in source_by_id and t.kind in ('conversation', 'registered', 'first_call', 'paid', 'retained'):
                    grouped[source_by_id[t.lead_id]][t.kind].add(t.lead_id)
            for c in calls:
                if c.lead_id in source_by_id:
                    g = grouped[source_by_id[c.lead_id]]
                    g['estimated_ai_usd'] += c.estimated_usd or 0
                    g['unresolved_calls'] += int(c.estimated_usd is None)
            for g in grouped.values():
                for k in ('conversation', 'registered', 'first_call', 'paid', 'retained'):
                    g[k] = len(g[k])
                g['valid_rate'] = g['valid'] / g['reviewed'] if g['reviewed'] else None
                g['ai_cost_per_valid'] = g['estimated_ai_usd'] / g['valid'] if g['valid'] and not g['unresolved_calls'] else None
            return {'channels': list(grouped.values()), 'counts': {
                'review': sum(1 for _, w in rows if not w or w.qualification == 'pending'),
                'contact': sum(1 for _, w in rows if w and w.qualification == 'valid' and w.stage == 'new'),
                'reply': sum(1 for _, w in rows if w and w.unread_reply),
                'followup': sum(1 for _, w in rows if w and w.next_followup and w.next_followup <= now() and w.stage not in ('declined','closed') and w.qualification != 'invalid')},
                'conversion_source': 'manual', 'billing_connected': False, 'inbox_connected': False,
                'gross_margin': None, 'development_cost': None}
