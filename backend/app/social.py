"""Multi-platform acquisition: bounded reading/import only; no outbox mutation."""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
import httpx
from .schemas import Strict
from .db import (Session, SourcePolicy, SourceItem, ChannelRun, Lead, CaptureEvidence,
                 locked_control, now, event)
from .pipeline import clean, classify, fingerprint
from .prospecting import intent
from .market import refresh_market
from .contact_discovery import extract_contacts, save_candidate
from .social_adapters import CATALOG, Capture, feed_items, validate_url
from .config import settings

VERSION = 'social-capture-v1'
DEFAULT_QUERIES = ['API', '模型', '中转']


def get_policy(s, platform):
    p = s.get(SourcePolicy, 'social:' + platform)
    if not p:
        p = SourcePolicy(channel='social:' + platform, enabled=False, options={
            'queries': DEFAULT_QUERIES, 'exclude': [], 'account': '', 'conversations': []})
        s.add(p)
        s.flush()
    return p


class SocialConfig(Strict):
    version: int = Field(ge=1)
    enabled: bool
    queries: list[str] = Field(min_length=1, max_length=3)
    exclude: list[str] = Field(default_factory=list, max_length=20)
    account: str = Field(default='', max_length=120)
    conversations: list[str] = Field(default_factory=list, max_length=10)


class Batch(Strict):
    version: int = Field(ge=1)
    items: list[Capture] = Field(max_length=200)
    mode: str = Field(pattern='^(executor|manual)$')


class ExecutorStatus(Strict):
    version: int = Field(ge=1)
    status: str = Field(pattern='^(login_required|unavailable|verification_required|failed)$')
    code: str = Field(pattern='^[a-zA-Z0-9_:.-]{1,100}$')


def validate_capture(platform, item, options):
    if platform in ('qq', 'wechat'):
        if not options.get('account') or item.account != options['account']:
            raise ValueError('采集账号与已保存配置不一致')
        if item.conversation not in options.get('conversations', []):
            raise ValueError('会话不在所选采集范围')
        if item.url:
            raise ValueError('聊天记录没有公开原帖链接，请留空')
        if platform == 'qq' and (not item.sender_id.isdigit() or not item.identity_verified):
            raise ValueError('QQ 采集需要平台发言者 ID')
        if platform == 'wechat' and item.identity_verified:
            raise ValueError('OCR 不提供已核实的平台身份')
    else:
        validate_url(platform, item.url)
        if item.account or item.conversation or item.identity_verified:
            raise ValueError('公开内容不能声明登录账号或已核实联系人')
    if item.published_at and datetime.fromisoformat(item.published_at) > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError('原文发布时间不能在未来')


def ingest(s, platform, items, options, provenance):
    stats = {'listed': len(items), 'filtered': 0, 'cache_hits': 0, 'new_leads': 0, 'updated': 0,
             'model_calls': 0, 'capture_mode': provenance, 'rule_version': VERSION,
             'filter_reasons': {}, 'filtered_samples': []}
    for item in items:
        validate_capture(platform, item, options)
    for item in items:
        title = clean(item.title or item.text.split('\n')[0])[:250]
        text = clean(item.text)
        event_id = fingerprint(item.url) if provenance == 'manual' else item.event_id
        if platform == 'v2ex':
            match = re.fullmatch(r'https://(?:www\.)?v2ex.com/t/(\d+)(?:#.*)?', item.url)
            if not match:
                raise ValueError('V2EX 需要具体主题链接')
            event_id = match[1]
        key = (platform + ':' + event_id if platform == 'v2ex' else
               platform + ':' + hashlib.sha256(json.dumps([item.account, item.conversation, event_id], ensure_ascii=False).encode()).hexdigest())
        digest = fingerprint(title + '\n' + text)
        cached_digest = fingerprint(digest + VERSION + json.dumps(options, sort_keys=True, ensure_ascii=False))
        cached = s.get(SourceItem, key)
        if cached and cached.fingerprint == cached_digest:
            stats['cache_hits'] += 1
            continue
        if cached:
            cached.fingerprint, cached.checked_at = cached_digest, now()
        else:
            s.add(SourceItem(key=key, fingerprint=cached_digest))
        s.flush()  # intra-batch duplicates see the same key, even with disabled autoflush
        reason = ('older_than_90_days' if item.published_at and datetime.fromisoformat(item.published_at) < datetime.now(timezone.utc) - timedelta(days=90)
                  else 'excluded_keyword' if any(x.lower() in (title + '\n' + text).lower() for x in options['exclude'])
                  else 'no_keyword' if not any(x.lower() in (title + '\n' + text).lower() for x in options['queries'])
                  else 'no_demand_evidence' if intent(title, text)['score'] < 35 else '')
        if reason:
            stats['filtered'] += 1
            stats['filter_reasons'][reason] = stats['filter_reasons'].get(reason, 0) + 1
            if len(stats['filtered_samples']) < 5:
                stats['filtered_samples'].append({'title': title[:160], 'url': item.url, 'reason': reason})
            continue
        lead = s.scalar(select(Lead).where(Lead.source_key == key))
        # Unknown OCR labels never become a cross-conversation identity.
        author = ((item.sender_id if item.sender_id else item.author) if platform != 'wechat'
                  else item.author[:100] + ' [OCR:' + fingerprint(item.account + item.conversation)[:12] + ']')
        analysis = classify(text)
        analysis.update(version=VERSION, next_action='核实原始需求、发言者与联系入口；采集不代表允许联系')
        if lead:
            # Preserve an AI result on exact duplicates acquired through another transport.
            if lead.fingerprint == digest:
                stats['cache_hits'] += 1
                continue
            lead.title, lead.text, lead.fingerprint, lead.analysis = title, text, digest, analysis
            stats['updated'] += 1
        else:
            lead = Lead(source='V2EX' if platform == 'v2ex' else CATALOG[platform]['name'], source_key=key,
                        url=item.url, author=author[:200], title=title, text=text, fingerprint=digest,
                        published_at=item.published_at, analysis=analysis)
            s.add(lead)
            s.flush()
            stats['new_leads'] += 1
        evidence = s.get(CaptureEvidence, lead.id)
        if not evidence:
            evidence = CaptureEvidence(lead_id=lead.id)
            s.add(evidence)
        evidence.platform, evidence.provenance = platform, provenance
        evidence.account, evidence.conversation = item.account, item.conversation
        evidence.sender_id, evidence.sender_label = item.sender_id, item.author
        evidence.identity_verified, evidence.captured_at = item.identity_verified, now()
        # No invented public URL for private chats. Candidate identities do not become verified contacts.
        if item.url:
            extract_contacts(s, lead)
        if platform == 'qq':
            save_candidate(s, lead, 'qq', item.sender_id, '',
                           f'授权账号 {item.account}，会话 {item.conversation}，消息 {item.event_id} 的平台发送者 ID；联系许可与私聊可达性未核实', 'authorized_chat')
        refresh_market(s, lead)
    return stats


def status_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return ('verification_required' if code in (401, 403, 429) else 'failed'), 'HTTP_' + str(code)
    return 'failed', type(exc).__name__


def sync_social(platform):
    if platform not in CATALOG or CATALOG[platform]['mode'] != 'feed':
        return {'status': 'executor_required'}
    with Session.begin() as s:
        locked_control(s)
        p = get_policy(s, platform)
        if not settings().source_enabled or not p.enabled:
            return {'status': 'disabled'}
        if p.next_allowed_at > now():
            return {'status': 'cooldown'}
        p.next_allowed_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        for old in s.scalars(select(ChannelRun).where(ChannelRun.channel == p.channel, ChannelRun.status == 'running')):
            old.status, old.finished_at, old.error = 'interrupted', now(), 'previous_run_interrupted'
        run = ChannelRun(channel=p.channel)
        s.add(run)
        s.flush()
        run_id, version = run.id, p.version
    try:
        with httpx.Client(timeout=20, follow_redirects=False, headers={'User-Agent': 'HuokePilot/0.3 bounded-public-feed'}) as client:
            items = feed_items(platform, client)
        with Session.begin() as s:
            locked_control(s)
            p = get_policy(s, platform)
            if p.version != version or not p.enabled:
                result = {'status': 'config_changed'}
            else:
                result = {'status': 'success', **ingest(s, platform, items, p.options, 'server_feed')}
            r = s.get(ChannelRun, run_id)
            r.status, r.stats, r.finished_at = result['status'], result, now()
        return result
    except Exception as exc:
        status, error = status_error(exc)
        with Session.begin() as s:
            r = s.get(ChannelRun, run_id)
            r.status, r.error, r.finished_at = status, error, now()
        return {'status': status, 'error': error}


def register_social(app, role):
    def platform_exists(platform):
        if platform not in CATALOG:
            raise HTTPException(404, '渠道不存在')

    @app.get('/api/social/channels')
    def channels(actor=Depends(role)):
        with Session.begin() as s:
            locked_control(s)
            result = []
            for platform, spec in CATALOG.items():
                p = get_policy(s, platform)
                runs = select(ChannelRun).where(ChannelRun.channel == p.channel)
                latest = s.scalar(runs.order_by(ChannelRun.id.desc()).limit(1))
                good = s.scalar(runs.where(ChannelRun.status == 'success').order_by(ChannelRun.id.desc()).limit(1))
                result.append({'platform': platform, **spec, 'version': p.version, 'enabled': p.enabled, **p.options,
                               'next_allowed_at': p.next_allowed_at, 'status': latest.status if latest else 'not_connected',
                               'last_success': good.finished_at if good else '', 'last_attempt': latest.started_at if latest else '',
                               'error': latest.error if latest else '', 'stats': latest.stats if latest else {}, 'can_send': False})
            return result

    @app.put('/api/social/channels/{platform}')
    def configure(platform: str, data: SocialConfig, actor=Depends(role)):
        platform_exists(platform)
        words = data.queries + data.exclude + data.conversations
        if any(not x.strip() or len(x) > 200 or '\n' in x or '\r' in x for x in words):
            raise HTTPException(422, '关键词/会话不能为空，每行最多 200 字')
        if platform in ('qq', 'wechat') and data.enabled and (not data.account.strip() or not data.conversations):
            raise HTTPException(422, '启用前请填写采集账号和允许读取的会话')
        if platform == 'qq' and (any(not re.fullmatch(r'(friend|group):[0-9]{4,20}', x) for x in data.conversations) or (data.account and not data.account.isdigit())):
            raise HTTPException(422, 'QQ 账号填写数字；会话格式 group:群号 或 friend:QQ号')
        if platform not in ('qq', 'wechat') and (data.account or data.conversations):
            raise HTTPException(422, '此渠道不使用聊天账号和会话范围')
        with Session.begin() as s:
            locked_control(s)
            p = get_policy(s, platform)
            if p.version != data.version:
                raise HTTPException(409, '配置已变化，请刷新')
            p.options = data.model_dump(exclude={'version', 'enabled'})
            p.enabled, p.version = data.enabled, p.version + 1
            event(s, 'social_config_updated', platform, version=p.version)
        return {'ok': True}

    @app.post('/api/social/channels/{platform}/sync')
    def sync(platform: str, actor=Depends(role)):
        platform_exists(platform)
        if CATALOG[platform]['mode'] != 'feed':
            raise HTTPException(409, '此渠道需要本地执行器或授权内容导入')
        with Session.begin() as s:
            p = get_policy(s, platform)
            if not p.enabled or not settings().source_enabled:
                raise HTTPException(409, '请先启用采集')
            if p.next_allowed_at > now():
                raise HTTPException(409, '本小时已尝试，请等待下一轮')
        from .tasks import sync_social_source
        try:
            task = sync_social_source.delay(platform)
            return {'status': 'scheduled', 'task_id': task.id}
        except Exception:
            raise HTTPException(503, '任务队列不可用') from None

    @app.post('/api/social/channels/{platform}/capture')
    def capture(platform: str, data: Batch, actor=Depends(role)):
        platform_exists(platform)
        if data.mode == 'executor' and CATALOG[platform]['mode'] == 'manual':
            raise HTTPException(409, '该渠道尚无自动适配器，请使用授权内容导入')
        with Session.begin() as s:
            locked_control(s)
            p = get_policy(s, platform)
            if not p.enabled or not settings().source_enabled:
                raise HTTPException(409, '采集已停用')
            if p.version != data.version:
                raise HTTPException(409, '配置已变化，丢弃旧范围的结果并重新读取配置')
            try:
                stats = ingest(s, platform, data.items, p.options, data.mode)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            status = 'imported' if data.mode == 'manual' else 'success'
            s.add(ChannelRun(channel=p.channel, status=status, stats=stats, finished_at=now()))
            return {'status': status, **stats}

    @app.post('/api/social/channels/{platform}/status')
    def report(platform: str, data: ExecutorStatus, actor=Depends(role)):
        platform_exists(platform)
        with Session.begin() as s:
            locked_control(s)
            p = get_policy(s, platform)
            if p.version != data.version or not p.enabled:
                raise HTTPException(409, '配置已变化或采集已停用')
            s.add(ChannelRun(channel=p.channel, status=data.status, error=data.code, finished_at=now()))
        return {'ok': True}
