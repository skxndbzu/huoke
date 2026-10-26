import hashlib
import json
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from .db import Session, Message, Contact, Lead, Suppression, DeliveryAttempt, locked_control, now, event
from .config import settings

BOUND_FIELDS = ('lead_id', 'contact_id', 'recipient', 'channel', 'account', 'subject', 'body', 'attachments', 'version')
TERMINAL = {'sending', 'submitted', 'sent', 'unknown', 'cancelled', 'simulated', 'failed'}


def content_hash(message):
    payload = {field: getattr(message, field) for field in BOUND_FIELDS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def transmission_hash(message):
    # Also deduplicate cloned drafts, independent of local lead/contact/version IDs.
    payload = {field: getattr(message, field) for field in ('recipient', 'channel', 'account', 'subject', 'body', 'attachments')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def eligibility(s, m):
    c, lead = s.get(Contact, m.contact_id), s.get(Lead, m.lead_id)
    if not c or not lead or c.lead_id != m.lead_id or c.address != m.recipient:
        return '联系人与审核内容不匹配'
    if not c.verified:
        return '商务联系方式尚未人工核实'
    if lead.human_takeover:
        return '客户已人工接管'
    if s.get(Suppression, m.recipient.lower()):
        return '收件地址已拒收/退订'
    if m.attachments:
        return '当前版本不支持附件'
    if m.channel == 'smtp':
        cfg = settings()
        if not cfg.real_send_enabled:
            return '测试环境：真实发送未启用'
        if not all((cfg.smtp_host, cfg.smtp_from, cfg.smtp_username, cfg.smtp_password)):
            return 'SMTP 配置未完成'
        if m.account != cfg.smtp_from:
            return '发送账号已变化，需要重新审核'
    elif m.channel != 'simulation' or m.account != 'simulation':
        return '未知发送渠道或账号'
    return ''


def smtp_submit(m):
    cfg = settings()
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'] = cfg.smtp_from, m.recipient, m.subject
    # Stable per-content version; SMTP does not guarantee deduplication on Message-ID.
    msg['Message-ID'] = f'<huoke-{m.id}-v{m.version}@{cfg.smtp_from.split("@")[-1]}>'
    msg.set_content(m.body)
    context = ssl.create_default_context()
    if cfg.smtp_mode == 'ssl':
        smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=20, context=context)
    elif cfg.smtp_mode == 'starttls':
        smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20)
        smtp.starttls(context=context)
    else:
        raise ValueError('SMTP 必须启用 TLS')
    try:
        smtp.login(cfg.smtp_username, cfg.smtp_password)
        refused = smtp.send_message(msg)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        # QUIT failure after DATA acceptance must not change the accepted result.
        smtp.close()
    return msg['Message-ID']


def dispatch_one(sender=None):
    cfg = settings()
    with Session.begin() as s:
        control = locked_control(s)
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        for stale in s.scalars(select(Message).where(Message.status == 'sending', Message.attempted_at < cutoff)):
            stale.status, stale.result = 'unknown', '发送进程中断；必须核对平台记录，禁止自动重发'
            event(s, 'send_unknown', stale.id)
        if control.paused:
            return {'status': 'paused'}
        recent = (datetime.now(timezone.utc) - timedelta(seconds=cfg.send_interval_seconds)).isoformat()
        if control.last_send and control.last_send > recent:
            return {'status': 'rate_limited'}
        m = s.scalar(select(Message).where(Message.status == 'queued', Message.not_before <= now()).order_by(Message.id).limit(1).with_for_update())
        if not m:
            return {'status': 'empty'}
        reason = eligibility(s, m)
        if reason or m.approval_hash != content_hash(m):
            m.status, m.approval_hash, m.result = 'pending_review', '', reason or '审核内容发生变化'
            event(s, 'send_blocked', m.id, reason=m.result)
            return {'status': 'blocked'}
        delivery_key = transmission_hash(m)
        if s.get(DeliveryAttempt, delivery_key):
            m.status, m.result = 'cancelled', '相同收件人、账号和内容已有发送尝试；禁止重复发送'
            event(s, 'duplicate_send_blocked', m.id)
            return {'status': 'duplicate_blocked'}
        s.add(DeliveryAttempt(fingerprint=delivery_key, message_id=m.id))
        m.status, m.attempted_at = 'sending', now()
        control.last_send = now()
        message_id = m.id
        event(s, 'send_started', m.id, version=m.version)
    # Durable sending state is committed BEFORE external I/O. If killed afterwards,
    # recovery marks unknown and never blindly retries. Control lock serializes final
    # checks against pause, suppression, edits and human takeover.
    with Session.begin() as s:
        control = locked_control(s)
        m = s.get(Message, message_id)
        if m.status != 'sending':
            return {'status': m.status}
        reason = eligibility(s, m)
        if control.paused or reason or m.approval_hash != content_hash(m):
            m.status, m.approval_hash, m.result = 'pending_review', '', reason or '暂停或审核失效'
            return {'status': 'blocked'}
        try:
            if m.channel == 'simulation':
                m.provider_id, m.status, m.result = f'simulation:{m.id}:{m.version}', 'simulated', '模拟完成：没有向外部发送消息'
            else:
                m.provider_id = (sender or smtp_submit)(m)
                m.status, m.result = 'submitted', 'SMTP 服务器已接受；未获得送达或已读回执'
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
            m.status, m.result = 'failed', f'服务器明确拒绝：{type(exc).__name__}；不自动重试'
        except Exception as exc:
            m.status, m.result = 'unknown', f'{type(exc).__name__}：结果未知，先核对平台记录，禁止自动重发'
        event(s, 'send_result', m.id, status=m.status, provider_id=m.provider_id)
        return {'id': m.id, 'status': m.status}
