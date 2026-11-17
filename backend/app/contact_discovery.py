"""Extract only explicitly published contact candidates. Never infer or verify addresses."""
import re
from sqlalchemy import select
from .db import ContactPoint
from .schemas import ContactInput

BUSINESS = re.compile(r'商务|合作|业务联系|business|partnership|contact me|联系邮箱', re.I)
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
SOCIAL = {
    'wechat': re.compile(r'(?:微信(?:号|\s*ID)?|WeChat(?:\s*ID)?)\s*[:：]\s*([A-Za-z][A-Za-z0-9_-]{5,19})(?![A-Za-z0-9_-])', re.I),
    'qq': re.compile(r'(?<![A-Za-z])QQ(?:号)?\s*[:：]\s*([1-9][0-9]{4,11})(?![0-9])', re.I),
}


def save_candidate(s, lead, kind, value, source_url, evidence, origin):
    if kind == 'email':
        if 'noreply' in value.lower() or 'no-reply' in value.lower() or value.lower().endswith(('.test','@example.com')):
            return False
        try:
            value = ContactInput(address=value, source_url=source_url, basis=evidence).address
        except ValueError:
            return False
    if s.scalar(select(ContactPoint.id).where(ContactPoint.lead_id == lead.id, ContactPoint.kind == kind, ContactPoint.value == value)):
        return False
    s.add(ContactPoint(lead_id=lead.id, kind=kind, value=value, source_url=source_url,
                       evidence=evidence[:2000], origin=origin, verified=False))
    return True


def extract_contacts(s, lead):
    return extract_contact_text(s, lead, lead.text, lead.url, 'public_source')


def extract_contact_text(s, lead, text, source_url, origin, public_profile=False):
    count = 0
    for m in EMAIL.finditer(text):
        snippet = text[max(0,m.start()-80):m.end()+80]
        explicit_profile_contact = public_profile and re.search(r'联系|邮箱|email|e-mail|mailto:|reach me|contact me', snippet, re.I)
        if BUSINESS.search(snippet) or explicit_profile_contact:
            count += save_candidate(s,lead,'email',m.group(0).lower(),source_url,snippet,origin)
    for kind, pattern in SOCIAL.items():
        for m in pattern.finditer(text):
            snippet = text[max(0,m.start()-60):m.end()+60]
            # Explicit account labels only; group invitations and guessed identities are not contacts.
            prefix = text[max(0,m.start()-15):m.start()]
            if re.search(r'群|group', prefix, re.I):
                continue
            count += save_candidate(s,lead,kind,m.group(1),source_url,snippet,origin)
    return count
