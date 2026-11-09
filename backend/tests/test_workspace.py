from sqlalchemy import select, func
from app.db import Session, LeadWorkspace, Touchpoint, Message, Lead, Contact


def work(client, **changes):
    old = client.get('/api/workspace/leads/1').json()['work']
    data = {k:old[k] for k in ('version','qualification','reason','stage','next_followup')}
    return client.put('/api/workspace/leads/1', json={**data, **changes})


def test_qualification_requires_reason_and_stale_edit_rejected(client):
    assert work(client, qualification='valid').status_code == 422
    assert work(client, qualification='valid',reason='明确寻找供应商').status_code == 200
    assert client.get('/api/workspace/leads?bucket=contact').json()['total'] == 1
    assert client.put('/api/workspace/leads/1',json={'version':0,'qualification':'invalid','reason':'old','stage':'new','next_followup':''}).status_code == 409


def test_due_followup_closure_and_invalid_stop_contact(client):
    assert work(client,next_followup='2026-01-01T09:00:00+08:00').status_code == 200
    assert client.get('/api/workspace/leads?bucket=followup').json()['total'] == 1
    assert work(client,stage='declined').status_code == 200
    assert client.get('/api/workspace/leads?bucket=followup').json()['total'] == 0
    with Session() as s:
        assert s.get(Lead,1).human_takeover
    assert client.get('/api/workspace/leads/1/suggestion').status_code == 409


def test_touchpoints_idempotent_no_messages_no_fake_receipts(client):
    data={'request_id':'test-touchpoint-0001','kind':'outbound','channel':'wechat','body':'owner actually contacted','occurred_at':'2026-01-01T00:00:00+00:00'}
    a=client.post('/api/workspace/leads/1/touchpoints',json=data)
    assert a.status_code == 200
    assert client.post('/api/workspace/leads/1/touchpoints',json=data).json()['id'] == a.json()['id']
    assert client.post('/api/workspace/leads/1/touchpoints',json={**data,'body':'different'}).status_code == 409
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Touchpoint)) == 1
        assert s.scalar(select(func.count()).select_from(Message)) == 0
        assert s.get(LeadWorkspace,1).stage == 'contacted'
    data.update(request_id='test-touchpoint-0002',kind='inbound',body='customer reply')
    assert client.post('/api/workspace/leads/1/touchpoints',json=data).status_code == 200
    assert client.get('/api/workspace/leads?bucket=reply').json()['total']==1
    client.post('/api/workspace/leads/1/read')
    assert client.get('/api/workspace/leads?bucket=reply').json()['total']==0


def test_metrics_not_inferred_from_stage_and_deduplicate_actual_events(client):
    assert work(client,stage='paid').status_code==200
    assert client.get('/api/workspace/metrics').json()['channels'][0]['paid']==0
    for i in range(2):
        client.post('/api/workspace/leads/1/touchpoints',json={'request_id':f'test-conversion-000{i}','kind':'paid','channel':'other','body':'actual payment record','occurred_at':'2026-01-01T00:00:00+00:00'})
    d=client.get('/api/workspace/metrics').json()
    assert d['channels'][0]['paid']==1 and d['gross_margin'] is None
    assert d['channels'][0]['valid_rate'] is None


def test_contact_point_operator_cannot_verify_and_invalid_url_rejected(client):
    data={'kind':'email','value':'biz@example.test','source_url':'https://example.test/contact','evidence':'Published business contact'}
    p=client.post('/api/workspace/leads/1/points',json=data).json()
    assert not p['verified']
    assert client.post(f'/api/workspace/points/{p["id"]}/verify',headers={'Authorization':'Bearer test-operator'}).status_code==403
    assert client.post(f'/api/workspace/points/{p["id"]}/verify').status_code==200
    with Session() as s:
        assert s.scalar(select(Contact).where(Contact.address=='biz@example.test')).verified
    assert client.post('/api/workspace/leads/1/points',json={**data,'kind':'url','value':'javascript:alert(1)'}).status_code==422
