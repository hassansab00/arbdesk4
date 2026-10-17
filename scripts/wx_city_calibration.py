"""Bounded probability calibration from causally frozen, served calls only.

Never fits a raw weather model or reads the blinded variant experiments.
Every city/peak phase has a seasonal, shrinkage fit and an unseen date gate.
"""
import datetime as dt
import hashlib
import json
import math
import random
from collections import defaultdict
from zoneinfo import ZoneInfo

POLICY = 'wx-city-phase-season-v1'
PHASES = ('before_peak', 'near_peak', 'after_peak')
IDENTITY = {'temperature': 1.0, 'tilt': 0.0}
PRIORS = dict(window_days=180, validation_days=14, min_train_dates=20,
              min_validation_dates=10, min_pool_rows=200, min_pool_dates=20,
              shrink_dates=30, max_temperature_step=.05, max_tilt_step=.025,
              temperature_bounds=[.8, 1.5], tilt_bounds=[-.2, .2],
              max_age_days=3, bootstrap_samples=1000, bootstrap_seed=20261017)


def timestamp(value):
    if not value:
        return None
    value = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def phase(context):
    try:
        delta = float(context['local_hour']) - float(context['clim_peak_hour'])
        if not math.isfinite(delta):
            return None
        return 'before_peak' if delta < -1 else 'after_peak' if delta > 1 else 'near_peak'
    except (KeyError, TypeError, ValueError):
        return None


def transform(probs, ids, params):
    """Keep impossible buckets impossible; ordinal tilt follows temperature order."""
    if len(ids) != len(set(ids)) or set(ids) != set(probs):
        raise ValueError('calibration requires the exact ordered bucket ladder')
    p = [float(probs[k]) for k in ids]
    if not p or any(not math.isfinite(x) or x < 0 or x > 1 for x in p) or abs(sum(p)-1) > 1e-4:
        raise ValueError('invalid probability ladder')
    t, tilt = float(params['temperature']), float(params['tilt'])
    if not .8 <= t <= 1.5 or not -.2 <= tilt <= .2:
        raise ValueError('calibration outside declared bounds')
    z = [math.log(v)/t + tilt*(i-(len(ids)-1)/2) if v > 0 else -math.inf for i, v in enumerate(p)]
    m = max(z)
    q = [math.exp(v-m) for v in z]
    total = sum(q)
    return {k: v/total for k, v in zip(ids, q)}


def metrics(probs, winner):
    hit = int(min(probs, key=lambda k: (-float(probs[k]), k)) == winner)
    return {'hit': hit, 'log_loss': -math.log(max(1e-6, float(probs.get(winner, 0)))),
            'brier': sum((float(p)-int(k == winner))**2 for k,p in probs.items()) + int(winner not in probs)}


def seasonal_weight(date, target):
    # Continuous across December/January; no northern-hemisphere assumption.
    d, t = dt.date.fromisoformat(str(date)), dt.date.fromisoformat(str(target))
    return .25 + .75*(1+math.cos(2*math.pi*(d.timetuple().tm_yday-t.timetuple().tm_yday)/365.25))/2


def eligible_rows(rows, as_of, model_version):
    cutoff = dt.datetime.combine(as_of, dt.time(), dt.timezone.utc)
    first = as_of-dt.timedelta(days=PRIORS['window_days'])
    selected = {}
    for r in rows:
        try:
            day = dt.date.fromisoformat(r['target_date'])
            ctx = r.get('predictive_context') or {}
            ph = phase(ctx)
            end = dt.datetime.combine(day+dt.timedelta(days=1), dt.time(), ZoneInfo(r['timezone']))
            frozen, input_at = timestamp(r['frozen_at']), timestamp(r['input_as_of'])
            settled, received = timestamp(r['settled_at']), timestamp(r['label_received_at'])
            fit = dt.date.fromisoformat(ctx['model_fit_through'])
            if (r['model_version'] != model_version or not ph or not first <= day < as_of
                or fit >= day or not input_at <= frozen < end <= cutoff
                or not frozen < settled <= received < cutoff):
                continue
            ids = ctx['band_ids']
            transform(r['raw_probs'], ids, IDENTITY)
            if r['winning_band_id'] not in ids:
                continue
            key = (r['city_key'], r['target_date'], ph)
            if key not in selected or frozen < timestamp(selected[key]['frozen_at']):
                selected[key] = dict(r, phase=ph)
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(selected.values(), key=lambda r: (r['target_date'], r['city_key'], r['frozen_at']))


def usable(state, target, model_version):
    try:
        target = dt.date.fromisoformat(str(target))
        age = (target-dt.date.fromisoformat(state['as_of'])).days
        validated = dt.date.fromisoformat(state['validated_through'])
        return (state['model_version'] == model_version and 0 <= age <= PRIORS['max_age_days']
                and 0 < (target-validated).days <= PRIORS['max_age_days']
                and state['params'] != IDENTITY and bool(state['version']))
    except (KeyError, TypeError, ValueError):
        return False


def _score(rows, params, target):
    loss, weight = 0., 0.
    for r in rows:
        w = seasonal_weight(r['target_date'], target)
        p = transform(r['raw_probs'], r['predictive_context']['band_ids'], params)
        loss += w*metrics(p, r['winning_band_id'])['log_loss']
        weight += w
    return loss/weight if weight else math.inf


def _validation(rows, candidate, current):
    daily = defaultdict(list)
    for r in rows:
        ps = [transform(r['raw_probs'], r['predictive_context']['band_ids'], p)
              for p in (IDENTITY, current, candidate)]
        daily[r['target_date']].append([metrics(p, r['winning_band_id']) for p in ps])
    dates = sorted(daily)
    # One independent unit is a local target date, not many overlapping calls.
    means = []
    for day in dates:
        group = daily[day]
        means.append([{k: sum(v[i][k] for v in group)/len(group)
                       for k in ('hit','log_loss','brier')} for i in range(3)])
    avg = [{k: sum(v[i][k] for v in means)/len(means) for k in ('hit','log_loss','brier')}
           for i in range(3)] if means else []
    gains = [v[1]['log_loss']-v[2]['log_loss'] for v in means]
    raw_gains = [v[0]['log_loss']-v[2]['log_loss'] for v in means]
    def lower_ci(values):
        rng = random.Random(PRIORS['bootstrap_seed'])
        boots = sorted(sum(rng.choices(values, k=len(values)))/len(values)
                       for _ in range(PRIORS['bootstrap_samples']))
        return boots[int(.05*len(boots))]
    if not means:
        return {'dates': 0, 'passed': False}
    lower, raw_lower = lower_ci(gains), lower_ci(raw_gains)
    no_regression = all(avg[2]['hit'] >= avg[i]['hit']-1e-12 and avg[2]['brier'] <= avg[i]['brier']+1e-12
                        for i in (0,1))
    return {'dates': len(dates), 'raw': avg[0], 'current': avg[1], 'candidate': avg[2],
            'gain_lower90': lower, 'raw_gain_lower90': raw_lower,
            'passed': lower > 0 and raw_lower > 0 and no_regression}


def fit(rows, cities, model_version, as_of, previous=()):
    as_of = dt.date.fromisoformat(str(as_of))
    rows = eligible_rows(rows, as_of, model_version)
    split = as_of-dt.timedelta(days=PRIORS['validation_days'])
    previous = {(s['city_key'], s['phase']): s for s in previous if s['model_version'] == model_version}
    states = []
    for city in cities:
        for ph in PHASES:
            old = previous.get((city, ph), {})
            current = old.get('params', IDENTITY)
            # A rerun uses the same night's anchor, never takes another step.
            anchor = old.get('anchor', current) if old.get('as_of') == str(as_of) else current
            pool = [r for r in rows if r['phase'] == ph and r['target_date'] < str(split)]
            train = [r for r in pool if r['city_key'] == city]
            val = [r for r in rows if r['phase'] == ph and r['city_key'] == city and r['target_date'] >= str(split)]
            nt, nv = len({r['target_date'] for r in train}), len({r['target_date'] for r in val})
            state = dict(model_version=model_version, city_key=city, phase=ph, as_of=str(as_of),
                         policy=POLICY, priors=PRIORS, anchor=anchor, params=current,
                         version=old.get('version', POLICY+':identity'),
                         validated_through=old.get('validated_through'),
                         train_dates=nt, validation_dates=nv, train_rows=len(train), validation_rows=len(val),
                         decision='held', reason='insufficient independent training/validation dates')
            state['input_digest'] = hashlib.sha256(json.dumps([(r['city_key'],r['target_date'],r['input_sha256'],r['winning_band_id'])
                for r in rows if r['phase'] == ph], separators=(',',':')).encode()).hexdigest()
            if (nt >= PRIORS['min_train_dates'] and nv >= PRIORS['min_validation_dates']
                and len(pool) >= PRIORS['min_pool_rows'] and len({r['target_date'] for r in pool}) >= PRIORS['min_pool_dates']):
                grid = [dict(temperature=t, tilt=b) for t in (.8,1.,1.2,1.5) for b in (-.2,0.,.2)]
                pooled = min(grid, key=lambda p: _score(pool, p, str(as_of)))
                local = min(grid, key=lambda p: _score(train, p, str(as_of)))
                w = nt/(nt+PRIORS['shrink_dates'])
                candidate = {}
                for k, step in [('temperature', PRIORS['max_temperature_step']), ('tilt', PRIORS['max_tilt_step'])]:
                    desired = w*local[k]+(1-w)*pooled[k]
                    candidate[k] = round(max(anchor[k]-step, min(anchor[k]+step, desired)), 8)
                evidence = _validation(val, candidate, current)
                state['validation'] = evidence
                if evidence['passed']:
                    state.update(params=candidate, decision='applied', reason='bounded candidate passed unseen date validation',
                                 validated_through=max(r['target_date'] for r in val))
                    digest = hashlib.sha256(json.dumps([model_version,city,ph,str(as_of),candidate,state['input_digest']],sort_keys=True).encode()).hexdigest()[:12]
                    state['version'] = f'wx-city-cal:{as_of}:{digest}'
                else:
                    # Unchanged good parameters need fresh validation, not a forced move.
                    keep = _validation(val, current, IDENTITY)
                    if current != IDENTITY and keep['passed']:
                        state.update(decision='revalidated', reason='unchanged parameters still pass fresh unseen dates',
                                     validated_through=max(r['target_date'] for r in val), validation=keep)
                    else:
                        state['reason'] = 'candidate did not pass unseen log-loss, Brier and hit-rate gates'
            states.append(state)
    return states
