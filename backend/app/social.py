"""Multi-platform acquisition: bounded reading/import only; no outbox mutation."""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import and_, or_, select
import httpx
from .schemas import Strict
from .db import (Session, SourcePolicy, SourceItem, ChannelRun, Lead, LeadMarket, CaptureEvidence, Event,
                 locked_control, now, event)
from .pipeline import clean, classify, fingerprint
from .prospecting import intent
from .market import refresh_market
from .contact_discovery import extract_contacts, extract_contact_text, save_candidate
from .social_adapters import (CATALOG, HOSTS, Capture, V2EXProfile, V2EX_USERNAME,
                              feed_items, gitee_items, gitee_issue_url, validate_url)
from .config import settings

VERSION = 'social-capture-v1'
DEFAULT_QUERIES = ['API', '模型', '中转']
GITEE_QUERIES = ['大模型 API', '中转站', 'DeepSeek API']
SERVER_MODES = {'feed', 'search'}
FILTER_REVIEW_ORDER = ('no_demand_evidence', 'no_keyword', 'excluded_keyword', 'older_than_90_days')
FILTER_SAMPLE_LIMIT = 5
SCREENING_REVIEW_EVENT = 'screening_sample_reviewed'


def get_policy(s, platform):
    p = s.get(SourcePolicy, 'social:' + platform)
    if not p:
        p = SourcePolicy(channel='social:' + platform, enabled=False, options={
            'queries': GITEE_QUERIES if platform == 'gitee' else DEFAULT_QUERIES,
            'exclude': [], 'account': '', 'conversations': []})
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


class V2EXProfileUpload(V2EXProfile):
    version: int = Field(ge=1)


class ScreeningReview(Strict):
    decision: Literal['has_demand', 'reasonable', 'unreviewed']


def lead_has_v2ex_author(lead):
    return (lead and re.fullmatch(r'v2ex:\d+', lead.source_key)
            and re.fullmatch(V2EX_USERNAME, lead.author or ''))


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
        if platform == 'gitee':
            gitee_issue_url(item.url)
        if item.account or item.conversation or item.identity_verified:
            raise ValueError('公开内容不能声明登录账号或已核实联系人')
    if item.published_at and datetime.fromisoformat(item.published_at) > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError('原文发布时间不能在未来')


def ingest(s, platform, items, options, provenance):
    stats = {'listed': len(items), 'filtered': 0, 'cache_hits': 0, 'new_leads': 0, 'updated': 0,
             'model_calls': 0, 'capture_mode': provenance, 'rule_version': VERSION,
             'filter_reasons': {}, 'filtered_samples': []}
    queries = [word.casefold().split() if platform == 'gitee' else [word.casefold()] for word in options['queries']]
    excluded = [word.casefold() for word in options['exclude']]
    samples = {reason: [] for reason in FILTER_REVIEW_ORDER}
    for item in items:
        validate_capture(platform, item, options)
    for item in items:
        title = clean(item.title or item.text.split('\n')[0])[:250]
        text = clean(item.text)
        url = gitee_issue_url(item.url) if platform == 'gitee' else (
            validate_url(platform, item.url) if platform == 'github_discussions' else item.url)
        event_id = fingerprint(url) if platform in {'gitee', 'github_discussions'} or provenance == 'manual' else item.event_id
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
        content = (title + '\n' + text).casefold()
        reason = ('older_than_90_days' if item.published_at and datetime.fromisoformat(item.published_at) < datetime.now(timezone.utc) - timedelta(days=90)
                  else 'excluded_keyword' if any(word in content for word in excluded)
                  else 'no_keyword' if not any(all(word in content for word in query) for query in queries)
                  else 'no_demand_evidence' if intent(title, text)['score'] < 35 else '')
        if reason:
            stats['filtered'] += 1
            stats['filter_reasons'][reason] = stats['filter_reasons'].get(reason, 0) + 1
            if url and len(samples[reason]) < FILTER_SAMPLE_LIMIT:
                samples[reason].append({'title': title[:160], 'url': url, 'reason': reason})
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
                        url=url, author=author[:200], title=title, text=text, fingerprint=digest,
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
    stats['filtered_samples'] = [
        group[index] for index in range(FILTER_SAMPLE_LIMIT)
        for group in samples.values() if index < len(group)
    ][:FILTER_SAMPLE_LIMIT]
    return stats


def status_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return ('verification_required' if code in (401, 403, 429) else 'failed'), 'HTTP_' + str(code)
    return 'failed', type(exc).__name__


def screening_key(channel, url):
    return hashlib.sha256((channel + '\n' + url.split('#', 1)[0].rstrip('/')).encode()).hexdigest()


def screening_batch(s, runs, recent):
    sample_run = recent
    if not sample_run or not sample_run.stats.get('filtered_samples'):
        sample_run = s.scalar(runs.where(ChannelRun.status.in_(('success', 'imported')),
                                         ChannelRun.stats['filtered_samples'][0]['reason'].as_string().is_not(None))
                              .order_by(ChannelRun.id.desc()).limit(1))
    if not sample_run:
        return None
    samples = sample_run.stats['filtered_samples']
    keys = [screening_key(sample_run.channel, sample['url']) if sample.get('url') else '' for sample in samples]
    reviews = {}
    if any(keys):
        for record in s.scalars(select(Event).where(Event.kind == SCREENING_REVIEW_EVENT, Event.entity.in_(keys))
                                .order_by(Event.id.desc())):
            reviews.setdefault(record.entity, record.detail['decision'])
    return {'id': sample_run.id, 'finished_at': sample_run.finished_at, 'mode': sample_run.stats.get('capture_mode', ''),
            'reasons': sample_run.stats.get('filter_reasons', {}),
            'samples': [{**sample, 'decision': reviews.get(key, 'unreviewed')} for sample, key in zip(samples, keys)]}


def feed_route(s, platform, mode, checked_at):
    # V2EX server collection predates the shared social connector.
    if platform == 'v2ex' and mode == 'server_feed':
        runs = select(ChannelRun).where(ChannelRun.channel == 'v2ex')
    else:
        stored_mode = ChannelRun.stats['capture_mode'].as_string()
        legacy_status = ('failed', 'running', 'interrupted', 'config_changed') if mode == 'server_feed' else ('unavailable', 'login_required')
        runs = select(ChannelRun).where(ChannelRun.channel == 'social:' + platform, or_(
            stored_mode == mode, and_(stored_mode.is_(None), ChannelRun.status.in_(legacy_status))))
    latest = s.scalar(runs.order_by(ChannelRun.id.desc()).limit(1))
    good = latest if latest and latest.status == 'success' else s.scalar(
        runs.where(ChannelRun.status == 'success').order_by(ChannelRun.id.desc()).limit(1))
    last_success = good.finished_at if good else ''
    stale = bool(last_success and checked_at - datetime.fromisoformat(last_success) > timedelta(hours=2))
    return {'mode': mode, 'status': latest.status if latest else 'not_connected',
            'last_attempt': latest.started_at if latest else '', 'last_success': last_success,
            'error': latest.error if latest else '', 'stats': good.stats if good else {}, 'stale': stale,
            'screening_batch': screening_batch(s, runs, good)}


def sync_social(platform):
    if platform not in CATALOG or CATALOG[platform]['mode'] not in SERVER_MODES:
        return {'status': 'executor_required'}
    provenance = 'server_search' if platform == 'gitee' else 'server_feed'
    with Session.begin() as s:
        locked_control(s)
        p = get_policy(s, platform)
        if not settings().source_enabled or not p.enabled:
            return {'status': 'disabled'}
        if p.next_allowed_at > now():
            return {'status': 'cooldown', 'next_allowed_at': p.next_allowed_at}
        p.next_allowed_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        for old in s.scalars(select(ChannelRun).where(ChannelRun.channel == p.channel, ChannelRun.status == 'running')):
            old.status, old.finished_at, old.error = 'interrupted', now(), 'previous_run_interrupted'
        run = ChannelRun(channel=p.channel, stats={'capture_mode': provenance})
        s.add(run)
        s.flush()
        run_id, version, queries = run.id, p.version, p.options['queries']
    try:
        with httpx.Client(timeout=20, follow_redirects=False, headers={'User-Agent': 'HuokePilot/0.3 bounded-public-source'}) as client:
            items = gitee_items(client, queries) if platform == 'gitee' else feed_items(platform, client)
        with Session.begin() as s:
            locked_control(s)
            p = get_policy(s, platform)
            if p.version != version or not p.enabled:
                result = {'status': 'config_changed', 'capture_mode': provenance}
            else:
                result = {'status': 'success', **ingest(s, platform, items, p.options, provenance)}
            r = s.get(ChannelRun, run_id)
            r.status, r.stats, r.finished_at = result['status'], result, now()
        return result
    except Exception as exc:
        status, error = status_error(exc)
        with Session.begin() as s:
            r = s.get(ChannelRun, run_id)
            r.status, r.error, r.finished_at = status, error, now()
        return {'status': status, 'error': error}


def register_social(app, role, reviewer):
    def platform_exists(platform):
        if platform not in CATALOG:
            raise HTTPException(404, '渠道不存在')

    @app.put('/api/social/screening/{run_id}/{sample_index}')
    def review_screening(run_id: int, sample_index: int, data: ScreeningReview, actor=Depends(reviewer)):
        with Session.begin() as s:
            locked_control(s)
            run = s.get(ChannelRun, run_id)
            if not run or run.channel.removeprefix('social:') not in HOSTS:
                raise HTTPException(404, '公开筛除样本不存在')
            samples = run.stats.get('filtered_samples', [])
            if not 0 <= sample_index < len(samples) or not samples[sample_index].get('url'):
                raise HTTPException(404, '公开筛除样本不存在')
            sample = samples[sample_index]
            key = screening_key(run.channel, sample['url'])
            latest = s.scalar(select(Event).where(Event.kind == SCREENING_REVIEW_EVENT, Event.entity == key)
                              .order_by(Event.id.desc()).limit(1))
            current = latest.detail['decision'] if latest else 'unreviewed'
            if current != data.decision:
                event(s, SCREENING_REVIEW_EVENT, key, run_id=run_id, sample_index=sample_index,
                      url=sample['url'], channel=run.channel, reason=sample['reason'], decision=data.decision, actor=actor)
            return {'decision': data.decision}

    @app.get('/api/social/v2ex/profiles')
    def pending_v2ex_profiles(actor=Depends(role)):
        checked_at = datetime.now(timezone.utc)
        day_ago = (checked_at - timedelta(days=1)).isoformat()
        recent = (checked_at - timedelta(days=90)).isoformat()
        with Session() as s:
            policy = s.get(SourcePolicy, 'social:v2ex')
            if not policy or not policy.enabled or not settings().source_enabled:
                return {'items': []}
            rows = s.execute(select(Lead, LeadMarket.profile).outerjoin(LeadMarket, LeadMarket.lead_id == Lead.id)
                             .where(Lead.source_key.startswith('v2ex:'), Lead.published_at >= recent)
                             .order_by(Lead.id.desc()).limit(120)).all()
            seen = {lead.author.lower() for lead, profile in rows if (profile or {}).get('checked_at', '') > day_ago}
            pending = []
            for lead, _ in rows:
                if not lead_has_v2ex_author(lead) or lead.author.lower() in seen or intent(lead.title, lead.text)['score'] < 35:
                    continue
                seen.add(lead.author.lower())
                pending.append({'id': lead.id, 'username': lead.author})
                if len(pending) == 3:
                    break
            return {'version': policy.version, 'items': pending}

    @app.post('/api/social/v2ex/profiles/{lead_id}')
    def upload_v2ex_profile(lead_id: int, data: V2EXProfileUpload, actor=Depends(role)):
        with Session.begin() as s:
            locked_control(s)
            policy = s.get(SourcePolicy, 'social:v2ex')
            if not policy or not policy.enabled or not settings().source_enabled or policy.version != data.version:
                raise HTTPException(409, 'V2EX 采集范围已变化或停用')
            lead = s.get(Lead, lead_id)
            if not lead_has_v2ex_author(lead) or lead.author.lower() != data.username.lower():
                raise HTTPException(422, '资料与线索作者不匹配')
            record = refresh_market(s, lead)
            day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            if (record.profile or {}).get('checked_at', '') > day_ago:
                return {'status': 'cached', 'contacts_added': 0}
            url = f'https://www.v2ex.com/member/{lead.author}'
            profile = {'url': url, 'status': data.status, 'checked_at': now()}
            contacts_added = 0
            if data.status == 'public_profile':
                profile.update(location=clean(data.location), github=data.github)
                contacts_added = extract_contact_text(s, lead, clean(data.bio), url, 'v2ex_public_profile', public_profile=True)
            elif record.profile and record.profile.get('status') == 'public_profile':
                profile = {**record.profile, 'checked_at': now(), 'refresh_status': 'unavailable'}
            record.profile = profile
            return {'status': data.status, 'contacts_added': contacts_added}

    @app.get('/api/social/channels')
    def channels(actor=Depends(role)):
        checked_at = datetime.now(timezone.utc)
        with Session.begin() as s:
            locked_control(s)
            result = []
            for platform, spec in CATALOG.items():
                p = get_policy(s, platform)
                runs = select(ChannelRun).where(ChannelRun.channel == p.channel)
                latest = s.scalar(runs.order_by(ChannelRun.id.desc()).limit(1))
                good = s.scalar(runs.where(ChannelRun.status == 'success').order_by(ChannelRun.id.desc()).limit(1))
                routes = [feed_route(s, platform, mode, checked_at) for mode in ('server_feed', 'executor')] if platform in {'v2ex', 'nodeseek', 'linuxdo'} else []
                # Keep samples available after subsequent empty or all-cache runs.
                samples = None
                if platform not in {'qq', 'wechat'} and (not routes or (latest and latest.status == 'imported')):
                    sample_runs = runs.where(ChannelRun.status == 'imported') if routes else runs
                    samples = screening_batch(s, sample_runs, latest)
                result.append({'platform': platform, **spec, 'version': p.version, 'enabled': p.enabled, **p.options,
                               'next_allowed_at': p.next_allowed_at, 'status': latest.status if latest else 'not_connected',
                               'last_success': good.finished_at if good else '', 'last_attempt': latest.started_at if latest else '', 'routes': routes,
                               'error': latest.error if latest else '', 'stats': latest.stats if latest else {},
                               'screening_batch': samples, 'can_send': False})
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
        if CATALOG[platform]['mode'] not in SERVER_MODES:
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
        if data.mode == 'executor' and CATALOG[platform]['mode'] in {'manual', 'search'}:
            raise HTTPException(409, '该渠道不接收本地执行器上传，请使用页面提供的采集方式')
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
            s.add(ChannelRun(channel=p.channel, status=data.status, error=data.code, finished_at=now(),
                             stats={'capture_mode': 'executor'}))
        return {'ok': True}
