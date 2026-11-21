"""Local read-only acquisition. No upstream AI bot, publishing or send methods."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import httpx
from app.social_adapters import FEEDS, feed_items, qq_items, v2ex_profile, xhs_items, wechat_items, AdapterError, V2EXProfile

PROJECT_TUNNEL = 'http://127.0.0.1:18081'
WORKBENCH_RETRY_SECONDS = 30


def private_url(value):
    p = urlparse(value)
    if p.scheme not in ('http', 'https') or p.username or p.password or p.query or p.fragment:
        raise ValueError('Use HTTPS or a loopback SSH tunnel; no credentials in URL')
    if p.scheme == 'http' and p.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('HTTP must use loopback')
    return value.rstrip('/')


def save_state(path, state):
    temp = path.with_suffix('.tmp')
    with open(temp, 'w', encoding='utf-8') as f:
        os.chmod(temp, 0o600)
        json.dump(state, f, ensure_ascii=False)
    temp.replace(path)


def research_v2ex_profiles(api, reader):
    route = '/api/social/v2ex/profiles'
    response = api.get(route)
    response.raise_for_status()
    jobs = response.json()
    totals = {'checked': 0, 'public_profiles': 0, 'contacts_added': 0}
    for job in jobs['items'][:3]:
        username = job['username']
        try:
            profile = v2ex_profile(reader, username)
        except (httpx.HTTPError, ValueError):
            profile = V2EXProfile(username=username, status='unavailable')
        payload = {**profile.model_dump(), 'version': jobs['version']}
        uploaded = api.post(f'{route}/{job["id"]}', json=payload)
        if uploaded.status_code == 409:
            break
        uploaded.raise_for_status()
        result = uploaded.json()
        totals['checked'] += 1
        totals['public_profiles'] += result['status'] == 'public_profile'
        totals['contacts_added'] += result['contacts_added']
    return totals


def ssh_operator_token(platform, loop):
    remote = "docker exec huoke-staging-web-1 python -c 'import os; print(os.environ[\"OPERATOR_TOKEN\"])'"
    while True:
        try:
            token = subprocess.check_output(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                                             'admin@117.72.35.216', remote], text=True, timeout=25,
                                            stderr=subprocess.DEVNULL).strip()
            if token:
                return token
        except (subprocess.SubprocessError, OSError):
            pass
        if not loop:
            raise SystemExit('Unable to load the project operator token over SSH')
        print(json.dumps({'platform': platform, 'status': 'workbench_unavailable', 'code': 'operator_ssh_unavailable'}), flush=True)
        time.sleep(WORKBENCH_RETRY_SECONDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=['qq', 'wechat', 'xiaohongshu', *FEEDS], required=True)
    parser.add_argument('--server', default=PROJECT_TUNNEL)
    parser.add_argument('--bridge', default='')
    parser.add_argument('--wechat-source', help='Pinned upstream xiaoli_desktop directory; imports wx_backend only')
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--ssh-operator', action='store_true', help='Fetch only OPERATOR_TOKEN using authorized SSH')
    args = parser.parse_args()
    server = private_url(args.server)
    bridge = private_url(args.bridge or ('http://127.0.0.1:' + ('3010' if args.platform == 'qq' else '18060')))
    if args.ssh_operator and server != PROJECT_TUNNEL:
        parser.error('--ssh-operator requires the project SSH tunnel at ' + PROJECT_TUNNEL)
    folder = ROOT / '.local' / 'social-executor'
    folder.mkdir(parents=True, exist_ok=True)
    lock = open(folder / (args.platform + '.lock'), 'a+b')
    lock.seek(0)
    if os.name == 'nt':
        import msvcrt
        if not os.fstat(lock.fileno()).st_size:
            lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    token = ssh_operator_token(args.platform, args.loop) if args.ssh_operator else os.environ.get('HUOKE_OPERATOR_TOKEN', '')
    if not token:
        raise SystemExit('Set HUOKE_OPERATOR_TOKEN or use --ssh-operator with an SSH tunnel')
    path = folder / (args.platform + '.json')
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    bridge_token = os.environ.get('HUOKE_QQ_TOKEN' if args.platform == 'qq' else 'HUOKE_XHS_TOKEN', '') if args.platform in ('qq','xiaohongshu') else ''
    bridge_headers = {'Authorization': 'Bearer ' + bridge_token} if bridge_token else {}
    backend = None
    interval = 60 if args.platform in ('qq', 'wechat') else 3600
    with httpx.Client(base_url=server, headers={'Authorization': 'Bearer ' + token}, timeout=30, follow_redirects=False, trust_env=False) as api, \
            httpx.Client(timeout=45, headers=bridge_headers, follow_redirects=False, trust_env=args.platform in FEEDS) as reader:
        while True:
            config = None
            connecting_to_workbench = True
            delay = interval
            try:
                response = api.get('/api/social/channels'); response.raise_for_status()
                config = next(x for x in response.json() if x['platform'] == args.platform)
                if not config['enabled']:
                    print(json.dumps({'platform': args.platform, 'status': 'disabled'}), flush=True)
                else:
                    scope = hashlib.sha256(json.dumps([config['account'], config['conversations']], sort_keys=True).encode()).hexdigest()
                    cursors = state.get('cursors', {}) if state.get('scope') == scope else {}
                    pending = state.get('pending')
                    if pending and pending['version'] != config['version']:
                        pending = None
                    if not pending:
                        connecting_to_workbench = False
                        next_cursors = cursors
                        if args.platform == 'qq':
                            items, next_cursors = qq_items(reader, bridge, config['account'], config['conversations'], cursors)
                        elif args.platform == 'xiaohongshu':
                            items = xhs_items(reader, bridge, config['queries'])
                        elif args.platform == 'wechat':
                            if not args.wechat_source:
                                raise AdapterError('wechat_backend_not_installed')
                            if backend is None:
                                sys.path.insert(0, str(Path(args.wechat_source).resolve()))
                                from wx_backend import create_backend
                                backend = create_backend('visual')
                            items = wechat_items(backend, config['account'], config['conversations'])
                        else:
                            items = feed_items(args.platform, reader)
                        pending = {'version': config['version'], 'mode': 'executor', 'items': [x.model_dump() for x in items]}
                        state = {'scope': scope, 'cursors': cursors, 'next_cursors': next_cursors, 'pending': pending}
                        save_state(path, state)
                    connecting_to_workbench = True
                    totals = {'listed': 0, 'new_leads': 0, 'filtered': 0, 'cache_hits': 0}
                    # Failed/unknown uploads retain the complete batch; stable IDs make retries safe.
                    batches = [pending['items'][i:i+200] for i in range(0, len(pending['items']), 200)] or [[]]
                    for batch in batches:
                        response = api.post('/api/social/channels/' + args.platform + '/capture',
                                            json={**pending, 'items': batch})
                        response.raise_for_status()
                        for k in totals:
                            totals[k] += response.json().get(k, 0)
                    state = {'scope': scope, 'cursors': state.get('next_cursors', cursors)}
                    save_state(path, state)
                    print(json.dumps({'platform': args.platform, 'status': 'success', **totals}), flush=True)
                    if args.platform == 'v2ex':
                        try:
                            profiles = research_v2ex_profiles(api, reader)
                            print(json.dumps({'platform': 'v2ex_profiles', 'status': 'finished', **profiles}), flush=True)
                        except (httpx.HTTPError, ValueError, KeyError) as exc:
                            print(json.dumps({'platform': 'v2ex_profiles', 'status': 'failed', 'code': type(exc).__name__}), flush=True)
            except Exception as exc:
                code = str(exc) if isinstance(exc, AdapterError) else type(exc).__name__
                status = 'login_required' if code == 'login_required' else 'unavailable'
                if connecting_to_workbench and isinstance(exc, httpx.TransportError):
                    status = 'workbench_unavailable'
                    delay = min(interval, WORKBENCH_RETRY_SECONDS)
                if isinstance(exc, httpx.HTTPStatusError):
                    code = 'HTTP_' + str(exc.response.status_code)
                    if exc.response.status_code in (401, 403, 429):
                        status = 'verification_required'
                print(json.dumps({'platform': args.platform, 'status': status, 'code': code}), flush=True)
                if config and config['enabled'] and status != 'workbench_unavailable':
                    try:
                        api.post('/api/social/channels/' + args.platform + '/status',
                                 json={'version': config['version'], 'status': status, 'code': code})
                    except Exception:
                        pass
            if not args.loop:
                break
            time.sleep(delay)
    if backend:
        backend.close()


if __name__ == '__main__':
    main()
