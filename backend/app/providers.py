import ipaddress
import os
from pathlib import Path
import re
import secrets
import socket
from types import SimpleNamespace
from urllib.parse import urlparse
import httpx
from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from .db import Session, ModelProvider, locked_control, event, now
from .config import settings
from .schemas import Strict


def validate_endpoint(url, allow_http=False):
    p = urlparse(url)
    if p.scheme not in (('https', 'http') if allow_http else ('https',)) or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError('使用无用户名、查询参数的 HTTPS API 地址；HTTP 必须明确允许')
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80))
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('禁止连接内网、回环或元数据地址')
    except (OSError, ValueError):
        raise ValueError('地址未解析到可用公网服务') from None
    return url.rstrip('/')


def read_secret(ref):
    if not re.fullmatch(r'[a-f0-9]{32}', ref):
        raise ValueError('无效密钥引用')
    return (Path(settings().secret_store_path) / ref).read_text(encoding='utf-8').strip()


def write_secret(value):
    folder = Path(settings().secret_store_path)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    ref = secrets.token_hex(16)
    fd = os.open(folder / ref, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(value.strip())
    return ref


def model_settings():
    cfg = settings()
    with Session() as s:
        p = s.scalar(select(ModelProvider).where(ModelProvider.active.is_(True)))
        if not p:
            return cfg
        values = cfg.model_dump()
        values.update(llm_base_url=p.base_url, llm_model=p.model, llm_api_key=read_secret(p.secret_ref),
            llm_input_per_million=p.input_price, llm_output_per_million=p.output_price,
            llm_cached_per_million=p.cached_price, provider_id=p.id, provider_version=p.version,
            provider_allow_http=p.allow_http, price_source=p.price_source)
        return SimpleNamespace(**values)


def public_provider(p):
    return {k: getattr(p, k) for k in ('id','name','base_url','model','input_price','output_price','cached_price','price_source','allow_http','active','version','probe_status','probed_at','analysis_verified')} | {'key_configured': bool(p.secret_ref)}


class ProviderInput(Strict):
    version: int = 0
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(max_length=1000)
    model: str = Field(min_length=1, max_length=150)
    api_key: str = Field(default='', max_length=500)
    input_price: float = Field(gt=0, le=1000)
    output_price: float = Field(gt=0, le=1000)
    cached_price: float = Field(ge=0, le=1000)
    price_source: str = Field(min_length=5, max_length=1000)
    allow_http: bool = False


def save_provider(data, provider_id=None):
    base = validate_endpoint(data.base_url, data.allow_http)
    if data.cached_price > data.input_price:
        raise HTTPException(422, '缓存输入单价不能高于输入单价')
    with Session.begin() as s:
        locked_control(s)
        p = s.get(ModelProvider, provider_id) if provider_id else None
        if provider_id and (not p or data.version != p.version):
            raise HTTPException(409, '配置版本已变化，请刷新')
        if not p:
            if not data.api_key.strip():
                raise HTTPException(422, '新增服务需要密钥')
            p = ModelProvider(secret_ref=write_secret(data.api_key))
            s.add(p)
        elif data.api_key.strip():
            p.secret_ref = write_secret(data.api_key)
        for k in ('name','model','input_price','output_price','cached_price','price_source','allow_http'):
            setattr(p, k, getattr(data, k))
        p.base_url, p.version = base, data.version + 1
        # Require re-verification after edits before using changed credentials/endpoints.
        p.active, p.probe_status, p.analysis_verified = False, 'unverified', False
        s.flush()
        event(s, 'model_provider_saved', p.id, version=p.version)
        return public_provider(p)


def probe_provider(provider_id):
    with Session() as s:
        p = s.get(ModelProvider, provider_id)
        if not p:
            raise HTTPException(404, '服务不存在')
        version, base, model, key, allow = p.version, p.base_url, p.model, read_secret(p.secret_ref), p.allow_http
    status, models = 'failed', []
    try:
        validate_endpoint(base, allow)
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', base + '/models', headers={'Authorization': f'Bearer {key}'}) as r:
                r.raise_for_status()
                raw = bytearray()
                for chunk in r.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1_000_000:
                        raise ValueError('models_response_too_large')
                import json
                models = [v['id'] for v in json.loads(raw).get('data', []) if isinstance(v.get('id'), str)][:300]
                status = 'models_verified' if model in models else 'model_not_listed'
    except Exception as exc:
        status = type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            status += ':' + str(exc.response.status_code)
    with Session.begin() as s:
        locked_control(s)
        p = s.get(ModelProvider, provider_id)
        if p.version != version:
            raise HTTPException(409, '验证期间配置发生变化')
        p.probe_status, p.probed_at = status, now()
        event(s, 'model_provider_probe', provider_id, status=status)
    return {'status': status, 'models': models, 'inference_verified': False}


def activate_provider(provider_id):
    with Session.begin() as s:
        locked_control(s)
        p = s.get(ModelProvider, provider_id)
        if not p or p.probe_status != 'models_verified':
            raise HTTPException(409, '请先验证接口和模型列表')
        for other in s.scalars(select(ModelProvider)):
            other.active = other.id == provider_id
        event(s, 'model_provider_activated', provider_id, version=p.version)
    return {'ok': True}


def register_providers(app, role, reviewer):
    @app.get('/api/providers')
    def listing(actor=Depends(role)):
        with Session() as s:
            return [public_provider(p) for p in s.scalars(select(ModelProvider).order_by(ModelProvider.id))]

    @app.post('/api/providers')
    def add(data: ProviderInput, actor=Depends(reviewer)):
        try:
            return save_provider(data)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.put('/api/providers/{provider_id}')
    def edit(provider_id: int, data: ProviderInput, actor=Depends(reviewer)):
        try:
            return save_provider(data, provider_id)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.post('/api/providers/{provider_id}/probe')
    def probe(provider_id: int, actor=Depends(reviewer)):
        return probe_provider(provider_id)

    @app.post('/api/providers/{provider_id}/activate')
    def activate(provider_id: int, actor=Depends(reviewer)):
        return activate_provider(provider_id)
