"""Target-market evidence, not nationality inference. Never use AI translations as evidence."""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
import httpx
from sqlalchemy import select, and_, or_
from .db import Lead, LeadMarket, MarketPolicy, Session, locked_control, event, now
from .contact_discovery import extract_contacts, save_candidate

VERSION = 'domestic-market-ai-v1'
ELIGIBLE = ('likely_cn', 'confirmed_cn')
LABELS = {'likely_cn': 'AI 达到国内目标阈值', 'confirmed_cn': '人工确认国内目标',
          'unknown': '证据不足，市场未知', 'outside_target': '人工排除市场',
          'pending_ai': '待 AI 判断市场', 'below_threshold': 'AI 低于国内目标阈值'}
CHINESE = re.compile(r'[\u4e00-\u9fff]')
KANA = re.compile(r'[\u3040-\u30ff\uac00-\ud7af]')
REGION = r'(?:mainland China|China|Beijing|Shanghai|Shenzhen|Guangzhou|Hangzhou|Chengdu)'
SELF_LOCATION = re.compile(r'\b(?:we|I|our team|our company|our business)\s+(?:are|am|is)\s+(?:based|located)\s+in\s+' + REGION + r'\b', re.I)
CUSTOMER_MARKET = re.compile(r'\b(?:our|my)\s+(?:users|customers|clients|team|company)\s+(?:are|is)\s+(?:based\s+|located\s+)?in\s+' + REGION + r'\b', re.I)


def authored_text(text):
    # Quoted translations, code, links and bilingual issue-template headings are not demand language.
    text = re.sub(r'```.*?```|~~~.*?~~~', '', text, flags=re.S)
    text = re.sub(r'https?://\S+|`[^`]*`', '', text)
    return '\n'.join(line for line in text.splitlines()
                     if not line.lstrip().startswith(('>', '#', '- [', '* [')))


def classify_market(title, text, url):
    original = authored_text(text)
    evidence = []
    for pattern in (SELF_LOCATION, CUSTOMER_MARKET):
        match = pattern.search(original)
        if match:
            evidence.append({'kind': 'stated_market', 'quote': match.group(0), 'url': url})
    # Language is a weak business-market clue, never proof of nationality or residence.
    for part in (title, original):
        han = len(CHINESE.findall(part))
        letters = sum(c.isalpha() for c in part)
        if han >= 12 and han / max(1, letters) >= .30 and not KANA.search(part):
            excerpt = next((line.strip() for line in part.splitlines() if len(CHINESE.findall(line)) >= 6), part.strip())
            evidence.append({'kind': 'chinese_demand_language', 'quote': excerpt[:240], 'url': url})
            break
    status = 'likely_cn' if evidence else 'unknown'
    reason = ('原文有中文交流或自述国内业务线索；仍需核实实际服务地区，不代表国籍或采购意愿。' if evidence
              else '原文暂无足够的国内服务对象证据；英文、昵称、头像及模型生成的中文摘要均不能确定地区或国籍。')
    return {'status': status, 'reason': reason, 'evidence': evidence, 'manual': False,
            'rule_version': VERSION}


def refresh_market(s, lead):
    record = s.get(LeadMarket, lead.id)
    if record and record.manual:
        return record
    digest = hashlib.sha256(json.dumps([lead.title, lead.text, lead.url], ensure_ascii=False).encode()).hexdigest()
    if record and record.fingerprint == digest and record.rule_version == VERSION:
        return record
    if not record:
        record = LeadMarket(lead_id=lead.id, version=0)
        s.add(record)
    data = classify_market(lead.title, lead.text, lead.url)
    for key, value in data.items():
        setattr(record, key, value)
    record.fingerprint, record.updated_at = digest, now()
    record.score, record.assessment_version, record.ai_result = None, '', {}
    record.version += 1
    return record


def threshold(s):
    p = s.get(MarketPolicy, 1)
    return p.threshold if p else 70


def market_state(record, cutoff):
    if not record:
        return 'pending_ai'
    if record.manual:
        return record.status
    if not record.assessment_version:
        return 'pending_ai'
    if record.score is None:
        return 'unknown'
    return 'likely_cn' if record.score >= cutoff else 'below_threshold'


def market_predicate(scope, cutoff):
    manual = LeadMarket.manual.is_(True)
    scored = and_(LeadMarket.manual.is_(False), LeadMarket.score.is_not(None))
    if scope == 'domestic':
        return or_(and_(manual, LeadMarket.status == 'confirmed_cn'), and_(scored, LeadMarket.score >= cutoff))
    if scope == 'below_threshold':
        return and_(scored, LeadMarket.score < cutoff)
    if scope == 'outside_target':
        return and_(manual, LeadMarket.status == 'outside_target')
    return or_(LeadMarket.lead_id.is_(None), and_(manual, LeadMarket.status == 'unknown'),
               and_(LeadMarket.manual.is_(False), LeadMarket.score.is_(None)))


def market_view(s, lead):
    record = s.get(LeadMarket, lead.id)
    cutoff = threshold(s)
    if record:
        data = {key: getattr(record, key) for key in ('status','reason','evidence','manual','version','rule_version','updated_at','score','profile')}
    else:
        # Missing persisted evaluations are kept out of automatic queues until refreshed.
        data = {'status': 'unknown', 'reason': '市场证据尚未整理', 'evidence': [],
                'manual': False, 'version': 0, 'rule_version': VERSION, 'updated_at': '', 'score': None, 'profile': {}}
    data['status'] = market_state(record, cutoff)
    return {**data, 'label': LABELS[data['status']], 'eligible': data['status'] in ELIGIBLE,
            'threshold': cutoff, 'score_note': 'AI 置信分数，非校准概率；不判断国籍'}


def public_profile(lead_id):
    """One bounded public GitHub request per lead/day; no private fields or linked-site fetches."""
    with Session() as s:
        lead = s.get(Lead, lead_id)
        if not lead or not lead.source_key.startswith('github:') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', lead.author):
            return {}
        login = lead.author
        record = s.get(LeadMarket, lead_id)
        cached = record.profile if record else {}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        if cached.get('checked_at', '') > cutoff:
            return cached
    result = {'url': f'https://github.com/{login}', 'checked_at': now(), 'status': 'unavailable'}
    try:
        with httpx.Client(timeout=10, follow_redirects=False, headers={'User-Agent':'HuokePilot/0.3 market-evidence'}) as client:
            with client.stream('GET', f'https://api.github.com/users/{login}') as r:
                r.raise_for_status()
                raw = bytearray()
                for chunk in r.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 200_000:
                        raise ValueError('profile_too_large')
            data = json.loads(raw)
            if data.get('login', '').lower() != login.lower():
                raise ValueError('profile_identity_mismatch')
            result.update(status='public_profile', **{k: str(data.get(k) or '')[:500] for k in ('location','company','bio')})
            if isinstance(data.get('email'), str):
                result['public_email'] = data['email'][:320]
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        pass
    with Session.begin() as s:
        locked_control(s)
        lead = s.get(Lead, lead_id)
        record = refresh_market(s, lead)
        record.profile = result
        if result.get('public_email'):
            save_candidate(s, lead, 'email', result['public_email'], result['url'],
                           'GitHub 作者资料主动公开的邮箱；商务用途和联系许可待所有者核实', 'github_public_profile')
    return result


def apply_ai_market(s, lead, assessment, profile):
    record = refresh_market(s, lead)
    if record.manual:
        return
    assessment = assessment or {'status':'unknown','score':None,'reason':'模型未返回市场判断，保留未知','evidence':[]}
    record.score = assessment['score'] if assessment['status'] == 'assessed' else None
    record.ai_result, record.assessment_version = assessment, VERSION
    record.status = 'ai_assessed'
    record.reason = assessment['reason']
    record.evidence = [{'kind':'ai_market_evidence','quote':quote,
                        'url':lead.url if quote in lead.text else (
                            profile.get('readme', {}).get('url', profile.get('url', lead.url))
                            if quote in profile.get('readme', {}).get('text', '') else profile.get('url', lead.url))}
                       for quote in assessment['evidence']]
    record.version += 1
    record.updated_at = now()


def backfill():
    """Idempotent local-rule backfill; preserves owner decisions, analysis and followups."""
    counts = {status: 0 for status in LABELS}
    last = 0
    while True:
        with Session.begin() as s:
            locked_control(s)
            leads = list(s.scalars(select(Lead).where(Lead.id > last).order_by(Lead.id).limit(200)))
            if not leads:
                break
            cutoff = threshold(s)
            for lead in leads:
                counts[market_state(refresh_market(s, lead), cutoff)] += 1
                extract_contacts(s, lead)
            last = leads[-1].id
    with Session.begin() as s:
        event(s, 'market_backfill', rule_version=VERSION, counts=counts, model_calls=0)
    return counts


if __name__ == '__main__':
    print(json.dumps({'rule_version': VERSION, 'counts': backfill(), 'model_calls': 0}))
