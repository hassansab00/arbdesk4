"""Pre-day experiment: no labels, market or target-day observations in inputs."""
import copy
import datetime as dt
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from wxpredict import early_daymax as early


def record(day='2026-09-01', unit='C'):
    date = dt.date.fromisoformat(day)
    local = dt.datetime.combine(date-dt.timedelta(days=1), dt.time(18, 1), dt.timezone.utc)
    return dict(row=dict(city_key='synthetic', station='TEST', unit=unit, date=day,
                         decision_local=local.isoformat(timespec='minutes'),
                         decision_utc=int(local.timestamp()), day_offset=-1, local_hour=18,
                         event_available_from=local.timestamp()-86400,
                         fc_models_mean_c=20., fc_bm_tmax_c=19.),
                bands=[(None, 20.), (20., 21.), (21., None)], winner=1, market=[.1, .6, .3])


@pytest.mark.parametrize('field,value', [('day_offset', 0), ('local_hour', 12),
    ('decision_utc', 1), ('decision_local', '2026-09-01T18:01+00:00'),
    ('decision_local', '2026-08-31T18:01'), ('unit', 'unknown'), ('station', ''),
    ('event_available_from', None), ('event_available_from', float('inf')),
    ('event_available_from', 9999999999)])
def test_rejects_non_pre_day_or_ambiguous_decisions(field, value):
    r = record(); r['row'][field] = value
    with pytest.raises(ValueError): early.validate(r)


def test_exact_venue_labels_tails_and_market_pair_denominator():
    a, b = record(), record('2026-09-02')
    a['winner'] = 0; a['row']['fc_models_mean_c'] = -40
    b['winner'] = 2; b['row']['fc_models_mean_c'] = 100; b['market'] = None
    s = early.summarize([a, b], [[.9, .05, .05], [.1, .1, .8]])
    assert s['candidate']['hits'] == 2 and s['raw_ensemble_point']['hits'] == 2
    assert s['matched_market']['n'] == 1
    assert s['matched_market']['candidate']['n'] == s['matched_market']['market']['n'] == 1
    assert s['matched_market']['market']['hits'] == 0
    r = record(unit='F'); r['row']['fc_models_mean_c'] = -22.5
    r['bands'] = [(None, -8.), (-8., -6.), (-6., None)]
    assert early.summarize([r], [[.1, .8, .1]])['raw_ensemble_point']['hits'] == 1
    for change in ({'winner': 3}, {'bands': [(None, 20), (21, None)]},
                   {'market': [.5, .5]}, {'market': [.1, .1, .1]}):
        invalid = record(); invalid.update(change)
        with pytest.raises(ValueError): early.validate(invalid)


def test_chronological_split_never_uses_repeated_or_future_dates():
    rows = [record((dt.date(2026, 8, 1)+dt.timedelta(days=i)).isoformat()) for i in range(40)]
    train, test = early.windows(rows, '2026-08-20', '2026-08-24', '2026-08-30')
    assert len(train) == 20 and len(test) == 7
    assert max(r['row']['date'] for r in train) < min(r['row']['date'] for r in test)
    for args in [('2026-08-24','2026-08-24','2026-08-30'),
                 ('2026-08-19','2026-08-24','2026-08-30'),
                 ('2026-08-20','2026-08-24','2026-08-29')]:
        with pytest.raises(ValueError): early.windows(rows, *args)
    with pytest.raises(ValueError, match='duplicate'):
        early.windows(rows+[copy.deepcopy(rows[0])], '2026-08-20','2026-08-24','2026-08-30')


def test_no_label_market_or_target_day_feature_leakage():
    pd = pytest.importorskip('pandas')
    a = record(); changed = copy.deepcopy(a)
    changed.update(winner=0, market=[.9, .05, .05])
    changed['row'].update(label_unit=-999, station_max_c=-999, rmax_c=-999,
                          rmax_unit=-999, t_now_c=-999, mkt_top=0, winner=0,
                          observation_contract='changed', event_id='changed')
    before, y, w, sizes = early.matrix([a], labels=True)
    after, _, _, _ = early.matrix([changed])
    pd.testing.assert_frame_equal(before, after)
    assert y == [0, 1, 0] and sum(w) == pytest.approx(1) and sizes == [3]
    assert early.NUMERIC and set(early.CATEGORICAL) <= set(before)


def test_real_shared_model_outputs_valid_ladders_and_rejects_invalid_output():
    np = pytest.importorskip('numpy')
    pytest.importorskip('pandas'); pytest.importorskip('lightgbm')
    rows = [record((dt.date(2026, 8, 1)+dt.timedelta(days=i)).isoformat()) for i in range(25)]
    model = early.fit(rows)
    result = early.predict(model, [record()])[0]
    assert len(result) == 3 and sum(result) == pytest.approx(1) and all(0 < p < 1 for p in result)

    class Broken:
        def feature_name(self): return model.feature_name()
        def predict(self, *_args, **_kwargs): return np.array([np.nan]*3)
    with pytest.raises(ValueError, match='output'): early.predict(Broken(), [record()])


def test_builder_uses_city_clock_and_no_target_day_reports(monkeypatch):
    from wxpredict import build_table as bt, features as feat, common
    from zoneinfo import ZoneInfo
    e = dict(event_id='synthetic', city='synthetic', date='2026-11-02', unit='C',
             station='TEST', bands=[(None, 20), (20, 21), (21, None)], winner=1)
    monkeypatch.setattr(bt, 'load_station_daily', lambda: ({'TEST': {}}, {}))
    monkeypatch.setattr(bt, 'load_events', lambda *_: ([e], {}, {}))
    monkeypatch.setattr(bt, 'load_daily_forecasts', lambda: ({}, {}))
    monkeypatch.setattr(bt, 'load_hourly_forecasts', lambda: {})
    monkeypatch.setattr(bt, 'load_prices', lambda _: {'synthetic': []})
    monkeypatch.setattr(common, 'market_rows', lambda _: iter([{'event_id':'synthetic','created_at':'2026-11-01T00:00:00Z'}]))
    monkeypatch.setattr(common, 'cities', lambda: {'synthetic': {}})
    monkeypatch.setattr(common, 'zone', lambda _: ZoneInfo('America/New_York'))
    monkeypatch.setattr(bt, 'load_reports', lambda *_: pytest.fail('must not load target-day reports'))

    def forecasts(v, city, day, t, tz, bm, md, hourly, rep):
        assert not rep.t
        assert dt.datetime.fromtimestamp(t, tz).isoformat() == '2026-11-01T18:01:00-05:00'
        v['fc_models_mean_c'] = 20
    monkeypatch.setattr(feat, 'fc_features', forecasts)
    previous = common.READ
    rows, meta = early.build_records('2026-10-01','2026-11-03')
    assert rows[0]['winner'] == 1 and rows[0]['market'] is None
    assert rows[0]['row']['decision_local'].endswith('-05:00')
    assert common.READ is previous and not meta['feature_exclusions']
    for stamp, reason in [(None, 'missing or invalid event start'),
                          ('2026-11-02T00:00:00Z', 'event opens after checkpoint')]:
        monkeypatch.setattr(common, 'market_rows', lambda _, stamp=stamp: iter([
            {'event_id':'synthetic','created_at':stamp}]))
        rows, meta = early.build_records('2026-10-01','2026-11-03')
        assert rows == [] and meta['feature_exclusions'] == {reason:1}
        assert common.READ is previous
