"""Read-only transports. No publisher, sender, friend-adding or login bypass methods."""
import hashlib
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
import httpx
from pydantic import Field, field_validator
from .schemas import Strict

CATALOG = {
    'github_discussions': {'name': 'GitHub Discussions', 'adapter': 'GitHub Atom', 'mode': 'executor',
                           'repo': 'https://github.com/CherryHQ/cherry-studio/discussions.atom',
                           'description': '本地读取 Cherry Studio 与 Dify 的公开讨论订阅，每轮最多 50 条。'},
    'gitee': {'name': 'Gitee Issues', 'adapter': 'Gitee OpenAPI v5', 'mode': 'search',
              'repo': 'https://gitee.com/api/v5/swagger',
              'description': '通过官方接口搜索公开且未关闭的 Issue。每小时最多三组词，每组最多 12 条。'},
    'qq': {'name': 'QQ', 'adapter': 'LLBot / Milky', 'mode': 'executor', 'repo': 'https://github.com/LuckyLilliaDev/LuckyLilliaBot',
           'description': '只读同步指定私聊/群聊的近期文本；需要已登录的 LLBot 和明确的会话 ID。陌生群成员不保证可私聊。'},
    'wechat': {'name': '微信', 'adapter': '小栗 visual backend', 'mode': 'executor', 'repo': 'https://github.com/wens6515/weixin-wechat-wxauto-ocr-xiaoli',
               'description': '本地微信 4.x 可见窗口 OCR 读取；需选择会话并保持窗口可见。昵称/发送者未核实，不支持自动发送。'},
    'xiaohongshu': {'name': '小红书', 'adapter': 'xiaohongshu-mcp HTTP', 'mode': 'executor', 'repo': 'https://github.com/xpzouying/xiaohongshu-mcp',
                    'description': '登录后按关键词搜索，读取笔记和有限评论；不调用发布/私信接口。'},
    'linuxdo': {'name': 'Linux.do', 'adapter': 'Discourse RSS', 'mode': 'feed', 'repo': 'https://github.com/DIYgod/RSSHub',
                'description': '读取站点公开订阅；遇到验证拦截则停止并显示原因，不绕过验证。'},
    'nodeseek': {'name': 'NodeSeek', 'adapter': '官方 RSS', 'mode': 'feed', 'repo': 'https://github.com/tyrad/nodeseek',
                 'description': '使用站点公开 RSS，避免部署不适用服务器的 iOS 客户端；只读近期帖子。'},
    'v2ex': {'name': 'V2EX 本地补采', 'adapter': '官方 Atom', 'mode': 'executor', 'repo': 'https://github.com/coolpace/V2EX_Polish',
             'description': '本地执行器补采服务器无法访问的官方订阅，与已有 V2EX 来源按帖子 ID 去重。'},
    'douyin': {'name': '抖音', 'adapter': '授权内容导入', 'mode': 'manual', 'repo': 'https://github.com/Johnserf-Seed/f2',
               'description': 'F2 的搜索/评论仍标为未来功能，暂不伪装成自动连接。可导入已授权的原文及链接进入分析。'},
    'kuaishou': {'name': '快手', 'adapter': '授权内容导入', 'mode': 'manual', 'repo': 'https://github.com/JoeanAmier/KS-Downloader',
                 'description': 'KS-Downloader 是下载器且商业推广范围受限，暂不作为自动获客器。可导入已授权内容。'},
}
DISCUSSION_REPOS = ('CherryHQ/cherry-studio', 'langgenius/dify')
FEEDS = {'github_discussions': [f'https://github.com/{repo}/discussions.atom' for repo in DISCUSSION_REPOS],
         'linuxdo': ['https://linux.do/latest.rss'], 'nodeseek': ['https://rss.nodeseek.com/'],
         'v2ex': ['https://www.v2ex.com/feed/deepseek.xml', 'https://www.v2ex.com/feed/programmer.xml']}
HOSTS = {'github_discussions': {'github.com'}, 'gitee': {'gitee.com'},
         'linuxdo': {'linux.do'}, 'nodeseek': {'www.nodeseek.com', 'nodeseek.com'},
         'v2ex': {'www.v2ex.com', 'v2ex.com'}, 'xiaohongshu': {'www.xiaohongshu.com', 'xiaohongshu.com'},
         'douyin': {'www.douyin.com', 'douyin.com'}, 'kuaishou': {'www.kuaishou.com', 'kuaishou.com', 'www.kuaishou.cn'}}
V2EX_USERNAME = r'[A-Za-z0-9_]{1,32}'
GITHUB_USERNAME = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?'


class Capture(Strict):
    event_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default='', max_length=500)
    text: str = Field(min_length=1, max_length=12000)
    url: str = Field(default='', max_length=2000)
    author: str = Field(default='未知', max_length=200)
    sender_id: str = Field(default='', max_length=120)
    account: str = Field(default='', max_length=120)
    conversation: str = Field(default='', max_length=200)
    published_at: str = Field(default='', max_length=50)
    identity_verified: bool = False  # transport identity only, NEVER contact/marketing approval

    @field_validator('published_at')
    @classmethod
    def timestamp(cls, value):
        if not value:
            return ''
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if d.tzinfo is None:
            raise ValueError('时间须含时区')
        return d.astimezone(timezone.utc).isoformat()


class AdapterError(ValueError):
    pass


class V2EXProfile(Strict):
    username: str = Field(pattern='^' + V2EX_USERNAME + '$')
    status: str = Field(pattern='^(public_profile|unavailable)$')
    location: str = Field(default='', max_length=200)
    bio: str = Field(default='', max_length=500)
    github: str = Field(default='', pattern='^(?:' + GITHUB_USERNAME + ')?$')


def read(client, method, url, **kwargs):
    with client.stream(method, url, **kwargs) as r:
        r.raise_for_status()
        data = bytearray()
        for chunk in r.iter_bytes():
            data.extend(chunk)
            if len(data) > 2_000_000:
                raise AdapterError('response_too_large')
        return bytes(data)


def json_read(client, method, url, **kwargs):
    data = json.loads(read(client, method, url, **kwargs))
    if not isinstance(data, dict):
        raise AdapterError('invalid_response')
    return data


def v2ex_profile(client, username):
    if not re.fullmatch(V2EX_USERNAME, username):
        raise AdapterError('invalid_profile_username')
    data = json_read(client, 'GET', 'https://www.v2ex.com/api/members/show.json', params={'username': username})
    returned = data.get('username')
    if not isinstance(returned, str) or returned.lower() != username.lower():
        raise AdapterError('profile_identity_mismatch')
    github = data.get('github')
    bio = '\n'.join(data[key] for key in ('tagline', 'bio') if isinstance(data.get(key), str))
    location = data.get('location')
    return V2EXProfile(username=username, status='public_profile', bio=bio[:500],
                       location=location[:200] if isinstance(location, str) else '',
                       github=github if isinstance(github, str) and re.fullmatch(GITHUB_USERNAME, github) else '')


def iso(value):
    if not value:
        return ''
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 100_000_000_000 else value, timezone.utc).isoformat()
        try:
            d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            d = parsedate_to_datetime(value)
        return d.astimezone(timezone.utc).isoformat() if d.tzinfo else ''
    except (ValueError, TypeError, OverflowError):
        return ''


def validate_url(platform, url):
    p = urlparse(url)
    if p.scheme != 'https' or p.hostname not in HOSTS.get(platform, set()) or p.username or p.password or p.port not in (None, 443):
        raise ValueError('原文链接必须属于所选平台的 HTTPS 主站')
    if p.query:
        raise ValueError('请移除原文链接中的查询参数，不保存会话或安全令牌')
    if platform == 'github_discussions':
        match = re.fullmatch(r'/([^/]+/[^/]+)/discussions/([0-9]+)', p.path.rstrip('/'))
        repo = next((r for r in DISCUSSION_REPOS if match and match[1].lower() == r.lower()), '')
        if not repo:
            raise ValueError('GitHub 原文必须属于试点仓库的公开讨论')
        return f'https://github.com/{repo}/discussions/{match[2]}'
    return url


def gitee_issue_url(url):
    if not isinstance(url, str):
        raise ValueError('Gitee 需要公开 Issue 的原文链接')
    path = urlparse(validate_url('gitee', url)).path.rstrip('/')
    if not re.fullmatch(r'/[\w.-]+/[\w.-]+/issues/[A-Za-z0-9]+', path):
        raise ValueError('Gitee 需要公开 Issue 的原文链接')
    return 'https://gitee.com' + path


def gitee_items(client, queries):
    items = {}
    for query in queries[:3]:
        data = json.loads(read(client, 'GET', 'https://gitee.com/api/v5/search/issues',
                               params={'q': query, 'state': 'open', 'per_page': 12}))
        if not isinstance(data, list):
            raise AdapterError('invalid_response')
        for issue in data[:12]:
            if not isinstance(issue, dict) or issue.get('state') != 'open':
                continue
            try:
                url = gitee_issue_url(issue.get('html_url') or '')
            except ValueError:
                continue
            title = issue.get('title') or ''
            body = issue.get('body') or title
            created_at = issue.get('created_at')
            stamp = iso(created_at) if isinstance(created_at, str) else ''
            if not stamp or not isinstance(title, str) or not isinstance(body, str) or not body.strip():
                continue
            user = issue.get('user')
            author = user.get('login') if isinstance(user, dict) else None
            items[url] = Capture(event_id=hashlib.sha256(url.encode()).hexdigest(), url=url,
                                 title=title[:500], text=body[:12000], published_at=stamp,
                                 author=author[:200] if isinstance(author, str) and author else '未知')
    return list(items.values())


def feed_items(platform, client):
    found = []
    for url in FEEDS[platform]:
        raw = read(client, 'GET', url)
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise AdapterError('unsafe_xml')
        root = ET.fromstring(raw)
        atom = '{http://www.w3.org/2005/Atom}'
        if root.tag not in ('rss', atom + 'feed'):
            raise AdapterError('not_a_feed')
        entries = root.findall('./channel/item') if root.tag == 'rss' else root.findall(atom + 'entry')
        for entry in entries[:25 if platform == 'github_discussions' else 30]:
            if root.tag == 'rss':
                link = entry.findtext('link', '')
                title = entry.findtext('title', '')
                body = entry.findtext('{http://purl.org/rss/1.0/modules/content/}encoded', '') or entry.findtext('description', '')
                author = entry.findtext('{http://purl.org/dc/elements/1.1/}creator', '') or entry.findtext('author', '未知')
                stamp = entry.findtext('pubDate', '')
            else:
                link = next((x.get('href', '') for x in entry.findall(atom + 'link') if x.get('rel', 'alternate') == 'alternate'), '')
                title = entry.findtext(atom + 'title', '')
                body = entry.findtext(atom + 'content', '') or entry.findtext(atom + 'summary', '')
                author = entry.findtext(atom + 'author/' + atom + 'name', '未知')
                stamp = entry.findtext(atom + 'published', '') or entry.findtext(atom + 'updated', '')
            try:
                link = validate_url(platform, link)
            except ValueError:
                continue
            match = re.search(r'/t/(\d+)', link) if platform == 'v2ex' else None
            if match:
                link = f'https://www.v2ex.com/t/{match[1]}'
            key = match[1] if match else hashlib.sha256(link.split('#')[0].encode()).hexdigest()
            found.append(Capture(event_id=key, title=title[:500], text=(body or title)[:12000], url=link,
                                 author=author[:200], published_at=iso(stamp)))
    return found


def milky(client, base, endpoint, payload):
    # This allowlist deliberately excludes every send, login and account mutation endpoint.
    if endpoint not in ('get_login_info', 'get_history_messages'):
        raise AdapterError('read_only_endpoint')
    response = json_read(client, 'POST', base + '/api/' + endpoint, json=payload)
    if response.get('retcode') != 0 or not isinstance(response.get('data'), dict):
        raise AdapterError('qq_read_failed')
    return response['data']


def qq_items(client, base, account, conversations, cursors):
    login = milky(client, base, 'get_login_info', {})
    if str(login.get('user_id', '')) != account:
        raise AdapterError('account_mismatch')
    found, next_cursors = [], dict(cursors)
    for conversation in conversations[:10]:
        scene, peer = conversation.split(':', 1)
        if scene not in ('friend', 'group') or not peer.isdigit():
            raise AdapterError('invalid_conversation')
        data = milky(client, base, 'get_history_messages', {'message_scene': scene, 'peer_id': int(peer), 'limit': 30})
        messages = data.get('messages')
        if not isinstance(messages, list):
            raise AdapterError('invalid_history')
        watermark = int(cursors.get(conversation, 0))
        for m in messages[:30]:
            if m.get('message_scene') != scene or str(m.get('peer_id')) != peer:
                raise AdapterError('conversation_mismatch')
            seq = int(m['message_seq'])
            next_cursors[conversation] = max(int(next_cursors.get(conversation, 0)), seq)
            if seq <= watermark or str(m.get('sender_id')) == account:
                continue
            text = '\n'.join(x.get('data', {}).get('text', '') for x in m.get('segments', []) if x.get('type') == 'text')
            if not text.strip():
                continue
            sender = str(m.get('sender_id', ''))
            if not sender.isdigit():
                continue
            found.append(Capture(event_id=str(seq), text=text[:12000], author=sender, sender_id=sender,
                                 account=account, conversation=conversation, published_at=iso(m.get('time')), identity_verified=True))
    return found, next_cursors


def xhs_items(client, base, queries):
    login = json_read(client, 'GET', base + '/api/v1/login/status')
    if not login.get('success') or not login.get('data', {}).get('is_logged_in'):
        raise AdapterError('login_required')
    items, seen = [], set()
    for query in queries[:3]:
        data = json_read(client, 'GET', base + '/api/v1/feeds/search', params={'keyword': query})
        if not data.get('success') or not isinstance(data.get('data', {}).get('feeds'), list):
            raise AdapterError('xhs_search_failed')
        for feed in data['data']['feeds'][:5]:
            nid = feed.get('id', '')
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', nid) or nid in seen or feed.get('modelType') != 'note':
                continue
            seen.add(nid)
            detail = json_read(client, 'POST', base + '/api/v1/feeds/detail',
                               json={'feed_id': nid, 'xsec_token': feed.get('xsecToken', ''), 'load_all_comments': False})
            if not detail.get('success'):
                raise AdapterError('xhs_detail_failed')
            d = detail.get('data', {}).get('data', {})
            note = d.get('note', {})
            if note.get('noteId') != nid:
                raise AdapterError('note_mismatch')
            url = 'https://www.xiaohongshu.com/explore/' + nid
            for key, title, body, user, stamp in [
                (nid, note.get('title', ''), note.get('desc') or note.get('title', ''), note.get('user', {}), note.get('time')),
                *[(nid + ':' + str(c.get('id', '')), '', c.get('content', ''), c.get('userInfo', {}), c.get('createTime'))
                  for c in d.get('comments', {}).get('list', [])[:10] if c.get('id')]]:
                if not body:
                    continue
                # A comment's author never inherits the note author's demand or contact.
                items.append(Capture(event_id=key, title=title[:500], text=body[:12000], url=url,
                                     author=str(user.get('nickname') or user.get('nickName') or '未知')[:200],
                                     sender_id=str(user.get('userId', ''))[:120], published_at=iso(stamp)))
    return items


def wechat_items(backend, account, conversations):
    """Only call the upstream read API, never instantiate its AI/auto-reply bot."""
    result = []
    for chat in conversations[:10]:
        for message in backend.get_messages(chat, limit=30):
            # OCR is not a reliable platform identity or timestamp. Never invent either.
            if message.chat != chat:
                raise AdapterError('conversation_mismatch')
            if message.sender == 'self' or message.type != 'text':
                continue
            text = str(getattr(message, 'content', '') or '')[:12000]
            if not text.strip():
                continue
            sender = str(getattr(message, 'sender', '') or '未核实发言者')[:200]
            key = hashlib.sha256((sender + '\n' + text).encode()).hexdigest()
            result.append(Capture(event_id=key, text=text, author=sender, account=account,
                                  conversation=chat, identity_verified=False))
    return result
