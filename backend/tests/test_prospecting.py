import base64
import json
import httpx
import pytest
from sqlalchemy import select, func
from app.db import Session, Lead, LeadMarket, AnalysisCall, ContactPoint, Message, SourceItem
from app.market import refresh_market, backfill
from app.prospecting import intent, analysis_candidates, supplier_promotion
from app.profile_research import research_profile


@pytest.mark.parametrize('title,text,qualified', [
    ('想请教一下 AI 答题的实际效果，以及用的哪家 API', '速度怎么样，希望评估接入。', True),
    ('Looking for an LLM API provider', 'Need stable inference for our app.', True),
    ('寻找国内稳定的模型 API', '我们在开发客服应用。', True),
    ('[Bug] Adapter 空值防御导致 HTTP 500', 'OpenAI 接口配置为空，修改 null 判断。', False),
    ('feat: Provider configuration', '选择 Ollama 供应商后填入 API key', False),
    ('[开源推荐] 低价模型 API 网关', '求推荐 API 供应商，欢迎使用我们的产品', False),
    ('新增站点建议：超稳 API 中转', '价格便宜，欢迎收录。', False),
    ('【开发者资源推荐】GPT-Rankings：150 家 GPT API 中转站对照表', '按模型 API 价格和延迟整理资源。', False),
    ('【资源投稿】GPT-Rankings：150 家国内 GPT API 中转站对照表', '选型对照表列出哪家 API 的价格最低。', False),
    ('求推荐 API 资源：哪家模型 API 支持按量付费？', '我们正在开发客服。', True),
    ('每日信息流', '寻找稳定的大模型 API', False),
    ('OpenAI settings bug', '```\n寻找稳定的 API 供应商\n```', False),
    ('自定义 API：两个中转站均出现 GPT 正常、Gemini 调用失败', '分别测试两家中转站的 Gemini 接入。', True),
    ('OpenAI API 工具调用失败', '内部工具调用失败，修复参数解析。', False),
    ('中转鸡都是当消耗品用的吗？', '萌新第一台中转鸡求推荐，想找网速快的便宜中转鸡。', False),
    ('中转鸡上转发模型接口', '中转鸡测速正常，但是 Gemini 调用失败，请问哪家 API 可用？', True),
    ('[Benchmark] 设计并实现多家 LLM/视觉 API 盲测', '比较准确率、P95 延迟、单价和每成功任务成本。', True),
    ('多家 LLM API 盲测框架重构', '更换脚本参数，修复测试桩。', False),
    ('【资源推荐】多家 LLM API 盲测', '整理资料，附延迟、单价和成本。', False),
])
def test_triage_recognizes_buyer_questions_without_treating_implementation_as_procurement(title, text, qualified):
    r = intent(title, text)
    assert (r['score'] >= 35) == qualified
    assert all(q in title or q in text for q in r['evidence'])


@pytest.mark.parametrize('title', [
    '建议新增某站国内直连 AI API 中转与拼车服务',
    '补充某站聚合 API 中转服务（Claude 多档报价）',
])
def test_supplier_submission_detected_in_raw_and_stored_issue(title):
    body = '申请收录 **某站**。\nAPI 价格和套餐请见站点说明。'
    for text in (body, title + '\n' + body):
        result = intent(title, text)
        assert result['score'] == 0 and result['category'] == 'supplier_promotion'
    question = '哪家模型 API 适合按量计费？'
    assert not supplier_promotion(question, '我们正在比较模型 API。\n> 申请收录某供应商。')
    assert intent(question, '我们正在比较模型 API。\n> 申请收录某供应商。')['score'] >= 35


def test_focused_source_collects_api_question_and_provider_evaluation(monkeypatch):
    from app.db import SourcePolicy
    from app.sources import PREVIOUS_FOCUSED_GITHUB_QUERIES, sync_public
    from test_discovery import transport
    with Session.begin() as session:
        session.add(SourcePolicy(channel='github', options={'queries': PREVIOUS_FOCUSED_GITHUB_QUERIES, 'exclude': []}))
    queries = []
    def handle(r):
        q = r.url.params['q']
        queries.append(q)
        assert 'in:title' in q and 'in:title,body' not in q
        def item(key,title,body):
            return {'id':key,'html_url':f'https://github.com/test/app/issues/{key}',
                    'title':title,'body':body,'user':{'login':f'test{key}','type':'User'}}
        if '"盲测"' in q:
            return httpx.Response(200, json={'items':[
                item(90, '[Benchmark] 设计并实现多家 LLM/视觉 API 盲测',
                     '定义供应商适配层。比较准确率、P95 延迟、单价和每成功任务成本。')
            ]})
        return httpx.Response(200,json={'items':[
            item(88,'想请教一下 AI 答题的实际效果，以及用的哪家 API','速度怎么样'),
            item(89,'OpenAI adapter null bug','模型 API 接入时缺少 null 检查，需要修复函数'),
            item(93,'建议新增国内直连模型 API 中转','申请收录某站，API 价格见官网。'),
        ]})
    transport(monkeypatch,handle)
    result=sync_public('github')
    assert len(queries) == 3 and sum('"盲测"' in q for q in queries) == 1
    assert all('"求助"' not in q for q in queries)
    assert result['new_leads'] == 2 and result['intent_filtered'] == 2 and result['model_calls'] == 0
    assert result['supplier_promotions'] == 1
    with Session() as s:
        assert s.scalar(select(Lead).where(Lead.source_key=='github:88'))
        assert not s.scalar(select(Lead).where(Lead.source_key=='github:89'))
        assert s.scalar(select(Lead).where(Lead.source_key=='github:90'))
        assert not s.scalar(select(Lead).where(Lead.source_key=='github:93'))


def test_rule_update_rechecks_filtered_issue_and_preserves_existing_analysis(monkeypatch):
    from app.pipeline import clean, fingerprint
    from app.sources import SOURCES, VERSION, sync_public
    from test_discovery import cooldown_off, transport

    options = {'queries': SOURCES['github']['queries'], 'exclude': []}
    rows = [
        {'id': 91, 'title': '自定义 API：两个中转站的 Gemini 调用失败', 'body': '请排查接入问题。'},
        {'id': 92, 'title': '想请教用的哪家大模型 API', 'body': '正在比较模型接口。'},
    ]
    for row in rows:
        row.update(html_url=f'https://github.com/test/app/issues/{row["id"]}',
                   user={'login': 'developer', 'type': 'User'})
    prior_analysis = {'method': 'ai', 'summary': 'Previously evaluated evidence'}
    with Session.begin() as s:
        for row in rows:
            key = f'github:{row["id"]}'
            text = clean(row['title'] + '\n' + row['body'])
            digest = fingerprint(text)
            s.add(SourceItem(key=key, fingerprint=fingerprint(digest + VERSION + json.dumps(options, sort_keys=True))))
            if row['id'] == 92:
                s.add(Lead(source='GitHub Issues', source_key=key, url=row['html_url'],
                           title=row['title'], text=text, fingerprint=digest, analysis=prior_analysis))
    transport(monkeypatch, lambda request: httpx.Response(200, json={'items': rows}))
    first = sync_public('github')
    assert first['new_leads'] == 1 and first['updated'] == 0 and first['model_calls'] == 0
    cooldown_off('github')
    again = sync_public('github')
    assert again['new_leads'] == 0 and again['cache_hits'] == 6
    with Session() as s:
        existing = s.scalar(select(Lead).where(Lead.source_key == 'github:92'))
        assert existing.analysis == prior_analysis


def test_queue_keeps_old_demand_skips_invalid_calls_and_diversifies_authors(client):
    with Session.begin() as s:
        for i, title in enumerate(['寻找稳定的大模型 API 供应商', '寻找稳定的大模型 API 供应商', 'OpenAI adapter null bug']):
            l=Lead(source='GitHub Issues', source_key=f'github:test{i}', author='same-author',
                   title=title,text=title,url=f'https://github.com/test/app/issues/{i+1}', fingerprint=f'p{i}')
            s.add(l);s.flush();refresh_market(s,l)
    backfill()
    with Session() as s:
        ids=analysis_candidates(s)
        assert len(ids)==2 and ids[0]==3 and ids[1]==1
    with Session.begin() as s:
        s.add(AnalysisCall(lead_id=3,cache_key='failed',model='TEST',status='unknown_or_invalid',reserved_usd=.01))
        s.get(LeadMarket,2).manual=True
    with Session() as s:
        assert analysis_candidates(s)==[1]
    d=client.get('/api/workspace/leads?bucket=opportunity&market=all').json()
    assert [l['id'] for l in d['items']]==[1]
    counts=client.get('/api/workspace/metrics').json()['acquisition']
    assert counts['total']==4 and counts['ai_assessed']==0 and counts['domestic']==0


def profile_mock(monkeypatch, status=200, wrong_owner=False):
    with Session.begin() as s:
        l=s.get(Lead,1);l.source_key='github:1';l.author='TestAuthor'
    calls=[]
    def handle(r):
        calls.append(str(r.url))
        if '/users/' in r.url.path:
            return httpx.Response(200,json={'login':'TestAuthor','location':'Shanghai','bio':'AI app developer'})
        assert str(r.url)=='https://api.github.com/repos/TestAuthor/TestAuthor/readme'
        content='Based in Shanghai, building AI apps. Email: public@company.cn\n微信: developer_01\nQQ群：12345678\n<!-- email: hidden@company.cn -->\n```\nEmail: code@company.cn\n```\n[website](http://127.0.0.1/secrets)'
        return httpx.Response(status,json={'encoding':'base64','html_url':f'https://github.com/{"Other" if wrong_owner else "TestAuthor"}/TestAuthor/blob/main/README.md',
                                          'content':base64.b64encode(content.encode()).decode()})
    original=httpx.Client
    monkeypatch.setattr('app.profile_research.httpx.Client',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    return calls


def test_readme_explicit_contacts_cached_with_source_and_no_send(monkeypatch,client):
    calls=profile_mock(monkeypatch)
    first=client.post('/api/workspace/leads/1/public-evidence',json={},headers={'Authorization':'Bearer test-operator'})
    assert first.status_code==200 and first.json()['status']=='public_readme'
    research_profile(1)
    assert len(calls)==2
    with Session() as s:
        points=list(s.scalars(select(ContactPoint)))
        assert {(p.kind,p.value) for p in points}=={('email','public@company.cn'),('wechat','developer_01')}
        assert all(not p.verified and p.origin=='github_profile_readme' and '/blob/main/' in p.source_url for p in points)
        assert s.scalar(select(func.count()).select_from(Message))==0
        assert s.scalar(select(func.count()).select_from(AnalysisCall))==0


@pytest.mark.parametrize('status,wrong_owner',[(404,False),(429,False),(200,True)])
def test_unavailable_or_wrong_owner_readme_does_not_fabricate_contacts_and_backs_off(monkeypatch,status,wrong_owner):
    calls=profile_mock(monkeypatch,status,wrong_owner)
    assert research_profile(1)['readme']['status']=='unavailable'
    research_profile(1)
    assert len(calls)==2
    with Session() as s: assert not list(s.scalars(select(ContactPoint)))


def test_token_free_research_is_bounded_cached_and_does_not_analyze(monkeypatch):
    from app.profile_research import research_pending
    from app.db import ChannelRun
    calls=profile_mock(monkeypatch)
    backfill()
    monkeypatch.setattr('app.analysis.analyze',lambda *a,**kw:pytest.fail('Public research must not invoke AI'))
    result=research_pending()
    assert result['researched']==1 and result['contacts_added']==2 and result['model_calls']==0
    assert research_pending()['status']=='cooldown' and len(calls)==2
    with Session() as s:
        assert s.scalar(select(ChannelRun).where(ChannelRun.channel=='github_profiles')).status=='success'


def test_multiple_demands_from_one_account_do_not_inflate_customer_count(client):
    backfill()
    with Session.begin() as s:
        l=s.get(Lead,1);l.author='SameAuthor'
        m=s.get(LeadMarket,1);m.manual=True;m.status='confirmed_cn'
        other=Lead(source=l.source,source_key='test:second',author='sameauthor',title=l.title,text=l.text,url=l.url,fingerprint='second')
        s.add(other);s.flush()
        m=refresh_market(s,other);m.manual=True;m.status='confirmed_cn'
    a=client.get('/api/workspace/metrics').json()['acquisition']
    assert a['domestic']==2 and a['domestic_accounts']==1
