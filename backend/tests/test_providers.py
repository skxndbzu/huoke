import json
import httpx
import pytest
from sqlalchemy import select
from app.providers import validate_endpoint, model_settings
from app.config import settings
from app.db import Session, ModelProvider, Event


def test_private_model_urls_rejected():
    with pytest.raises(ValueError):validate_endpoint('http://127.0.0.1/v1',True)
    with pytest.raises(ValueError):validate_endpoint('https://169.254.169.254/v1')
    with pytest.raises(ValueError):validate_endpoint('https://secret@example.com/v1')


def test_model_management_redacts_keys_requires_owner_and_reverification(client,monkeypatch,tmp_path):
    monkeypatch.setattr(settings(),'secret_store_path',str(tmp_path))
    monkeypatch.setattr('app.providers.validate_endpoint',lambda url,allow=False:url)
    payload={'name':'Test service','base_url':'https://model.example.test/v1','model':'test-model','api_key':'not-a-real-secret',
             'input_price':2,'output_price':10,'cached_price':.1,'price_source':'test-only price','allow_http':False}
    assert client.post('/api/providers',json=payload,headers={'Authorization':'Bearer test-operator'}).status_code==403
    p=client.post('/api/providers',json=payload).json()
    assert 'not-a-real-secret' not in json.dumps(p) and 'secret_ref' not in p and 'api_key' not in p
    assert client.post(f'/api/providers/{p["id"]}/activate').status_code==409
    real=httpx.Client
    monkeypatch.setattr('app.providers.httpx.Client',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'data':[{'id':'test-model'}]})),**kw))
    assert client.post(f'/api/providers/{p["id"]}/probe').json()['status']=='models_verified'
    assert client.post(f'/api/providers/{p["id"]}/activate').status_code==200
    assert model_settings().llm_api_key=='not-a-real-secret'
    new=client.put(f'/api/providers/{p["id"]}',json={**payload,'version':p['version'],'api_key':''}).json()
    assert not new['active'] and new['probe_status']=='unverified'
    with Session() as s:
        assert 'not-a-real-secret' not in str([e.detail for e in s.scalars(select(Event))])
