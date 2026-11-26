import httpx
from app.pipeline import hn_demand_evidence, sync_hn
from app.db import Session, Lead, AnalysisCall
from sqlalchemy import select
from app.analysis import analyze
from app.config import settings
import pytest
import json
from app.db import Event


def test_source_filters_and_second_run_reuses_cache(monkeypatch):
    real_client=httpx.Client
    requests=[]
    def handle(request):
        requests.append(request.url.path)
        if request.url.path.endswith('askstories.json'):return httpx.Response(200,json=[100,101,102])
        if request.url.path.endswith('100.json'):return httpx.Response(200,json={'id':100,'title':'Looking for LLM API provider','text':'Need help with model API pricing','by':'real-source-test','time':1700000000})
        if request.url.path.endswith('102.json'):
            return httpx.Response(200, json={'id': 102, 'title': 'Ask HN: AI hype is cooling down',
                                             'text': 'OpenAI was a big topic. I studied CS and develop software.'})
        return httpx.Response(200,json={'id':101,'title':'My garden','time':1700000000})
    monkeypatch.setattr('app.pipeline.httpx.Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handle),**kwargs))
    first=sync_hn();second=sync_hn()
    assert first['new_leads'] == 1 and first['filtered'] == 2 and first['model_calls'] == 0
    assert first['filter_reasons'] == {'no_api_demand': 2}
    assert first['filtered_samples'][-1]['url'] == 'https://news.ycombinator.com/item?id=102'
    assert second['cache_hits'] == 3 and second['fetched'] == 0 and len(requests) == 5
    with Session() as s:
        lead=s.scalar(select(Lead).where(Lead.source_key=='hn:100'))
        assert lead.analysis['contact_permission']=='unknown'
        assert 'LLM API' in lead.analysis['evidence']


@pytest.mark.parametrize('title,body,expected', [
    ('What are some economical ways to access LLMs?',
     'Want to hear about your experiences in terms of cost from various providers of LLM APIs.', True),
    ("Claude Code's API billing depends on how often the cache expires", '', True),
    ('What is going on regarding quality of service for API access to LLMs?', '', True),
    ('How do you find out if the LLM API is giving degraded responses?', '', True),
    ('Inception’s Diffusion LLM API is shutting down', '<pre>We need to switch to an LLM API</pre>', False),
    ('General chat about developer tools', '&gt; Looking for an LLM API provider\nI am quoting another post.', False),
])
def test_hn_demand_requires_local_service_evidence(title, body, expected):
    assert bool(hn_demand_evidence(title, body)) is expected


def model_config(monkeypatch):
    for k,v in {'llm_base_url':'https://example.test/v1','llm_api_key':'test','llm_model':'test-model','llm_input_per_million':1,'llm_output_per_million':2}.items():monkeypatch.setattr(settings(),k,v)


def test_model_missing_config_does_not_call(monkeypatch):
    monkeypatch.setattr('app.analysis.httpx.post',lambda *a,**k:pytest.fail('Unexpected external call'))
    with pytest.raises(ValueError,match='未配置'):analyze(1)


def test_budget_blocks_before_request(monkeypatch):
    model_config(monkeypatch);monkeypatch.setattr(settings(),'ai_round_budget',.000001)
    monkeypatch.setattr('app.analysis.httpx.post',lambda *a,**k:pytest.fail('Budget exceeded external call'))
    with pytest.raises(ValueError,match='预算'):analyze(1)


def test_unknown_usage_keeps_reservation_and_prevents_recall(monkeypatch):
    model_config(monkeypatch)
    calls=[]
    def fail(*a,**kw):calls.append(1);raise httpx.ReadTimeout('timeout')
    monkeypatch.setattr('app.analysis.httpx.post',fail)
    with pytest.raises(ValueError):analyze(1)
    with pytest.raises(ValueError):analyze(1)
    assert len(calls)==1
    with Session() as s:
        call=s.scalar(select(AnalysisCall));assert call.estimated_usd is None and call.reserved_usd>0


@pytest.mark.parametrize('invalid', [False, True])
def test_structured_analysis_usage_cache_and_validation_diagnostics(monkeypatch, invalid):
    model_config(monkeypatch)
    calls = []
    result = {'summary': 'Explicit API search', 'fit': 'high', 'priority': 'high',
              'purchase_stage': 'explicit_need', 'recency': 'unknown',
              'evidence': ['Looking for an LLM API provider'], 'unknowns': ['budget'],
              'next_action': 'Review source'}
    if invalid:
        result['recency'] = {'unexpected': 'do-not-log-source-content'}
    def respond(*args, **kwargs):
        calls.append(kwargs['json'])
        return httpx.Response(200, request=httpx.Request('POST', 'https://example.test/v1/chat/completions'),
            json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result)}}],
                  'usage': {'prompt_tokens': 100, 'completion_tokens': 50}})
    monkeypatch.setattr('app.analysis.httpx.post', respond)
    if invalid:
        with pytest.raises(ValueError):
            analyze(1)
        with pytest.raises(ValueError):
            analyze(1)
        with Session() as s:
            detail = s.scalar(select(Event).where(Event.kind == 'ai_failure')).detail
            assert detail['validation'] == [{'field': 'recency', 'type': 'string_type'}]
            assert 'do-not-log-source-content' not in str(detail)
    else:
        assert analyze(1)['analysis']['contact_permission'] == 'unknown'
        assert analyze(1)['cached'] is True
    assert len(calls) == 1
    with Session() as s:
        call = s.scalar(select(AnalysisCall))
        assert call.input_tokens == 100 and call.output_tokens == 50
        assert call.estimated_usd == pytest.approx(.0002)
