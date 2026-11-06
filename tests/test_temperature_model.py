"""Synthetic contracts for independent temperature location/scale forecasting."""
import copy
import datetime as dt
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import temperature_model as m, temperature_evaluation as e, pre_day_hourly as h, pre_day_pairs as pp


def record(day='2026-08-01', city='synthetic', zone='UTC', slot='d1_eve', unit='C'):
    date = dt.date.fromisoformat(day)
    off, hour = m.SLOTS[slot]
    at = dt.datetime.combine(date+dt.timedelta(days=off), dt.time(hour, 37), ZoneInfo(zone))
    received = at-dt.timedelta(minutes=5)
    hours = {dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M'):
             [20+3*math.sin(i/24*math.pi), 15, 30, 100, 10, 0, 1010]
             for i, t in enumerate(h.target_hours(day, zone))}
    raw = dict(capture_id=1, kind='hourly0', key=city+'|snapshot', captured_at=received.isoformat(), written_at=received.isoformat(),
               payload=dict(schema='nwp-current-snapshot-v1', hours=hours))
    context, _ = h.History([raw]).select(city, day, zone, at.isoformat())
    r = dict(policy=m.POLICY, city=city, date=day, checkpoint=slot, station='TEST', timezone=zone, unit=unit,
             contract=m.CONTRACT, rules_sha256='a'*64, rules_received_at='2026-01-01T00:00:00Z',
             as_of=at.isoformat(), input_received_at=at.isoformat(), hourly=context, floor_c=None, observation=None,
             band_ids=['a', 'b', 'c'], bounds=[[None, 22], [22, 24], [24, None]],
             peak_reference=dict(city_key=city, timezone=zone, month=date.month, peak_hour_local=14., computed_at='2026-01-01T00:00:00Z'))
    if unit == 'F':
        r['bounds'] = [[None, 72], [72, 75], [75, None]]
    return m.seal(r)


def artifact():
    a = m.reference()
    a.update(available_at='2026-01-01T00:00:00Z', fit_started_at='2026-01-01T00:00:00Z')
    a['sha256'] = pp.digest({k: v for k, v in a.items() if k != 'sha256'})
    return a


@pytest.mark.parametrize('zone,day,n', [('UTC', '2026-08-01', 24), ('Asia/Riyadh', '2026-08-01', 24),
    ('Asia/Kathmandu', '2026-08-01', 24), ('America/New_York', '2026-11-01', 25), ('America/New_York', '2026-03-08', 23)])
@pytest.mark.parametrize('slot', list(m.SLOTS))
def test_complete_local_day_and_three_distinct_early_horizons(zone, day, n, slot):
    r = record(zone=zone, day=day, slot=slot)
    before = copy.deepcopy(r)
    result = m.predict(artifact(), r)
    assert len(r['hourly']['hours']) == n and sum(result['probs'].values()) == pytest.approx(1)
    assert result['q10_c'] < result['median_c'] < result['q90_c']
    assert r == before


def test_distribution_integrates_celsius_fahrenheit_and_open_tails():
    c, f = record(), record(unit='F')
    assert m.edge(32, 'F') == pytest.approx(-5/18)
    assert m.edge(-8, 'C') == -8.5
    for r in (c, f):
        p = m.distribution(r, 23, 1.5)
        assert sum(p.values()) == pytest.approx(1)
    assert m.interval_probability(20, None, 22, 1) == pytest.approx(m.NORMAL.cdf(2))
    assert m.interval_probability(30, 31, 20, 1) > 0  # stable remote upper tail


def test_floor_atom_cannot_put_probability_below_the_received_maximum():
    r = record(slot='morning')
    at = pp.timestamp(r['as_of'])
    r.update(floor_c=24., observation=dict(basis=m.CONTRACT, station='TEST', raw_sha256='b'*64, rmax_c=24.,
        captured_at=(at-dt.timedelta(minutes=2)).isoformat(), latest_reading_at=(at-dt.timedelta(minutes=7)).isoformat()))
    r = m.seal(r)
    result = m.predict(artifact(), r)
    assert result['probs'] == {'a': 0., 'b': 0., 'c': 1.}
    assert result['q10_c'] == result['median_c'] == 24.
    r['observation']['basis'] = 'all_reports';r = m.seal(r)
    with pytest.raises(ValueError, match='qualified'):m.predict(artifact(), r)


def test_labels_quotes_and_city_names_are_not_temperature_predictors():
    r = record();changed = copy.deepcopy(r)
    changed.update(label={'winner':'a'}, market={'future':1}, incumbent={'a':1}, city='another-station')
    changed = m.seal(changed)
    assert m.vector(changed) == m.vector(r)
    assert m.predict(artifact(), changed) == m.predict(artifact(), r)


@pytest.mark.parametrize('mutation,why', [
    (lambda r:r.update(contract='unknown'), 'contract'),
    (lambda r:r.update(rules_received_at='2027-01-01T00:00:00Z'), 'rules arrived'),
    (lambda r:r.update(checkpoint='noon'), 'horizon'),
    (lambda r:r['bounds'].__setitem__(1,[23,24]), 'gap'),
    (lambda r:r['bounds'].__setitem__(0,[20,22]), 'open-ended'),
    (lambda r:r['hourly']['hours'][0].update(written_at='2027-01-01T00:00:00Z'), 'hourly'),
])
def test_missing_or_later_evidence_is_not_repaired_by_current_data(mutation, why):
    r = record();mutation(r);r = m.seal(r)
    with pytest.raises(ValueError, match=why):m.predict(artifact(), r)


def test_morning_is_before_both_forecast_and_original_seasonal_peak():
    r = record(slot='morning');r['peak_reference']['peak_hour_local']=9.;r = m.seal(r)
    with pytest.raises(ValueError, match='one hour'):m.predict(artifact(), r)
    r = record(slot='morning');r['peak_reference']=None;r = m.seal(r)
    with pytest.raises(ValueError, match='peak proof'):m.predict(artifact(), r)


def test_forward_registration_cannot_backdate_research():
    r = record();at = pp.timestamp(r['as_of']);a = artifact()
    assert m.predict(a, r, registered_at='2026-01-02T00:00:00Z', now=at.isoformat())
    for reg, now in [(at+dt.timedelta(seconds=1),at),(at-dt.timedelta(days=1),at+dt.timedelta(seconds=301))]:
        with pytest.raises(ValueError, match='unavailable'):
            m.predict(a, r, registered_at=reg.isoformat(), now=now.isoformat())


def test_artifact_roundtrip_and_unfitted_state_cannot_hide_learned_parameters():
    a = artifact();m.model(json.loads(json.dumps(a)))
    a['mean_coefficients'][0] = float(1).hex();a['sha256'] = pp.digest({k:v for k,v in a.items() if k!='sha256'})
    with pytest.raises(ValueError, match='unfitted'):m.model(a)
    a = artifact();a['sources']['tools/wxpredict/temperature_model.py'] = 'changed'
    a['sha256'] = pp.digest({k:v for k,v in a.items() if k!='sha256'})
    with pytest.raises(ValueError, match='identity'):m.model(a)


def test_temperature_location_can_move_beyond_an_incumbent_zero_probability():
    a = artifact();a['state'] = 'fitted_candidate';a['training'] = dict(dates=7, rows=280, cities_per_date_min=20,
        through='2026-07-29', evidence_cutoff='2025-12-31T00:00:00Z')
    a['mean_coefficients'][0] = float(2).hex();a['mean_coefficients'][16] = float(2).hex()
    a['sha256'] = pp.digest({k:v for k,v in a.items() if k!='sha256'})
    r = record();r['incumbent'] = {'a':1.,'b':0.,'c':0.};r=m.seal(r)
    prediction = m.predict(a,r)
    assert prediction['centre_c'] > m.vector(r)[0]+3
    assert prediction['probs']['c'] > .9


def test_fitting_requires_independent_dates_and_actual_label_availability():
    r = record();r['label'] = dict(winner='b',settled_at='2026-08-02T00:00:00Z',available_at='2026-08-02T00:05:00Z')
    r['frozen_at'] = r['as_of']
    with pytest.raises(ValueError, match='insufficient'):m.fit([r], '2026-08-05T00:00:00Z')
    with pytest.raises(ValueError, match='duplicate'):m.fit([r,r], '2026-08-05T00:00:00Z')
    with pytest.raises(ValueError, match='unavailable'):m.fit([r], '2026-08-02T00:01:00Z')


def test_shared_interval_fit_uses_seven_dates_and_generalizes_without_a_city_selector():
    rows=[]
    for d in range(1,8):
        for city in range(20):
            for slot in ('day_ahead','d1_eve'):
                r=record(day=f'2026-08-{d:02}',city=f'city{city}',slot=slot)
                r['frozen_at']=r['as_of'];r['label']=dict(winner='c',settled_at=f'2026-08-{d+1:02}T01:00:00Z',available_at=f'2026-08-{d+1:02}T01:01:00Z')
                rows.append(r)
    a=m.fit(rows,'2026-08-10T00:00:00Z')
    r=record(day='2026-08-11',city='unseen-city')
    result=m.predict(a,r)
    assert a['training']['dates']==7 and a['training']['rows']==280
    assert a['training']['target']=='confirmed_venue_intervals'
    assert result['centre_c']>m.vector(r)[0]+1 and result['probs']['c']>.9
    with pytest.raises(ValueError,match='overlaps'):m.predict(a,record(day='2026-08-07'))
    sparse=[r for r in rows if r['date']!='2026-08-01' or r['city']=='city0']
    with pytest.raises(ValueError,match='insufficient'):m.fit(sparse,'2026-08-10T00:00:00Z')


def test_point_crps_matches_numeric_integral_including_censored_normal():
    # Midpoint numerical integration is an independent check of the closed form.
    for floor, actual in [(None,21.), (20.,21.), (24.,24.)]:
        mean,sigma = 22.,1.5
        step=.002;total=0.
        for i in range(20000):
            x=mean-20+(i+.5)*step
            cdf=0. if floor is not None and x<floor else m.NORMAL.cdf((x-mean)/sigma)
            total += (cdf-int(x>=actual))**2*step
        assert m.point_metrics(mean,sigma,actual,floor)['crps_c'] == pytest.approx(total,abs=1e-5)


def test_evaluation_never_promotes_on_correlated_snapshots_or_missing_pairs():
    expected=[(f'city{i}',f'2026-10-{d:02}') for d in range(1,21) for i in range(20)]
    rows=[dict(city=c,date=d,winner='a',candidate={'a':.8,'b':.2},**{n:{'a':.5,'b':.5} for n in e.specification()['comparisons']}) for c,d in expected]
    assert e.compare(rows,expected)['passed']
    assert not e.compare(rows[:200],expected)['passed']
    rows[0]['market']=None
    for r in rows[::2]:r['market']=None
    assert not e.compare(rows,expected)['passed']
    with pytest.raises(ValueError,match='duplicate'):e.compare([rows[0],rows[0]],expected)
