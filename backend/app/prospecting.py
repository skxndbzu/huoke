"""Deterministic triage before paid analysis; scores order research, never qualify leads."""
import re
from sqlalchemy import select, func
from .db import Lead, LeadMarket, LeadWorkspace, AnalysisCall, ContactPoint
from .market import VERSION

VERSION_TRIAGE = 'intent-triage-v1'
MODEL = re.compile(r'\b(?:llm|openai|gpt|claude|gemini|deepseek|model api)\b|大模型|模型.{0,8}(?:API|接口)|中转|AI\s*(?:答题|应用|客服)', re.I)
ASK = re.compile(r'(?:求推荐|求购|寻找|想找|找个|采购|有没有|哪家).{0,30}(?:API|接口|供应商|服务商|中转)|(?:API|接口|供应商|中转).{0,25}(?:求推荐|哪家|太贵|不稳定)|\b(?:looking for|recommend|need)\s+(?:(?:a|an|the|reliable|cheaper|stable|LLM|model|inference|OpenAI)\s+){0,4}(?:API|provider)\b', re.I)
PAIN = re.compile(r'(?:API|接口|中转|供应商|服务商).{0,35}(?:超时|限流|不稳定|价格|成本|计费|充值|余额|不可用)|(?:超时|限流|不稳定|太贵).{0,25}(?:API|接口|中转)|\b(?:api|provider)\b.{0,35}\b(?:cost|pricing|rate limit|latency|unreliable)\b', re.I)
BUILD = re.compile(r'(?:正在|我们|我想|计划).{0,25}(?:开发|搭建|做).{0,35}(?:应用|客服|知识库|agent)|\b(?:building|developing)\b.{0,35}\b(?:app|agent|chatbot)\b', re.I)
NOISE = re.compile(r'教程|周报|日报|资讯|信息流|推荐新工具|开源推荐|新增推荐|新增站点|站点推荐|收录|自荐|推广|\b(?:newsletter|digest|tutorial|research / reuse|refactor)\b|(?:spec|PRD)[:：\]]|规格', re.I)
RESOURCE_POST = re.compile(r'^\s*[【\[]\s*(?:开发者)?资源(?:推荐|投稿)\s*[】\]]')


def intent(title, text):
    # Large logs, quoted examples and checklists must not dominate a short actual request.
    body = re.sub(r'```.*?```|~~~.*?~~~', '', text[:12000], flags=re.S)
    body = '\n'.join(x for x in body.splitlines() if not x.lstrip().startswith(('>', '- [', '* [')))
    if RESOURCE_POST.search(title) or NOISE.search(title) or not MODEL.search(title + '\n' + body):
        return {'score': 0, 'reason': '资讯、推广或缺少模型需求，保留记录供人工查看', 'evidence': []}
    evidence, score, reason = [], 0, '仅有技术讨论，暂不优先消耗模型额度'
    for pattern, weight, label in ((ASK, 70, '有接口选型或求助表述'), (PAIN, 45, '有模型服务使用问题'), (BUILD, 35, '有应用开发场景')):
        match = pattern.search(title) or pattern.search(body)
        if match and weight > score:
            score, reason, evidence = weight, label, [match.group(0)]
            if pattern.search(title):
                score += 10
    return {'score': score, 'reason': reason, 'evidence': evidence}


def pending_ranked(s):
    """Old high-intent demand can outrank new implementation tickets; never delete either."""
    stmt = (select(Lead, LeadMarket).outerjoin(LeadMarket, LeadMarket.lead_id == Lead.id)
            .outerjoin(LeadWorkspace, LeadWorkspace.lead_id == Lead.id)
            .where(func.coalesce(LeadWorkspace.qualification, 'pending') != 'invalid',
                   func.coalesce(LeadWorkspace.stage, 'new').not_in(['closed', 'declined']),
                   func.coalesce(LeadMarket.manual, False).is_(False),
                   func.coalesce(LeadMarket.assessment_version, '') != VERSION,
                   ~Lead.id.in_(select(AnalysisCall.lead_id).where(AnalysisCall.status != 'success'))))
    contacts = set(s.scalars(select(ContactPoint.lead_id).where(ContactPoint.kind.in_(['email', 'wechat', 'qq']))))
    rows = []
    for lead, market in s.execute(stmt):
        triage = intent(lead.title, lead.text)
        if triage['score'] < 35:
            continue
        domestic_clue = bool(market and market.status == 'likely_cn')
        rows.append({'id': lead.id, 'author': lead.author, 'source': lead.source,
                     'title': lead.title, 'has_contact': lead.id in contacts,
                     'domestic_clue': domestic_clue, **triage})
    return sorted(rows, key=lambda r: (r['score'] + 20 * r['domestic_clue'], r['has_contact'], r['id']), reverse=True)


def analysis_candidates(s, limit=2, excluded_authors=()):
    # Avoid spending this round on several issues from the same public account.
    chosen, authors = [], set(excluded_authors)
    for item in pending_ranked(s):
        identity = (item['source'], item['author'].lower())
        if identity in authors:
            continue
        authors.add(identity)
        chosen.append(item['id'])
        if len(chosen) >= limit:
            break
    return chosen
