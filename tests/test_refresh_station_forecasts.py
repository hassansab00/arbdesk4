import copy
import datetime as dt

import pytest

import refresh_station_forecasts as rf

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 19, 9, tzinfo=UTC)


def fixture():
    city = dict(city_key='seoul', timezone='Asia/Seoul')
    md = [['seoul', 1, m, '2026-10-20', 20+i, 19+i] for i, m in enumerate(rf.hr.MODELS)]
    bm = [['seoul', 1, '2026-10-20', 25, 24, 15, 17, 10, 11, 20, 30, 2000, 10, 0, 1010, 24]]
    cells = [dict(city_key='seoul', source='open_meteo_'+m, lead_days=1, bias_c=.5, n=100, version='bias-fit',
                  as_of='2026-10-19', computed_at=NOW.isoformat()) for m in rf.hr.MODELS]
    widths = [dict(city_key='seoul', lead_days=1, sigma_c=1.2, version='width-fit',
                   as_of='2026-10-19', computed_at=NOW.isoformat())]
    coefs = [dict(city_key='seoul', lead_days=1, coef=dict.fromkeys(['intercept']+rf.sm.FEATURES, 0),
                  calibration={'applies': False}, version='mos-fit', as_of='2026-10-19', computed_at=NOW.isoformat())]
    labels = {('seoul', '2026-10-18'): 21}
    return [[city], bm, md, cells, widths, coefs, labels, NOW, NOW+dt.timedelta(minutes=2)]


def test_forward_reuses_exact_math_and_keeps_parameters_unchanged():
    args = fixture()
    original = copy.deepcopy(args)
    raw, p39, mos = rf.build(*args)
    assert len(raw) == len(rf.hr.MODELS) and len(p39) == len(mos) == 1
    assert p39[0]['combined_c'] == pytest.approx(sum(r[4]+.5 for r in args[2])/len(args[2]))
    assert p39[0]['width_c'] == 1.2 and mos[0]['calibration'] == args[5][0]['calibration']
    assert mos[0]['mos_c'] == pytest.approx(sum(r[5] for r in args[2])/len(args[2]))
    assert mos[0]['blend_c'] == pytest.approx(round((mos[0]['mos_c']+p39[0]['combined_c'])/2, 3))
    assert mos[0]['p39_version'] == p39[0]['version'] != 'bias-fit'
    assert p39[0]['inputs_oldest_run_at'] == args[-1].isoformat()
    assert {r['model'] for r in raw} == {r['source'] for r in args[3]}
    assert args == original
    args[-1] += dt.timedelta(minutes=1)
    assert rf.build(*args)[1][0]['version'] != p39[0]['version']


@pytest.mark.parametrize('bad', ['stale', 'future', 'target-day', 'mixed', 'nan', 'few'])
def test_invalid_fits_cannot_be_laundered_into_fresh_forecasts(bad):
    args = fixture()
    if bad == 'stale':
        for r in args[3]: r['computed_at'] = (NOW-dt.timedelta(days=2)).isoformat()
    elif bad == 'future':
        for r in args[3]: r['computed_at'] = (NOW+dt.timedelta(hours=1)).isoformat()
    elif bad == 'target-day':
        for r in args[3]: r['as_of'] = '2026-10-20'
    elif bad == 'mixed': args[3][0]['version'] = 'other-night'
    elif bad == 'nan':
        for r in args[3]: r['bias_c'] = float('nan')
    else: args[3][:] = args[3][:3]
    raw, corrected, mos = rf.build(*args)
    assert raw and not corrected and not mos


def test_missing_mos_keeps_fresh_p39_but_never_reuses_old_mos():
    args = fixture()
    args[1].clear()
    raw, p39, mos = rf.build(*args)
    assert raw and p39 and not mos
    assert p39[0]['version'] != args[3][0]['version']
    writes = []
    rf.publish(raw, p39, mos, lambda table, rows, conflict: writes.append((table, rows, conflict)))
    assert [x[0] for x in writes] == ['weather_forecast_models', 'derived_corrected_forecast']
    assert writes[0][2] == 'city_key,model,run_at,for_date'


def test_publish_failure_cannot_leave_old_mos_matched_to_new_inputs():
    raw, p39, mos = rf.build(*fixture())
    writes = []
    def upsert(table, rows, conflict):
        writes.append(table)
        if table == 'derived_mos_forecast': raise RuntimeError('write failed')
    with pytest.raises(RuntimeError): rf.publish(raw, p39, mos, upsert)
    assert writes == ['weather_forecast_models', 'derived_corrected_forecast', 'derived_mos_forecast']
    assert p39[0]['version'] != 'bias-fit'


def test_local_midnight_during_fetch_skips_city_and_wrong_leads():
    args = fixture()
    args[-2] = dt.datetime(2026, 10, 19, 14, 59, tzinfo=UTC)
    args[-1] = dt.datetime(2026, 10, 19, 15, 1, tzinfo=UTC)
    assert rf.build(*args) == ([], [], [])
    args = fixture()
    for r in args[2]: r[1] = 2
    assert rf.build(*args) == ([], [], [])


@pytest.mark.parametrize('day,zone,hours', [('2026-10-20','Asia/Seoul',24),
    ('2026-10-25','Europe/Amsterdam',25), ('2026-03-29','Europe/Amsterdam',23),
    ('2026-10-20','Asia/Kolkata',24)])
def test_complete_local_days_cover_dst_and_fractional_offsets(day, zone, hours):
    start = dt.datetime.fromisoformat(day).replace(tzinfo=rf.ZoneInfo(zone)).astimezone(UTC)
    first = start.replace(minute=0)
    if first < start: first += dt.timedelta(hours=1)
    stamps = [(first+dt.timedelta(hours=i)).replace(tzinfo=None).isoformat() for i in range(hours)]
    hourly = dict(time=stamps, t=list(range(hours)))
    assert rf.complete(hourly, 't', day, zone)
    for change in ('missing', 'duplicate', 'null', 'nan'):
        bad = copy.deepcopy(hourly)
        if change == 'missing': bad['time'].pop(); bad['t'].pop()
        elif change == 'duplicate': bad['time'][-1] = bad['time'][0]
        elif change == 'null': bad['t'][12] = None
        else: bad['t'][12] = float('nan')
        assert not rf.complete(bad, 't', day, zone)


def test_fetch_rejects_incomplete_future_days_and_retains_complete_models(monkeypatch):
    city = fixture()[0][0]
    stamps = [(dt.datetime(2026, 10, 19)+dt.timedelta(hours=i)).isoformat() for i in range(96)]
    heating = dict(hourly=dict(time=stamps, **{v:[20]*96 for v in rf.hr.HEATING}))
    models = dict(hourly=dict(time=stamps, **{f'temperature_2m_{m}':[20]*96 for m in rf.hr.MODELS}))
    models['hourly'][f'temperature_2m_{rf.hr.MODELS[0]}'][30] = None
    monkeypatch.setattr(rf.hr, 'fetch_each', lambda *a: [(city, (heating, models))])
    bm, md, missing = rf.fetch([city], NOW, [])
    assert bm and len(md) == 2*len(rf.hr.MODELS)-1
    assert not any(r[1] == 1 and r[2] == rf.hr.MODELS[0] for r in md)
    assert not missing
    models['hourly']['time'] = stamps[:38]
    assert not rf.fetch([city], NOW, [])[1]


def test_engine_rejects_previous_input_blend_and_accepts_matching_refresh(monkeypatch):
    import probability_engine as pe
    _, p39, mos = rf.build(*fixture())
    selected = [dict(mos[0], p39_version='bias-fit', blend_c=99)]
    monkeypatch.setattr(pe, 'rest', lambda table, query: [dict(value=dict(enabled=True))]
                        if table == 'settings' else selected)
    key = ('seoul', '2026-10-20')
    cache = {key: copy.deepcopy(p39[0])}
    assert pe._blend_station_model(cache) == 0
    assert cache[key]['combined_c'] == p39[0]['combined_c']
    selected[:] = mos
    assert pe._blend_station_model(cache) == 1
    assert cache[key]['combined_c'] == mos[0]['blend_c']


def test_missing_completed_day_uses_the_cache_source_view_without_rewriting_labels(monkeypatch):
    cities = [dict(city_key='seoul', timezone='Asia/Seoul'),
              dict(city_key='seattle', timezone='America/Los_Angeles')]
    before = dt.datetime(2026, 10, 19, 17, tzinfo=UTC)
    cached = {('seoul', '2026-10-18'): 20}
    monkeypatch.setattr(rf.sm, 'load_labels', lambda rest: dict(cached))
    asks = []
    def rest(table, params, **kw):
        asks.append((table, params))
        return [dict(city_key='seoul', obs_date='2026-10-19', max_c=16, n_obs=30),
                dict(city_key='seattle', obs_date='2026-10-18', max_c=14, n_obs=25),
                dict(city_key='seoul', obs_date='2026-10-18', max_c=99, n_obs=25),
                dict(city_key='seoul', obs_date='2026-10-20', max_c=99, n_obs=25),
                dict(city_key='seattle', obs_date='2026-10-19', max_c=99, n_obs=25),
                dict(city_key='other', obs_date='2026-10-19', max_c=99, n_obs=25)]
    labels, detail = rf.forward_labels(rest, cities, before)
    assert labels == cached | {('seoul','2026-10-19'):16, ('seattle','2026-10-18'):14}
    assert cached == {('seoul', '2026-10-18'):20}
    assert len(detail['days']) == 2 and detail['error'] is None
    assert asks[0][0] == 'v_city_day_features'
    assert ('obs_date','gte.2026-10-18') in asks[0][1]
    assert ('obs_date','lte.2026-10-19') in asks[0][1]


@pytest.mark.parametrize('bad', [None, float('nan'), float('inf')])
def test_unavailable_or_invalid_completed_day_does_not_invent_a_feature(monkeypatch, bad):
    monkeypatch.setattr(rf.sm, 'load_labels', lambda rest: {})
    rows = [dict(city_key='seoul', obs_date='2026-10-18', max_c=bad, n_obs=24),
            dict(city_key='seoul', obs_date='2026-10-18', max_c=20, n_obs=0)]
    labels, detail = rf.forward_labels(lambda *a, **k: rows, fixture()[0], NOW)
    assert not labels and not detail['days']
    def refused(*a, **k): raise RuntimeError('source unavailable')
    labels, detail = rf.forward_labels(refused, fixture()[0], NOW)
    assert not labels and detail['error']


def test_existing_completed_cache_day_does_not_need_a_second_read(monkeypatch):
    cached = {('seoul','2026-10-18'):21}
    monkeypatch.setattr(rf.sm, 'load_labels', lambda rest: dict(cached))
    def refused(*a, **k): raise AssertionError('not needed')
    labels, detail = rf.forward_labels(refused, fixture()[0], NOW)
    assert labels == cached and detail['days'] == []
