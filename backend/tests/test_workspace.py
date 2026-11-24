from sqlalchemy import select, func
import pytest
from app.db import Session, LeadWorkspace, Touchpoint, Message, Lead, Contact, ContactPoint


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


@pytest.mark.parametrize(('text', 'quote'), [
    ('原来的渠道已停止，有知道的请私聊我地址 谢谢了', '有知道的请私聊我地址'),
    ('> 有知道的请私聊我地址', None),
    ('可在评论区回复，请不要私聊我。', None),
    ('其他团队的采购，请私聊我们的合作方。', None),
])
def test_v2ex_contact_invitation_is_evidence_without_contact_details(client, text, quote):
    with Session.begin() as s:
        lead = Lead(source='V2EX', source_key='v2ex:98765', author='test',
                    url='https://www.v2ex.com/t/98765', title='哪家中转有 Gemini 模型 API？',
                    text=text, fingerprint='test-v2ex-invitation')
        s.add(lead)
        s.flush()
        lead_id, url = lead.id, lead.url
    data = client.get(f'/api/workspace/leads/{lead_id}').json()
    assert data['contact_invitation'] == ({'quote': quote, 'url': url} if quote else None)
    assert data['contact_status'] == 'public_discussion' and data['points'] == []
    assert client.get('/api/workspace/leads?bucket=contact').json()['total'] == 0
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Message)) == 0


def test_contact_queue_requires_verified_path_and_research_queue_tracks_missing_path(client):
    with Session.begin() as s:
        lead = Lead(source='GitHub Issues', source_key='github:200',
                    url='https://github.com/org/repo/issues/2', author='developer',
                    title='Looking for API vendor', text='Need a provider', fingerprint='other', analysis={})
        s.add(lead)
        s.flush()
        lead_id = lead.id
    old = client.get(f'/api/workspace/leads/{lead_id}').json()['work']
    assert client.put(f'/api/workspace/leads/{lead_id}', json={
        'version': old['version'], 'qualification': 'valid', 'reason': '采购需求已人工核实',
        'stage': 'new', 'next_followup': ''}).status_code == 200
    assert client.get('/api/workspace/leads?bucket=contact').json()['total'] == 0
    research = client.get('/api/workspace/leads?bucket=research').json()
    assert research['total'] == 1 and research['items'][0]['contact_status'] == 'public_discussion'
    assert client.get('/api/workspace/leads?contact=public_discussion').json()['total'] == 1
    data = {'kind':'email', 'value':'biz@example.org',
            'source_url':'https://example.org/contact', 'evidence':'Published business contact for this lead'}
    point = client.post(f'/api/workspace/leads/{lead_id}/points', json=data).json()
    assert client.get('/api/workspace/leads?bucket=research').json()['total'] == 1
    assert client.get('/api/workspace/leads?contact=unverified_candidate').json()['total'] == 1
    before = next(c for c in client.get('/api/workspace/metrics').json()['channels'] if c['source'] == 'GitHub Issues')
    assert before['with_contact'] == 0 and before['with_candidate_contact'] == 1
    assert client.post(f'/api/workspace/points/{point["id"]}/verify').status_code == 200
    contact = client.get('/api/workspace/leads?bucket=contact').json()
    assert contact['total'] == 1 and contact['items'][0]['contact_status'] == 'verified_contact'
    assert client.get('/api/workspace/leads?contact=verified_contact').json()['total'] == 2
    assert client.get('/api/workspace/leads?bucket=research').json()['total'] == 0
    metrics = client.get('/api/workspace/metrics').json()
    channel = next(c for c in metrics['channels'] if c['source'] == 'GitHub Issues')
    assert channel['with_contact'] == 1 and channel['with_candidate_contact'] == 1


def test_public_github_profile_research_only_suggests_and_never_contacts(client, monkeypatch):
    import httpx
    with Session.begin() as s:
        s.add(Lead(source='GitHub Issues', source_key='github:201',
                   url='https://github.com/org/repo/issues/3', author='developer',
                   title='Need API', text='Looking for provider', fingerprint='research', analysis={}))
    calls = []
    real = httpx.Client
    def handle(req):
        calls.append(str(req.url))
        return httpx.Response(200, json={
            'login': 'developer', 'html_url': 'https://github.com/developer',
            'email': 'public@company.example', 'blog': 'https://company.example',
            'company': 'Example Co', 'bio': 'Developer'})
    monkeypatch.setattr('httpx.Client', lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    data = client.get('/api/workspace/leads/2/contact-research').json()
    assert calls == ['https://api.github.com/users/developer']
    assert data['profile_url'] == 'https://github.com/developer'
    assert data['public_email'] == 'public@company.example'
    assert data['website_url'] == 'https://company.example'
    assert data['verified'] is False and data['contact_permission'] == 'unknown'
    with Session() as s:
        assert not s.query(ContactPoint).filter(ContactPoint.lead_id == 2).count()
        assert not s.query(Message).count()
    assert client.get('/api/workspace/leads/1/contact-research').status_code == 422
    assert client.get('/api/workspace/leads/2/contact-research',
                      headers={'Authorization': 'Bearer invalid'}).status_code == 401


def test_contact_research_rejects_untrusted_identity_and_unusable_addresses(client, monkeypatch):
    import httpx
    with Session.begin() as s:
        s.add(Lead(source='GitHub Issues', source_key='github:202',
                   url='https://github.com/org/repo/issues/4', author='developer',
                   title='Need API', text='Looking for provider', fingerprint='research2', analysis={}))
    real = httpx.Client
    result = {'login': 'other-user', 'email': 'noreply@users.noreply.github.com',
              'blog': 'javascript:alert(1)'}
    def handle(req):
        return httpx.Response(200, json=result)
    monkeypatch.setattr('httpx.Client', lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    assert client.get('/api/workspace/leads/2/contact-research').status_code == 502
    result['login'] = 'developer'
    data = client.get('/api/workspace/leads/2/contact-research').json()
    assert data['public_email'] == '' and data['website_url'] == ''
    assert client.get('/api/workspace/leads?contact=unrecognized').status_code == 422


def test_contact_research_returns_generic_error_without_exposing_response(client, monkeypatch):
    import httpx
    with Session.begin() as s:
        s.add(Lead(source='GitHub Issues', source_key='github:203',
                   url='https://github.com/org/repo/issues/5', author='developer',
                   title='Need API', text='Looking for provider', fingerprint='research3', analysis={}))
    real = httpx.Client
    monkeypatch.setattr('httpx.Client', lambda **kw: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(403, text='provider private error details')), **kw))
    response = client.get('/api/workspace/leads/2/contact-research')
    assert response.status_code == 502 and 'provider private error details' not in response.text


def test_contact_research_handles_malformed_public_profile(client, monkeypatch):
    import httpx
    with Session.begin() as s:
        s.add(Lead(source='GitHub Issues', source_key='github:204',
                   url='https://github.com/org/repo/issues/6', author='developer',
                   title='Need API', text='Looking for provider', fingerprint='research4', analysis={}))
    real = httpx.Client
    monkeypatch.setattr('httpx.Client', lambda **kw: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json=['unexpected profile'])), **kw))
    assert client.get('/api/workspace/leads/2/contact-research').status_code == 502


def test_channel_measures_manual_outreach_and_replies_without_inventing_delivery(client):
    def record(request_id, kind):
        return client.post('/api/workspace/leads/1/touchpoints', json={
            'request_id': request_id, 'kind': kind, 'channel': 'github',
            'body': 'manual event with evidence', 'occurred_at': '2026-01-01T00:00:00+00:00'})
    assert record('manual-outreach-0001', 'outbound').status_code == 200
    assert record('manual-outreach-0002', 'outbound').status_code == 200
    channel = client.get('/api/workspace/metrics').json()['channels'][0]
    assert channel['manual_contacted'] == 1 and channel['manual_replied'] == 0
    assert channel['manual_reply_rate'] == 0
    assert record('manual-replied-0001', 'inbound').status_code == 200
    channel = client.get('/api/workspace/metrics').json()['channels'][0]
    assert channel['manual_contacted'] == 1 and channel['manual_replied'] == 1
    assert channel['manual_reply_rate'] == 1
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Message)) == 0
