"""Password login is provisioned only by the owner, never by maintenance."""
import hashlib
import hmac
import secrets
from datetime import datetime, timezone, timedelta
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete
from pydantic import Field
from .db import Session, OwnerLogin, LoginSession, LoginThrottle, locked_control, now, event
from .schemas import Strict

COOKIE = 'huoke_session'


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()
    return salt + ':' + value


def matches(password, encoded):
    return hmac.compare_digest(password_hash(password, encoded.split(':')[0]), encoded)


def session_role(request):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    # Cookies must not permit another website to submit approved actions.
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin != str(request.base_url).rstrip('/'):
            raise HTTPException(403, '请从工作台页面提交操作')
    with Session() as s:
        session = s.get(LoginSession, hashlib.sha256(token.encode()).hexdigest())
        if session and session.expires_at > now():
            return 'reviewer'
    return None


class Credentials(Strict):
    username: str = Field(min_length=3, max_length=64, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=12, max_length=128)


class Setup(Credentials):
    current_password: str = Field(default='', max_length=128)


def register_login(app, reviewer):
    @app.get('/api/auth/status')
    def status():
        with Session() as s:
            return {'password_configured': bool(s.get(OwnerLogin, 1))}

    @app.post('/api/auth/setup')
    def setup(data: Setup, actor=Depends(reviewer)):
        with Session.begin() as s:
            locked_control(s)
            owner = s.get(OwnerLogin, 1)
            if owner and not matches(data.current_password, owner.password_hash):
                raise HTTPException(403, '修改登录设置需要当前密码')
            if not owner:
                owner = OwnerLogin(id=1, username=data.username, password_hash=password_hash(data.password))
                s.add(owner)
            else:
                owner.username, owner.password_hash = data.username, password_hash(data.password)
            s.execute(delete(LoginSession))
            event(s, 'owner_password_configured', actor=actor)
        return {'ok': True, 'login_required': True}

    @app.post('/api/auth/login')
    def login(data: Credentials, request: Request, response: Response):
        failure = False
        with Session.begin() as s:
            locked_control(s)
            t = s.get(LoginThrottle, 'owner')
            current = now()
            if t and t.until > current and t.count >= 10:
                raise HTTPException(429, '登录尝试过多，请 15 分钟后重试')
            if not t:
                t = LoginThrottle(key='owner', count=0, until='')
                s.add(t)
            if t.until <= current:
                t.count, t.until = 0, (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
            t.count += 1
            owner = s.get(OwnerLogin, 1)
            valid_password = matches(data.password, owner.password_hash if owner else password_hash('dummy-not-a-real-password', 'dummy-salt'))
            if not owner or not valid_password or not secrets.compare_digest(data.username, owner.username):
                failure = True
            else:
                t.count = 0
                raw = secrets.token_urlsafe(32)
                s.execute(delete(LoginSession).where(LoginSession.expires_at <= current))
                s.add(LoginSession(digest=hashlib.sha256(raw.encode()).hexdigest(), expires_at=(datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()))
                response.set_cookie(COOKIE, raw, httponly=True, samesite='strict', secure=request.url.scheme == 'https', max_age=43200, path='/')
        if failure:
            raise HTTPException(401, '用户名或密码不正确')
        return {'ok': True}

    @app.post('/api/auth/logout')
    def logout(request: Request, response: Response):
        raw = request.cookies.get(COOKIE, '')
        with Session.begin() as s:
            s.execute(delete(LoginSession).where(LoginSession.digest == hashlib.sha256(raw.encode()).hexdigest()))
        response.delete_cookie(COOKIE, path='/')
        return {'ok': True}
