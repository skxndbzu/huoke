import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import select, func
from app.db import Session, Lead, CaptureEvidence, ContactPoint, Message, AnalysisCall, ChannelRun
from app.social import get_policy, sync_social
from app.social_adapters import (feed_items, gitee_items, v2ex_profile, qq_items, xhs_items, wechat_items, Capture,
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
    assert 'gitee' in {row['platform'] for row in rows}
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


def test_nodeseek_filters_network_relay_and_keeps_model_relay(client):
    version = enable(client, 'nodeseek')
    network = item(title='【人找车】想找一个香港中转vps 大口子 稳定连接 电信友好', text='如上')
    buyer = item(event_id='buyer', url='https://www.nodeseek.com/post-456-1',
                 title='求推荐gemini中转站', text='想比较两家的价格。')
    payload = {'version': version, 'mode': 'executor', 'items': [network, buyer]}
    route = '/api/social/channels/nodeseek/capture'
    first = client.post(route, json=payload)
    assert first.status_code == 200
    assert first.json()['filter_reasons'] == {'vps_relay': 1}
    assert first.json()['filtered_samples'][0]['url'] == network['url']
    assert first.json()['new_leads'] == 1
    second = client.post(route, json=payload).json()
    assert second['cache_hits'] == 2 and second['new_leads'] == 0
    with Session() as s:
        assert list(s.scalars(select(Lead.url).where(Lead.source == 'NodeSeek'))) == [buyer['url']]
        assert s.scalar(select(func.count()).select_from(AnalysisCall)) == 0


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


@pytest.mark.parametrize('failed_nodes', [('deepseek',), ('agent',), ('aigc',), ('deepseek', 'agent')])
def test_v2ex_preserves_other_feed_on_network_failure(client, failed_nodes):
    raw = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>寻找大模型 API</title>'
           '<link href="https://www.v2ex.com/t/99123#reply1"/><content>' + DEMAND + '</content></entry></feed>')

    def handle(request):
        if request.url.path in {f'/feed/{node}.xml' for node in failed_nodes}:
            raise httpx.ReadTimeout('network timeout', request=request)
        return httpx.Response(200, text=raw)

    failures = {}
    with httpx.Client(transport=httpx.MockTransport(handle)) as reader:
        items = feed_items('v2ex', reader, network_failures=failures)
    assert failures == {node: 'timeout' for node in failed_nodes}
    assert len(items) == 3 - len(failed_nodes)
    assert {post.url for post in items} == {'https://www.v2ex.com/t/99123'}

    version = enable(client, 'v2ex')
    route = '/api/social/channels/v2ex/capture'
    payload = {'version': version, 'mode': 'executor', 'items': [post.model_dump() for post in items]}
    assert client.post(route, json={**payload, 'items': []}).json()['status'] == 'success'

    def reading():
        channel = next(row for row in client.get('/api/social/channels').json() if row['platform'] == 'v2ex')
        return next(result for result in channel['routes'] if result['mode'] == 'executor')

    last_success = reading()['last_success']
    partial = client.post(route, json={**payload, 'feed_failures': failures}).json()
    assert partial['status'] == 'partial' and partial['new_leads'] == 1
    assert reading()['last_success'] == last_success
    assert reading()['stats']['feed_failures'] == failures
    assert client.post(route, json={**payload, 'mode': 'manual', 'feed_failures': failures}).status_code == 422
    assert client.post(route, json={**payload, 'feed_failures': {'unknown': 'timeout'}}).status_code == 422
    recovery = client.post(route, json=payload).json()
    assert recovery['status'] == 'success' and recovery['cache_hits'] == len(items)
    assert reading()['last_success'] > last_success
    assert 'feed_failures' not in reading()['stats']


def test_v2ex_total_network_failure_or_platform_verification_is_not_partial():
    def unavailable(request):
        raise httpx.ConnectError('network unavailable', request=request)

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as reader:
        with pytest.raises(httpx.ConnectError):
            feed_items('v2ex', reader, network_failures={})

    calls = []

    def verification(request):
        calls.append(request.url.path)
        return httpx.Response(403)

    with httpx.Client(transport=httpx.MockTransport(verification)) as reader:
        with pytest.raises(httpx.HTTPStatusError):
            feed_items('v2ex', reader, network_failures={})
    assert calls == ['/feed/deepseek.xml']


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
    assert sync_social('linuxdo')['status']=='cooldown'


def test_manual_only_channel_rejects_executor_claim(client):
    version=enable(client,'douyin')
    r=client.post('/api/social/channels/douyin/capture',json={'version':version,'mode':'executor','items':[]})
    assert r.status_code==409


def test_private_same_text_different_people_not_merged(client):
    version=enable(client,'qq',account='12345',conversations=['group:54321'])
    records=[item(event_id=str(i),url='',account='12345',conversation='group:54321',sender_id=str(90000+i),identity_verified=True) for i in [1,2]]
    assert client.post('/api/social/channels/qq/capture',json={'version':version,'mode':'executor','items':records}).json()['new_leads']==2


def test_gitee_search_filters_and_deduplicates_in_workspace(client, monkeypatch):
    version = enable(client, 'gitee')
    current = datetime.now(timezone.utc)
    issue_url = 'https://gitee.com/test-team/test-repo/issues/ITEST1'
    issue = {'html_url': issue_url, 'created_at': current.isoformat(), 'state': 'open',
             'title': '哪家大模型API稳定？', 'body': DEMAND, 'user': {'login': 'test-author'}}
    pages = [issue, {**issue, 'state': 'closed'}, {**issue, 'html_url': 'https://example.test/t/1'},
             {**issue, 'html_url': issue_url[:-1] + '2',
              'created_at': (current - timedelta(days=91)).isoformat()},
             {**issue, 'html_url': issue_url[:-1] + '3', 'title': 'OpenAPI 端点', 'body': '文档更新'}]
    searches = []

    def serve(request):
        assert request.method == 'GET'
        assert request.url.host == 'gitee.com' and request.url.path == '/api/v5/search/issues'
        assert request.url.params['state'] == 'open' and request.url.params['per_page'] == '12'
        searches.append(request.url.params['q'])
        return httpx.Response(200, json=pages)

    def search(_, queries):
        with httpx.Client(transport=httpx.MockTransport(serve)) as source:
            return gitee_items(source, queries)

    monkeypatch.setattr('app.social.gitee_items', search)
    result = sync_social('gitee')
    assert searches == ['大模型 API', '中转站', 'DeepSeek API']
    assert result['listed'] == 3 and result['new_leads'] == 1
    assert result['filter_reasons'] == {'older_than_90_days': 1, 'no_keyword': 1}
    assert result['model_calls'] == 0 and result['capture_mode'] == 'server_search'
    cooldown = sync_social('gitee')
    assert cooldown['status'] == 'cooldown' and datetime.fromisoformat(cooldown['next_allowed_at']) > current
    options = {'source': 'Gitee Issues', 'contact': 'public_discussion'}
    row = client.get('/api/workspace/leads', params=options).json()['items'][0]
    assert row['url'] == issue_url and row['capture']['provenance'] == 'server_search'
    assert row['contact_status'] == 'public_discussion'
    duplicate = {'url': issue_url + '#comment', 'title': issue['title'], 'text': DEMAND, 'event_id': 'manual'}
    result = client.post('/api/social/channels/gitee/capture',
                         json={'version': version, 'mode': 'manual', 'items': [duplicate]}).json()
    assert result['new_leads'] == 0 and result['cache_hits'] == 1


def test_feed_routes_separate_failures_imports_and_stale_success(client):
    version = enable(client, 'nodeseek')
    old_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with Session.begin() as s:
        s.add(ChannelRun(channel='social:nodeseek', status='failed', error='ConnectError'))
        s.add(ChannelRun(channel='social:nodeseek', status='success', finished_at=old_time,
                         stats={'capture_mode': 'executor', 'listed': 20}))
        s.add(ChannelRun(channel='v2ex', status='failed', error='ConnectError'))
    status = client.post('/api/social/channels/nodeseek/status', json={
        'version': version, 'status': 'verification_required', 'code': 'HTTP_403'})
    assert status.status_code == 200
    imported = client.post('/api/social/channels/nodeseek/capture', json={
        'version': version, 'mode': 'manual', 'items': [item()]})
    assert imported.status_code == 200

    rows = {r['platform']: r for r in client.get('/api/social/channels').json()}
    assert rows['nodeseek']['status'] == 'imported'
    server, local = rows['nodeseek']['routes']
    assert server['status'] == 'failed' and server['error'] == 'ConnectError'
    assert not server['last_success']
    assert local['status'] == 'verification_required' and local['error'] == 'HTTP_403'
    assert local['stale'] and local['last_success'] == old_time and local['stats']['listed'] == 20
    assert rows['v2ex']['routes'][0]['status'] == 'failed'


def test_v2ex_profile_requires_same_author_and_keeps_contact_unverified(client):
    version = enable(client, 'v2ex')
    capture = item(url='https://www.v2ex.com/t/99123', author='test_author',
                   published_at=datetime.now(timezone.utc).isoformat())
    result = client.post('/api/social/channels/v2ex/capture',
                         json={'version': version, 'mode': 'executor', 'items': [capture]})
    assert result.status_code == 200 and result.json()['new_leads'] == 1
    route = '/api/social/v2ex/profiles'
    job, = client.get(route).json()['items']
    assert job['username'] == 'test_author'

    def serve(request):
        assert request.url.host == 'www.v2ex.com' and request.url.params['username'] == 'test_author'
        return httpx.Response(200, json={'username': 'test_author', 'github': 'test-github',
                                         'email': 'private-looking@sample.test', 'location': '公开地区',
                                         'bio': '业务联系邮箱 contact@sample-company.org'})

    with httpx.Client(transport=httpx.MockTransport(serve)) as source:
        data = v2ex_profile(source, 'test_author').model_dump()
    assert 'email' not in data
    profile_route = f'{route}/{job["id"]}'
    assert client.post(profile_route, json={**data, 'version': version, 'username': 'wrong'}).status_code == 422
    result = client.post(profile_route, json={**data, 'version': version})
    assert result.json() == {'status': 'public_profile', 'contacts_added': 1}
    assert client.get(route).json()['items'] == []
    assert client.post(profile_route, json={**data, 'version': version}).json()['status'] == 'cached'
    detail = client.get(f'/api/workspace/leads/{job["id"]}').json()
    assert detail['market']['profile']['github'] == 'test-github'
    assert detail['market']['status'] == 'pending_ai' and detail['contact_status'] == 'unverified_candidate'


def test_discussion_atom_and_import_share_public_topic_identity(client):
    version = enable(client, 'github_discussions')
    raw = f'''<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>哪家大模型 API 稳定？</title><content>{DEMAND}</content>
      <link href="https://github.com/cherryHQ/cherry-studio/discussions/123#discussioncomment-1"/>
      </entry><entry><title>同名 Issue</title><link href="https://github.com/CherryHQ/cherry-studio/issues/123"/>
      </entry></feed>'''
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=raw))) as source:
        items = feed_items('github_discussions', source)
    assert len(items) == 2  # identical feed items deduplicate when they reach the capture endpoint
    assert items[0].url == 'https://github.com/CherryHQ/cherry-studio/discussions/123'
    route = '/api/social/channels/github_discussions/capture'
    result = client.post(route, json={'version': version, 'mode': 'executor', 'items': [r.model_dump() for r in items]})
    assert result.status_code == 200 and result.json()['new_leads'] == 1
    imported = {**items[0].model_dump(), 'url': items[0].url + '#discussioncomment-2'}
    result = client.post(route, json={'version': version, 'mode': 'manual', 'items': [imported]})
    assert result.status_code == 200 and result.json()['cache_hits'] == 1
    body = client.get('/api/workspace/leads', params={'source': 'GitHub Discussions', 'contact': 'public_discussion'}).json()
    assert body['total'] == 1


def test_screening_review_covers_different_reasons_and_survives_cache_only_run(client):
    version = enable(client, 'nodeseek')
    old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    batch = [item(event_id=str(i), url=f'https://www.nodeseek.com/post-{i}-1', published_at=old) for i in range(101,107)]
    batch.extend([
        item(event_id='107', url='https://www.nodeseek.com/post-107-1', title='今日天气', text='晴'),
        item(event_id='108', url='https://www.nodeseek.com/post-108-1', title='模型 API 介绍', text='文档更新'),
    ])
    route = '/api/social/channels/nodeseek/capture'
    payload = {'version': version, 'mode': 'executor', 'items': batch}
    first = client.post(route, json=payload).json()
    assert len(first['filtered_samples']) == 5
    assert {x['reason'] for x in first['filtered_samples']} == {'older_than_90_days','no_keyword','no_demand_evidence'}
    assert client.post(route, json=payload).json()['cache_hits'] == 8
    row = next(r for r in client.get('/api/social/channels').json() if r['platform'] == 'nodeseek')
    local = row['routes'][1]
    assert local['stats']['cache_hits'] == 8 and local['stats']['filtered'] == 0
    assert local['screening_batch']['samples'] == [{**sample, 'decision': 'unreviewed'} for sample in first['filtered_samples']]
    assert local['screening_batch']['reasons'] == first['filter_reasons']


def test_nodeseek_reports_publication_window_for_cache_only_batch(client):
    version = enable(client, 'nodeseek')
    latest = datetime.now(timezone.utc) - timedelta(minutes=2)
    earliest = latest - timedelta(minutes=10)
    route = '/api/social/channels/nodeseek/capture'
    items = [
        item(published_at=latest.isoformat()),
        item(event_id='two', url='https://www.nodeseek.com/post-456-1',
             title='今日天气', text='晴', published_at=earliest.isoformat()),
        item(event_id='three', url='https://www.nodeseek.com/post-789-1'),
    ]
    payload = {'version': version, 'mode': 'executor', 'items': items}
    expected = {'known': 2, 'earliest_at': earliest.isoformat(), 'latest_at': latest.isoformat()}
    first = client.post(route, json=payload)
    assert first.status_code == 200
    assert first.json()['publication_window'] == expected
    replay = client.post(route, json=payload).json()
    assert replay['cache_hits'] == 3 and replay['publication_window'] == expected

    row = next(r for r in client.get('/api/social/channels').json() if r['platform'] == 'nodeseek')
    assert row['routes'][1]['stats']['publication_window'] == expected
    undated = client.post(route, json={**payload, 'items': items[2:]}).json()
    assert undated['publication_window'] == {'known': 0, 'earliest_at': None, 'latest_at': None}
    assert 'publication_window' not in client.post(route, json={**payload, 'mode': 'manual'}).json()


def test_public_screening_feedback_is_owner_only_and_does_not_change_the_lead_pool(client):
    version = enable(client, 'nodeseek')
    rejected = item(title='独立测试样本', text='没有采集关键词。')
    route = '/api/social/channels/nodeseek/capture'
    client.post(route, json={'version': version, 'mode': 'executor', 'items': [rejected]}).raise_for_status()

    def sample_batch():
        row = next(row for row in client.get('/api/social/channels').json() if row['platform'] == 'nodeseek')
        return row['routes'][1]['screening_batch']

    batch = sample_batch()
    review = f'/api/social/screening/{batch["id"]}/0'
    rejected = client.put(review, json={'decision': 'has_demand'}, headers={'Authorization':'Bearer test-operator'})
    assert rejected.status_code == 403
    assert client.put(review, json={'decision': 'has_demand'}).status_code == 200
    assert sample_batch()['samples'][0]['decision'] == 'has_demand'
    history = '/api/social/screening-history'
    saved = client.get(history).json()
    assert saved['counts'] == [{'channel': 'nodeseek', 'decision': 'has_demand', 'count': 1}]
    assert saved['items'][0]['url'] == 'https://www.nodeseek.com/post-123-1'
    assert client.put(review, json={'decision': 'reasonable'}).status_code == 200
    assert client.get(history).json()['counts'] == [{'channel': 'nodeseek', 'decision': 'reasonable', 'count': 1}]
    assert client.put(review, json={'decision': 'unreviewed'}).status_code == 200
    assert sample_batch()['samples'][0]['decision'] == 'unreviewed'
    assert client.get(history).json() == {'counts': [], 'items': []}
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Lead)) == 1  # existing test fixture only
        assert s.scalar(select(func.count()).select_from(AnalysisCall)) == 0
        assert s.scalar(select(func.count()).select_from(Message)) == 0
