"""Bounded public demand discovery. Source content is data, never instructions."""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET
import httpx
from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from .db import (Session, SourcePolicy, ChannelRun, SourceItem, Lead, LeadWorkspace,
                 ContactPoint, AnalysisCall, locked_control, event, now)
from .pipeline import TECH, NEED, clean, fingerprint, classify
from .schemas import Strict
from .config import settings

VERSION = 'discovery-v1'
SOURCES = {
    'github': {'name': 'GitHub Issues', 'queries': ['"API" "供应商"', '"模型" "接口"', '"LLM" "provider"'],
               'docs': 'https://docs.github.com/en/rest/search/search#search-issues-and-pull-requests',
               'scope': '公开 Issue 搜索，每小时最多 3 次搜索、每次 10 条；排除 PR 和机器人，不读取私人资料。'},
    'v2ex': {'name': 'V2EX', 'queries': ['python', 'programmer'],
             'docs': 'https://www.v2ex.com/feed/programmer.xml',
             'scope': '官方节点订阅源，每小时各读取一次；只覆盖订阅源近期帖子，不是全站搜索。'}
}
DEMAND = re.compile(r'供应商|推荐.{0,12}(api|接口)|寻找|接入|收费|计费|稳定|限流|延迟|预算|采购|\b(provider|pricing|cost|rate limit|latency|alternative|integrat\w*|looking for|need)\b', re.I)
NON_DEMAND = re.compile(r'教程|周报|日报|招聘|求职|\b(tutorial|newsletter|hiring|release notes)\b', re.I)
BUSINESS = re.compile(r'商务|合作|业务联系|business|partnership|contact me', re.I)
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')


def policy(s, channel):
    p = s.get(SourcePolicy, channel)
    if not p:
        p = SourcePolicy(channel=channel, options={'queries': SOURCES[channel]['queries'], 'exclude': []})
        s.add(p)
        s.flush()
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


def candidates(client, channel, options):
    items, incomplete = [], False
    if channel == 'github':
        since = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
        for query in options['queries']:
            data = json.loads(read_bounded(client, 'https://api.github.com/search/issues', {
                'q': f'{query} is:issue is:open created:>={since} in:title,body',
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


def extract_contacts(s, lead):
    for m in EMAIL.finditer(lead.text):
        snippet = lead.text[max(0, m.start()-80):m.end()+80]
        address = m.group(0).lower()
        if not BUSINESS.search(snippet) or address.endswith(('.test', '@example.com')) or 'noreply' in address:
            continue
        if not s.scalar(select(ContactPoint.id).where(ContactPoint.lead_id == lead.id, ContactPoint.value == address)):
            s.add(ContactPoint(lead_id=lead.id, kind='email', value=address, source_url=lead.url,
                               evidence=snippet, origin='public_source', verified=False))


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
    stats = {'listed': 0, 'filtered': 0, 'cache_hits': 0, 'duplicates': 0, 'new_leads': 0, 'updated': 0, 'model_calls': 0, 'rule_version': VERSION}
    try:
        with httpx.Client(timeout=15, follow_redirects=False, headers={'User-Agent': 'HuokePilot/0.2 public-demand-reader', 'Accept': 'application/vnd.github+json' if channel == 'github' else 'application/atom+xml'}) as client:
            items, incomplete = candidates(client, channel, options)
        stats['listed'], stats['incomplete_results'] = len(items), incomplete
        for item in items:
            title = clean(item['title'])
            text = clean(title + '\n' + item['text'])
            digest = fingerprint(text)
            cache_digest = fingerprint(digest + VERSION + json.dumps(options, sort_keys=True))
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
                if not TECH.search(text) or not DEMAND.search(text) or NON_DEMAND.search(title) or any(word.lower() in text.lower() for word in options.get('exclude', [])):
                    stats['filtered'] += 1
                    continue
                lead = s.scalar(select(Lead).where(Lead.source_key == item['key']))
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
    from .analysis import analyze, configured
    if not configured():
        return {'status': 'not_configured', 'analyzed': 0}
    with Session() as s:
        # Failed/unknown calls are not retried automatically; their cost needs review.
        ids = list(s.scalars(select(Lead.id).outerjoin(LeadWorkspace, LeadWorkspace.lead_id == Lead.id)
            .where(~Lead.id.in_(select(AnalysisCall.lead_id)), func_coalesce_qualification() != 'invalid')
            .order_by(Lead.id.desc()).limit(2)))
    count = 0
    for lead_id in ids:
        try:
            analyze(lead_id)
            count += 1
        except ValueError:
            break
    return {'analyzed': count}


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
                    'can_send': False, 'can_receive': False, 'can_read_profiles': False})
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
