"""Evaluate one shared candidate against paired, originally frozen forecasts.

Receipts and artifact hashes are assertions supplied by the exporter, not
cryptographic verification of the upstream database. This tool never trains,
selects a serving model, reconstructs a missing forecast, or changes a label.
"""
import collections
import datetime as dt
import hashlib
import json
import math
import random
import statistics
from decimal import Decimal, ROUND_FLOOR
from zoneinfo import ZoneInfo

SCHEMA = 'arbdesk.predictive-evaluation.v1'
POLICY = 'shared-model-offline-review-v1'
HEX = set('0123456789abcdef')


def stamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be an offset-aware ISO string')
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('timestamp has no UTC offset')
    return result.astimezone(dt.timezone.utc)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sha(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load_json(path):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('duplicate JSON key: ' + k)
            result[k] = v
        return result
    def invalid(value):
        raise ValueError('non-finite JSON number: ' + value)
    with open(path, encoding='utf-8') as source:
        return json.load(source, object_pairs_hook=unique, parse_constant=invalid)


def settlement_value(temp_c, unit, rounding):
    """Explicit rule only; negative half values round toward positive infinity.

    This is a diagnostic conversion. The confirmed venue winner always remains
    the scoring label. No default rounding convention is invented for a city.
    """
    if not finite(temp_c) or unit not in ('C', 'F'):
        raise ValueError('invalid temperature or unit')
    value = Decimal(str(temp_c))
    if unit == 'F':
        value = value * Decimal(9) / Decimal(5) + Decimal(32)
    if rounding == 'nearest_half_to_positive_infinity':
        value = (value + Decimal('.5')).to_integral_value(rounding=ROUND_FLOOR)
    elif rounding != 'none':
        raise ValueError('an explicit rounding rule is required')
    return float(value)


def peak_lead(issued_at, outcome):
    """Full offset-aware peak instants only; absent peak proof stays unknown."""
    if not outcome.get('first_max_at'):
        return None
    return (stamp(outcome['first_max_at']) - stamp(issued_at)).total_seconds() / 3600


def phase(lead):
    if lead is None:
        return 'unknown_peak_time'
    if lead <= 0:
        return 'at_or_after_first_max'
    if lead < 1:
        return 'under_1h_before_first_max'
    if lead < 2:
        return '1_to_2h_before_first_max'
    return 'at_least_2h_before_first_max'


def score(probs, winner, top):
    return dict(hit=int(top == winner),
                brier=sum((p - int(k == winner)) ** 2 for k, p in probs.items()),
                log_loss=-math.log(max(1e-6, probs[winner])))


def summary(rows):
    if not rows:
        return dict(calls=0, city_dates=0, target_dates=0, hit_rate=None,
                    brier=None, log_loss=None, temperature_mae_c=None,
                    temperature_scored=0, top_probability_calibration=[])
    cells = collections.defaultdict(list)
    for r in rows:
        cells[(r['city_key'], r['target_date'])].append(r)
    # Each city/date has equal weight, regardless of how many slots it has.
    means = [{k: statistics.fmean(r[k] for r in group) for k in ('hit', 'brier', 'log_loss')}
             for group in cells.values()]
    bins = []
    for b in range(10):
        group = [r for r in rows if min(9, int(r['top_probability'] * 10)) == b]
        if group:
            bins.append(dict(lower=b / 10, upper=(b + 1) / 10, calls=len(group),
                             claimed=statistics.fmean(r['top_probability'] for r in group),
                             observed=statistics.fmean(r['hit'] for r in group)))
    temps = [r['temperature_error_c'] for r in rows if r.get('temperature_error_c') is not None]
    return dict(calls=len(rows), city_dates=len(cells), target_dates=len({r['target_date'] for r in rows}),
                hit_rate=statistics.fmean(r['hit'] for r in means),
                brier=statistics.fmean(r['brier'] for r in means),
                log_loss=statistics.fmean(r['log_loss'] for r in means),
                temperature_mae_c=statistics.fmean(temps) if temps else None,
                temperature_scored=len(temps), top_probability_calibration=bins)


def paired_test(candidate, baseline):
    days = collections.defaultdict(list)
    for x, y in zip(candidate, baseline):
        days[x['target_date']].append(x['hit'] - y['hit'])
    gains = [statistics.fmean(values) for values in days.values()]
    a, b = sum(g > 0 for g in gains), sum(g < 0 for g in gains)
    n = a + b
    p = sum(math.comb(n, k) for k in range(a, n + 1)) / 2 ** n if n else 1.
    return dict(candidate_better_dates=a, baseline_better_dates=b,
                tied_dates=sum(g == 0 for g in gains), target_dates=len(days), p_candidate_better=p,
                test='One sign per city/date after averaging its declared primary slots.',
                caveat='Exploratory sign test; assumes independent dates. Not the global readiness gate.')


def block_intervals(pairs, block_days, samples, seed, alpha):
    """Resample calendar-day blocks, retaining all cities and slots together."""
    by_day = collections.defaultdict(lambda: collections.defaultdict(list))
    for a, b in pairs:
        by_day[a['target_date']][a['city_key']].append(dict(
            hit=a['hit'] - b['hit'], brier=b['brier'] - a['brier'],
            log_loss=b['log_loss'] - a['log_loss']))
    days = sorted(by_day)
    series = []
    for day in days:
        city_means = [{k: statistics.fmean(v[k] for v in group) for k in ('hit', 'brier', 'log_loss')}
                      for group in by_day[day].values()]
        series.append({k: statistics.fmean(v[k] for v in city_means) for k in ('hit', 'brier', 'log_loss')})
    if not days:
        return dict(dates=0, full_blocks=0, gains=None, intervals=None)
    blocks = []
    for i in range(len(days) - block_days + 1):
        if (dt.date.fromisoformat(days[i + block_days - 1]) - dt.date.fromisoformat(days[i])).days == block_days - 1:
            blocks.append(series[i:i + block_days])
    gains = {k: statistics.fmean(v[k] for v in series) for k in ('hit', 'brier', 'log_loss')}
    if not blocks:
        return dict(dates=len(days), full_blocks=0, gains=gains, intervals=None)
    rng = random.Random(seed)
    draws = {k: [] for k in gains}
    for _ in range(samples):
        picked = []
        while len(picked) < len(series):
            picked.extend(rng.choice(blocks))
        picked = picked[:len(series)]
        for k in draws:
            draws[k].append(statistics.fmean(v[k] for v in picked))
    # alpha already accounts for all requested baselines and three metrics.
    intervals = {}
    for k, values in draws.items():
        values.sort()
        intervals[k] = [values[int(alpha / 2 * (samples - 1))],
                        values[math.ceil((1 - alpha / 2) * (samples - 1))]]
    return dict(dates=len(days), full_blocks=len(blocks), gains=gains, intervals=intervals,
                block_days=block_days, samples=samples, seed=seed,
                interval_alpha=alpha, weighting='equal cities within target date, equal target dates')


def _key(row):
    return row['event_id'], row['checkpoint']


def _manifest(packet):
    if packet.get('schema') != SCHEMA:
        raise ValueError('unsupported packet schema')
    spec = packet['spec']
    required = ['registered_at', 'evaluation_as_of', 'from_date', 'to_date', 'candidate_id',
                'baseline_ids', 'expected_cities', 'minimum_dates', 'minimum_city_dates',
                'minimum_pair_coverage', 'block_days', 'bootstrap_samples', 'seed',
                'confidence', 'max_city_hit_regression', 'pair_time_tolerance_seconds',
                'max_market_quote_age_seconds', 'capture_grace_minutes',
                'expected_checkpoints', 'primary_checkpoints']
    if any(k not in spec for k in required):
        raise ValueError('spec must explicitly declare every evaluation threshold')
    stamp(spec['registered_at']); stamp(spec['evaluation_as_of'])
    start, end = dt.date.fromisoformat(spec['from_date']), dt.date.fromisoformat(spec['to_date'])
    if start > end or not 0 < spec['minimum_pair_coverage'] <= 1:
        raise ValueError('invalid window or coverage')
    for k in ['minimum_dates', 'minimum_city_dates', 'block_days', 'bootstrap_samples']:
        if type(spec[k]) is not int or spec[k] <= 0:
            raise ValueError('invalid positive integer: ' + k)
    if 2 * spec['block_days'] > spec['minimum_dates'] or spec['bootstrap_samples'] < 200:
        raise ValueError('insufficient declared block/bootstrap budget')
    if not .5 < spec['confidence'] < 1 or not 0 <= spec['max_city_hit_regression'] < 1:
        raise ValueError('invalid confidence or regression threshold')
    for k in ['pair_time_tolerance_seconds', 'max_market_quote_age_seconds', 'capture_grace_minutes']:
        if not finite(spec[k]) or spec[k] < 0:
            raise ValueError('invalid timing threshold: ' + k)
    models = {}
    for m in packet['models']:
        if m['model_id'] in models:
            raise ValueError('duplicate model identity')
        if m['role'] not in ('candidate', 'incumbent', 'market', 'weather') or not m.get('version'):
            raise ValueError('invalid model role/version')
        models[m['model_id']] = m
    candidate = models[spec['candidate_id']]
    if sum(m['role'] == 'candidate' for m in models.values()) != 1:
        raise ValueError('declare exactly one shared candidate')
    if candidate['role'] != 'candidate' or candidate.get('scope') != 'global':
        raise ValueError('candidate must be one globally shared model')
    if not sha(candidate.get('artifact_sha256')):
        raise ValueError('candidate artifact SHA256 is required')
    stamp(candidate['fit_at']); dt.date.fromisoformat(candidate['trained_through'])
    if (not spec['baseline_ids'] or len(set(spec['baseline_ids'])) != len(spec['baseline_ids'])
            or spec['candidate_id'] in spec['baseline_ids']):
        raise ValueError('distinct declared baselines are required')
    for bid in spec['baseline_ids']:
        if models[bid]['role'] == 'candidate':
            raise ValueError('baseline cannot be a candidate')
    if not {'incumbent', 'market'} <= {models[bid]['role'] for bid in spec['baseline_ids']}:
        raise ValueError('fixed incumbent and market baselines are required')
    checkpoints, primary = spec['expected_checkpoints'], spec['primary_checkpoints']
    if (not checkpoints or len(set(checkpoints)) != len(checkpoints) or not primary
            or len(set(primary)) != len(primary) or not set(primary) <= set(checkpoints)
            or 'postpeak_1h' in primary):
        raise ValueError('declare unique expected and primary advance checkpoints')
    cities = spec['expected_cities']
    if not cities or len(set(cities)) != len(cities):
        raise ValueError('explicit unique city cohort required')
    outcomes = {}
    for o in packet['outcomes']:
        if o['event_id'] in outcomes:
            raise ValueError('duplicate/conflicting outcome: export one authoritative version')
        ids = o['band_ids']
        if not ids or len(set(ids)) != len(ids) or o['winner_band_id'] not in ids:
            raise ValueError('invalid outcome ladder')
        stamp(o['confirmed_at']); stamp(o['received_at'])
        if stamp(o['received_at']) < stamp(o['confirmed_at']):
            raise ValueError('outcome receipt predates confirmation')
        ZoneInfo(o['timezone'])
        if o.get('first_max_at'):
            peak = stamp(o['first_max_at']).astimezone(ZoneInfo(o['timezone']))
            if peak.date().isoformat() != o['target_date']:
                raise ValueError('first maximum is outside the city-local target day')
        outcomes[o['event_id']] = o
    plan = {}
    for p in packet['plan']:
        key = _key(p)
        if key in plan or p['city_key'] not in cities:
            raise ValueError('duplicate plan case or unexpected city')
        if not start <= dt.date.fromisoformat(p['target_date']) <= end:
            raise ValueError('plan date is outside evaluation window')
        stamp(p['scheduled_at'])
        plan[key] = p
    if not plan or set(p['city_key'] for p in plan.values()) != set(cities):
        raise ValueError('plan must include the entire declared city cohort')
    for city in cities:
        if {p['checkpoint'] for p in plan.values() if p['city_key'] == city} != set(checkpoints):
            raise ValueError('every city must have the declared checkpoint cohort')
    return spec, models, outcomes, plan


def _reasons(f, model, outcome, planned, spec):
    reasons = []
    if f.get('evidence') != 'original':
        reasons.append('not_original_receipt_evidence')
    if f.get('model_version') != model['version']:
        reasons.append('model_version_mismatch')
    if model['role'] == 'candidate' and f.get('artifact_sha256') != model['artifact_sha256']:
        reasons.append('candidate_artifact_mismatch')
    if not f.get('receipt_id') or not sha(f.get('input_sha256')):
        reasons.append('missing_receipt_or_input_hash')
    if outcome is None:
        return reasons + ['missing_confirmed_outcome']
    if planned is None:
        return reasons + ['outside_registered_plan']
    for k in ('city_key', 'target_date', 'station', 'rule_hash', 'timezone'):
        if not f.get(k) or f[k] != outcome.get(k):
            reasons.append(k + '_outcome_mismatch')
    if any(f.get(k) != planned.get(k) for k in ('city_key', 'target_date', 'scheduled_at')):
        reasons.append('plan_case_mismatch')
    if not sha(f.get('rule_hash')):
        reasons.append('missing_rule_fingerprint')
    if outcome.get('source') != 'venue_confirmed':
        reasons.append('outcome_not_venue_confirmed')
    try:
        issued, frozen, inputs, available = map(stamp, [f['issued_at'], f['frozen_at'], f['input_as_of'], f['input_available_at']])
        scheduled = stamp(planned['scheduled_at'])
        confirmed, received, asof = map(stamp, [outcome['confirmed_at'], outcome['received_at'], spec['evaluation_as_of']])
        end = dt.datetime.combine(dt.date.fromisoformat(f['target_date']) + dt.timedelta(days=1),
                                  dt.time(), ZoneInfo(f['timezone'])).astimezone(dt.timezone.utc)
        if not available <= inputs <= issued <= frozen < min(confirmed, end):
            reasons.append('noncausal_or_post_outcome_timestamps')
        if not scheduled <= issued <= frozen <= scheduled + dt.timedelta(minutes=spec['capture_grace_minutes']):
            reasons.append('outside_capture_window')
        if not confirmed <= received <= asof or frozen > asof:
            reasons.append('outcome_or_forecast_not_yet_available')
        if model['role'] == 'candidate':
            if (dt.date.fromisoformat(model['trained_through']) >= dt.date.fromisoformat(f['target_date'])
                    or stamp(model['fit_at']) > inputs):
                reasons.append('candidate_training_overlap_or_late_fit')
        if model['role'] == 'market':
            quote = stamp(f['market_quote_at'])
            if quote > available or not 0 <= (issued - quote).total_seconds() <= spec['max_market_quote_age_seconds']:
                reasons.append('stale_or_future_market_quote')
    except (KeyError, TypeError, ValueError, OverflowError):
        reasons.append('missing_or_invalid_causal_timestamp')
    probs = f.get('probabilities')
    if (not isinstance(probs, dict) or set(probs) != set(outcome['band_ids'])
            or any(not finite(v) or not 0 <= v <= 1 for v in probs.values())
            or abs(sum(probs.values()) - 1) > 1e-6):
        reasons.append('incomplete_or_invalid_probability_ladder')
    elif (f.get('top_band_id') not in probs
          or probs[f['top_band_id']] < max(probs.values()) - 1e-12):
        reasons.append('saved_top_does_not_match_probabilities')
    return list(dict.fromkeys(reasons))


def evaluate(packet):
    spec, models, outcomes, plan = _manifest(packet)
    forecast_ids, original = set(), {}
    for f in packet['forecasts']:
        if f['forecast_id'] in forecast_ids:
            raise ValueError('duplicate forecast ID')
        forecast_ids.add(f['forecast_id'])
        if f['model_id'] not in models:
            raise ValueError('unknown model ID')
        key = (f['model_id'], *_key(f))
        # Pick the FIRST original before testing eligibility. A later valid
        # revision cannot replace an earlier failed or losing prediction.
        order = stamp(f['issued_at']), f['forecast_id']
        if key not in original or order < (stamp(original[key]['issued_at']), original[key]['forecast_id']):
            original[key] = f
    accepted, excluded, reasons = {}, [], collections.Counter()
    for key, f in original.items():
        o, p, m = outcomes.get(f['event_id']), plan.get(_key(f)), models[f['model_id']]
        problems = _reasons(f, m, o, p, spec)
        if problems:
            excluded.append(dict(forecast_id=f['forecast_id'], model_id=f['model_id'],
                                 event_id=f['event_id'], checkpoint=f['checkpoint'], reasons=problems))
            reasons.update(problems)
            continue
        s = score(f['probabilities'], o['winner_band_id'], f['top_band_id'])
        lead = peak_lead(f['issued_at'], o)
        error = None
        if (finite(f.get('predicted_max_c')) and finite(o.get('actual_max_c'))
                and o.get('temperature_rule_hash') == o['rule_hash']):
            error = abs(f['predicted_max_c'] - o['actual_max_c'])
        accepted[key] = dict(s, city_key=f['city_key'], target_date=f['target_date'],
                             event_id=f['event_id'], checkpoint=f['checkpoint'], forecast_id=f['forecast_id'],
                             issued_at=f['issued_at'], input_as_of=f['input_as_of'],
                             top_probability=f['probabilities'][f['top_band_id']],
                             lead_hours=lead, phase=phase(lead), peak_source=o.get('peak_source', 'unspecified'),
                             temperature_error_c=error)
    model_reports = {}
    for mid in models:
        a = [r for (m, _, _), r in accepted.items() if m == mid]
        model_reports[mid] = dict(overall=summary(a),
            by_city={c: summary([r for r in a if r['city_key'] == c]) for c in spec['expected_cities']},
            by_checkpoint={cp: summary([r for r in a if r['checkpoint'] == cp]) for cp in sorted({r['checkpoint'] for r in a})},
            by_actual_lead={ph: summary([r for r in a if r['phase'] == ph]) for ph in sorted({r['phase'] for r in a})})
    holds = []
    candidate = models[spec['candidate_id']]
    if sum((spec['candidate_id'], *case) in accepted for case in plan) != len(plan):
        holds.append('missing_eligible_original_candidate_cases')
    registration = stamp(spec['registered_at'])
    if stamp(candidate['fit_at']) > registration or registration >= min(stamp(p['scheduled_at']) for p in plan.values()):
        holds.append('specification_or_candidate_artifact_was_not_frozen_before_evaluation')
    if packet.get('synthetic'):
        holds.append('synthetic_demonstration_is_not_validation')
    if dt.date.fromisoformat(candidate['trained_through']) >= dt.date.fromisoformat(spec['from_date']):
        holds.append('training_window_overlaps_evaluation_window')
    alpha = (1 - spec['confidence']) / (len(spec['baseline_ids']) * 3)
    comparisons = {}
    for bid in spec['baseline_ids']:
        pairs, pair_reasons = [], collections.Counter()
        for case in plan:
            a, b = accepted.get((spec['candidate_id'], *case)), accepted.get((bid, *case))
            if a is None or b is None:
                pair_reasons['missing_eligible_candidate_or_baseline'] += 1
                continue
            if any(abs((stamp(a[k]) - stamp(b[k])).total_seconds()) > spec['pair_time_tolerance_seconds']
                   for k in ('issued_at', 'input_as_of')):
                pair_reasons['unmatched_prediction_cutoffs'] += 1
                continue
            pairs.append((a, b))
        primary_pairs = [(a, b) for a, b in pairs if a['checkpoint'] in spec['primary_checkpoints']]
        interval = block_intervals(primary_pairs, spec['block_days'], spec['bootstrap_samples'], spec['seed'], alpha)
        coverage = len(pairs) / len(plan)
        city_comparisons = {}
        for city in spec['expected_cities']:
            local = [(a, b) for a, b in primary_pairs if a['city_key'] == city]
            ca, ba = [a for a, _ in local], [b for _, b in local]
            city_comparisons[city] = dict(candidate=summary(ca), baseline=summary(ba), exploratory_pair_test=paired_test(ca, ba))
            n = len({a['target_date'] for a in ca})
            if n < spec['minimum_city_dates']:
                holds.append(f'{bid}:{city}:insufficient_paired_city_dates')
            elif summary(ca)['hit_rate'] - summary(ba)['hit_rate'] < -spec['max_city_hit_regression']:
                holds.append(f'{bid}:{city}:city_hit_regression_exceeds_declared_limit')
        comparisons[bid] = dict(paired_cases=len(pairs), planned_cases=len(plan), pair_coverage=coverage,
            candidate=summary([a for a, _ in pairs]), baseline=summary([b for _, b in pairs]),
            primary_candidate=summary([a for a, _ in primary_pairs]),
            primary_baseline=summary([b for _, b in primary_pairs]),
            primary_checkpoints=spec['primary_checkpoints'], primary_paired_cases=len(primary_pairs),
            day_block_evidence=interval, by_city=city_comparisons, excluded_pairs=dict(pair_reasons))
        if coverage < spec['minimum_pair_coverage']:
            holds.append(bid + ':insufficient_paired_plan_coverage')
        if interval['dates'] < spec['minimum_dates']:
            holds.append(bid + ':insufficient_evaluation_dates')
        if interval['full_blocks'] < 2 or interval['intervals'] is None:
            holds.append(bid + ':insufficient_contiguous_date_blocks')
        elif not (interval['intervals']['hit'][0] > 0 and interval['intervals']['brier'][0] >= 0
                  and interval['intervals']['log_loss'][0] >= 0):
            holds.append(bid + ':accuracy_improvement_and_probability_nonregression_not_established')
    city_tests = [c['exploratory_pair_test'] for comparison in comparisons.values()
                  for c in comparison['by_city'].values()]
    previous = 0.
    for i, result in enumerate(sorted(city_tests, key=lambda r: r['p_candidate_better'])):
        previous = max(previous, min(1., (len(city_tests) - i) * result['p_candidate_better']))
        result['p_holm_all_city_baseline_tests'] = previous
    return dict(schema=SCHEMA, policy=POLICY, input_sha256=digest(packet), plan_sha256=digest(packet['plan']),
        readiness=dict(status='hold' if holds else 'ready_for_independent_review', reasons=sorted(set(holds)),
                       automatic_promotion=False, evidence='exporter receipt assertions; upstream proof still requires review'),
        spec=spec, models=models, scored=model_reports, comparisons=comparisons,
        exclusions=dict(original_rows=len(original), later_revisions_ignored=len(packet['forecasts']) - len(original),
                        scored_rows=len(accepted), rows=excluded, counts_by_reason=dict(reasons)),
        limitations=['No real-money P/L or executable fill claim.',
                     'Actual-high categories use hindsight for diagnostics only, never readiness or entry selection.',
                     'Bootstrap intervals assume calendar blocks capture relevant dependence; they are not future-profit probabilities.',
                     'Input hashes/receipts and training dates are supplied assertions, not independently verified source signatures.'])
