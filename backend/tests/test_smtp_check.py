import json
import smtplib
import ssl
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import select
from app.config import settings
from app.db import Session, ChannelRun, Control, Message
from app.smtp_check import check_smtp, smtp_status
from app.smtp_connection import authenticated_smtp


@pytest.fixture
def smtp_config(monkeypatch):
    cfg = settings()
    for key, value in {'smtp_host':'smtp.example.test', 'smtp_port':587,
        'smtp_mode':'starttls', 'smtp_force_login':True,
        'smtp_username':'owner@example.test', 'smtp_from':'owner@example.test',
        'smtp_password':'test-sensitive-secret'}.items():
        monkeypatch.setattr(cfg, key, value)
    return cfg


class FakeSMTP:
    def __init__(self, *args, **kwargs):
        self.actions = []
        if 'context' in kwargs:
            self.check_context(kwargs['context'])
            self.actions.append('ssl')

    def check_context(self, context):
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED

    def ehlo(self):
        self.actions.append('ehlo')

    def starttls(self, *, context):
        self.check_context(context)
        self.actions.append('starttls')

    def auth(self, mechanism, callback, *, initial_response_ok):
        assert mechanism == 'LOGIN' and initial_response_ok is False
        assert callback(b'Username:') == 'owner@example.test'
        assert callback(b'Password:') == 'test-sensitive-secret'
        self.actions.append('auth_login')

    def login(self, user, password):
        self.actions.append('login')

    def close(self):
        self.actions.append('close')

    def send_message(self, *args, **kwargs):
        pytest.fail('Authentication diagnostics must not submit any message')


def test_probe_verifies_tls_before_auth_without_sending(smtp_config, monkeypatch, client):
    smtp = FakeSMTP()
    monkeypatch.setattr('app.smtp_connection.smtplib.SMTP', lambda *a, **kw: smtp)
    result = check_smtp()
    assert result == {'status':'success','error':'','messages_submitted':0,'sending_verified':False}
    assert smtp.actions == ['ehlo','starttls','ehlo','auth_login','close']
    response = client.get('/api/overview').json()
    assert response['integrations']['smtp'] == 'auth_verified_send_unverified'
    assert response['paused'] is True and response['real_send_enabled'] is False
    with Session() as s:
        assert list(s.scalars(select(Message))) == []
        stats = s.scalar(select(ChannelRun)).stats
        assert smtp_config.smtp_password not in json.dumps(stats)


def test_tls_failure_never_attempts_auth(smtp_config, monkeypatch):
    smtp = FakeSMTP()
    def fail(**kwargs):
        raise ssl.SSLCertVerificationError('test certificate failure')
    smtp.starttls = fail
    monkeypatch.setattr('app.smtp_connection.smtplib.SMTP', lambda *a, **kw: smtp)
    assert check_smtp()['status'] == 'failed'
    assert smtp.actions == ['ehlo','close']


def test_auth_error_redacts_provider_content(smtp_config, monkeypatch):
    smtp = FakeSMTP()
    def fail(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, smtp_config.smtp_password.encode())
    smtp.auth = fail
    monkeypatch.setattr('app.smtp_connection.smtplib.SMTP', lambda *a, **kw: smtp)
    result = check_smtp()
    assert result['error'] == 'SMTPAuthenticationError'
    with Session() as s:
        run = s.scalar(select(ChannelRun))
        assert smtp_config.smtp_password not in json.dumps([result, run.stats, run.error])
        assert smtp_status(s) == 'auth_check_failed'
    assert smtp.actions[-1] == 'close'


def test_changed_credentials_and_stale_probe_are_not_verified(smtp_config, monkeypatch):
    monkeypatch.setattr('app.smtp_connection.smtplib.SMTP', FakeSMTP)
    assert check_smtp()['status'] == 'success'
    with Session() as s:
        assert smtp_status(s) == 'auth_verified_send_unverified'
        monkeypatch.setattr(smtp_config, 'smtp_password', 'changed')
        assert smtp_status(s) == 'configured_unverified'
        monkeypatch.setattr(smtp_config, 'smtp_password', 'test-sensitive-secret')
    with Session.begin() as s:
        s.scalar(select(ChannelRun)).finished_at = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    with Session() as s:
        assert smtp_status(s) == 'configured_unverified'


def test_plaintext_mode_rejected_before_connect(smtp_config, monkeypatch):
    monkeypatch.setattr(smtp_config, 'smtp_mode', 'plain')
    monkeypatch.setattr('app.smtp_connection.smtplib.SMTP', lambda *a, **kw: pytest.fail('Must not connect'))
    assert check_smtp()['error'] == 'ValueError'


def test_ssl_and_standard_auth_supported(smtp_config, monkeypatch):
    monkeypatch.setattr(smtp_config, 'smtp_mode', 'ssl')
    monkeypatch.setattr(smtp_config, 'smtp_force_login', False)
    smtp = FakeSMTP(context=ssl.create_default_context())
    monkeypatch.setattr('app.smtp_connection.smtplib.SMTP_SSL', lambda *a, **kw: smtp)
    with authenticated_smtp():
        pass
    assert smtp.actions == ['ssl','ehlo','login','close']
