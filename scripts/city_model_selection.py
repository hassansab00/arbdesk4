"""Two fixed predictors, matched forward calls, chronological validation.

Policy constants are declared priors, not measured improvements. No blinded
experiment, reconstructed prediction or label received after the fit cutoff
enters this selector. Calibration of raw v3 remains a separate bounded fit.
"""
import datetime as dt
import hashlib
import json
import random
import statistics
from zoneinfo import ZoneInfo

import wx_city_calibration as cal

POLICY = 'city-horizon-selector-v1'
BASELINE_POLICY = 'corrected-weather-baseline-v1'
HORIZONS = ('day_ahead', 'd1_eve', 'morning', 'noon', 'prepeak_2h', 'prepeak_1h', 'postpeak_1h')
SUPPORTED = frozenset(('noon', 'prepeak_2h', 'prepeak_1h', 'postpeak_1h'))
CHOICES = ('wxrd3', 'baseline')
PRIORS = dict(window_days=180, validation_days=14, min_train_dates=20,
              min_validation_dates=10, max_age_days=3, max_nightly_switches=1,
              bootstrap_samples=2000, bootstrap_seed=20261018, confidence=.95)


def prior(horizon):
    return 'wxrd3' if horizon is None or horizon in SUPPORTED else 'baseline'


def horizon_at(target, timezone, peak, now):
    """Only borrow a checkpoint's evidence inside its actual capture window."""
    from tick import decision_times, GRACE_MIN
    candidates=[(utc,name) for name,(_,utc) in decision_times(str(target),timezone,peak).items()
                if utc <= now <= utc+dt.timedelta(minutes=GRACE_MIN)]
    return max(candidates)[1] if candidates else None


def eligible_rows(rows, as_of, model_version):
    as_of = dt.date.fromisoformat(str(as_of))
    cutoff = dt.datetime.combine(as_of, dt.time(), dt.timezone.utc)
    first = as_of-dt.timedelta(days=PRIORS['window_days'])
    chosen = {}
    for row in rows:
        try:
            day = dt.date.fromisoformat(str(row['target_date']))
            ctx = row['predictive_context']
            received = cal.timestamp(row['label_received_at'])
            frozen, inputs = cal.timestamp(row['frozen_at']), cal.timestamp(row['input_as_of'])
            end = dt.datetime.combine(day+dt.timedelta(days=1), dt.time(), ZoneInfo(row['timezone']))
            if (row['model_version'] != model_version or row['checkpoint'] not in SUPPORTED
                or not isinstance(row.get('winning_band_id'),str) or not row['winning_band_id']
                or not first <= day < as_of or dt.date.fromisoformat(ctx['model_fit_through']) >= day
                or not inputs <= frozen < end <= cutoff
                or not frozen < cal.timestamp(row['settled_at']) <= received < cutoff):
                continue
            ids = ctx['band_ids']
            for field in ('raw_probs', 'baseline_probs'):
                cal.transform(row[field], ids, cal.IDENTITY)  # validate, never reshape the saved ladder
            key = (row['city_key'], str(day), row['checkpoint'])
            if key not in chosen or frozen < cal.timestamp(chosen[key]['frozen_at']):
                chosen[key] = dict(row, target_date=str(day))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(chosen.values(), key=lambda r: (r['target_date'], r['city_key'], r['checkpoint']))


def score(rows, choice, target=None):
    values = [cal.metrics(r['raw_probs' if choice == 'wxrd3' else 'baseline_probs'], r['winning_band_id']) for r in rows]
    weights = [cal.seasonal_weight(r['target_date'], target) if target else 1 for r in rows]
    return {k: sum(v[k]*w for v,w in zip(values,weights))/sum(weights) if weights else None
            for k in ('hit', 'log_loss', 'brier')}


def validation(rows, candidate, incumbent):
    # A cell has exactly one original call per local target date.
    gains = [cal.metrics(r['raw_probs' if incumbent == 'wxrd3' else 'baseline_probs'], r['winning_band_id'])['log_loss']
             -cal.metrics(r['raw_probs' if candidate == 'wxrd3' else 'baseline_probs'], r['winning_band_id'])['log_loss'] for r in rows]
    a, b = score(rows, candidate), score(rows, incumbent)
    lower = None
    if gains:
        rng = random.Random(PRIORS['bootstrap_seed'])
        samples = sorted(statistics.fmean(rng.choices(gains,k=len(gains))) for _ in range(PRIORS['bootstrap_samples']))
        lower = samples[int((1-PRIORS['confidence'])/2*len(samples))]
    return dict(candidate=a, incumbent=b, dates=len(rows), gain_lower95=lower,
                passed=bool(lower is not None and lower > 0 and a['hit'] >= b['hit']-1e-12 and a['brier'] <= b['brier']+1e-12))


def usable(state, city, horizon, version, target, now):
    try:
        day = dt.date.fromisoformat(str(target))
        validated = dt.date.fromisoformat(state['validated_through'])
        return (horizon in SUPPORTED and state['policy'] == POLICY
                and state['baseline_policy'] == BASELINE_POLICY and state['model_version'] == version
                and state['city_key'] == city and state['checkpoint'] == horizon
                and state['selected'] in CHOICES and bool(state['version'])
                and cal.timestamp(state['fit_at']) <= cal.timestamp(now)
                and 0 <= (day-dt.date.fromisoformat(state['as_of'])).days <= PRIORS['max_age_days']
                and 0 < (day-validated).days <= PRIORS['max_age_days']
                and state['train_dates'] >= PRIORS['min_train_dates']
                and state['validation_dates'] >= PRIORS['min_validation_dates']
                and state['train_through'] < state['validation_from']
                and state['validation']['passed'])
    except (KeyError, TypeError, ValueError):
        return False


def choose(state, city, horizon, version, target, now):
    active = usable(state or {}, city, horizon, version, target, now)
    return dict(policy=POLICY, version=state['version'] if active else POLICY+':prior',
                checkpoint=horizon, selected=state['selected'] if active else prior(horizon),
                active=active, reason=state['reason'] if active else 'authorized horizon prior; no fresh validated override',
                model_version=version, baseline_policy=BASELINE_POLICY,
                fit_at=state.get('fit_at') if active else None,
                validated_through=state.get('validated_through') if active else None)


def fit(rows, cities, version, now, previous=()):
    now = cal.timestamp(now)
    day = now.date()
    rows = eligible_rows(rows, day, version)
    split = str(day-dt.timedelta(days=PRIORS['validation_days']))
    previous = {(s['city_key'],s['checkpoint']):s for s in previous
                if s.get('model_version') == version and s.get('policy') == POLICY
                and s.get('baseline_policy') == BASELINE_POLICY and s.get('selected') in CHOICES
                and s.get('anchor') in CHOICES}
    result = []
    for city in cities:
        for horizon in HORIZONS:
            group = [r for r in rows if r['city_key'] == city and r['checkpoint'] == horizon]
            train = [r for r in group if r['target_date'] < split]
            val = [r for r in group if r['target_date'] >= split]
            old = previous.get((city,horizon), {})
            current = choose(old,city,horizon,version,str(day),now)['selected']
            anchor = old.get('anchor',current) if old.get('as_of') == str(day) else current
            state = dict(policy=POLICY,baseline_policy=BASELINE_POLICY,model_version=version,
                city_key=city,checkpoint=horizon,as_of=str(day),fit_at=now.isoformat(),priors=PRIORS,
                anchor=anchor,selected=current,version=POLICY+':prior',decision='held',
                reason='insufficient independent training/validation dates',
                train_dates=len(train),validation_dates=len(val),
                train_through=max((r['target_date'] for r in train),default=None),
                validation_from=min((r['target_date'] for r in val),default=None),validated_through=None)
            state['readiness'] = cal.readiness(len(train), len(val), PRIORS)
            if horizon not in SUPPORTED:
                state['reason'] = 'raw v3 is not authorized for this horizon; baseline retained'
            elif len(train) >= PRIORS['min_train_dates'] and len(val) >= PRIORS['min_validation_dates']:
                losses = {c:score(train,c,str(day))['log_loss'] for c in CHOICES}
                candidate = min(CHOICES,key=lambda c:(losses[c],c != current))
                other = next(c for c in CHOICES if c != candidate)
                evidence = validation(val,candidate,other)
                state.update(training=losses,validation=evidence)
                if evidence['passed'] and (day-dt.date.fromisoformat(max(r['target_date'] for r in val))).days <= PRIORS['max_age_days']:
                    # A repeat run cannot reverse a switch already made tonight.
                    if old.get('as_of') == str(day) and old.get('selected') != anchor and candidate != old.get('selected'):
                        state.update({k:old[k] for k in ('selected','version','validated_through','validation')})
                        state['reason'] = 'nightly switch limit; previous validated selection retained'
                    else:
                        state.update(selected=candidate,decision='switched' if candidate != current else 'validated',
                                     reason='older-date choice passed later paired log-loss, Brier and hit-rate gates',
                                     validated_through=max(r['target_date'] for r in val))
                        digest=hashlib.sha256(json.dumps([version,city,horizon,str(day),candidate,
                            [(r['target_date'],r['input_sha256'],r['winning_band_id'],r['raw_probs'],r['baseline_probs']) for r in group]],sort_keys=True).encode()).hexdigest()[:16]
                        state['version']=f'{POLICY}:{day}:{digest}'
                elif usable(old,city,horizon,version,str(day),now):
                    state.update({k:old[k] for k in ('selected','version','validated_through','validation')})
                    state['reason']='new candidate did not pass; previous still-fresh validated selection retained'
                else:
                    state.update(selected=prior(horizon),reason='candidate did not pass fresh unseen validation; horizon prior retained')
            # A thin/stale fit must never accidentally refresh an old approval.
            result.append(state)
    return result
