import json
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import select, func
from app.db import Session, Lead, CaptureEvidence, ContactPoint, Message, AnalysisCall, ChannelRun
from app.social import get_policy, sync_social
from app.social_adapters import (feed_items, qq_items, xhs_items, wechat_items, Capture,
                                 AdapterError, validate_url, milky)

DEMAND = '我们正在开发 AI 应用，想找稳定的大模型 API 供应商，哪家 API 支持按量计费？'


def enable(client, platform, **kwargs):
    rows = client.get('/api/social/channels').json()
    row = next(x for x in rows if x['platform'] == platform)
    body = {k: row[k] for k in ('version','enabled','queries','exclude','account','conversations')}
    body.update(enabled=True, **kwargs)
    assert client.put('/api/social/channels/' + platform, json=body).status_code == 200
    return row['version'] + 1


def item(**kwargs):
    return {'event_id':'one', 'text':DEMAND, 'title':'哪家大模型 API 稳定？',
            'author':'test', 'url':'https://www.nodeseek.com/post-123-1', **kwargs}


def test_catalog_is_honest_and_auth_required(client):
    rows = client.get('/api/social/channels').json()
    assert len(rows) == 8
    assert all(not x['can_send'] and not x['enabled'] and x['status'] == 'not_connected' for x in rows)
    assert client.get('/api/social/channels', headers={'Authorization':'Bearer wrong'}).status_code == 401
    assert client.post('/api/social/channels/douyin/sync', json={}).status_code == 409


def test_import_dedup_filters_and_never_spends_or_sends(client):
    version = enable(client, 'nodeseek')
    data = {'version':version,'mode':'manual','items':[item(),item(),item(event_id='two',url='https://www.nodeseek.com/post-456-1',title='今日天气',text='晴天') ]}
    r = client.post('/api/social/channels/nodeseek/capture',json=data)
    assert r.status_code == 200, r.text
    assert (r.json()['new_leads'],r.json()['cache_hits'],r.json()['filtered']) == (1,1,1)
    assert r.json()['filter_reasons'] == {'no_keyword':1}
    assert client.post('/api/social/channels/nodeseek/capture',json=data).json()['new_leads'] == 0
    row = next(x for x in client.get('/api/social/channels').json() if x['platform']=='nodeseek')
    assert row['status']=='imported' and row['last_success']==''
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Message)) == 0
        assert s.scalar(select(func.count()).select_from(AnalysisCall)) == 0
        lead = s.scalar(select(Lead).where(Lead.source=='NodeSeek'))
        assert s.get(CaptureEvidence,lead.id).provenance=='manual'


def test_qq_scope_sender_and_unverified_contact(client):
    version=enable(client,'qq',account='12345',conversations=['group:54321'])
    data={'version':version,'mode':'executor','items':[item(url='',account='12345',conversation='group:54321',sender_id='98765',identity_verified=True)]}
    r=client.post('/api/social/channels/qq/capture',json=data)
    assert r.status_code==200,r.text
    with Session() as s:
        p=s.scalar(select(ContactPoint).where(ContactPoint.kind=='qq'))
        assert p.value=='98765' and not p.verified and p.source_url==''
    data['items'][0]['conversation']='group:11111'
    assert client.post('/api/social/channels/qq/capture',json=data).status_code==422
    data['items'][0]['conversation']='group:54321';data['items'][0]['account']='22222'
    assert client.post('/api/social/channels/qq/capture',json=data).status_code==422


def test_config_revision_and_disable_reject_inflight_capture(client):
    version=enable(client,'nodeseek')
    data={'version':version,'mode':'executor','items':[item()]}
    enable(client,'nodeseek',exclude=['广告'])
    assert client.post('/api/social/channels/nodeseek/capture',json=data).status_code==409
    with Session.begin() as s:get_policy(s,'nodeseek').enabled=False
    data['version']=version+1
    assert client.post('/api/social/channels/nodeseek/capture',json=data).status_code==409


@pytest.mark.parametrize('url',['http://linux.do/t/1','https://evil.test/t/1','https://linux.do@evil.test/x','https://linux.do:444/t/1','https://linux.do/t/1?token=secret'])
def test_source_links_restricted(url):
    with pytest.raises(ValueError):validate_url('linuxdo',url)


def test_rss_rejects_html_entities_and_untrusted_links():
    def run(body):
        with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text=body))) as c:
            return feed_items('nodeseek',c)
    for raw in ['<html/>','<!DOCTYPE rss><rss/>']:
        with pytest.raises(AdapterError):run(raw)
    raw='<rss><channel><item><link>https://evil.test/x</link><title>test</title></item><item><link>https://www.nodeseek.com/post-1-1</link><title>模型 API 求助</title><description>原文</description></item></channel></rss>'
    assert len(run(raw))==1


def test_qq_adapter_only_reads_selected_identity_and_skips_self():
    calls=[]
    def serve(r):
        calls.append(r.url.path)
        if r.url.path.endswith('get_login_info'):return httpx.Response(200,json={'retcode':0,'data':{'user_id':12345}})
        payload=json.loads(r.content);assert payload=={'message_scene':'group','peer_id':54321,'limit':30}
        messages=[{'message_scene':'group','peer_id':54321,'sender_id':sender,'message_seq':seq,'time':1700000000,'segments':[{'type':'text','data':{'text':DEMAND}}]} for sender,seq in [(12345,2),(98765,3)]]
        return httpx.Response(200,json={'retcode':0,'data':{'messages':messages}})
    with httpx.Client(transport=httpx.MockTransport(serve)) as c:
        records,cursors=qq_items(c,'http://bridge','12345',['group:54321'],{})
        assert len(records)==1 and records[0].sender_id=='98765' and cursors['group:54321']==3
        assert qq_items(c,'http://bridge','12345',['group:54321'],cursors)[0]==[]
        with pytest.raises(AdapterError,match='account_mismatch'):qq_items(c,'http://bridge','11111',['group:54321'],{})
        with pytest.raises(AdapterError,match='read_only_endpoint'):milky(c,'http://bridge','send_private_message',{})
    assert set(calls)=={'/api/get_login_info','/api/get_history_messages'}


def test_wechat_ocr_never_claims_identity_or_reads_other_chat():
    backend=SimpleNamespace(get_messages=lambda chat,limit:[SimpleNamespace(chat=chat,sender='self',content=DEMAND,type='text'),SimpleNamespace(chat=chat,sender='同名人',content=DEMAND,type='text')])
    records=wechat_items(backend,'my-account',['chosen'])
    assert len(records)==1 and not records[0].identity_verified and records[0].published_at==''
    backend.get_messages=lambda chat,limit:[SimpleNamespace(chat='wrong',sender='x',content=DEMAND,type='text')]
    with pytest.raises(AdapterError,match='conversation_mismatch'):wechat_items(backend,'my-account',['chosen'])


def test_xhs_note_and_comment_have_distinct_authors_no_publish():
    paths=[]
    def serve(r):
        paths.append(r.url.path)
        if r.url.path.endswith('/status'):data={'is_logged_in':True}
        elif r.url.path.endswith('/search'):data={'feeds':[{'id':'abc123','modelType':'note','xsecToken':'test-only'}]}
        else:
            assert json.loads(r.content)['load_all_comments'] is False
            data={'data':{'note':{'noteId':'abc123','title':'test','desc':DEMAND,'user':{'userId':'author1'}},'comments':{'list':[{'id':'comment1','content':DEMAND,'userInfo':{'userId':'author2'}}]}}}
        return httpx.Response(200,json={'success':True,'data':data})
    with httpx.Client(transport=httpx.MockTransport(serve)) as c:records=xhs_items(c,'http://bridge',['模型 API'])
    assert [x.sender_id for x in records]==['author1','author2']
    assert records[0].event_id!=records[1].event_id
    assert 'test-only' not in json.dumps([x.model_dump() for x in records])
    assert set(paths)=={'/api/v1/login/status','/api/v1/feeds/search','/api/v1/feeds/detail'}


def test_xhs_stops_when_not_logged_in():
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'success':True,'data':{'is_logged_in':False}}))) as c:
        with pytest.raises(AdapterError,match='login_required'):xhs_items(c,'http://bridge',['API'])


def test_forum_failure_records_challenge_and_cooldown(client,monkeypatch):
    enable(client,'linuxdo')
    def denied(*a):
        r=httpx.Response(403,request=httpx.Request('GET','https://linux.do/latest.rss'));r.raise_for_status()
    monkeypatch.setattr('app.social.feed_items',denied)
    assert sync_social('linuxdo')=={'status':'verification_required','error':'HTTP_403'}
    assert sync_social('linuxdo')=={'status':'cooldown'}


def test_manual_only_channel_rejects_executor_claim(client):
    version=enable(client,'douyin')
    r=client.post('/api/social/channels/douyin/capture',json={'version':version,'mode':'executor','items':[]})
    assert r.status_code==409


def test_private_same_text_different_people_not_merged(client):
    version=enable(client,'qq',account='12345',conversations=['group:54321'])
    records=[item(event_id=str(i),url='',account='12345',conversation='group:54321',sender_id=str(90000+i),identity_verified=True) for i in [1,2]]
    assert client.post('/api/social/channels/qq/capture',json={'version':version,'mode':'executor','items':records}).json()['new_leads']==2
