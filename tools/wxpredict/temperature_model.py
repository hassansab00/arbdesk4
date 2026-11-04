"""Pooled temperature location/scale model, independent of incumbent logits.

Inference uses only the standard library. Fits and evidence stay private.
Unknown contracts are not relabeled from current rules. A reference artifact
is explicitly unfitted; collecting its calls does not establish learned skill.
"""
import collections
import datetime as dt
import hashlib
import math
import time
from pathlib import Path
from statistics import NormalDist
from zoneinfo import ZoneInfo

from wxpredict import pre_day_hourly as hourly, pre_day_pairs as pp

POLICY = 'shared-temperature-normal-v1'
CONTRACT = 'awc-routine-v1'
SLOTS = {'day_ahead': (-1, 9), 'd1_eve': (-1, 18), 'morning': (0, 9)}
FEATURES = ('intercept', 'range', 'cloud_at_peak', 'solar_to_peak', 'wind_at_peak',
            'dewpoint_gap', 'rain', 'pressure_change', 'cooling_after_peak',
            'weather_age', 'peak_lead', 'season_sin', 'season_cos',
            'morning', 'day_ahead', 'observed_innovation', 'observation_missing')
MIN_DATES, MIN_CITIES, MIN_ROWS = 7, 20, 200
PRIOR_SIGMA, MIN_SIGMA, MAX_SIGMA = 1.5, .5, 6.
MAX_SHIFT, COEF_BOUND, L2 = 8., 2., .1
NORMAL = NormalDist()
ROOT = Path(__file__).resolve().parents[2]
SOURCES = ('tools/wxpredict/temperature_model.py', 'tools/wxpredict/temperature_live.py',
           'tools/wxpredict/temperature_evaluation.py',
           'tools/wxpredict/temperature_learning.py',
           'tools/wxpredict/temperature_points.py',
           'tools/wxpredict/pre_day_hourly.py', 'scripts/temperature_forward.py')


def number(v):
    if isinstance(v, bool):
        raise ValueError('boolean temperature value')
    v = float(v)
    if not math.isfinite(v):
        raise ValueError('non-finite temperature value')
    return v


def source_hashes():
    return {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}


def seal(record):
    out = dict(record)
    out.pop('sha256', None)
    out['sha256'] = pp.digest({k: v for k, v in out.items() if k not in ('label', 'frozen_at')})
    return out


def validate(r):
    if (r.get('policy') != POLICY or r.get('contract') != CONTRACT
            or r.get('unit') not in ('C', 'F') or not r.get('station')
            or not r.get('rules_sha256') or len(r['rules_sha256']) != 64
            or pp.digest({k: v for k, v in r.items() if k not in ('label', 'frozen_at', 'sha256')}) != r.get('sha256')):
        raise ValueError('temperature input identity or contract mismatch')
    at = pp.timestamp(r['as_of'])
    receipt = pp.timestamp(r['input_received_at'])
    if not receipt <= at or (at-receipt).total_seconds() > 300:
        raise ValueError('temperature input packet is late or stale')
    if not r.get('rules_received_at') or pp.timestamp(r['rules_received_at']) > at:
        raise ValueError('rules arrived after temperature decision')
    zone = ZoneInfo(r['timezone'])
    day = dt.date.fromisoformat(r['date'])
    if r['checkpoint'] not in SLOTS:
        raise ValueError('unknown early horizon')
    off, hour = SLOTS[r['checkpoint']]
    scheduled = dt.datetime.combine(day+dt.timedelta(days=off), dt.time(hour), zone)
    if not scheduled <= at.astimezone(zone) <= scheduled+dt.timedelta(minutes=75):
        raise ValueError('outside the declared city-local slot')
    if r['checkpoint'] != 'morning' and at.astimezone(zone).date() >= day:
        raise ValueError('pre-day call reached the target date')
    # Validate the actual hourly values as well as their receipt checksums.
    # Derived summaries are recomputed below; caller-supplied summaries never
    # become features merely because their enclosing hash is self-consistent.
    hourly.validate(r['hourly'], r)
    for h in r['hourly']['hours']:
        v = [number(x) for x in h['values']]
        if len(v) != 7 or not (-90 <= v[0] <= 70 and -100 <= v[1] <= 70
                and 0 <= v[2] <= 100 and all(x >= 0 for x in v[3:6])
                and 800 <= v[6] <= 1100):
            raise ValueError('invalid received weather units')
    if r['checkpoint'] == 'morning':
        p = r.get('peak_reference') or {}
        if (p.get('city_key') != r['city'] or p.get('timezone') != r['timezone']
                or p.get('month') != day.month or not p.get('computed_at')
                or pp.timestamp(p['computed_at']) > at):
            raise ValueError('missing as-of seasonal peak proof')
        hour = number(p['peak_hour_local'])
        if not 0 <= hour < 24:
            raise ValueError('invalid seasonal peak hour')
        peak = dt.datetime.combine(day, dt.time(), zone)+dt.timedelta(hours=hour)
        forecast_peak = max(r['hourly']['hours'], key=lambda h: h['values'][0])['utc']
        if min(peak.timestamp(), forecast_peak) < at.timestamp()+3600:
            raise ValueError('morning is less than one hour before an as-of peak')
    floor = r.get('floor_c')
    if floor is not None:
        o = r.get('observation') or {}
        if (r['checkpoint'] != 'morning' or o.get('basis') != CONTRACT
                or o.get('station') != r['station'] or not o.get('raw_sha256')
                or number(o['rmax_c']) != number(floor)
                or not 0 <= (at-pp.timestamp(o['captured_at'])).total_seconds() <= 5400
                or not 0 <= (at-pp.timestamp(o['latest_reading_at'])).total_seconds() <= 5400
                or pp.timestamp(o['latest_reading_at']).astimezone(zone).date() != day):
            raise ValueError('floor lacks current contract-qualified observation')
    ids, bounds = r['band_ids'], r['bounds']
    if not 2 <= len(ids) <= 64 or len(set(ids)) != len(ids) or len(bounds) != len(ids):
        raise ValueError('temperature ladder identity mismatch')
    if bounds[0][0] is not None or bounds[-1][1] is not None:
        raise ValueError('complete open-ended ladder required')
    for i, (lo, hi) in enumerate(bounds):
        for v in (lo, hi):
            if v is not None and number(v) != int(number(v)):
                raise ValueError('whole-unit contract boundary required')
        if lo is not None and hi is not None and number(lo) >= number(hi):
            raise ValueError('invalid ladder width')
        if i and (lo is None or bounds[i-1][1] is None or number(lo) != number(bounds[i-1][1])):
            raise ValueError('ladder gap or overlap')
    return r


def vector(r):
    validate(r)
    hours = r['hourly']['hours']
    vals = [h['values'] for h in hours]
    peak = max(range(len(vals)), key=lambda i: vals[i][0])
    maximum = number(vals[peak][0])
    around = vals[max(0, peak-2):peak+3]
    at = pp.timestamp(r['as_of']).timestamp()
    before = [h['values'][0] for h in hours if h['utc'] <= at]
    floor = r.get('floor_c')
    innovation = (number(floor)-max(before))/5 if floor is not None and before else 0.
    angle = 2*math.pi*(dt.date.fromisoformat(r['date']).timetuple().tm_yday-1)/365.2425
    age = max((at-h['available'])/3600 for h in hours)
    v = [1., (maximum-min(h[0] for h in vals))/10,
         sum(h[2] for h in around)/len(around)/100,
         sum(h[3] for h in vals[:peak+1])/5000,
         sum(h[4] for h in around)/len(around)/30,
         (maximum-sum(h[1] for h in around)/len(around))/10,
         math.log1p(sum(h[5] for h in vals))/3,
         (vals[-1][6]-vals[0][6])/10,
         (maximum-min(h[0] for h in vals[peak:]))/10,
         age/12, (hours[peak]['utc']-at)/86400,
         math.sin(angle), math.cos(angle), float(r['checkpoint'] == 'morning'),
         float(r['checkpoint'] == 'day_ahead'), innovation, float(floor is None)]
    return maximum, [max(-4., min(4., number(x))) for x in v]


def edge(value, unit):
    if value is None:
        return None
    v = number(value)-.5
    return (v-32)*5/9 if unit == 'F' else v


def interval_probability(lo, hi, mean, sigma, floor=None):
    """Normal mass; for a known maximum, Y=max(floor,Z), including its atom."""
    if sigma <= 0:
        raise ValueError('nonpositive uncertainty')
    if floor is not None and hi is not None and hi <= floor:
        return 0.
    low = 0. if lo is None or (floor is not None and lo <= floor) else NORMAL.cdf((lo-mean)/sigma)
    high = 1. if hi is None else NORMAL.cdf((hi-mean)/sigma)
    # erfc avoids catastrophic subtraction in the positive upper tail.
    if lo is not None and lo > mean and not (floor is not None and lo <= floor):
        upper = 0. if hi is None else .5*math.erfc((hi-mean)/(sigma*math.sqrt(2)))
        return max(0., .5*math.erfc((lo-mean)/(sigma*math.sqrt(2)))-upper)
    return max(0., high-low)


def distribution(r, mean, sigma):
    validate(r)
    mean, sigma = number(mean), number(sigma)
    q = {bid: interval_probability(edge(lo, r['unit']), edge(hi, r['unit']), mean, sigma, r.get('floor_c'))
         for bid, (lo, hi) in zip(r['band_ids'], r['bounds'])}
    total = sum(q.values())
    if abs(total-1) > 1e-10:
        raise ValueError('temperature distribution is not complete')
    return {b: p/total for b, p in q.items()}


def reference():
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    a = dict(policy=POLICY, features=list(FEATURES), sources=source_hashes(),
             mean_coefficients=[float(0).hex()]*len(FEATURES),
             scale_coefficients=[float(0).hex()]*len(FEATURES),
             state='unfitted_reference', training=None, fit_started_at=now, available_at=now,
             limits=dict(shift=MAX_SHIFT, sigma_min=MIN_SIGMA, sigma_max=MAX_SIGMA,
                         coefficient=COEF_BOUND, prior_sigma=PRIOR_SIGMA, l2=L2),
             changes_serving=False, eligible_for_promotion=False)
    a['sha256'] = pp.digest(a)
    return a


def model(a):
    prior = reference()
    if (a.get('policy') != POLICY or a.get('features') != list(FEATURES)
            or a.get('sources') != source_hashes() or a.get('limits') != prior['limits']
            or a.get('changes_serving') is not False or a.get('eligible_for_promotion') is not False
            or pp.digest({k: v for k, v in a.items() if k != 'sha256'}) != a.get('sha256')
            or a.get('state') not in ('unfitted_reference', 'fitted_candidate')):
        raise ValueError('temperature artifact identity mismatch')
    try:
        beta = [[float.fromhex(x) for x in a[k]] for k in ('mean_coefficients', 'scale_coefficients')]
    except (ValueError, TypeError):
        raise ValueError('canonical coefficient encoding required') from None
    if any(len(b) != len(FEATURES) or any(not math.isfinite(v) or abs(v) > COEF_BOUND for v in b) for b in beta):
        raise ValueError('temperature coefficient bounds exceeded')
    if a['state'] == 'unfitted_reference':
        if a.get('training') is not None or any(any(b) for b in beta):
            raise ValueError('unfitted reference contains learned parameters')
    else:
        t = a.get('training') or {}
        if (t.get('dates', 0) < MIN_DATES or t.get('rows', 0) < MIN_ROWS or t.get('cities_per_date_min', 0) < MIN_CITIES
                or not pp.timestamp(t['evidence_cutoff']) <= pp.timestamp(a['fit_started_at'])):
            raise ValueError('temperature training gates unmet')
    return beta


def parameters(a, r):
    beta, gamma = model(a)
    maximum, x = vector(r)
    shift = MAX_SHIFT*math.tanh(sum(b*v for b, v in zip(beta, x))/MAX_SHIFT)
    width = math.exp(max(-20., min(20., sum(g*v for g, v in zip(gamma, x))))) * PRIOR_SIGMA
    return maximum+shift, max(MIN_SIGMA, min(MAX_SIGMA, width))


def predict(a, r, *, registered_at=None, now=None):
    if a.get('training'):
        t = a['training']
        if r['date'] <= t['through'] or pp.timestamp(r['as_of']) < pp.timestamp(t['evidence_cutoff']):
            raise ValueError('evaluation overlaps temperature training')
    if registered_at is not None or now is not None:
        if not registered_at or not now:
            raise ValueError('forward registration and clock required')
        available, reg, at, current = map(pp.timestamp, (a['available_at'], registered_at, r['as_of'], now))
        if not available <= reg <= at <= current or (current-at).total_seconds() > 300:
            raise ValueError('temperature model unavailable before live decision')
    mean, sigma = parameters(a, r)
    floor = r.get('floor_c')
    quantile = lambda p: max(floor if floor is not None else -math.inf, mean+sigma*NORMAL.inv_cdf(p))
    return dict(centre_c=mean, median_c=quantile(.5), sigma_c=sigma,
                q10_c=quantile(.1), q90_c=quantile(.9), probs=distribution(r, mean, sigma))


def fit(records, evidence_cutoff, budget_s=60):
    """Date-balanced interval likelihood; a venue band is never a point label.

    This creates a fixed research artifact, never a nightly serving update.
    Each later artifact requires a new preregistered prospective trial.
    """
    started = time.monotonic()
    if not 1 <= budget_s <= 120:
        raise ValueError('bounded temperature fit budget required')
    dates = collections.defaultdict(set)
    seen = set()
    slots = collections.Counter((r['city'], r['date']) for r in records)
    cutoff = pp.timestamp(evidence_cutoff)
    if len(records) > 6000:
        raise ValueError('temperature fit row budget exceeded')
    for r in records:
        if time.monotonic() > started+budget_s:
            raise ValueError('temperature fit deadline exceeded')
        validate(r)
        key = (r['city'], r['date'], r['checkpoint'])
        if key in seen:
            raise ValueError('duplicate temperature city/date/horizon')
        seen.add(key)
        dates[r['date']].add(r['city'])
        label = r.get('label') or {}
        end = dt.datetime.combine(dt.date.fromisoformat(r['date'])+dt.timedelta(days=1), dt.time(), ZoneInfo(r['timezone']))
        if (label.get('winner') not in r['band_ids'] or not label.get('settled_at')
                or not label.get('available_at') or not r.get('frozen_at')
                or not pp.timestamp(r['as_of']) <= pp.timestamp(r['frozen_at']) < pp.timestamp(label['settled_at']) <= pp.timestamp(label['available_at']) < cutoff
                or (pp.timestamp(r['frozen_at'])-pp.timestamp(r['as_of'])).total_seconds() > 300 or end > cutoff):
            raise ValueError('temperature training label unavailable or not original')
    if len(dates) < MIN_DATES or len(records) < MIN_ROWS or any(len(c) < MIN_CITIES for c in dates.values()):
        raise ValueError('insufficient legitimate temperature dates/cities/rows')
    import numpy as np
    import scipy
    from scipy.optimize import minimize
    a = reference()
    a['fit_started_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    if cutoff > pp.timestamp(a['fit_started_at']):
        raise ValueError('future temperature evidence cutoff')
    arrays = [vector(r) for r in records]
    weights = [1/(len(dates)*len(dates[r['date']])*slots[(r['city'], r['date'])]) for r in records]
    edges = [r['bounds'][r['band_ids'].index(r['label']['winner'])] for r in records]
    n = len(FEATURES)
    def objective(coef):
        if time.monotonic() > started+budget_s:
            raise ValueError('temperature fit deadline exceeded')
        loss = L2*float(coef@coef)/2
        for r, (maximum, x), (lo, hi), w in zip(records, arrays, edges, weights):
            mean = maximum+MAX_SHIFT*math.tanh(float(np.dot(coef[:n], x))/MAX_SHIFT)
            sigma = max(MIN_SIGMA, min(MAX_SIGMA, PRIOR_SIGMA*math.exp(max(-20., min(20., float(np.dot(coef[n:], x)))))))
            p = interval_probability(edge(lo, r['unit']), edge(hi, r['unit']), mean, sigma, r.get('floor_c'))
            if p == 0 and r.get('floor_c') is not None and hi is not None and edge(hi, r['unit']) <= r['floor_c']:
                raise ValueError('winner contradicts the received contractual floor')
            loss -= w*math.log(max(p, 1e-15))
        return loss
    result = minimize(objective, np.zeros(2*n), method='L-BFGS-B', bounds=[(-COEF_BOUND, COEF_BOUND)]*(2*n),
                      options={'maxiter':200, 'maxfun':16000, 'ftol':1e-9})
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError('temperature fit failed: '+str(result.message))
    a.update(numpy=np.__version__, scipy=scipy.__version__, mean_coefficients=[float(v).hex() for v in result.x[:n]],
             scale_coefficients=[float(v).hex() for v in result.x[n:]], state='fitted_candidate',
             training=dict(first=min(dates), through=max(dates), dates=len(dates), rows=len(records),
                           cities_per_date_min=min(map(len, dates.values())), evidence_cutoff=evidence_cutoff,
                           evidence_sha256=pp.digest(records), target='confirmed_venue_intervals'),
             available_at=dt.datetime.now(dt.timezone.utc).isoformat())
    a['sha256'] = pp.digest({k: v for k, v in a.items() if k != 'sha256'})
    model(a)
    return a


def point_metrics(mean, sigma, actual, floor=None):
    """Only for separately verified source points, never band midpoints."""
    actual = number(actual)
    if sigma <= 0 or floor is not None and actual < floor:
        raise ValueError('invalid point target or floor')
    z = (actual-mean)/sigma
    phi = lambda v: math.exp(-v*v/2)/math.sqrt(2*math.pi)
    crps = sigma*(z*(2*NORMAL.cdf(z)-1)+2*phi(z)-1/math.sqrt(math.pi))
    if floor is not None:
        v = (floor-mean)/sigma
        crps -= sigma*(v*NORMAL.cdf(v)**2+2*NORMAL.cdf(v)*phi(v)-NORMAL.cdf(math.sqrt(2)*v)/math.sqrt(math.pi))
    median = max(floor if floor is not None else -math.inf, mean)
    return dict(absolute_error_c=abs(actual-median), crps_c=max(0., crps))
