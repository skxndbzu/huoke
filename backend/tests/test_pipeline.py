import httpx
from app.pipeline import sync_hn
from app.db import Session, Lead, AnalysisCall
from sqlalchemy import select
from app.analysis import analyze
from app.config import settings
import pytest


def test_source_filters_and_second_run_reuses_cache(monkeypatch):
    real_client=httpx.Client
    requests=[]
    def handle(request):
        requests.append(request.url.path)
        if request.url.path.endswith('askstories.json'):return httpx.Response(200,json=[100,101])
        if request.url.path.endswith('100.json'):return httpx.Response(200,json={'id':100,'title':'Looking for LLM API provider','text':'Need help with model API pricing','by':'real-source-test','time':1700000000})
        return httpx.Response(200,json={'id':101,'title':'My garden','time':1700000000})
    monkeypatch.setattr('app.pipeline.httpx.Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handle),**kwargs))
    first=sync_hn();second=sync_hn()
    assert first['new_leads']==1 and first['filtered']==1
    assert second['cache_hits']==2 and second['fetched']==0 and len(requests)==4
    with Session() as s:
        lead=s.scalar(select(Lead).where(Lead.source_key=='hn:100'))
        assert lead.analysis['contact_permission']=='unknown'


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
