"""Human relationship work: never approves, queues, or sends a message."""
from datetime import datetime, timedelta, timezone
from typing import Literal
import re
import httpx
from fastapi import Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select, func, exists, or_, and_, case
from .db import (Session, Lead, LeadWorkspace, Touchpoint, ContactPoint, Contact, GithubIssueState,
                 Control, AnalysisCall, LeadMarket, MarketPolicy, CaptureEvidence, locked_control, event, now)
from .schemas import Strict, safe_url, ContactInput
from .social_adapters import HOSTS
from .market import authored_text, market_view, refresh_market, market_predicate, market_state, threshold, ELIGIBLE, LABELS
from .prospecting import intent, supplier_promotion

STAGES = {'new': '待联系', 'contacted': '已联系', 'conversation': '沟通中',
          'trial': '试用中', 'paid': '已付费', 'retained': '持续使用',
          'declined': '已拒绝', 'closed': '已结束'}
QUALIFICATIONS = {'pending': '待核实', 'valid': '有效需求', 'invalid': '不匹配'}
GITHUB_LOGIN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z')
CONTACT_KINDS = ('email', 'wechat', 'qq', 'url')
PUBLIC_DISCUSSION_PREFIXES = ('github:', 'hn:', *(platform + ':' for platform in HOSTS))
ISSUE_STATE_MAX_AGE = timedelta(days=2)
V2EX_PRIVATE_INVITATION = re.compile(
    r'(?:^|[\n，,。！？；])\s*((?:有(?:知道|了解|推荐)的\s*)?'
    r'(?:请|欢迎|麻烦)(?:直接)?(?:私聊|私信|站内信)我(?:地址|链接|详情)?)(?=$|[\s，,。！？；!?：:])'
)


def direct_contact(lead_id, verified=False):
    points = select(ContactPoint.id).where(ContactPoint.lead_id == lead_id, ContactPoint.kind.in_(('email','wechat','qq')))
    emails = select(Contact.id).where(Contact.lead_id == lead_id)
    if verified:
        points, emails = points.where(ContactPoint.verified.is_(True)), emails.where(Contact.verified.is_(True))
    return or_(exists(points), exists(emails))


def high_value():
    return and_(Lead.analysis['method'].as_string() == 'ai', Lead.analysis['fit'].as_string() == 'high',
                Lead.analysis['priority'].as_string() == 'high')


def verified_path(lead_id):
    return or_(
        exists(select(ContactPoint.id).where(ContactPoint.lead_id == lead_id,
                                             ContactPoint.verified.is_(True),
                                             ContactPoint.kind.in_(CONTACT_KINDS))),
        exists(select(Contact.id).where(Contact.lead_id == lead_id, Contact.verified.is_(True))))


def contact_status(s, lead):
    if s.scalar(select(verified_path(lead.id))):
        return 'verified_contact'
    if s.scalar(select(ContactPoint.id).where(ContactPoint.lead_id == lead.id).limit(1)) or s.scalar(
            select(Contact.id).where(Contact.lead_id == lead.id).limit(1)):
        return 'unverified_candidate'
    if lead.source_key.startswith(PUBLIC_DISCUSSION_PREFIXES):
        return 'public_discussion'
    return 'no_path'


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


def pending_domestic_promotions(s, cutoff):
    rows = s.execute(
        select(Lead.id, Lead.title, Lead.text).join(LeadMarket, LeadMarket.lead_id == Lead.id)
        .outerjoin(LeadWorkspace, LeadWorkspace.lead_id == Lead.id)
        .where(market_predicate('domestic', cutoff),
               func.coalesce(LeadWorkspace.qualification, 'pending') == 'pending',
               func.coalesce(LeadWorkspace.stage, 'new') == 'new')
    )
    return {lead_id for lead_id, title, text in rows if supplier_promotion(title, text)}


def market_review_ids(s, cutoff):
    recent = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
    rows = s.execute(
        select(Lead.id, Lead.title, Lead.text).join(LeadMarket, LeadMarket.lead_id == Lead.id)
        .outerjoin(LeadWorkspace, LeadWorkspace.lead_id == Lead.id)
        .outerjoin(GithubIssueState, GithubIssueState.lead_id == Lead.id)
        .where(LeadMarket.assessment_version != '', LeadMarket.manual.is_(False),
               or_(LeadMarket.score.is_(None), LeadMarket.score < cutoff),
               Lead.analysis['method'].as_string() == 'ai',
               Lead.analysis['fit'].as_string().in_(('medium', 'high')),
               Lead.analysis['purchase_stage'].as_string().in_(('explicit_need', 'exploring')),
               or_(Lead.published_at == '', Lead.published_at >= recent),
               func.coalesce(LeadWorkspace.qualification, 'pending') != 'invalid',
               func.coalesce(LeadWorkspace.stage, 'new') == 'new',
               func.coalesce(GithubIssueState.state, 'unavailable').not_in(('closed', 'gone', 'mismatch')))
    )
    return [lead_id for lead_id, title, text in rows if intent(title, text)['score'] >= 35]


def view(s, lead):
    w = s.get(LeadWorkspace, lead.id)
    evidence = s.get(CaptureEvidence, lead.id)
    issue = s.get(GithubIssueState, lead.id) if lead.source_key.startswith('github:') else None
    issue_state = None if not issue else {
        'state': issue.state, 'reason': issue.reason, 'checked_at': issue.checked_at,
        'stale': issue.checked_at < (datetime.now(timezone.utc) - ISSUE_STATE_MAX_AGE).isoformat(),
    }
    triage = intent(lead.title, lead.text)
    contact = contact_status(s, lead)
    invitation = None
    if triage['score'] >= 70 and contact == 'public_discussion' and lead.source_key.startswith('v2ex:'):
        match = V2EX_PRIVATE_INVITATION.search(authored_text(lead.text))
        if match and match[1] in lead.text:
            invitation = {'quote': match[1], 'url': lead.url}
    return {**row(lead), 'capture': row(evidence) if evidence else None, 'triage': triage, 'market': market_view(s, lead), 'contact_status': contact,
        'contact_invitation': invitation,
        'issue_state': issue_state,
        'high_value_no_contact': bool(lead.analysis.get('method') == 'ai' and lead.analysis.get('fit') == 'high'
            and lead.analysis.get('priority') == 'high' and not s.scalar(select(direct_contact(lead.id)))),
        'work': row(w) if w else {
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


class MarketEdit(Strict):
    version: int = Field(ge=0)
    status: Literal['confirmed_cn', 'outside_target', 'unknown', 'auto']
    reason: str = Field(min_length=5, max_length=2000)
    source_url: str = Field(default='', max_length=2000)

    @field_validator('source_url')
    @classmethod
    def evidence_url(cls, value):
        return safe_url(value) if value else ''


class ThresholdEdit(Strict):
    version: int = Field(ge=0)
    threshold: int = Field(ge=0, le=100)


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
    @app.post('/api/workspace/leads/{lead_id}/public-evidence')
    def public_evidence(lead_id: int, actor=Depends(role)):
        with Session() as s:
            if not s.get(Lead, lead_id):
                raise HTTPException(404, '客户不存在')
        from .profile_research import research_profile
        profile = research_profile(lead_id)
        return {'status': profile.get('readme', {}).get('status', profile.get('status', 'unsupported')),
                'source_url': profile.get('readme', {}).get('url', ''), 'verified': False,
                'notice': '仅整理作者公开披露的候选；身份、商务用途和联系许可仍需核实。'}

    @app.get('/api/workspace/market-policy')
    def market_policy(actor=Depends(role)):
        with Session() as s:
            p = s.get(MarketPolicy, 1)
            return {'threshold': threshold(s), 'version': p.version if p else 0,
                    'meaning': '国内目标市场 AI 置信分数，非校准概率；未知不自动纳入'}

    @app.put('/api/workspace/market-policy')
    def update_market_policy(data: ThresholdEdit, actor=Depends(reviewer)):
        with Session.begin() as s:
            locked_control(s)
            p = s.get(MarketPolicy, 1)
            if (p.version if p else 0) != data.version:
                raise HTTPException(409, '筛选阈值已变化，请刷新')
            if not p:
                p = MarketPolicy(id=1)
                s.add(p)
            p.threshold, p.version = data.threshold, data.version + 1
            event(s, 'market_threshold_changed', threshold=data.threshold, version=p.version, actor=actor)
            return {'threshold': p.threshold, 'version': p.version}

    @app.get('/api/workspace/leads')
    def lead_list(q: str = '', source: str = '', qualification: str = '', stage: str = '',
                  bucket: str = '', contact: str = '', market: Literal['all', 'domestic', 'unknown', 'below_threshold', 'outside_target'] = 'all',
                  offset: int = 0, limit: int = 50, actor=Depends(role)):
        limit, offset = max(1, min(limit, 100)), max(0, offset)
        if contact and contact not in ('verified_contact', 'unverified_candidate', 'public_discussion', 'no_path'):
            raise HTTPException(422, '联系方式筛选无效')
        with Session() as s:
            stmt = select(Lead).outerjoin(LeadWorkspace, LeadWorkspace.lead_id == Lead.id).outerjoin(LeadMarket, LeadMarket.lead_id == Lead.id)
            research_ids = []
            if bucket == 'opportunity':
                from .prospecting import pending_ranked
                research_ids = [r['id'] for r in pending_ranked(s)]
                stmt = stmt.where(Lead.id.in_(research_ids))
            if market != 'all':
                cutoff = threshold(s)
                stmt = stmt.where(market_predicate(market, cutoff))
                if market == 'domestic':
                    promotions = pending_domestic_promotions(s, cutoff)
                    if promotions:
                        stmt = stmt.where(Lead.id.not_in(promotions))
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
                stmt = stmt.where(LeadWorkspace.qualification == 'valid', LeadWorkspace.stage == 'new', verified_path(Lead.id))
            elif bucket == 'research':
                stmt = stmt.where(LeadWorkspace.qualification == 'valid', LeadWorkspace.stage == 'new', ~verified_path(Lead.id))
            elif bucket == 'reply':
                stmt = stmt.where(LeadWorkspace.unread_reply.is_(True))
            elif bucket == 'followup':
                stmt = stmt.where(LeadWorkspace.next_followup != '', LeadWorkspace.next_followup <= now(),
                                  LeadWorkspace.stage.not_in(['closed', 'declined']), LeadWorkspace.qualification != 'invalid')
            elif bucket == 'market':
                stmt = stmt.where(market_predicate('unknown', threshold(s)))
            elif bucket == 'market_review':
                stmt = stmt.where(Lead.id.in_(market_review_ids(s, threshold(s))))
            elif bucket == 'high_value':
                stmt = stmt.where(high_value(), ~direct_contact(Lead.id))
            candidate = or_(exists(select(ContactPoint.id).where(ContactPoint.lead_id == Lead.id)),
                            exists(select(Contact.id).where(Contact.lead_id == Lead.id)))
            if contact == 'verified_contact':
                stmt = stmt.where(verified_path(Lead.id))
            elif contact == 'unverified_candidate':
                stmt = stmt.where(~verified_path(Lead.id), candidate)
            elif contact == 'public_discussion':
                stmt = stmt.where(~candidate, or_(*(Lead.source_key.startswith(prefix, autoescape=True) for prefix in PUBLIC_DISCUSSION_PREFIXES)))
            elif contact == 'no_path':
                stmt = stmt.where(~candidate, *(~Lead.source_key.startswith(prefix, autoescape=True) for prefix in PUBLIC_DISCUSSION_PREFIXES))
            total = s.scalar(select(func.count()).select_from(stmt.subquery()))
            contact_order = case((direct_contact(Lead.id, True), 0), (direct_contact(Lead.id), 1), (high_value(), 2), else_=3)
            if research_ids:
                ranking = [case({lead_id: i for i, lead_id in enumerate(research_ids)},
                                value=Lead.id, else_=len(research_ids))]
            else:
                ranking = [contact_order]
                if bucket == 'market_review':
                    ranking += [case((Lead.analysis['purchase_stage'].as_string() == 'explicit_need', 0), else_=1),
                                Lead.published_at.desc()]
                if market == 'domestic' and bucket in ('', 'review'):
                    issue_cutoff = (datetime.now(timezone.utc) - ISSUE_STATE_MAX_AGE).isoformat()
                    stmt = stmt.outerjoin(GithubIssueState, GithubIssueState.lead_id == Lead.id)
                    ranking.append(case(
                        (and_(GithubIssueState.checked_at >= issue_cutoff,
                              GithubIssueState.state == 'mismatch'), 2),
                        (and_(GithubIssueState.checked_at >= issue_cutoff,
                              GithubIssueState.state.in_(('closed', 'gone'))), 1), else_=0))
                    assessed = Lead.analysis['method'].as_string() == 'ai'
                    stage_value = Lead.analysis['purchase_stage'].as_string()
                    ranking.append(case(
                        (assessed & (stage_value == 'explicit_need'), 0),
                        (assessed & (stage_value == 'exploring'), 1),
                        else_=2,
                    ))
            items = list(s.scalars(stmt.order_by(*ranking, Lead.id.desc()).offset(offset).limit(limit)))
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

    @app.put('/api/workspace/leads/{lead_id}/market')
    def review_market(lead_id: int, data: MarketEdit, actor=Depends(reviewer)):
        if not data.reason.strip() or (data.status in ('confirmed_cn', 'outside_target') and not data.source_url):
            raise HTTPException(422, '请填写业务地区判断依据和来源链接，不能仅凭昵称或语言确认')
        with Session.begin() as s:
            locked_control(s)
            lead = s.get(Lead, lead_id)
            if not lead:
                raise HTTPException(404, '客户不存在')
            old = s.get(LeadMarket, lead_id)
            if (old.version if old else 0) != data.version:
                raise HTTPException(409, '市场判断已变化，请刷新后重试')
            if not old:
                old = LeadMarket(lead_id=lead_id, version=0)
                s.add(old)
            if data.status == 'auto':
                old.manual, old.rule_version = False, ''
                refresh_market(s, lead)
            else:
                old.status, old.reason, old.manual = data.status, data.reason.strip(), True
                old.evidence = [{'kind': 'owner_business_evidence', 'quote': data.reason.strip(), 'url': data.source_url}]
                old.version = data.version + 1
                old.updated_at = now()
            s.flush()
            event(s, 'market_reviewed', lead_id, status=old.status, version=old.version, actor=actor)
            return market_view(s, lead)
    @app.get('/api/workspace/leads/{lead_id}/contact-research')
    def research(lead_id: int, actor=Depends(role)):
        with Session() as s:
            lead = s.get(Lead, lead_id)
            if not lead:
                raise HTTPException(404, '客户不存在')
            login = lead.author
            if not lead.source_key.startswith('github:') or not re.fullmatch(
                    r'https://github\.com/[^/]+/[^/]+/issues/\d+', lead.url) or not GITHUB_LOGIN.fullmatch(login):
                raise HTTPException(422, '仅支持 GitHub Issue 的有效公开作者资料')
        # Only call a fixed official API; never fetch URLs from an issue or profile.
        try:
            with httpx.Client(timeout=8, follow_redirects=False, headers={
                    'User-Agent': 'HuokePilot/0.2 public-profile-review',
                    'Accept': 'application/vnd.github+json'}) as client:
                response = client.get(f'https://api.github.com/users/{login}')
                response.raise_for_status()
                profile = response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, '公开资料暂时无法获取，请稍后重试或手工查看') from None
        if not isinstance(profile, dict) or not isinstance(profile.get('login'), str) or profile['login'].lower() != login.lower():
            raise HTTPException(502, '公开资料身份不匹配')
        email = profile.get('email') if isinstance(profile.get('email'), str) else ''
        if email and not email.lower().startswith(('noreply@', 'no-reply@')) and 'noreply.github.com' not in email.lower():
            try:
                email = ContactInput(address=email, source_url=f'https://github.com/{login}',
                                     basis='公开资料候选，未核实商务用途').address
            except ValueError:
                email = ''
        else:
            email = ''
        blog = profile.get('blog') if isinstance(profile.get('blog'), str) else ''
        if blog and not re.match(r'^https?://', blog, re.I):
            blog = '' if re.match(r'^[a-z][a-z0-9+.-]*:', blog, re.I) else 'https://' + blog
        try:
            safe_url(blog)
        except ValueError:
            blog = ''
        return {'profile_url': f'https://github.com/{login}', 'public_email': email,
                'website_url': blog, 'company': str(profile.get('company') or '')[:200],
                'verified': False, 'contact_permission': 'unknown',
                'notice': '仅供人工核实身份和商务用途；资料不自动入库，也不表示允许营销联系'}

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
            candidates = set(s.scalars(select(ContactPoint.lead_id))) | set(s.scalars(select(Contact.lead_id)))
            direct_ids = set(s.scalars(select(ContactPoint.lead_id).where(ContactPoint.kind.in_(('email','wechat','qq'))))) | set(s.scalars(select(Contact.lead_id)))
            points = set(s.scalars(select(ContactPoint.lead_id).where(ContactPoint.verified.is_(True), ContactPoint.kind.in_(CONTACT_KINDS)))) | set(
                s.scalars(select(Contact.lead_id).where(Contact.verified.is_(True))))
            events = list(s.scalars(select(Touchpoint)))
            calls = list(s.scalars(select(AnalysisCall)))
            cutoff = threshold(s)
            markets = {m.lead_id: market_state(m, cutoff) for m in s.scalars(select(LeadMarket))}
            promotions = pending_domestic_promotions(s, cutoff)
            domestic_ids = {lead.id for lead, _ in rows if markets.get(lead.id) in ELIGIBLE} - promotions
            from .prospecting import pending_ranked
            opportunities = pending_ranked(s)
            grouped = {}
            for l, w in rows:
                g = grouped.setdefault(l.source, {'source': l.source, 'candidates': 0, 'reviewed': 0, 'valid': 0, 'invalid': 0,
                    'with_contact': 0, 'with_candidate_contact': 0, 'review_seconds': 0, 'conversation': set(), 'registered': set(), 'first_call': set(), 'paid': set(), 'retained': set(),
                    'manual_contacted': set(), 'manual_replied': set(),
                    'estimated_ai_usd': 0, 'unresolved_calls': 0})
                g['candidates'] += 1
                if w:
                    g['reviewed'] += int(w.qualification != 'pending')
                    g['valid'] += int(w.qualification == 'valid')
                    g['invalid'] += int(w.qualification == 'invalid')
                    g['review_seconds'] += w.review_seconds
                g['with_contact'] += int(l.id in points)
                g['with_candidate_contact'] += int(l.id in candidates)
            source_by_id = {l.id: l.source for l, _ in rows}
            for t in events:
                if t.lead_id in source_by_id and t.provenance == 'manual' and t.kind in ('outbound', 'inbound'):
                    grouped[source_by_id[t.lead_id]]['manual_contacted' if t.kind == 'outbound' else 'manual_replied'].add(t.lead_id)
                if t.lead_id in source_by_id and t.kind in ('conversation', 'registered', 'first_call', 'paid', 'retained'):
                    grouped[source_by_id[t.lead_id]][t.kind].add(t.lead_id)
            for c in calls:
                if c.lead_id in source_by_id:
                    g = grouped[source_by_id[c.lead_id]]
                    g['estimated_ai_usd'] += c.estimated_usd or 0
                    g['unresolved_calls'] += int(c.estimated_usd is None)
            for g in grouped.values():
                replies = len(g['manual_contacted'] & g['manual_replied'])
                contacted = len(g['manual_contacted'])
                g['manual_contacted'], g['manual_replied'] = contacted, replies
                g['manual_reply_rate'] = replies / contacted if contacted else None
                for k in ('conversation', 'registered', 'first_call', 'paid', 'retained'):
                    g[k] = len(g[k])
                g['valid_rate'] = g['valid'] / g['reviewed'] if g['reviewed'] else None
                g['ai_cost_per_valid'] = g['estimated_ai_usd'] / g['valid'] if g['valid'] and not g['unresolved_calls'] else None
            return {'channels': list(grouped.values()),
                'acquisition': {
                    'total': len(rows),
                    'pending': sum(1 for l, _ in rows if markets.get(l.id, 'pending_ai') == 'pending_ai'),
                    'ai_assessed': s.scalar(select(func.count()).select_from(LeadMarket).where(LeadMarket.assessment_version != '')),
                    'domestic': sum(1 for l, _ in rows if l.id in domestic_ids),
                    'domestic_accounts': len({(l.source, l.author.lower() if l.author and l.author != '未知' else str(l.id)) for l, _ in rows if l.id in domestic_ids}),
                    'domestic_with_contact': len(domestic_ids & direct_ids),
                    'with_contact': len(direct_ids), 'priority_pending': len(opportunities),
                    'note': '待分析不是筛选失败；市场分数不是成交概率，公开联系方式仍需核实。'},
                'market_counts': {status: sum(1 for l, _ in rows if markets.get(l.id, 'pending_ai') == status) for status in LABELS},
                'counts': {
                'review': sum(1 for l, w in rows if l.id in domestic_ids and (not w or w.qualification == 'pending')),
                'contact': sum(1 for l, w in rows if l.id in domestic_ids and w and w.qualification == 'valid' and w.stage == 'new' and l.id in points),
                'market': sum(1 for l, _ in rows if markets.get(l.id, 'pending_ai') in ('unknown','pending_ai')),
                'market_review': len(market_review_ids(s, cutoff)),
                'high_value': sum(1 for l, _ in rows if l.id in domestic_ids and l.id not in direct_ids
                                  and l.analysis.get('method') == 'ai' and l.analysis.get('fit') == 'high' and l.analysis.get('priority') == 'high'),
                'research': sum(1 for l, w in rows if l.id in domestic_ids and w and w.qualification == 'valid' and w.stage == 'new' and l.id not in points),
                'reply': sum(1 for _, w in rows if w and w.unread_reply),
                'followup': sum(1 for _, w in rows if w and w.next_followup and w.next_followup <= now() and w.stage not in ('declined','closed') and w.qualification != 'invalid')},
                'conversion_source': 'manual', 'billing_connected': False, 'inbox_connected': False,
                'gross_margin': None, 'development_cost': None}
