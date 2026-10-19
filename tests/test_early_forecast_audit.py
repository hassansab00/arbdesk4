import copy
import datetime as dt

import pytest

import early_forecast_audit as audit
import predictive_validation as validation
import probability_engine as pe
import tick


@pytest.fixture(autouse=True)
def no_network_calibration(monkeypatch):
    monkeypatch.setattr(pe, '_calibration', {})


def fixture(unit='C'):
    lo = 20 if unit == 'C' else 68
    width = 1 if unit == 'C' else 2
    bands = [dict(band_id='a', band_lo=None, band_hi=lo, open_low=True),
             dict(band_id='b', band_lo=lo, band_hi=lo+width),
             dict(band_id='c', band_lo=lo+width, band_hi=None, open_high=True)]
    rows = [dict(band_id=k, forecast_max_c=20, centre_c=22, sigma_c=1,
                 calibrated_prob=round(pe.clamp_prob(p), 6))
            for k, p in pe.compute_band_probabilities(22, 1, unit, bands)]
    return rows, bands


@pytest.mark.parametrize('unit', ['C', 'F'])
def test_alternative_is_frozen_before_settlement_and_cannot_change_served_call(unit):
    rows, bands = fixture(unit)
    original = copy.deepcopy(rows)
    packet = audit.freeze(rows, bands, unit)
    assert packet and packet['raw_centre_probs'] != packet['corrected_probs']
    assert sum(packet['raw_centre_probs'].values()) == pytest.approx(1, abs=1e-4)
    probs = {r['band_id']: r['calibrated_prob'] for r in rows}
    pair = audit.score(packet, probs, 'b')
    assert pair['raw_centre']['hit'] == 1 and pair['corrected']['hit'] == 0
    # The same packet scores differently against a different outcome; no
    # hidden truth was used to construct the alternative.
    assert audit.score(packet, probs, 'c')['corrected']['hit'] == 1
    assert rows == original
    assert not audit.score(packet, dict(probs, a=.5), 'b')
    assert not audit.score(dict(packet, policy='other'), probs, 'b')


def test_non_reproducible_incomplete_or_intraday_inputs_never_become_pairs():
    rows, bands = fixture()
    for patch in [dict(centre_c=None), dict(sigma_c=0), dict(forecast_max_c=float('nan')),
                  dict(observed_floor_c=18)]:
        bad = [dict(r, **patch) for r in rows]
        assert not audit.freeze(bad, bands, 'C')
    assert not audit.freeze(rows, bands[:-1], 'C')
    assert not audit.freeze(rows, bands+[bands[0]], 'C')
    assert not audit.freeze([dict(rows[0], forecast_max_c=21), *rows[1:]], bands, 'C')
    assert not audit.freeze([dict(r, calibrated_prob=.333333) for r in rows], bands, 'C')


@pytest.mark.parametrize('calibration', [dict(method='temperature', T=1.2), dict(method='platt', a=.8, b=.1)])
def test_the_same_probability_calibration_is_used_for_both_centres(monkeypatch, calibration):
    monkeypatch.setattr(pe, '_calibration', calibration)
    rows, bands = fixture()
    q = {k: round(pe.clamp_prob(pe._calibrate(p)), 6)
         for k, p in pe.compute_band_probabilities(22, 1, 'C', bands)}
    total = sum(q.values())
    for r in rows:
        r['calibrated_prob'] = round(pe.clamp_prob(q[r['band_id']]/total), 6)
    packet = audit.freeze(rows, bands, 'C')
    assert packet and audit.score(packet, packet['corrected_probs'], 'b')


def test_tick_attaches_the_pair_only_to_the_original_evening_call():
    rows, bands = fixture()
    args = ('city', '2026-10-20', 'd1_eve', dt.datetime(2026, 10, 19, 18),
            rows, ['priced_from:station_correction:test'], {}, None, 'git:test')
    old, _ = tick.build_row(*args)
    captured, why = tick.build_row(*args, bands=bands, unit='C')
    assert why is None
    assert captured.pop('predictive_context')['early_forecast_audit']['policy'] == audit.POLICY
    assert captured == old, 'capture metadata must not change a call or its probabilities'
    morning = list(args)
    morning[2] = 'morning'
    assert 'predictive_context' not in tick.build_row(*morning, bands=bands, unit='C')[0]


def test_valid_blocked_forecasts_still_collect_comparisons_without_changing_trading_gates():
    rows, bands = fixture()
    for row in rows:
        row.update(pricing_eligible=False, pricing_block_reason='no_verified_skill')
    original = copy.deepcopy(rows)
    call, why = tick.build_row('city', '2026-10-20', 'd1_eve', dt.datetime(2026, 10, 19, 18),
        rows, ['priced_from:station_correction:test'], {}, None, 'git:test', bands=bands, unit='C')
    assert why is None and call['inputs_ok'] is False and call['block_reason'] == 'no_verified_skill'
    assert call['predictive_context']['early_forecast_audit']['policy'] == audit.POLICY
    assert rows == original


def test_learning_only_scores_original_receipted_pairs_after_city_day_ends():
    rows, bands = fixture()
    p = dict(checkpoint_id='first', city_key='city', target_date='2026-10-18', checkpoint='d1_eve',
             local_decision_time='2026-10-17T18:00:00', decided_at='2026-10-17T18:36:00Z',
             frozen_at='2026-10-17T18:36:00Z', engine_version='v',
             probs={r['band_id']: r['calibrated_prob'] for r in rows},
             predictive_context={'early_forecast_audit': audit.freeze(rows, bands, 'C')})
    f = dict(checkpoint_id='first', winner_band_id='b', settled_at='2026-10-19T01:00:00Z',
             banked_at='2026-10-19T02:00:00Z')
    cities = [dict(city_key='city', timezone='UTC')]
    now = dt.datetime(2026, 10, 19, 3, tzinfo=dt.timezone.utc)
    collected = validation.collect([p], [f], [], cities, {}, now)
    assert audit.summarize(collected)['correction_hurt'] == 1
    assert audit.summarize(validation.collect([dict(p, frozen_at=None)], [f], [], cities, {}, now))['calls'] == 0
    assert audit.summarize(validation.collect([p], [f], [], cities, {}, now-dt.timedelta(days=1)))['calls'] == 0
    late = dict(p, checkpoint_id='replacement', decided_at='2026-10-17T18:50:00Z')
    assert validation.collect([late, p], [f], [], cities, {}, now) == collected


def test_recent_dates_do_not_hide_a_bad_day_in_the_month_or_turn_pending_into_misses():
    rows = [dict(target_date='2026-10-18', checkpoint='d1_eve', settled=True, verified=True,
                 metrics=dict(hit=0, log_loss=2, brier=1), top_prob=.7),
            dict(target_date='2026-10-19', checkpoint='d1_eve', settled=False, verified=True,
                 metrics=None, top_prob=.8)]
    dates = validation.daily_scores(rows)
    assert dates[0]['n'] == 0 and dates[0]['pending'] == 1 and dates[0]['hit_rate'] is None
    assert dates[1]['n'] == 1 and dates[1]['hits'] == 0
    rows[0]['checkpoint'] = 'day_ahead'
    assert validation.daily_scores(rows)[1]['pending'] is None


def test_early_learning_is_the_city_weather_fit_and_not_a_noon_model_adjustment():
    now = dt.datetime(2026, 10, 19, tzinfo=dt.timezone.utc)
    state = dict(city_key='city', lead_days=1, version='mos:city', as_of='2026-10-19', calibration={'applies':False})
    reports = validation.reports([], [dict(city_key='city', timezone='UTC')], [], [], now,
                                 early_states=[state, dict(state, city_key='other')])
    for row in reports:
        expected = [state] if row['checkpoint'] in ('day_ahead','d1_eve') and row['scope']=='served' else []
        assert row['report']['early_learning'] == expected
