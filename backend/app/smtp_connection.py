"""Shared TLS/authentication path; this module cannot submit a message."""
import smtplib
import ssl
from contextlib import contextmanager
from .config import settings


@contextmanager
def authenticated_smtp():
    cfg = settings()
    if not all((cfg.smtp_host, cfg.smtp_username, cfg.smtp_password, cfg.smtp_from)):
        raise ValueError('SMTP configuration incomplete')
    if cfg.smtp_mode not in ('ssl', 'starttls'):
        raise ValueError('SMTP requires verified TLS')
    context = ssl.create_default_context()
    smtp = None
    try:
        if cfg.smtp_mode == 'ssl':
            smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=20, context=context)
            smtp.ehlo()
        else:
            smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20)
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        if cfg.smtp_force_login:
            answers = iter((cfg.smtp_username, cfg.smtp_password))
            smtp.auth('LOGIN', lambda challenge: next(answers), initial_response_ok=False)
        else:
            smtp.login(cfg.smtp_username, cfg.smtp_password)
        yield smtp
    finally:
        if smtp is not None:
            # Close the local socket. A QUIT timeout cannot invalidate an accepted DATA response.
            smtp.close()
