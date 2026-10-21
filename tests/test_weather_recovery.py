import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
import weather_outcomes as w


def test_final_evidence_uses_city_midnight_and_dst():
    utc = dt.timezone.utc
    assert not w.local_day_finished('2026-10-18', 'Asia/Riyadh', dt.datetime(2026,10,18,20,59,tzinfo=utc))
    assert w.local_day_finished('2026-10-18', 'Asia/Riyadh', dt.datetime(2026,10,18,21,tzinfo=utc))
    assert not w.local_day_finished('2026-11-01', 'America/New_York', dt.datetime(2026,11,2,4,59,tzinfo=utc))
    assert w.local_day_finished('2026-11-01', 'America/New_York', dt.datetime(2026,11,2,5,tzinfo=utc))
    with pytest.raises(w.OutcomeError, match='timezone'):
        w.local_day_finished('2026-10-18', None, dt.datetime.now(utc))


def test_long_retry_after_defers_without_retrying_early(monkeypatch):
    calls = []
    session = SimpleNamespace(get=lambda *a, **kw: calls.append(1) or SimpleNamespace(status_code=429, headers={'Retry-After':'120'}))
    monkeypatch.setattr(w.time, 'sleep', lambda _: pytest.fail('must defer instead of sleeping or retrying early'))
    with pytest.raises(w.OutcomeError, match='HTTP 429'):
        w._get(session, 'https://example.test/authority')
    assert len(calls) == 1
    # A different city on the same provider must respect that same wait.
    with pytest.raises(w.OutcomeError, match='cooldown'):
        w._get(session, 'https://example.test/another-city')
    assert len(calls) == 1


def test_recovery_request_and_retry_cannot_outlive_budget(monkeypatch):
    import common
    clock = [100.0]
    monkeypatch.setattr(w.time, 'monotonic', lambda: clock[0])
    calls = []
    def get(*args, **kwargs):
        calls.append(kwargs['timeout'])
        return SimpleNamespace(status_code=429, headers={'Retry-After': '20'})
    with common.request_deadline(110):
        with pytest.raises(w.OutcomeError, match='budget'):
            w._get(SimpleNamespace(get=get), 'https://example.test')
    assert calls == [5]
    assert common._request_end.get() is None


@pytest.mark.parametrize('budget', [0, -1, float('nan'), float('inf')])
def test_recovery_rejects_invalid_budget(budget):
    with pytest.raises(ValueError, match='finite and positive'):
        w.run(budget_seconds=budget)


def test_budget_preserves_success_and_defers_unrequested_cities(monkeypatch):
    import common
    clock = [100.0]
    monkeypatch.setattr(w.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(w, 'rest_all', lambda *a, **kw:
                        [{'city_key': c, 'timezone': 'UTC'} for c in ['a', 'b']])
    monkeypatch.setattr(w, '_existing_evidence', lambda _: {})
    monkeypatch.setattr(w, '_unparseable', lambda _: {})
    monkeypatch.setattr(w, '_load_targets', lambda *a:
        [dict(city_key=c, resolution_date='2026-10-20', market_id=c, unit='C', rules_text='rules') for c in ['a', 'b']])
    monkeypatch.setattr(w, 'parse_rule_spec', lambda *a:
                        w.RuleSpec('weather_gov_wrh', 'authority', 'STA', 'https://example.test', 'C', True))
    def collect(*args):
        clock[0] = 178.0
        return w.Outcome(Decimal('25'), None, 'hash', {}, {})
    monkeypatch.setattr(w, 'collect_one', collect)
    writes, logs = [], []
    monkeypatch.setattr(w, 'upsert', lambda table, rows, key: writes.extend((table, r) for r in rows))
    monkeypatch.setattr(w, 'log_run', lambda *a: logs.append(a))
    result = w.run(now=dt.datetime(2026,10,21,8,tzinfo=dt.timezone.utc), budget_seconds=90)
    assert result['captured'] == result['deferred'] == 1 and not result['failures']
    assert {r['city_key'] for _,r in writes} == {'a'}
    assert logs[0][1] == 'partial' and common._request_end.get() is None


def test_intraday_recovery_uses_existing_cadence_without_fitting():
    import yaml
    workflow = yaml.safe_load(open('.github/workflows/pipeline_intraday.yml'))
    steps = workflow['jobs']['intraday']['steps']
    step = next(s for s in steps if s.get('name') == 'Recover recent final weather evidence')
    assert '--budget-seconds 90' in step['run'] and '--days 3' in step['run']
    assert step['timeout-minutes'] == 3 and "dry_run != 'true'" in step['if']
    assert steps.index(step) > next(i for i,s in enumerate(steps) if s.get('name') == 'Bank the ladders just confirmed (P4.7, bands only)')


def test_provider_wait_is_honoured_and_http_date_is_supported(monkeypatch):
    responses=iter([SimpleNamespace(status_code=429,headers={'Retry-After':'5'}),SimpleNamespace(status_code=200)])
    waits=[]
    monkeypatch.setattr(w.time,'sleep',waits.append)
    assert w._get(SimpleNamespace(get=lambda *a,**kw:next(responses)),'https://example.test').status_code == 200
    assert waits == [5]
    now=dt.datetime(2026,10,18,10,tzinfo=dt.timezone.utc)
    assert w.retry_wait(SimpleNamespace(headers={'Retry-After':'Sun, 18 Oct 2026 10:00:20 GMT'}),0,now)==20


def test_run_records_ongoing_day_without_requesting_final_weather(monkeypatch):
    targets=[{'city_key':'jeddah','resolution_date':'2026-10-18','market_id':'j','rules_text':'rules','unit':'C'},
             {'city_key':'los_angeles','resolution_date':'2026-10-17','market_id':'l','rules_text':'rules','unit':'F'}]
    monkeypatch.setattr(w,'rest_all',lambda *a,**kw:[{'city_key':'jeddah','timezone':'Asia/Riyadh'},
        {'city_key':'los_angeles','timezone':'America/Los_Angeles'}])
    monkeypatch.setattr(w,'_existing_evidence',lambda _: {})
    monkeypatch.setattr(w,'_unparseable',lambda _: {})
    monkeypatch.setattr(w,'_load_targets',lambda *a: targets)
    spec=w.RuleSpec('weather_gov_wrh','named authority','STATION','https://example.test','C',True)
    monkeypatch.setattr(w,'parse_rule_spec',lambda *a:spec)
    called=[]
    def collect(session,spec,day):
        called.append(day)
        return w.Outcome(Decimal('25'),'2026-10-17T12:00:00Z','digest',{}, {})
    monkeypatch.setattr(w,'collect_one',collect)
    writes=[]
    monkeypatch.setattr(w,'upsert',lambda table,rows,key:writes.extend((table,r) for r in rows))
    monkeypatch.setattr(w,'log_run',lambda *a:None)
    result=w.run(now=dt.datetime(2026,10,18,10,tzinfo=dt.timezone.utc))
    assert called == [dt.date(2026,10,17)]
    assert result['captured']==1 and result['not_final']==1 and result['failures']==0
    assert [r['city_key'] for t,r in writes if t=='weather_resolution_evidence']==['los_angeles']


def test_evidence_only_workflow_never_fits_or_runs_heavy_steps():
    import yaml
    workflow=yaml.safe_load(open('.github/workflows/pipeline_daily.yml'))
    for step in workflow['jobs']['daily']['steps']:
        if 'uses' in step or step.get('run','').startswith('pip install'):
            continue
        if step.get('name')=='Authoritative weather outcome evidence':
            assert 'OUTCOME_CITIES' in step['env']
            continue
        assert step.get('if')=='${{ false }}' or 'inputs.evidence_only != true' in step['if']
    assert 'inputs.evidence_only != true' in workflow['jobs']['candidate']['if']
