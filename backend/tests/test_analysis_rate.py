from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from app.db import Session, Lead, LeadMarket, ChannelRun, AnalysisCall
from app.market import refresh_market, backfill, VERSION
from app.sources import analyze_pending


def enable(monkeypatch):
    monkeypatch.setattr('app.analysis.configured', lambda: True)
    backfill()


def test_rolling_hour_limits_100_attempts_and_recovers_without_resetting_database(monkeypatch):
    enable(monkeypatch)
    with Session.begin() as s:
        for i in range(101):
            lead = Lead(source='TEST ONLY', source_key=f'rate:{i}', author=f'author-{i}',
                        title='Looking for an LLM API provider', text='Looking for an LLM API provider',
                        url=f'https://example.test/{i}', fingerprint=f'rate:{i}')
            s.add(lead);s.flush();refresh_market(s, lead)
    moment = datetime(2026, 11, 18, tzinfo=timezone.utc)
    monkeypatch.setattr('app.sources.now', lambda: moment.isoformat())
    calls = []
    def simulate(lead_id, automatic=False):
        assert automatic
        calls.append(lead_id)
        with Session.begin() as s:
            s.get(LeadMarket, lead_id).assessment_version = VERSION
    monkeypatch.setattr('app.analysis.analyze', simulate)
    for _ in range(100):
        assert analyze_pending()['analyzed'] == 1
    assert len(calls) == len(set(calls)) == 100
    # The cap is persisted, rather than a process counter that a worker restart can clear.
    assert analyze_pending()['status'] == 'hourly_limit'
    moment += timedelta(seconds=3599)
    assert analyze_pending()['status'] == 'hourly_limit'
    moment += timedelta(seconds=1)
    assert analyze_pending()['analyzed'] == 1
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(ChannelRun).where(ChannelRun.channel == 'ai_auto')) == 101


def test_overlapping_ticks_do_not_start_duplicate_analysis(monkeypatch):
    enable(monkeypatch)
    calls = []
    def simulate(lead_id, automatic=False):
        calls.append(lead_id)
        assert analyze_pending()['status'] == 'already_running'
    monkeypatch.setattr('app.analysis.analyze', simulate)
    assert analyze_pending()['analyzed'] == 1 and calls == [1]


def test_budget_guard_still_blocks_and_reports_no_success(monkeypatch):
    enable(monkeypatch)
    def stop(*a, **kw):
        raise ValueError('预算上限阻止调用')
    monkeypatch.setattr('app.analysis.analyze', stop)
    result = analyze_pending()
    assert result['status'] == 'budget_blocked' and result['analyzed'] == 0
    with Session() as s:
        run = s.scalar(select(ChannelRun).where(ChannelRun.channel == 'ai_auto'))
        assert run.status == 'budget_blocked' and run.error == 'budget_limit'
        assert s.scalar(select(func.count()).select_from(AnalysisCall)) == 0


def test_expired_task_lease_recovers_but_unknown_paid_call_is_not_retried(monkeypatch):
    enable(monkeypatch)
    moment = datetime.now(timezone.utc)
    with Session.begin() as s:
        s.add(ChannelRun(channel='ai_auto', started_at=(moment - timedelta(minutes=6)).isoformat()))
        s.add(AnalysisCall(lead_id=1, cache_key='unknown-cost', status='unknown_or_invalid', model='TEST', reserved_usd=.01))
    monkeypatch.setattr('app.analysis.analyze', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Must not retry unknown call')))
    assert analyze_pending()['status'] == 'no_candidates'
    with Session() as s:
        assert s.scalar(select(ChannelRun).where(ChannelRun.channel == 'ai_auto')).status == 'interrupted'


def test_scheduler_uses_short_expiring_ticks():
    from app.tasks import celery
    schedule = celery.conf.beat_schedule['bounded-ai-analysis']
    assert schedule['schedule'] == 36 and schedule['options']['expires'] == 36
