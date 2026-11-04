"""Fixed candidate gates; paired date blocks, no hindsight horizon selection."""
import collections
import math
import random
import datetime as dt
from wxpredict import temperature_model as model, pre_day_pairs as pp

POLICY = 'shared-temperature-evaluation-v1'
MIN_DATES, MIN_CITIES, MIN_CALLS = 20, 20, 200
MIN_COVERAGE = .9
HORIZONS = ('day_ahead', 'd1_eve', 'morning')


def specification():
    return dict(policy=POLICY, horizons=list(HORIZONS), future_dates=MIN_DATES,
                minimum_cities=MIN_CITIES, minimum_calls_per_horizon=MIN_CALLS,
                fixed_cohort_coverage=MIN_COVERAGE, date_cluster_bootstrap_replicates=2000,
                confidence=.95, seed=470, primary='paired_date_mean_log_loss',
                comparisons=['incumbent', 'raw_normal_reference', 'hourly_reference', 'market'],
                saved_raw_centre='additional controlled comparison where originally frozen; never reconstructed',
                promotion='all_horizons_all_comparisons_positive_lower_log_loss_gain; no_hit_or_brier_regression',
                point_metrics='MAE and CRPS only on independently verified complete source points',
                point_comparisons=['hourly_reference','raw_normal_reference'],
                point_gate='same date/city/call/coverage minima; positive lower 95% paired-date CRPS gain and no MAE regression',
                calibration='fixed probability bins and 80% intervals; bucket labels give coverage bounds',
                automatic_promotion=False, changes_serving=False)


def metrics(probs, winner):
    if (winner not in probs or abs(sum(probs.values())-1) > 1e-5
            or any(not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in probs.values())):
        raise ValueError('invalid scored ladder')
    top = min(probs, key=lambda b: (-probs[b], b))
    return dict(hit=int(top == winner), log_loss=-math.log(max(probs[winner], 1e-15)),
                brier=sum((p-int(b == winner))**2 for b, p in probs.items()),
                top_probability=probs[top])


def compare(rows, expected_city_dates, *, replicates=2000):
    """Rows must already be filtered to one fixed trial window and horizon.

    Missing pairs stay in coverage denominators. Multiple cities share a date
    cluster; snapshots cannot manufacture extra independent validation days.
    """
    expected = set(expected_city_dates)
    seen = set()
    for r in rows:
        key = (r['city'], r['date'])
        if key in seen or key not in expected:
            raise ValueError('duplicate or out-of-cohort evaluation call')
        seen.add(key)
    result = dict(expected=len(expected), calls=len(rows), coverage=len(rows)/len(expected) if expected else 0,
                  dates=len({r['date'] for r in rows}), cities=len({r['city'] for r in rows}), comparisons={})
    ready = (result['coverage'] >= MIN_COVERAGE and len(rows) >= MIN_CALLS
             and result['dates'] >= MIN_DATES and result['cities'] >= MIN_CITIES)
    for name in specification()['comparisons']:
        pairs = [r for r in rows if r.get(name)]
        blocks = collections.defaultdict(list)
        for r in pairs:
            c, b = metrics(r['candidate'], r['winner']), metrics(r[name], r['winner'])
            blocks[r['date']].append((b['log_loss']-c['log_loss'], c['hit']-b['hit'], b['brier']-c['brier']))
        daily = [tuple(sum(v[j] for v in values)/len(values) for j in range(3)) for _, values in sorted(blocks.items())]
        rng = random.Random(470)
        means = sorted(sum(rng.choice(daily)[0] for _ in daily)/len(daily) for _ in range(replicates)) if daily else []
        lower = means[int(.025*len(means))] if means else None
        hit = sum(v[1] for v in daily)/len(daily) if daily else None
        brier = sum(v[2] for v in daily)/len(daily) if daily else None
        paired_coverage = len(pairs)/len(expected) if expected else 0
        passed = (ready and len(blocks) >= MIN_DATES and paired_coverage >= MIN_COVERAGE
                  and lower is not None and lower > 0 and hit >= 0 and brier >= 0)
        result['comparisons'][name] = dict(pairs=len(pairs), dates=len(blocks), coverage=paired_coverage,
                                         log_loss_gain_lower_95=lower, hit_gain=hit, brier_gain=brier, passed=passed)
    result['passed'] = ready and all(p['passed'] for p in result['comparisons'].values())
    return result


def evaluate_trial(registration, items, now):
    """Evaluate one frozen future window. Never selects a city/model/time."""
    if registration['protocol'] != specification():
        raise ValueError('temperature preregistration mismatch')
    cohort = registration['cohort']
    first, last = map(dt.date.fromisoformat, (registration['evaluation_first'], registration['evaluation_last']))
    if len(set(cohort)) != len(cohort) or not 20 <= len(cohort) <= 64 or (last-first).days != 19:
        raise ValueError('invalid fixed evaluation cohort/window')
    expected = [(c, (first+dt.timedelta(days=i)).isoformat()) for i in range(20) for c in cohort]
    slots = {s: [] for s in HORIZONS}
    seen = set()
    excluded = collections.Counter()
    for item in items:
        c, score = item['call'], item.get('score')
        key = (c['city_key'], c['target_date'], c['checkpoint'])
        if c['model_sha256'] != registration['model_sha256']:
            raise ValueError('call belongs to a different fixed artifact')
        if c['city_key'] not in cohort or not first.isoformat() <= c['target_date'] <= last.isoformat():
            excluded['outside_fixed_window'] += 1
            continue
        if key in seen:
            raise ValueError('duplicate frozen trial call')
        seen.add(key)
        if c['checkpoint'] not in slots:
            raise ValueError('unknown evaluation horizon')
        if not score:
            excluded['pending_outcome'] += 1
            continue
        at, frozen, settled, scored = map(pp.timestamp, (c['decided_at'], c['frozen_at'], score['confirmed_at'], score['scored_at']))
        if not pp.timestamp(registration['registered_at']) <= at <= frozen < settled <= scored <= pp.timestamp(now) or (frozen-at).total_seconds() > 300:
            raise ValueError('evaluation lacks prospective source/label chronology')
        r = c['record'];model.validate(r)
        if pp.timestamp(r['as_of']) != pp.timestamp(c['decided_at']) or r['city'] != c['city_key'] or r['date'] != c['target_date'] or r['checkpoint'] != c['checkpoint']:
            raise ValueError('frozen temperature input identity mismatch')
        row = dict(city=c['city_key'], date=c['target_date'], winner=score['winner_band_id'], candidate=c['prediction']['probs'])
        row.update({n: c['comparisons'].get(n) for n in specification()['comparisons']})
        slots[c['checkpoint']].append(row)
    results = {slot: compare(rows, expected) for slot, rows in slots.items()}
    for slot,rows in slots.items():
        bins=collections.defaultdict(list)
        for row in rows:
            m=metrics(row['candidate'],row['winner']);bins[min(9,int(m['top_probability']*10))].append(m)
        results[slot]['top_probability_calibration']=[dict(lower=k/10,upper=(k+1)/10,calls=len(v),
            predicted=sum(x['top_probability'] for x in v)/len(v),observed=sum(x['hit'] for x in v)/len(v)) for k,v in sorted(bins.items())]
    # The fixed reference cannot be relabeled a learned candidate, even if a
    # chance result looks good. Allow all city-local days to finish everywhere.
    complete = pp.timestamp(now) >= dt.datetime.combine(last+dt.timedelta(days=2), dt.time(), dt.timezone.utc)
    qualified = registration['state'] == 'fitted_candidate' and complete and all(x['passed'] for x in results.values())
    return dict(policy=POLICY, model_sha256=registration['model_sha256'], state=registration['state'],
                evaluation_first=first.isoformat(), evaluation_last=last.isoformat(), by_horizon=results,
                exclusions=dict(excluded), window_finished=complete, distribution_gates_passed=qualified,
                automatic_promotion=False, changes_serving=False,
                point_validation='separate complete-source-point evaluation required; bucket midpoints prohibited',
                eligible_for_promotion=False)
