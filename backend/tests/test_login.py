from app.db import Session, LoginSession
from sqlalchemy import select, func

CREDS={'username':'owner','password':'test-password-not-real'}


def test_owner_provisions_password_operator_cannot(client):
    assert client.post('/api/auth/setup',json=CREDS,headers={'Authorization':'Bearer test-operator'}).status_code==403
    assert client.post('/api/auth/setup',json=CREDS).status_code==200
    client.headers.pop('Authorization')
    assert client.post('/api/auth/login',json={**CREDS,'password':'incorrect-password'}).status_code==401
    r=client.post('/api/auth/login',json=CREDS)
    assert r.status_code==200 and 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    assert client.get('/api/overview').json()['role']=='reviewer'
    assert client.post('/api/control/pause',json={'paused':True},headers={'Origin':'https://evil.test'}).status_code==403
    assert client.post('/api/control/pause',json={'paused':True}).status_code==403
    assert client.post('/api/control/pause',json={'paused':True},headers={'Origin':'http://testserver'}).status_code==200
    client.post('/api/auth/logout')
    assert client.get('/api/overview').status_code==401


def test_password_change_needs_current_and_revokes_sessions(client):
    client.post('/api/auth/setup',json=CREDS)
    client.post('/api/auth/login',json=CREDS)
    assert client.post('/api/auth/setup',json={**CREDS,'password':'new-long-password'}).status_code==403
    assert client.post('/api/auth/setup',json={**CREDS,'password':'new-long-password','current_password':CREDS['password']}).status_code==200
    with Session() as s: assert s.scalar(select(func.count()).select_from(LoginSession))==0


def test_failed_logins_are_persistently_throttled(client):
    client.post('/api/auth/setup',json=CREDS)
    for _ in range(10):
        assert client.post('/api/auth/login',json={**CREDS,'password':'wrong-long-password'}).status_code==401
    assert client.post('/api/auth/login',json=CREDS).status_code==429
