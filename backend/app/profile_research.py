"""Read only an author's own public GitHub profile README, never arbitrary linked sites."""
import base64
import json
import re
from datetime import datetime, timezone, timedelta
import httpx
from sqlalchemy import select, func
from .db import Session, Lead, LeadMarket, ChannelRun, ContactPoint, locked_control, now, event
from .market import public_profile, refresh_market
from .contact_discovery import extract_contact_text


def research_profile(lead_id):
    profile = public_profile(lead_id)
    if not profile:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with Session() as s:
        lead = s.get(Lead, lead_id)
        login = lead.author
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', login):
            return profile
        cached = profile.get('readme', {})
        # Same platform account is reliable identity evidence; do not merge across platforms.
        if cached.get('checked_at', '') <= cutoff:
            for other in s.scalars(select(LeadMarket).join(Lead, Lead.id == LeadMarket.lead_id)
                                  .where(Lead.source_key.like('github:%'), func.lower(Lead.author) == login.lower())):
                candidate = (other.profile or {}).get('readme', {})
                if candidate.get('checked_at', '') > cutoff:
                    cached = candidate
                    break
    if cached.get('checked_at', '') <= cutoff:
        cached = {'checked_at': now(), 'status': 'unavailable', 'url': f'https://github.com/{login}/{login}', 'text': ''}
        try:
            with httpx.Client(timeout=10, follow_redirects=False,
                              headers={'User-Agent': 'HuokePilot/0.4 public-contact-research', 'Accept': 'application/vnd.github+json'}) as client:
                with client.stream('GET', f'https://api.github.com/repos/{login}/{login}/readme') as response:
                    response.raise_for_status()
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 200_000:
                            raise ValueError('readme_too_large')
                data = json.loads(raw)
            expected = f'https://github.com/{login}/{login}/blob/'.lower()
            if data.get('encoding') != 'base64' or not str(data.get('html_url', '')).lower().startswith(expected):
                raise ValueError('readme_identity_mismatch')
            content = base64.b64decode(data['content'], validate=False).decode('utf-8')
            if len(content.encode()) > 75_000:
                raise ValueError('readme_too_large')
            # Keep plain source, no rendered HTML, no embedded image or external link requests.
            content = re.sub(r'```.*?```|~~~.*?~~~|<!--.*?-->', '', content, flags=re.S)
            cached.update(status='public_readme', url=data['html_url'], text=content[:12000])
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            cached['error'] = (f'HTTP {exc.response.status_code}' if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__)
    result = {**profile, 'readme': cached}
    with Session.begin() as s:
        locked_control(s)
        lead = s.get(Lead, lead_id)
        record = refresh_market(s, lead)
        record.profile = result
        extract_contact_text(s, lead, profile.get('bio', ''), profile['url'], 'github_public_bio', public_profile=True)
        if cached['status'] == 'public_readme':
            extract_contact_text(s, lead, cached['text'], cached['url'], 'github_profile_readme', public_profile=True)
    return result


def research_pending(limit=5):
    """A bounded, token-free public-evidence pass independent of the inference queue."""
    from .config import settings
    from .prospecting import pending_ranked
    if not settings().source_enabled:
        return {'status': 'disabled', 'researched': 0, 'model_calls': 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with Session.begin() as s:
        locked_control(s)
        last = s.scalar(select(ChannelRun).where(ChannelRun.channel == 'github_profiles').order_by(ChannelRun.id.desc()).limit(1))
        if last and last.started_at > (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat():
            return {'status': 'cooldown', 'researched': 0, 'model_calls': 0}
        if last and last.status == 'running':
            last.status, last.finished_at, last.error = 'interrupted', now(), 'previous_run_interrupted'
        ids, authors = [], set()
        for item in pending_ranked(s):
            lead = s.get(Lead, item['id'])
            market = s.get(LeadMarket, lead.id)
            checked = ((market.profile or {}).get('readme', {}).get('checked_at', '') if market else '')
            if not lead.source_key.startswith('github:') or checked > cutoff or lead.author.lower() in authors:
                continue
            ids.append(lead.id)
            authors.add(lead.author.lower())
            if len(ids) >= max(1, min(limit, 5)):
                break
        run = ChannelRun(channel='github_profiles')
        s.add(run);s.flush()
        run_id = run.id
        before = set(s.scalars(select(ContactPoint.id)))
    stats = {'researched': 0, 'model_calls': 0, 'public_profiles': 0, 'public_readmes': 0, 'contacts_added': 0}
    status, error = 'success', ''
    try:
        for lead_id in ids:
            profile = research_profile(lead_id)
            stats['researched'] += 1
            stats['public_profiles'] += int(profile.get('status') == 'public_profile')
            stats['public_readmes'] += int(profile.get('readme', {}).get('status') == 'public_readme')
    except Exception as exc:
        status, error = 'failed', type(exc).__name__
    with Session.begin() as s:
        stats['contacts_added'] = len(set(s.scalars(select(ContactPoint.id).where(ContactPoint.lead_id.in_(ids)))) - before)
        run = s.get(ChannelRun, run_id)
        run.status, run.finished_at, run.error, run.stats = status, now(), error, stats
        event(s, 'public_profile_research', status=status, error=error, stats=stats)
    return {'status': status, **stats}


def model_profile(profile):
    """Only relevant public text goes to AI; contact values are not needed for scoring."""
    data = {k: profile.get(k, '') for k in ('url', 'status', 'location', 'company', 'bio')}
    readme = profile.get('readme', {})
    if readme.get('status') == 'public_readme':
        # Preserve self-description near the beginning; avoid sending full badge/project lists.
        data['self_description'] = readme.get('text', '')[:2500]
        data['self_description_url'] = readme.get('url', '')
    return data
