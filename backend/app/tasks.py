from celery import Celery
from .config import settings
from .pipeline import sync_hn
from .delivery import dispatch_one

celery = Celery('huoke', broker=settings().redis_url)
celery.conf.update(timezone='Asia/Shanghai', task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={'bounded-source-sync': {'task': 'source.sync', 'schedule': 3600},
                   'outbox-tick': {'task': 'outbox.tick', 'schedule': 15}},
    task_soft_time_limit=180, task_time_limit=240)


@celery.task(name='source.sync')
def sync_source():
    return sync_hn()


@celery.task(name='outbox.tick')
def outbox_tick():
    return dispatch_one()
