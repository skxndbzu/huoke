"""CLI-only SMTP authentication probe. No recipients, MAIL, RCPT, DATA or message sending."""
import hashlib
import hmac
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from .config import settings
from .db import Session, ChannelRun, now, event
from .smtp_connection import authenticated_smtp


def config_fingerprint():
    cfg = settings()
    data = [cfg.smtp_host, cfg.smtp_port, cfg.smtp_mode, cfg.smtp_force_login,
            cfg.smtp_username, cfg.smtp_from, cfg.smtp_password]
    return hmac.new(cfg.operator_token.encode(), json.dumps(data).encode(), hashlib.sha256).hexdigest()


def smtp_status(s):
    cfg = settings()
    if not all((cfg.smtp_host, cfg.smtp_username, cfg.smtp_password, cfg.smtp_from)):
        return 'not_configured'
    run = s.scalar(select(ChannelRun).where(ChannelRun.channel == 'smtp_auth').order_by(ChannelRun.id.desc()).limit(1))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    if run and run.stats.get('config_fingerprint') == config_fingerprint():
        if run.status == 'success' and run.finished_at >= cutoff:
            return 'auth_verified_send_unverified'
        if run.status == 'failed':
            return 'auth_check_failed'
    return 'configured_unverified'


def check_smtp():
    with Session.begin() as s:
        run = ChannelRun(channel='smtp_auth', stats={'config_fingerprint': config_fingerprint(), 'messages_submitted': 0})
        s.add(run)
        s.flush()
        run_id = run.id
    error = ''
    try:
        with authenticated_smtp():
            pass
    except Exception as exc:
        # Provider errors can echo AUTH data or usernames: persist only the exception class.
        error = type(exc).__name__
    with Session.begin() as s:
        run = s.get(ChannelRun, run_id)
        run.status = 'failed' if error else 'success'
        run.error, run.finished_at = error, now()
        event(s, 'smtp_auth_check', run_id, status=run.status, error=error, messages_submitted=0)
    return {'status': run.status, 'error': error, 'messages_submitted': 0, 'sending_verified': False}


if __name__ == '__main__':
    result = check_smtp()
    print(json.dumps(result))
    raise SystemExit(0 if result['status'] == 'success' else 1)
