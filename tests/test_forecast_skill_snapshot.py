import copy

import common
import probability_engine as pe


def row(city, lead=1, **kw):
    return dict(city_key=city, lead_days=lead, n_days=30, mae_c=1.2, bias_c=-.1,
                computed_at='2026-11-06T00:00:00+00:00', evidence_scope='verified_outcomes_v1', **kw)


def test_snapshot_and_historical_prefetch_select_identical_inputs(monkeypatch):
    older = dict(row('a'), computed_at='2026-11-04T00:00:00+00:00')
    newer_unverified = dict(row('a'), mae_c=9, evidence_scope=None)
    newest_verified = dict(row('a'), computed_at='2026-11-05T00:00:00+00:00')
    fallback = dict(row('b'), evidence_scope='legacy')
    model = row('a', model='nws')
    history = {'derived_forecast_skill': [newer_unverified, older, newest_verified, fallback],
               'derived_forecast_skill_model': [model]}
    monkeypatch.setattr(pe, '_skill_cache', {})
    monkeypatch.setattr(common, 'rpc', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('not installed')))
    reads = []
    monkeypatch.setattr(pe, 'rest_all', lambda table, *a, **kw: reads.append(table) or copy.deepcopy(history[table]))
    pe._warm_skill_cache()
    legacy = copy.deepcopy(pe._skill_cache)
    assert legacy[('a', 1)]['mae_c'] == 1.2
    assert legacy[('b', 1)]['verified'] is False
    calls = []
    snapshot = dict(policy='latest-forecast-skill-v1', evidence_scope=pe.VERIFIED_EVIDENCE_SCOPE,
                    pooled=[newest_verified, fallback], models=[model])
    monkeypatch.setattr(common, 'rpc', lambda name, **kw: calls.append((name, kw)) or snapshot)
    monkeypatch.setattr(pe, '_skill_cache', {})
    reads.clear()
    pe._warm_skill_cache()
    assert pe._skill_cache == legacy
    assert reads == []
    assert calls == [('forecast_skill_snapshot', {'timeout': 5})]


def test_bad_snapshot_cannot_leave_a_partial_cache(monkeypatch):
    good = row('a')
    monkeypatch.setattr(pe, '_skill_cache', {})
    monkeypatch.setattr(common, 'rpc', lambda *a, **k: dict(policy='latest-forecast-skill-v1',
        evidence_scope=pe.VERIFIED_EVIDENCE_SCOPE, pooled=[good], models=[{'city_key': 'bad'}]))
    reads = []
    def fallback(table, *a, **kw):
        assert pe._skill_cache == {}
        reads.append(table)
        return []
    monkeypatch.setattr(pe, 'rest_all', fallback)
    pe._warm_skill_cache()
    assert reads == ['derived_forecast_skill', 'derived_forecast_skill_model']
    assert pe._skill_cache == {}


def test_empty_snapshot_is_a_complete_result(monkeypatch):
    monkeypatch.setattr(pe, '_skill_cache', {})
    monkeypatch.setattr(common, 'rpc', lambda *a, **k: dict(policy='latest-forecast-skill-v1',
        evidence_scope=pe.VERIFIED_EVIDENCE_SCOPE, pooled=[], models=[]))
    monkeypatch.setattr(pe, 'rest_all', lambda *a, **k: (_ for _ in ()).throw(AssertionError('historical read')))
    pe._warm_skill_cache()
    assert pe._skill_cache == {}
