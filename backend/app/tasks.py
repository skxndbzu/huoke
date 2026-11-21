from datetime import datetime, timezone
from math import ceil

from celery import Celery
from .config import settings
from .pipeline import sync_hn
from .delivery import dispatch_one
from .sources import AUTO_ANALYSIS_INTERVAL_SECONDS

celery = Celery('huoke', broker=settings().redis_url)
celery.conf.update(timezone='Asia/Shanghai', task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={'bounded-source-sync': {'task': 'source.sync', 'schedule': 3600},
                   'github-discovery': {'task': 'source.public', 'schedule': 3600, 'args': ['github']},
                   'v2ex-discovery': {'task': 'source.public', 'schedule': 3600, 'args': ['v2ex']},
                   'nodeseek-discovery': {'task': 'source.social', 'schedule': 3600, 'args': ['nodeseek']},
                   'linuxdo-discovery': {'task': 'source.social', 'schedule': 3600, 'args': ['linuxdo']},
                   'gitee-discovery': {'task': 'source.social', 'schedule': 3600, 'args': ['gitee']},
                   'bounded-ai-analysis': {'task': 'source.analyze_pending', 'schedule': AUTO_ANALYSIS_INTERVAL_SECONDS,
                                          'options': {'expires': AUTO_ANALYSIS_INTERVAL_SECONDS}},
                   'public-contact-research': {'task': 'source.research_pending', 'schedule': 3600},
                   'outbox-tick': {'task': 'outbox.tick', 'schedule': 15}},
    task_soft_time_limit=180, task_time_limit=240)


@celery.task(name='source.sync')
def sync_source():
    return sync_hn()


def retry_near_cooldown(task, result):
    # An on-time hourly tick may reach the worker before the previous run's hourly gate.
    if result['status'] == 'cooldown' and not task.request.retries:
        allowed = datetime.fromisoformat(result['next_allowed_at'])
        wait = (allowed - datetime.now(timezone.utc)).total_seconds()
        if wait <= 120:
            raise task.retry(countdown=max(1, ceil(wait) + 1), expires=180)
    return result


@celery.task(name='source.social', bind=True, max_retries=1)
def sync_social_source(self, platform):
    from .social import sync_social
    return retry_near_cooldown(self, sync_social(platform))


@celery.task(name='outbox.tick')
def outbox_tick():
    return dispatch_one()


@celery.task(name='source.public', bind=True, max_retries=1)
def sync_public_source(self, channel):
    from .sources import sync_public
    return retry_near_cooldown(self, sync_public(channel))


@celery.task(name='source.analyze_pending')
def analyze_pending_source():
    from .sources import analyze_pending
    return analyze_pending()


@celery.task(name='source.research_pending')
def research_pending_source():
    from .profile_research import research_pending
    return research_pending()
