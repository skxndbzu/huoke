import hashlib
import html
import re
from datetime import datetime, timezone, timedelta
import httpx
from sqlalchemy import select
from .db import Session, Lead, SourceItem, ChannelRun, locked_control, event, now
from .config import settings

HN = 'https://hacker-news.firebaseio.com/v0'
RULE_VERSION = 'intent-v1'
TECH = re.compile(r'\b(llm|openai|gpt|claude|gemini|inference|model api|ai api)\b|模型.{0,4}(接口|API)|大模型', re.I)
NEED = re.compile(r'\b(need|looking for|recommend|alternative|provider|cost|pricing|integrat|building|develop|rate limit|latency)\w*|寻找|接入|开发|价格|供应商', re.I)


def clean(text):
    return html.unescape(re.sub(r'<[^>]+>', ' ', text or '')).strip()[:12000]


def fingerprint(text):
    return hashlib.sha256(re.sub(r'\s+', ' ', text).strip().encode()).hexdigest()


def classify(text):
    tech, need = TECH.search(text), NEED.search(text)
    return {
        'method': 'rules', 'version': RULE_VERSION, 'summary': text[:350],
        'fit': 'unknown', 'priority': 'medium' if tech and need else 'low',
        'purchase_stage': 'unknown', 'recency': '请核对原文发布时间',
        'evidence': [m.group(0) for m in (tech, need) if m],
        'unknowns': ['采购意愿', '预算', '企业规模', '是否允许商务联系'],
        'next_action': '人工核实实际需求与公开商务联系方式',
        'contact_permission': 'unknown',
    }


def sync_hn():
    cfg = settings()
    if not cfg.source_enabled:
        return {'status': 'disabled'}
    # A DB lease prevents overlapping API/worker source runs. Expired runs can recover.
    with Session.begin() as s:
        locked_control(s)
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        active = s.scalar(select(ChannelRun).where(ChannelRun.channel == 'hackernews', ChannelRun.status == 'running', ChannelRun.started_at > cutoff))
        if active:
            return {'status': 'already_running'}
        for stale in s.scalars(select(ChannelRun).where(ChannelRun.status == 'running', ChannelRun.started_at <= cutoff)):
            stale.status, stale.error, stale.finished_at = 'interrupted', '运行租约过期，可重新同步', now()
        run = ChannelRun(channel='hackernews')
        s.add(run)
        s.flush()
        run_id = run.id
    stats = {'listed': 0, 'fetched': 0, 'cache_hits': 0, 'filtered': 0, 'duplicates': 0, 'new_leads': 0, 'model_calls': 0}
    try:
        with httpx.Client(timeout=15, follow_redirects=False, headers={'User-Agent': 'HuokePilot/0.1 (bounded official API reader)'}) as client:
            response = client.get(f'{HN}/askstories.json')
            response.raise_for_status()
            ids = response.json()[:cfg.source_batch_size]
            stats['listed'] = len(ids)
            for item_id in ids:
                key = f'hn:{int(item_id)}'
                with Session() as s:
                    if s.get(SourceItem, key):
                        stats['cache_hits'] += 1
                        continue
                response = client.get(f'{HN}/item/{int(item_id)}.json')
                response.raise_for_status()
                item = response.json() or {}
                stats['fetched'] += 1
                text = clean(item.get('title', '') + '\n' + item.get('text', ''))
                digest = fingerprint(text)
                with Session.begin() as s:
                    locked_control(s)
                    if s.get(SourceItem, key):
                        stats['cache_hits'] += 1
                        continue
                    s.add(SourceItem(key=key, fingerprint=digest))
                    if item.get('deleted') or item.get('dead') or not TECH.search(text) or not NEED.search(text):
                        stats['filtered'] += 1
                    elif s.scalar(select(Lead.id).where(Lead.fingerprint == digest)):
                        stats['duplicates'] += 1
                    else:
                        s.add(Lead(source='Hacker News / Ask', source_key=key,
                            url=f'https://news.ycombinator.com/item?id={int(item_id)}',
                            author=item.get('by', '未知'), title=clean(item.get('title')),
                            text=text, fingerprint=digest,
                            published_at=datetime.fromtimestamp(item.get('time', 0), timezone.utc).isoformat(),
                            analysis=classify(text)))
                        stats['new_leads'] += 1
        with Session.begin() as s:
            run = s.get(ChannelRun, run_id)
            run.status, run.stats, run.finished_at = 'success', stats, now()
            event(s, 'source_sync', run_id, **stats)
        return stats
    except Exception as exc:
        with Session.begin() as s:
            run = s.get(ChannelRun, run_id)
            run.status, run.stats, run.error, run.finished_at = 'failed', stats, type(exc).__name__, now()
            event(s, 'source_failure', run_id, error=type(exc).__name__)
        raise RuntimeError('来源同步失败，请查看任务中心（未记录凭据或响应正文）') from None
