from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import select
from app.db import Session, Message, Contact, Control, Lead, engine
from app.delivery import dispatch_one, content_hash
from app.config import settings
from conftest import approve, enqueue


def test_auth_required(client):
    assert client.get('/api/leads',headers={'Authorization':''}).status_code == 401


def test_operator_cannot_approve_or_verify_or_resume(client,draft):
    ops={'Authorization':'Bearer test-operator'}
    assert client.post('/api/messages/1/approve',json={'version':1,'content_hash':draft['content_hash']},headers=ops).status_code == 403
    assert client.post('/api/contacts/1/verify',json={},headers=ops).status_code == 403
    assert client.post('/api/control/pause',json={'paused':False},headers=ops).status_code == 403
    client.post('/api/leads/1/feedback',json={'text':'takeover','human_takeover':True})
    assert client.post('/api/leads/1/feedback',json={'text':'reset','human_takeover':False},headers=ops).status_code == 403


def test_generation_never_authorizes(client,draft):
    assert draft['status']=='pending_review' and draft['approval_hash']==''
    client.post('/api/control/pause',json={'paused':False})
    assert client.post('/api/messages/1/queue',json={}).status_code==409


def test_unverified_contact_blocks(client,draft):
    with Session.begin() as s: s.get(Contact,1).verified=False
    assert approve(client,draft).status_code==409


def test_version_edit_revokes_and_old_review_rejected(client,draft):
    assert approve(client,draft).status_code==200
    edit={k:draft[k] for k in ('version','subject','body','contact_id','channel','attachments')}
    edit['body']='Changed content'
    m=client.put('/api/messages/1',json=edit).json()
    assert m['version']==2 and m['status']=='pending_review' and not m['approval_hash']
    assert approve(client,draft).status_code==409
    assert approve(client,m).status_code==200


@pytest.mark.parametrize(('field','value'), [('lead_id',99),('contact_id',99),('recipient','other@example.test'),('channel','smtp'),('account','other'),('subject','changed'),('body','changed'),('attachments',[{'name':'x'}]),('version',9)])
def test_all_bound_fields_block_tampering(client,draft,field,value):
    enqueue(client,draft)
    with Session.begin() as s: setattr(s.get(Message,1),field,value)
    assert dispatch_one()['status']=='blocked'
    with Session() as s: assert s.get(Message,1).status=='pending_review'


def test_queue_idempotent_and_send_once(client,draft):
    enqueue(client,draft)
    assert client.post('/api/messages/1/queue',json={}).status_code==200
    assert dispatch_one()['status']=='simulated'
    assert dispatch_one()['status'] in ('rate_limited','empty')
    assert client.post('/api/messages/1/queue',json={}).status_code==409
    with Session() as s: assert len(list(s.scalars(select(Message))))==1


def test_cloned_draft_cannot_repeat_same_transmission(client,draft):
    enqueue(client,draft)
    assert dispatch_one()['status']=='simulated'
    other=client.post('/api/leads/1/draft',json={'contact_id':1,'channel':'simulation'}).json()
    enqueue(client,other)
    with Session.begin() as s: s.get(Control,1).last_send=''
    assert dispatch_one()['status']=='duplicate_blocked'


@pytest.mark.parametrize('change',['pause','suppression','takeover','unverify'])
def test_final_gate(client,draft,change):
    enqueue(client,draft)
    if change=='pause': client.post('/api/control/pause',json={'paused':True})
    if change=='suppression': client.post('/api/suppressions',json={'address':'TEST@example.test','reason':'unsubscribe'})
    if change=='takeover': client.post('/api/leads/1/feedback',json={'text':'manual','human_takeover':True})
    if change=='unverify':
        with Session.begin() as s: s.get(Contact,1).verified=False
    assert dispatch_one()['status'] in ('paused','blocked')


def test_cancelled_never_sends(client,draft):
    enqueue(client,draft)
    assert client.post('/api/messages/1/cancel',json={'reason':'skip'}).status_code==200
    assert dispatch_one()['status']=='empty'


def test_scheduled_not_early(client,draft):
    assert approve(client,draft).status_code==200
    client.post('/api/control/pause',json={'paused':False})
    client.post('/api/messages/1/queue',json={'not_before':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()})
    assert dispatch_one()['status']=='empty'


def test_interrupted_send_becomes_unknown_without_retry(client,draft):
    enqueue(client,draft)
    with Session.begin() as s:
        m=s.get(Message,1);m.status='sending';m.attempted_at='2000-01-01T00:00:00+00:00'
    assert dispatch_one()['status']=='empty'
    with Session() as s: assert s.get(Message,1).status=='unknown'


def test_smtp_timeout_is_unknown_and_never_retried(client,draft,monkeypatch):
    cfg=settings()
    for k,v in {'real_send_enabled':True,'smtp_host':'example.test','smtp_from':'sender@example.test','smtp_username':'test','smtp_password':'test'}.items(): monkeypatch.setattr(cfg,k,v)
    edit={k:draft[k] for k in ('version','subject','body','contact_id','channel','attachments')};edit['channel']='smtp'
    draft=client.put('/api/messages/1',json=edit).json()
    enqueue(client,draft)
    attempts=[]
    def timeout(m): attempts.append(m.id);raise TimeoutError()
    assert dispatch_one(sender=timeout)['status']=='unknown'
    dispatch_one(sender=timeout)
    assert attempts==[1]
    assert client.post('/api/messages/1/queue',json={}).status_code==409


def test_smtp_acceptance_is_not_delivery(client,draft,monkeypatch):
    cfg=settings()
    for k,v in {'real_send_enabled':True,'smtp_host':'example.test','smtp_from':'sender@example.test','smtp_username':'test','smtp_password':'test'}.items(): monkeypatch.setattr(cfg,k,v)
    edit={k:draft[k] for k in ('version','subject','body','contact_id','channel','attachments')};edit['channel']='smtp'
    draft=client.put('/api/messages/1',json=edit).json();enqueue(client,draft)
    assert dispatch_one(sender=lambda m:'provider-accepted')['status']=='submitted'


def test_real_send_disabled_by_default(client):
    m=client.post('/api/leads/1/draft',json={'contact_id':1,'channel':'smtp'}).json()
    assert approve(client,m).status_code==409


def test_attachment_rejected(client,draft):
    edit={k:draft[k] for k in ('version','subject','body','contact_id','channel','attachments')};edit['attachments']=['file']
    assert client.put('/api/messages/1',json=edit).status_code==422


@pytest.mark.skipif(engine.dialect.name!='postgresql',reason='Requires PostgreSQL row locks')
def test_concurrent_dispatch_claims_only_once(client,draft):
    enqueue(client,draft)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:dispatch_one(),range(4)))
    assert sum(r['status']=='simulated' for r in results)==1
