"""Independent evaluator contracts, including adversarial false-perfect data."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from predictive_evaluation.core import evaluate, load_json, peak_lead, settlement_value, stamp
from predictive_evaluation.historical import audit_scores, review_instants
from predictive_evaluation.__main__ import main


def packet(days=12):
    """Synthetic test data only; positive gates exercise the decision logic."""
    result = dict(schema='arbdesk.predictive-evaluation.v1',
        spec=dict(registered_at='2026-01-04T00:00:00Z', evaluation_as_of='2026-02-01T00:00:00Z',
                  from_date='2026-01-05', to_date=f'2026-01-{4 + days:02d}', candidate_id='shared',
                  baseline_ids=['market', 'incumbent'], expected_cities=['city_a', 'city_b'], minimum_dates=6,
                  expected_checkpoints=['morning'], primary_checkpoints=['morning'],
                  minimum_city_dates=6, minimum_pair_coverage=1., block_days=3,
                  bootstrap_samples=200, seed=17, confidence=.95, max_city_hit_regression=.05,
                  pair_time_tolerance_seconds=60, max_market_quote_age_seconds=300, capture_grace_minutes=75),
        models=[dict(model_id='shared', role='candidate', scope='global', version='one-global-v1',
                     artifact_sha256='a' * 64, fit_at='2026-01-03T00:00:00Z', trained_through='2026-01-01'),
                dict(model_id='market', role='market', version='captured-market-v1'),
                dict(model_id='incumbent', role='incumbent', version='captured-market-v1')],
        plan=[], outcomes=[], forecasts=[])
    for day in range(5, 5 + days):
        target = f'2026-01-{day:02d}'
        for city, timezone in [('city_a', 'UTC'), ('city_b', 'Asia/Riyadh')]:
            event = city + ':' + target
            scheduled = target + 'T09:00:00Z'
            result['plan'].append(dict(event_id=event, city_key=city, target_date=target,
                                       checkpoint='morning', scheduled_at=scheduled))
            confirmed = f'2026-01-{day + 1:02d}T12:00:00Z'
            result['outcomes'].append(dict(event_id=event, city_key=city, target_date=target,
                station=city + '_station', timezone=timezone, rule_hash='b' * 64,
                band_ids=['a', 'b'], winner_band_id='a', source='venue_confirmed',
                confirmed_at=confirmed, received_at=confirmed, first_max_at=target + 'T13:00:00Z',
                peak_source='contract_qualified', actual_max_c=20., temperature_rule_hash='b' * 64))
            for mid in ['shared', 'market', 'incumbent']:
                result['forecasts'].append(dict(forecast_id=mid + ':' + event, model_id=mid,
                    model_version='one-global-v1' if mid == 'shared' else 'captured-market-v1',
                    artifact_sha256='a' * 64 if mid == 'shared' else None,
                    evidence='original', event_id=event, city_key=city, target_date=target,
                    station=city + '_station', timezone=timezone, rule_hash='b' * 64,
                    checkpoint='morning', scheduled_at=scheduled, issued_at=scheduled,
                    frozen_at=target + 'T09:01:00Z', input_as_of=target + 'T08:59:00Z',
                    input_available_at=target + 'T08:58:00Z', receipt_id='test-receipt:' + mid + event,
                    input_sha256='c' * 64, market_quote_at=target + 'T08:57:00Z',
                    probabilities={'a': .8, 'b': .2} if mid == 'shared' else {'a': .4, 'b': .6},
                    top_band_id='a' if mid == 'shared' else 'b', predicted_max_c=20.2))
    return result


def test_one_shared_candidate_can_reach_review_but_never_automatic_promotion():
    r = evaluate(packet())
    assert r['readiness']['status'] == 'ready_for_independent_review'
    assert r['readiness']['automatic_promotion'] is False
    assert r['comparisons']['market']['paired_cases'] == 24
    assert r['comparisons']['market']['day_block_evidence']['dates'] == 12
    assert r['comparisons']['market']['by_city']['city_a']['exploratory_pair_test']['target_dates'] == 12
    assert r['scored']['shared']['overall']['hit_rate'] == 1.
    assert r['scored']['shared']['overall']['temperature_mae_c'] == pytest.approx(.2)


@pytest.mark.parametrize('field,value,reason', [
    ('evidence', 'replay', 'not_original_receipt_evidence'),
    ('receipt_id', None, 'missing_receipt_or_input_hash'),
    ('input_sha256', 'not-a-hash', 'missing_receipt_or_input_hash'),
    ('rule_hash', 'd' * 64, 'rule_hash_outcome_mismatch'),
    ('station', 'different_airport', 'station_outcome_mismatch'),
    ('model_version', 'city-specific-version', 'model_version_mismatch'),
    ('artifact_sha256', 'd' * 64, 'candidate_artifact_mismatch'),
    ('frozen_at', '2026-01-07T00:00:00Z', 'noncausal_or_post_outcome_timestamps'),
    ('input_available_at', '2026-01-05T09:02:00Z', 'noncausal_or_post_outcome_timestamps'),
    ('input_as_of', '2026-01-05T09:02:00Z', 'noncausal_or_post_outcome_timestamps'),
    ('frozen_at', '2026-01-05T12:00:00Z', 'outside_capture_window'),
    ('probabilities', {'a': 1.}, 'incomplete_or_invalid_probability_ladder'),
    ('probabilities', {'a': .9, 'b': .9}, 'incomplete_or_invalid_probability_ladder'),
    ('top_band_id', 'b', 'saved_top_does_not_match_probabilities'),
])
def test_false_perfect_predictions_fail_closed(field, value, reason):
    p = packet(); p['forecasts'][0][field] = value
    r = evaluate(p)
    assert r['readiness']['status'] == 'hold'
    assert r['exclusions']['counts_by_reason'][reason] >= 1


def test_first_invalid_original_is_not_replaced_by_a_later_correct_revision():
    p = packet(); first = p['forecasts'][0]; first['receipt_id'] = None
    later = dict(first, forecast_id='later', issued_at='2026-01-05T09:02:00Z',
                 frozen_at='2026-01-05T09:03:00Z', receipt_id='later-receipt')
    p['forecasts'].append(later)
    r = evaluate(p)
    assert r['exclusions']['later_revisions_ignored'] == 1
    assert r['readiness']['status'] == 'hold'
    assert r['exclusions']['counts_by_reason']['missing_receipt_or_input_hash'] == 1


def test_first_losing_original_is_not_replaced_by_a_later_winner():
    p = packet(); first = p['forecasts'][0]
    later = dict(first, forecast_id='later', issued_at='2026-01-05T09:02:00Z', frozen_at='2026-01-05T09:03:00Z')
    first.update(probabilities={'a': .2, 'b': .8}, top_band_id='b')
    p['forecasts'].append(later)
    r = evaluate(p)
    assert r['scored']['shared']['overall']['hit_rate'] == pytest.approx(23 / 24)


@pytest.mark.parametrize('revision_id', ['aaa-later-revision', 'zzz-later-revision'])
def test_equal_issue_time_receipts_are_ambiguous_regardless_of_opaque_id(revision_id):
    p = packet(); first = p['forecasts'][0]; first['receipt_id'] = None
    later = dict(first, forecast_id=revision_id, frozen_at='2026-01-05T09:02:00Z', receipt_id='later-receipt')
    p['forecasts'].append(later)
    r = evaluate(p)
    assert r['readiness']['status'] == 'hold'
    assert r['exclusions']['counts_by_reason']['ambiguous_original_issue_time'] == 2
    assert r['exclusions']['ambiguous_original_groups'] == 1
    assert r['exclusions']['later_revisions_ignored'] == 0


def test_ties_only_between_later_revisions_do_not_replace_the_unique_first_call():
    p = packet(); original = p['forecasts'][0]
    for fid in ['revision-a', 'revision-b']:
        p['forecasts'].append(dict(original, forecast_id=fid, issued_at='2026-01-05T09:02:00Z',
                                  frozen_at='2026-01-05T09:03:00Z'))
    r = evaluate(p)
    assert r['exclusions']['later_revisions_ignored'] == 2
    assert r['exclusions']['ambiguous_original_groups'] == 0
    assert r['readiness']['status'] == 'ready_for_independent_review'


def test_isolated_losing_dates_cannot_be_omitted_from_bootstrap_evidence():
    p = packet(); mapping = {f'2026-01-{5+i:02d}': f'2026-01-{5+i if i<4 else 10+2*(i-4):02d}' for i in range(12)}
    isolated = set(list(mapping.values())[4:]); p['spec']['to_date'] = '2026-01-24'
    p['spec']['max_city_hit_regression'] = .9
    for row in p['plan'] + p['outcomes'] + p['forecasts']:
        old = row['target_date']; new = mapping[old]
        for key in ['scheduled_at','issued_at','frozen_at','input_as_of','input_available_at','market_quote_at','first_max_at']:
            if key in row:row[key] = row[key].replace(old,new)
        row['event_id'] = row['event_id'].replace(old,new)
        row['target_date'] = new
        if 'confirmed_at' in row:
            stamp_next = (dt.date.fromisoformat(new)+dt.timedelta(days=1)).isoformat()+'T12:00:00Z'
            row['confirmed_at'] = row['received_at'] = stamp_next
        if row.get('model_id') == 'shared' and new in isolated:
            row.update(probabilities={'a':.2,'b':.8},top_band_id='b')
        elif row.get('model_id') in ('market','incumbent') and new in isolated:
            row.update(probabilities={'a':.8,'b':.2},top_band_id='a')
    r = evaluate(p); e = r['comparisons']['market']['day_block_evidence']
    assert e['gains']['hit'] == pytest.approx(-1/3)
    assert len(e['uncovered_dates']) == 8 and e['intervals'] is None
    assert r['readiness']['status'] == 'hold'
    assert 'market:bootstrap_population_does_not_cover_all_evaluated_dates' in r['readiness']['reasons']


def test_training_fit_and_registration_cannot_peek_into_evaluation():
    p = packet(); p['models'][0]['trained_through'] = '2026-01-05'
    r = evaluate(p)
    assert r['exclusions']['counts_by_reason']['candidate_training_overlap_or_late_fit'] == 2
    assert 'training_window_overlaps_evaluation_window' in r['readiness']['reasons']
    p = packet(); p['spec']['registered_at'] = '2026-01-15T00:00:00Z'
    assert 'specification_or_candidate_artifact_was_not_frozen_before_evaluation' in evaluate(p)['readiness']['reasons']
    p = packet(); p['models'][0]['fit_at'] = '2026-01-08T00:00:00Z'
    assert evaluate(p)['readiness']['status'] == 'hold'


def test_global_candidate_cannot_be_replaced_with_per_city_model_identity():
    p = packet(); p['models'][0]['scope'] = 'city'
    with pytest.raises(ValueError, match='globally shared'):
        evaluate(p)
    p = packet(); p['models'].append(dict(p['models'][0], model_id='city-b-model'))
    with pytest.raises(ValueError, match='exactly one'):
        evaluate(p)


def test_missing_candidate_cannot_hide_behind_relaxed_baseline_coverage():
    p = packet(); p['spec']['minimum_pair_coverage'] = .5; p['forecasts'].pop(0)
    assert 'missing_eligible_original_candidate_cases' in evaluate(p)['readiness']['reasons']


def test_freshness_and_cutoff_match_are_required_for_market_comparison():
    p = packet(); p['forecasts'][1]['market_quote_at'] = '2026-01-05T08:00:00Z'
    r = evaluate(p)
    assert r['exclusions']['counts_by_reason']['stale_or_future_market_quote'] == 1
    p = packet(); p['forecasts'][1].update(issued_at='2026-01-05T09:04:00Z', frozen_at='2026-01-05T09:05:00Z',
        market_quote_at='2026-01-05T09:01:00Z', input_available_at='2026-01-05T09:01:30Z', input_as_of='2026-01-05T09:02:00Z')
    r = evaluate(p)
    assert r['comparisons']['market']['excluded_pairs']['unmatched_prediction_cutoffs'] == 1


def test_outcomes_not_yet_received_and_conflicting_winners_are_not_scored():
    p = packet(); p['outcomes'][0]['received_at'] = '2026-02-02T00:00:00Z'
    assert evaluate(p)['exclusions']['counts_by_reason']['outcome_or_forecast_not_yet_available'] == 3
    p = packet(); p['outcomes'].append(dict(p['outcomes'][0], winner_band_id='b'))
    with pytest.raises(ValueError, match='conflicting outcome'):
        evaluate(p)


def test_same_issue_time_cannot_hide_different_information_cutoffs():
    p = packet(); p['forecasts'][0].update(input_as_of='2026-01-05T08:50:00Z',
                                         input_available_at='2026-01-05T08:49:00Z')
    r = evaluate(p)
    assert r['comparisons']['market']['excluded_pairs']['unmatched_prediction_cutoffs'] == 1
    assert r['readiness']['status'] == 'hold'


def test_repeated_slots_do_not_inflate_independent_target_dates():
    p = packet()
    p['spec']['expected_checkpoints'].append('same-instant-second-slot')
    for plan in list(p['plan']):
        p['plan'].append(dict(plan, checkpoint='same-instant-second-slot'))
    for f in list(p['forecasts']):
        p['forecasts'].append(dict(f, forecast_id='second:' + f['forecast_id'], checkpoint='same-instant-second-slot'))
    r = evaluate(p)
    assert r['scored']['shared']['overall']['calls'] == 48
    assert r['scored']['shared']['overall']['city_dates'] == 24
    assert r['comparisons']['market']['day_block_evidence']['dates'] == 12
    assert r['comparisons']['market']['by_city']['city_a']['exploratory_pair_test']['candidate_better_dates'] == 12


def test_unknown_peak_stays_unknown_and_does_not_select_the_evaluation_sample():
    p = packet()
    for o in p['outcomes']:
        o.pop('first_max_at')
    r = evaluate(p)
    assert r['readiness']['status'] == 'ready_for_independent_review'
    assert r['scored']['shared']['by_actual_lead']['unknown_peak_time']['calls'] == 24
    assert peak_lead('2026-10-25T00:00:00Z', {}) is None


def test_offset_aware_peak_arithmetic_handles_fall_back_without_a_one_hour_error():
    o = {'first_max_at': '2026-10-25T12:00:00+01:00'}
    assert peak_lead('2026-10-25T09:00:00Z', o) == 2
    with pytest.raises(ValueError, match='UTC offset'):
        stamp('2026-10-25T12:00:00')


def test_ambiguous_review_peak_and_conflicting_review_are_never_guessed():
    peaks, notes = review_instants([dict(city='paris', day='2026-10-25', peak=2.5)], {'paris': 'Europe/Paris'})
    assert peaks == {} and notes['ambiguous_or_nonexistent_local_peak'] == 1
    with pytest.raises(ValueError, match='conflicting peak'):
        review_instants([dict(city='paris', day='2026-10-24', peak=12)] * 2, {'paris': 'Europe/Paris'})


def test_jeddah_noon_is_observation_assisted_in_descriptive_extract():
    row = dict(id='x', city='jeddah', day='2026-10-20', cp='noon', at='2026-10-20T09:37:00Z',
               h=True, mh=True, settled='2026-10-21T03:00:00Z', b=.1, mb=.05, mc=True)
    r = audit_scores([row], [dict(city_key='jeddah', timezone='Asia/Riyadh')],
                     [dict(city='jeddah', day='2026-10-20', peak=11.)], '2026-10-22T00:00:00Z')
    assert r['readiness']['status'] == 'not_assessable'
    assert r['by_actual_lead']['at_or_after_first_max']['calls'] == 1
    assert r['overall']['calls_at_or_after_first_max'] == 1


def test_temperature_rounding_obeys_negative_half_rule_and_never_guesses():
    rule = 'nearest_half_to_positive_infinity'
    assert settlement_value(-22.5, 'F', rule) == -8.
    assert settlement_value(18.3, 'F', rule) == 65.
    assert settlement_value(17.8, 'F', rule) == 64.
    assert settlement_value(19., 'F', rule) == 66.
    with pytest.raises(ValueError, match='explicit rounding'):
        settlement_value(19., 'F', 'guess')


def test_temperature_mae_requires_same_contract_source_and_is_not_fabricated_from_buckets():
    p = packet()
    for o in p['outcomes']:
        o['temperature_rule_hash'] = 'd' * 64
    assert evaluate(p)['scored']['shared']['overall']['temperature_mae_c'] is None


def test_synthetic_packet_can_never_pass_review():
    p = packet(); p['synthetic'] = True
    assert 'synthetic_demonstration_is_not_validation' in evaluate(p)['readiness']['reasons']


def test_late_perfect_calls_cannot_mask_worse_advance_predictions():
    p = packet()
    p['spec']['expected_checkpoints'].append('postpeak_1h')
    for plan in list(p['plan']):
        p['plan'].append(dict(plan, checkpoint='postpeak_1h'))
    for f in list(p['forecasts']):
        late = dict(f, forecast_id='late:' + f['forecast_id'], checkpoint='postpeak_1h')
        if f['model_id'] == 'shared':
            f.update(probabilities={'a': .2, 'b': .8}, top_band_id='b')
        else:
            f.update(probabilities={'a': .7, 'b': .3}, top_band_id='a')
        p['forecasts'].append(late)
    r = evaluate(p)
    assert r['comparisons']['market']['primary_candidate']['hit_rate'] == 0
    assert r['comparisons']['market']['primary_baseline']['hit_rate'] == 1
    assert r['readiness']['status'] == 'hold'


def test_primary_checkpoint_cannot_be_explicit_postpeak_and_missing_baselines_fail():
    p = packet(); p['spec']['primary_checkpoints'] = ['postpeak_1h']
    with pytest.raises(ValueError, match='primary advance'):
        evaluate(p)
    p = packet(); p['spec']['baseline_ids'] = ['incumbent']
    with pytest.raises(ValueError, match='incumbent and market'):
        evaluate(p)


def test_json_loader_rejects_duplicate_keys_and_nan(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text('{"x":1,"x":2}')
    with pytest.raises(ValueError, match='duplicate JSON'):
        load_json(path)
    path.write_text('{"x":NaN}')
    with pytest.raises(ValueError, match='non-finite'):
        load_json(path)


def test_cli_is_reproducible_and_does_not_overwrite_any_existing_output(tmp_path):
    source = tmp_path / 'packet.json'; source.write_text(json.dumps(packet()))
    before = source.read_bytes()
    first, second = tmp_path / 'one', tmp_path / 'two'
    for out in (first, second):
        assert main(['--output-dir', str(out), '--require-ready', 'evaluate', str(source)]) == 0
    assert (first / 'report.json').read_bytes() == (second / 'report.json').read_bytes()
    saved = (first / 'report.json').read_bytes()
    assert main(['--output-dir', str(first), 'evaluate', str(source)]) == 2
    assert (first / 'report.json').read_bytes() == saved and source.read_bytes() == before
