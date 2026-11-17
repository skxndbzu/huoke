import pytest
from sqlalchemy import select, func
from app.db import Session, Lead, LeadMarket, LeadWorkspace, AnalysisCall, Message, ContactPoint
from app.market import classify_market, refresh_market, backfill, apply_ai_market, VERSION, public_profile
from app.sources import analyze_pending, policy, OLD_GITHUB_QUERIES, SOURCES
from app.analysis import analyze
from test_pipeline import model_config


@pytest.mark.parametrize('title,text,status', [
    ('寻找稳定可靠的大模型接口供应商', '希望服务企业智能客服应用', 'likely_cn'),
    ('Need an OpenAI API provider', 'Our team is based in Shanghai and needs stable inference.', 'likely_cn'),
    ('Need an LLM API', 'Our customers are in mainland China.', 'likely_cn'),
    ('Feature Request: Configurable OpenAI prompt caching for chat and internal LLM calls',
     'Onyx currently relies on implicit prompt caching. Expose per-call cached_tokens.', 'unknown'),
    ('DeepSeek API integration', 'Need help with this Chinese model API', 'unknown'),
    ('OpenAI prompt caching', '````\n我们是国内团队希望寻找模型接口供应商\n````\nNeed a cache option.', 'unknown'),
    ('API caching', '> 我们是国内团队希望寻找模型接口供应商\nNeed a cache option.', 'unknown'),
    ('API feature', '### 请详细描述你的问题和模型供应商接口\nNew model API request.', 'unknown'),
    ('モデル API', '中国語モデルを使用しています。API が必要です。', 'unknown'),
    ('LLM API', 'We are not based in China. Need an API.', 'unknown'),
])
def test_market_uses_business_evidence_not_nationality(title, text, status):
    result = classify_market(title, text, 'https://example.test/issue')
    assert result['status'] == status
    assert result['manual'] is False
    assert all(e['quote'] in title or e['quote'] in text for e in result['evidence'])


def add_cn(s, key='cn-test'):
    lead = Lead(source='GitHub Issues', source_key=key, title='寻找国内稳定的大模型 API 接口供应商',
                text='我们在开发企业知识库应用，希望寻找按量计费的大模型 API 服务。',
                author='EnglishUsername', url='https://github.com/test/app/issues/2', fingerprint=key)
    s.add(lead)
    s.flush()
    refresh_market(s, lead)
    return lead.id


def test_existing_ai_chinese_translation_is_not_market_evidence_and_pagination(client):
    with Session.begin() as s:
        lead = s.get(Lead, 1)
        lead.author = 'dmpanch'
        lead.analysis = {'method': 'ai', 'summary': '原文提出明确的提示缓存控制及用量可观测需求，需要接入大模型接口。'}
        lead_id = add_cn(s)
    counts = backfill()
    assert counts['pending_ai'] == 2
    # Chinese text alone is no longer eligible after the owner requested AI-threshold selection.
    assert client.get('/api/workspace/leads?market=domestic').json()['total'] == 0
    with Session.begin() as s:
        apply_ai_market(s, s.get(Lead,lead_id), {'status':'assessed','score':80,'reason':'Test evidence of domestic business','evidence':['企业知识库应用']}, {})
    assert client.get('/api/workspace/leads?market=domestic&limit=1').json()['items'][0]['id'] == lead_id
    data = client.get('/api/workspace/leads?market=unknown').json()
    assert data['total'] == 1 and data['items'][0]['id'] == 1
    assert data['items'][0]['analysis']['method'] == 'ai'
    assert client.get('/api/workspace/leads?market=all').json()['total'] == 2
    metrics = client.get('/api/workspace/metrics').json()
    assert metrics['counts']['review'] == 1 and metrics['counts']['market'] == 1


def test_market_review_requires_owner_evidence_version_and_survives_backfill(client):
    backfill()
    m = client.get('/api/workspace/leads/1').json()['market']
    data = {'version': m['version'], 'status':'confirmed_cn', 'reason':'企业官网说明其服务国内企业', 'source_url':'https://example.test/company'}
    assert client.put('/api/workspace/leads/1/market', json=data, headers={'Authorization':'Bearer test-operator'}).status_code == 403
    assert client.put('/api/workspace/leads/1/market', json={**data, 'source_url':''}).status_code == 422
    assert client.put('/api/workspace/leads/1/market', json={**data, 'source_url':'javascript:alert(1)'}).status_code == 422
    first = client.put('/api/workspace/leads/1/market', json=data)
    assert first.status_code == 200 and first.json()['eligible']
    assert client.put('/api/workspace/leads/1/market', json=data).status_code == 409
    backfill()
    assert client.get('/api/workspace/leads/1').json()['market']['status'] == 'confirmed_cn'
    changed = client.put('/api/workspace/leads/1/market', json={**data, 'version':first.json()['version'], 'status':'outside_target'})
    assert changed.status_code == 200 and not changed.json()['eligible']
    assert client.get('/api/workspace/leads?market=domestic').json()['total'] == 0
    assert client.get('/api/workspace/leads?market=outside_target').json()['total'] == 1
    reset = client.put('/api/workspace/leads/1/market', json={**data,'version':changed.json()['version'],'status':'auto'})
    assert reset.status_code == 200 and reset.json()['status'] == 'pending_ai' and not reset.json()['manual']
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Message)) == 0


def test_market_review_works_before_backfill(client):
    data = {'version':0,'status':'auto','reason':'重新运行原文市场规则','source_url':''}
    response = client.put('/api/workspace/leads/1/market',json=data)
    assert response.status_code == 200 and response.json()['version'] == 1


def test_auto_ai_prioritizes_domestic_clues_but_can_assess_unknown_and_preserves_owner_decisions(monkeypatch):
    model_config(monkeypatch)
    with Session.begin() as s:
        cn_id = add_cn(s)
    backfill()
    called = []
    original = analyze
    monkeypatch.setattr('app.analysis.analyze',lambda lead_id, automatic=False: called.append((lead_id,automatic)))
    assert analyze_pending()['analyzed'] == 1 and called == [(cn_id,True)]
    assert analyze_pending()['analyzed'] == 1 and called == [(cn_id,True),(1,True)]
    monkeypatch.setattr('app.analysis.public_profile',lambda _: {})
    monkeypatch.setattr('app.analysis.httpx.post',lambda *a,**k:pytest.fail('Manual decision must not consume tokens'))
    with Session.begin() as s:
        s.get(LeadMarket,cn_id).status = 'outside_target'
        s.get(LeadMarket,cn_id).manual = True
    with pytest.raises(ValueError, match='人工市场判断'):
        original(cn_id, automatic=True)
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(AnalysisCall)) == 0


def test_market_filters_do_not_hide_existing_reply_or_followup(client):
    backfill()
    with Session.begin() as s:
        s.add(LeadWorkspace(lead_id=1, qualification='valid', stage='new', unread_reply=True, next_followup='2020-01-01T00:00:00+00:00'))
    metrics = client.get('/api/workspace/metrics').json()['counts']
    assert metrics['contact'] == 0 and metrics['reply'] == 1 and metrics['followup'] == 1
    assert client.get('/api/workspace/leads?market=all&bucket=reply').json()['total'] == 1
    assert client.get('/api/workspace/leads?market=domestic&bucket=contact').json()['total'] == 0


def test_only_unchanged_legacy_search_defaults_are_migrated():
    with Session.begin() as s:
        p = policy(s,'github')
        p.options = {'queries': OLD_GITHUB_QUERIES, 'exclude':['tutorial']}
    with Session.begin() as s:
        p = policy(s,'github')
        assert p.options == {'queries':SOURCES['github']['queries'],'exclude':['tutorial']}
        p.options = {'queries':['custom demand words'],'exclude':[]}
    with Session.begin() as s:
        assert policy(s,'github').options['queries'] == ['custom demand words']


def test_threshold_boundary_unknown_exclusion_and_no_new_model_calls(client):
    backfill()
    with Session.begin() as s:
        lead_id = add_cn(s)
        apply_ai_market(s,s.get(Lead,lead_id),{'status':'assessed','score':70,'reason':'Test domestic market evidence','evidence':['企业知识库应用']},{})
    assert client.get('/api/workspace/leads?market=domestic').json()['total'] == 1
    assert client.put('/api/workspace/market-policy',json={'version':0,'threshold':71},headers={'Authorization':'Bearer test-operator'}).status_code == 403
    p = client.put('/api/workspace/market-policy',json={'version':0,'threshold':71}).json()
    assert client.get('/api/workspace/leads?market=domestic').json()['total'] == 0
    assert client.get('/api/workspace/leads?market=below_threshold').json()['total'] == 1
    assert client.put('/api/workspace/market-policy',json={'version':0,'threshold':70}).status_code == 409
    assert client.put('/api/workspace/market-policy',json={'version':p['version'],'threshold':101}).status_code == 422
    assert client.put('/api/workspace/market-policy',json={'version':p['version'],'threshold':0}).status_code == 200
    assert client.get('/api/workspace/leads?market=domestic').json()['total'] == 1
    assert client.get('/api/workspace/leads?market=unknown').json()['total'] == 1
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(AnalysisCall)) == 0


@pytest.mark.parametrize('bad_quote',[False,True])
def test_ai_score_is_evidence_checked_cached_and_not_retried_when_invalid(monkeypatch,client,bad_quote):
    import json,httpx
    model_config(monkeypatch)
    result={'summary':'Explicit API search','fit':'medium','priority':'medium','purchase_stage':'unknown',
            'recency':'unknown','evidence':['Looking for an LLM API provider'],'unknowns':['budget'],'next_action':'Check market',
            'market':{'status':'assessed','score':42,'reason':'Test only','evidence':['invented quote' if bad_quote else 'Looking for an LLM API provider']}}
    calls=[]
    def respond(*args,**kwargs):
        calls.append(kwargs['json'])
        return httpx.Response(200,request=httpx.Request('POST','https://example.test/v1/chat/completions'),json={
            'choices':[{'message':{'content':json.dumps(result)},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':100,'completion_tokens':50}})
    monkeypatch.setattr('app.analysis.httpx.post',respond)
    if bad_quote:
        with pytest.raises(ValueError):analyze(1)
        with pytest.raises(ValueError):analyze(1)
        assert client.get('/api/workspace/leads?market=domestic').json()['total']==0
    else:
        assert not analyze(1)['cached']
        assert analyze(1)['cached']
        assert client.get('/api/workspace/leads/1').json()['market']['score']==42
        assert analyze_pending()['analyzed']==0
    assert len(calls)==1


def test_public_profile_fixed_endpoint_limited_fields_and_cache(monkeypatch):
    import httpx
    with Session.begin() as s:
        l=s.get(Lead,1);l.source_key='github:1';l.author='dmpanch'
    calls=[]
    def handle(r):
        calls.append(str(r.url))
        return httpx.Response(200,json={'login':'dmpanch','location':None,'company':'BAS-IP','bio':None,'email':'public@company.cn'})
    real=httpx.Client
    monkeypatch.setattr('app.market.httpx.Client',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    p=public_profile(1)
    assert p['location']=='' and p['company']=='BAS-IP' and 'email' not in p
    assert public_profile(1)==p and calls==['https://api.github.com/users/dmpanch']
    with Session() as s:
        point=s.scalar(select(ContactPoint).where(ContactPoint.lead_id==1))
        assert point.value=='public@company.cn' and not point.verified and point.source_url=='https://github.com/dmpanch'


def test_public_social_accounts_extracted_with_evidence_not_guessed():
    from app.contact_discovery import extract_contacts
    with Session.begin() as s:
        l=s.get(Lead,1)
        l.text='商务合作：微信: dev_api01，QQ：12345678，联系邮箱 biz@company.cn。QQ群：87654321，微信群请看链接。不披露其他资料。'
        assert extract_contacts(s,l)==3
        assert extract_contacts(s,l)==0
    with Session() as s:
        points=list(s.scalars(select(ContactPoint)))
        assert {(p.kind,p.value) for p in points}=={('wechat','dev_api01'),('qq','12345678'),('email','biz@company.cn')}
        assert all(not p.verified and p.source_url=='https://example.test/post' and p.evidence for p in points)
        assert s.scalar(select(func.count()).select_from(Message))==0


def test_contact_priority_preserves_high_value_without_contacts(client):
    backfill()
    with Session.begin() as s:
        s.get(LeadMarket,1).manual=True
        s.get(LeadMarket,1).status='confirmed_cn'
        ids=[add_cn(s,f'contact-priority-{i}') for i in range(3)]
        for lead_id in ids:
            m=s.get(LeadMarket,lead_id);m.manual=True;m.status='confirmed_cn'
        s.add(ContactPoint(lead_id=ids[0],kind='wechat',value='published_test',source_url='https://example.test/business',evidence='Published WeChat for business',verified=False))
        s.get(Lead,ids[1]).analysis={'method':'ai','fit':'high','priority':'high','evidence':['Explicit test need']}
    data=client.get('/api/workspace/leads?market=domestic').json()
    assert [l['id'] for l in data['items']]==[1,*ids]
    high=client.get('/api/workspace/leads?market=domestic&bucket=high_value').json()
    assert high['total']==1 and high['items'][0]['id']==ids[1] and high['items'][0]['high_value_no_contact']
    assert client.get('/api/workspace/metrics').json()['counts']['high_value']==1
