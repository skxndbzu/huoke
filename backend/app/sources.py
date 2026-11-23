"""Bounded public demand discovery. Source content is data, never instructions."""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET
import httpx
from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, case, or_, func
from .db import (Session, SourcePolicy, ChannelRun, SourceItem, Lead, LeadWorkspace, LeadMarket,
                 GithubIssueState, ContactPoint, AnalysisCall, locked_control, event, now)
from .pipeline import TECH, NEED, clean, fingerprint, classify
from .schemas import Strict
from .config import settings
from .market import refresh_market, market_predicate, threshold, VERSION as MARKET_VERSION
from .contact_discovery import extract_contacts
from .prospecting import intent, supplier_promotion, VERSION_TRIAGE

VERSION = 'discovery-v3-intent'
AUTO_ANALYSIS_HOURLY_LIMIT = 100
AUTO_ANALYSIS_INTERVAL_SECONDS = 36
GITHUB_ISSUE_RECHECK_LIMIT = 5
GITHUB_ISSUE_URL = re.compile(
    r'https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})/issues/([1-9][0-9]{0,15})'
)
OLD_GITHUB_QUERIES = ['"API" "供应商"', '"模型" "接口"', '"LLM" "provider"']
PREVIOUS_GITHUB_QUERIES = ['"API" "供应商"', '"模型" "接口"', '"API" "中国"']
PREVIOUS_FOCUSED_GITHUB_QUERIES = ['"API" "哪家"', '"API" "求助"', '"中转" "API"']
SOURCES = {
    'github': {'name': 'GitHub Issues', 'queries': ['"API" "哪家"', '"API" "盲测"', '"中转" "API"'],
               'docs': 'https://docs.github.com/en/rest/search/search#search-issues-and-pull-requests',
               'scope': '默认搜索近 90 天未关闭 Issue 标题中的选型、盲测和中转问题；自定义词查近 30 天标题及正文。每小时最多 3 次搜索、每次 10 条，需求规则过滤后入库。'},
    'v2ex': {'name': 'V2EX', 'queries': ['python', 'programmer'],
             'docs': 'https://www.v2ex.com/feed/programmer.xml',
             'scope': '官方节点订阅源，每小时各读取一次；只覆盖订阅源近期帖子，不是全站搜索。'}
}
DEMAND = re.compile(r'供应商|推荐.{0,12}(api|接口)|寻找|哪家|求助|接入|收费|计费|稳定|限流|延迟|预算|采购|\b(provider|pricing|cost|rate limit|latency|alternative|integrat\w*|looking for|need)\b', re.I)
NON_DEMAND = re.compile(r'教程|周报|日报|招聘|求职|\b(tutorial|newsletter|hiring|release notes)\b', re.I)
BUSINESS = re.compile(r'商务|合作|业务联系|business|partnership|contact me', re.I)
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')


def policy(s, channel):
    p = s.get(SourcePolicy, channel)
    if not p:
        p = SourcePolicy(channel=channel, options={'queries': SOURCES[channel]['queries'], 'exclude': []})
        s.add(p)
        s.flush()
    elif channel == 'github' and p.options.get('queries') in (
        OLD_GITHUB_QUERIES, PREVIOUS_GITHUB_QUERIES, PREVIOUS_FOCUSED_GITHUB_QUERIES
    ):
        p.options = {**p.options, 'queries': SOURCES[channel]['queries']}
        p.version += 1
        event(s, 'domestic_default_queries', channel, version=p.version)
    return p


def read_bounded(client, url, params=None):
    with client.stream('GET', url, params=params) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > 2_000_000:
                raise ValueError('response_too_large')
            chunks.append(chunk)
        return b''.join(chunks)


def github_issue_state(client, source_key, url):
    target = GITHUB_ISSUE_URL.fullmatch(url)
    issue_id = re.fullmatch(r'github:([0-9]{1,20})', source_key)
    if not target or not issue_id or target[2] in ('.', '..'):
        return 'unavailable', 'unsupported_source'
    owner, repo, number = target.groups()
    data = json.loads(read_bounded(client, f'https://api.github.com/repos/{owner}/{repo}/issues/{number}'))
    if (not isinstance(data, dict) or not isinstance(data.get('id'), int)
            or data.get('number') != int(number) or 'pull_request' in data
            or data.get('state') not in ('open', 'closed')):
        return 'unavailable', 'unexpected_response'
    if data['id'] != int(issue_id[1]):
        return 'mismatch', 'issue_id_mismatch'
    reason = data.get('state_reason')
    return data['state'], reason if reason in ('completed', 'not_planned', 'duplicate', 'reopened') else ''


def recheck_github_issues(client):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with Session() as s:
        targets = s.execute(
            select(Lead.id, Lead.source_key, Lead.url)
            .join(LeadMarket, LeadMarket.lead_id == Lead.id)
            .outerjoin(LeadWorkspace, LeadWorkspace.lead_id == Lead.id)
            .outerjoin(GithubIssueState, GithubIssueState.lead_id == Lead.id)
            .where(Lead.source_key.startswith('github:', autoescape=True),
                   market_predicate('domestic', threshold(s)),
                   func.coalesce(LeadWorkspace.qualification, 'pending') != 'invalid',
                   func.coalesce(LeadWorkspace.stage, 'new').not_in(('closed', 'declined')),
                   or_(GithubIssueState.checked_at.is_(None), GithubIssueState.checked_at < cutoff))
            .order_by(GithubIssueState.checked_at.asc().nulls_first(), Lead.id.desc())
            .limit(GITHUB_ISSUE_RECHECK_LIMIT)
        ).all()
    counts = {'open': 0, 'closed': 0, 'gone': 0, 'mismatch': 0, 'unavailable': 0}
    for lead_id, source_key, url in targets:
        stop = False
        try:
            state, reason = github_issue_state(client, source_key, url)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            state, reason = ('gone' if code == 410 else 'unavailable'), f'HTTP_{code}'
            stop = code in (403, 429)
        except httpx.RequestError as exc:
            state, reason = 'unavailable', type(exc).__name__
            stop = True
        except ValueError:
            state, reason = 'unavailable', 'invalid_response'
        with Session.begin() as s:
            checked = s.get(GithubIssueState, lead_id)
            if not checked:
                checked = GithubIssueState(lead_id=lead_id)
                s.add(checked)
            checked.state, checked.reason, checked.checked_at = state, reason, now()
        counts[state] += 1
        if stop:
            break
    return counts


def candidates(client, channel, options):
    items, incomplete = [], False
    if channel == 'github':
        focused = options['queries'] == SOURCES['github']['queries']
        since = (datetime.now(timezone.utc) - timedelta(days=90 if focused else 30)).date().isoformat()
        scope = 'title' if focused else 'title,body'
        for query in options['queries']:
            data = json.loads(read_bounded(client, 'https://api.github.com/search/issues', {
                'q': f'{query} is:issue is:open created:>={since} in:{scope}',
                'sort': 'created', 'order': 'desc', 'per_page': 10}))
            incomplete |= bool(data.get('incomplete_results'))
            for item in data.get('items', [])[:10]:
                if 'pull_request' in item or item.get('user', {}).get('type') == 'Bot':
                    continue
                url = item.get('html_url', '')
                if not re.fullmatch(r'https://github.com/[^/]+/[^/]+/issues/\d+', url):
                    continue
                items.append({'key': f'github:{int(item["id"])}', 'url': url,
                              'title': item.get('title', ''), 'text': item.get('body') or '',
                              'author': item.get('user', {}).get('login') or '未知',
                              'published_at': item.get('created_at', ''),
                              'updated_at': item.get('updated_at', '')})
    else:
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        for node in options['queries']:
            raw = read_bounded(client, f'https://www.v2ex.com/feed/{node}.xml')
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError('unsafe_xml')
            feed = ET.fromstring(raw)
            for entry in feed.findall('a:entry', ns)[:20]:
                url = next((link.get('href', '') for link in entry.findall('a:link', ns) if link.get('rel', 'alternate') == 'alternate'), '')
                match = re.fullmatch(r'https://(?:www\.)?v2ex.com/t/(\d+)(?:#.*)?', url)
                if not match:
                    continue
                items.append({'key': f'v2ex:{match[1]}', 'url': f'https://www.v2ex.com/t/{match[1]}',
                              'title': entry.findtext('a:title', '', ns),
                              'text': entry.findtext('a:content', '', ns) or entry.findtext('a:summary', '', ns),
                              'author': entry.findtext('a:author/a:name', '未知', ns),
                              'published_at': entry.findtext('a:published', '', ns) or entry.findtext('a:updated', '', ns),
                              'updated_at': entry.findtext('a:updated', '', ns)})
    return items, incomplete


def sync_public(channel):
    if channel not in SOURCES:
        raise ValueError('unknown_source')
    with Session.begin() as s:
        locked_control(s)
        p = policy(s, channel)
        if not settings().source_enabled or not p.enabled:
            return {'status': 'disabled'}
        if p.next_allowed_at > now():
            return {'status': 'cooldown', 'next_allowed_at': p.next_allowed_at}
        p.next_allowed_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        options = p.options
        for r in s.scalars(select(ChannelRun).where(ChannelRun.channel == channel, ChannelRun.status == 'running')):
            r.status, r.finished_at, r.error = 'interrupted', now(), 'previous_run_interrupted'
        run = ChannelRun(channel=channel)
        s.add(run)
        s.flush()
        run_id = run.id
    stats = {'listed': 0, 'filtered': 0, 'cache_hits': 0, 'duplicates': 0, 'new_leads': 0, 'updated': 0, 'model_calls': 0, 'rule_version': VERSION,
             'domestic_candidates': 0, 'market_pending': 0, 'intent_filtered': 0, 'triage_version': VERSION_TRIAGE}
    try:
        with httpx.Client(timeout=15, follow_redirects=False, headers={'User-Agent': 'HuokePilot/0.2 public-demand-reader', 'Accept': 'application/vnd.github+json' if channel == 'github' else 'application/atom+xml'}) as client:
            items, incomplete = candidates(client, channel, options)
            if channel == 'github':
                stats['issue_state_checks'] = recheck_github_issues(client)
        stats['listed'], stats['incomplete_results'] = len(items), incomplete
        for item in items:
            title = clean(item['title'])
            text = clean(title + '\n' + item['text'])
            digest = fingerprint(text)
            cache_digest = fingerprint(digest + VERSION + VERSION_TRIAGE + json.dumps(options, sort_keys=True))
            with Session.begin() as s:
                locked_control(s)
                cached = s.get(SourceItem, item['key'])
                if cached and cached.fingerprint == cache_digest:
                    stats['cache_hits'] += 1
                    continue
                if cached:
                    cached.fingerprint, cached.checked_at = cache_digest, now()
                else:
                    s.add(SourceItem(key=item['key'], fingerprint=cache_digest))
                if supplier_promotion(title, text):
                    stats['filtered'] += 1
                    stats['intent_filtered'] += 1
                    stats['supplier_promotions'] = stats.get('supplier_promotions', 0) + 1
                    continue
                if not TECH.search(text) or not DEMAND.search(text) or NON_DEMAND.search(title) or any(word.lower() in text.lower() for word in options.get('exclude', [])):
                    stats['filtered'] += 1
                    continue
                if intent(title, text)['score'] < 35:
                    stats['filtered'] += 1
                    stats['intent_filtered'] += 1
                    continue
                lead = s.scalar(select(Lead).where(Lead.source_key == item['key']))
                if lead and lead.fingerprint == digest:
                    stats['cache_hits'] += 1
                    continue
                if not lead and s.scalar(select(Lead.id).where((Lead.fingerprint == digest) | (Lead.url == item['url']))):
                    stats['duplicates'] += 1
                    continue
                analysis = classify(text)
                analysis.update(version=VERSION, evidence=[m.group(0) for m in (TECH.search(text), DEMAND.search(text)) if m],
                                recency=item['published_at'] or '未知', source_updated_at=item['updated_at'],
                                next_action='核实需求与公开商务联系证据；技术提问不代表采购意愿')
                if lead:
                    lead.title, lead.text, lead.fingerprint, lead.analysis = title, text, digest, analysis
                    stats['updated'] += 1
                else:
                    lead = Lead(source=SOURCES[channel]['name'], source_key=item['key'], url=item['url'], author=item['author'], title=title,
                                text=text, fingerprint=digest, published_at=item['published_at'], analysis=analysis)
                    s.add(lead)
                    s.flush()
                    stats['new_leads'] += 1
                extract_contacts(s, lead)
                market = refresh_market(s, lead)
                stats['market_pending'] += 1
        status, error = 'success', ''
    except Exception as exc:
        status, error = 'failed', type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            error += ':' + str(exc.response.status_code)
    with Session.begin() as s:
        r = s.get(ChannelRun, run_id)
        r.status, r.error, r.stats, r.finished_at = status, error, stats, now()
        event(s, 'public_source_sync', channel, status=status, error=error, stats=stats)
    return {'status': status, **stats}


def analyze_pending():
    """One short task per tick; a DB lease and rolling-hour quota survive worker restarts."""
    from .analysis import analyze, configured
    from .prospecting import analysis_candidates
    if not configured():
        return {'status': 'not_configured', 'analyzed': 0}
    with Session.begin() as s:
        locked_control(s)
        stamp = now()
        current = datetime.fromisoformat(stamp)
        lease_cutoff = (current - timedelta(minutes=5)).isoformat()
        active = list(s.scalars(select(ChannelRun).where(ChannelRun.channel == 'ai_auto', ChannelRun.status == 'running')))
        if any(r.started_at > lease_cutoff for r in active):
            return {'status': 'already_running', 'analyzed': 0}
        for r in active:
            r.status, r.finished_at, r.error = 'interrupted', stamp, 'previous_run_interrupted'
        recent = list(s.scalars(select(ChannelRun).where(ChannelRun.channel == 'ai_auto',
                            ChannelRun.started_at > (current - timedelta(hours=1)).isoformat())))
        if len(recent) >= AUTO_ANALYSIS_HOURLY_LIMIT:
            return {'status': 'hourly_limit', 'analyzed': 0}
        authors = {(r.stats.get('source'), r.stats.get('author')) for r in recent if r.stats.get('author')}
        ids = analysis_candidates(s, limit=1, excluded_authors=authors)
        if not ids:
            return {'status': 'no_candidates', 'analyzed': 0}
        lead = s.get(Lead, ids[0])
        stats = {'lead_id': lead.id, 'source': lead.source, 'author': lead.author.lower(), 'analyzed': 0,
                 'hourly_limit': AUTO_ANALYSIS_HOURLY_LIMIT}
        run = ChannelRun(channel='ai_auto', started_at=stamp, stats=stats)
        s.add(run);s.flush()
        run_id, lead_id = run.id, lead.id
    status, error = 'success', ''
    try:
        analyze(lead_id, automatic=True)
        stats['analyzed'] = 1
    except ValueError as exc:
        # Existing analysis code retains failed/unknown calls and budget reservations.
        status, error = ('budget_blocked', 'budget_limit') if str(exc) == '预算上限阻止调用' else ('failed', 'analysis_guard_or_validation')
    except Exception as exc:
        status, error = 'failed', type(exc).__name__
    with Session.begin() as s:
        run = s.get(ChannelRun, run_id)
        run.status, run.finished_at, run.error, run.stats = status, now(), error, stats
    return {'status': status, 'analyzed': stats['analyzed'], 'lead_id': lead_id}


def func_coalesce_qualification():
    from sqlalchemy import func
    return func.coalesce(LeadWorkspace.qualification, 'pending')


class PolicyEdit(Strict):
    version: int
    enabled: bool
    queries: list[str] = Field(min_length=1, max_length=3)
    exclude: list[str] = Field(default_factory=list, max_length=20)


def register_sources(app, role):
    @app.get('/api/discovery/sources')
    def sources(actor=Depends(role)):
        with Session.begin() as s:
            locked_control(s)
            output = []
            for channel, spec in SOURCES.items():
                p = policy(s, channel)
                latest = s.scalar(select(ChannelRun).where(ChannelRun.channel == channel).order_by(ChannelRun.id.desc()).limit(1))
                success = s.scalar(select(ChannelRun).where(ChannelRun.channel == channel, ChannelRun.status == 'success').order_by(ChannelRun.id.desc()).limit(1))
                output.append({'channel': channel, **spec, 'enabled': p.enabled, 'queries': p.options['queries'], 'exclude': p.options.get('exclude', []),
                    'version': p.version, 'next_allowed_at': p.next_allowed_at, 'status': latest.status if latest else 'unverified',
                    'last_success': success.finished_at if success else '', 'error': latest.error if latest else '', 'stats': latest.stats if latest else {},
                    'can_send': False, 'can_receive': False, 'can_read_profiles': channel == 'github'})
            return output

    @app.put('/api/discovery/sources/{channel}')
    def edit(channel: str, data: PolicyEdit, actor=Depends(role)):
        if channel not in SOURCES:
            raise HTTPException(404, '来源不存在')
        if any(not q.strip() or len(q) > 150 for q in data.queries + data.exclude):
            raise HTTPException(422, '关键词不能为空且不超过 150 字')
        if channel == 'v2ex' and any(q not in ('python', 'programmer', 'ai') for q in data.queries):
            raise HTTPException(422, '订阅节点仅支持 python、programmer、ai')
        if channel == 'github' and any(':' in q or '\n' in q or '\r' in q for q in data.queries):
            raise HTTPException(422, '只填写关键词，不要包含搜索限定符或换行')
        with Session.begin() as s:
            locked_control(s)
            p = policy(s, channel)
            if data.version != p.version:
                raise HTTPException(409, '配置已更新，请刷新')
            p.enabled, p.options, p.version = data.enabled, {'queries': data.queries, 'exclude': data.exclude}, p.version+1
            event(s, 'source_policy_updated', channel, version=p.version)
            return {'ok': True}

    @app.post('/api/discovery/sources/{channel}/sync')
    def sync(channel: str, actor=Depends(role)):
        if channel not in SOURCES:
            raise HTTPException(404, '来源不存在')
        from .tasks import sync_public_source
        try:
            task = sync_public_source.delay(channel)
            return {'status': 'scheduled', 'task_id': task.id}
        except Exception:
            raise HTTPException(503, '任务队列不可用') from None
