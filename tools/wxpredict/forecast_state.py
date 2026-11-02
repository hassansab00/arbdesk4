"""Research-only, shared correction of original frozen forecast distributions.

Uses the saved forecast, observation and city clock of each first call. It
does not fetch weather, rewrite calls, choose city models or serve predictions.
An observation exceeding a forecast identifies a state, not a final outcome.
"""
import collections
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from wxpredict import pre_day_pairs as pp

POLICY = 'shared-frozen-forecast-state-v1'
MAX_FREEZE_DELAY_S = 300
MAX_OBS_AGE_MIN = 90
MIN_PEAK_LEAD_H = 1.
MAX_LOGIT_SHIFT = math.log(2.)
L2 = .1
COEFFICIENT_BOUND = 2.
MIN_FIT_DATES, MIN_FIT_CITIES, MIN_FIT_ROWS = 7, 20, 200
FEATURES = ('lower_z','upper_z','open_low','open_high','log_probability',
            'overrun_lower','overrun_upper','raw_overrun_lower','raw_overrun_upper',
            'prepeak_lower','prepeak_upper','obs_age_lower','obs_age_upper',
            'pre_day_lower','pre_day_upper','correction_lower','correction_upper')


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('non-finite forecast-state value')
    return value


def edge_c(value, unit):
    if value is None:
        return None
    value = number(value) - .5
    return (value-32)*5/9 if unit == 'F' else value


def attach(packet, definitions, through):
    """Return an original early call or an explicit evidence-gap reason.

    Missing evidence excludes a row. Contradictory identities, malformed
    probabilities and future observations are errors; never silently repaired.
    The caller must supply first calls, with a separately verified denominator.
    """
    c, city, fact = packet['call'], packet['city'], packet.get('fact')
    if c.get('model_path') == 'wxpredict':
        return None, 'different_wxpredict_serving_path'
    if not c.get('frozen_at'):
        return None, 'missing_server_freeze'
    if c.get('inputs_ok') is not True:
        return None, 'original_inputs_not_ok'
    at, frozen, cutoff = map(pp.timestamp, (c['decided_at'], c['frozen_at'], through))
    if not at <= frozen <= at+dt.timedelta(seconds=MAX_FREEZE_DELAY_S):
        return None, 'late_or_backdated_freeze'
    if frozen > cutoff:
        return None, 'freeze_after_cutoff'
    if not city.get('timezone') or city.get('unit') not in ('C','F') or not c.get('station'):
        return None, 'missing_clock_unit_or_station'
    zone = ZoneInfo(city['timezone'])
    local = at.astimezone(zone)
    day = dt.date.fromisoformat(c['target_date'])
    start = dt.datetime.combine(day, dt.time(), zone)
    end = dt.datetime.combine(day+dt.timedelta(days=1), dt.time(), zone)
    pre_day = c['checkpoint'] == 'd1_eve'
    if pre_day:
        scheduled = dt.datetime.combine(day-dt.timedelta(days=1),dt.time(18),zone)
        if not scheduled <= at <= frozen <= scheduled+dt.timedelta(minutes=75) or frozen >= start:
            return None, 'not_on_time_evening_before'
    elif local.date() != day or frozen >= end:
        return None, 'not_same_city_local_day'
    if c.get('forecast_issued_at') and pp.timestamp(c['forecast_issued_at']) > at:
        raise ValueError('forecast issued after original decision')
    if not c.get('forecast_issued_at'):
        return None, 'missing_forecast_timestamp'
    probs = pp.distribution(c['probs'])
    top = str(c['top_band_id'])
    if top not in probs or probs[top] < max(probs.values())-1e-6:
        raise ValueError('favourite differs from original distribution')
    if any(bid not in definitions for bid in probs):
        return None, 'missing_exact_band_geometry'
    bands = [definitions[bid] for bid in probs]
    bands.sort(key=lambda b: -math.inf if b['lo'] is None else number(b['lo']))
    if any(a['hi'] is None or b['lo'] is None or number(a['hi']) != number(b['lo'])
           for a,b in zip(bands,bands[1:])):
        raise ValueError('frozen band geometry has a gap or overlap')
    if any(b['lo'] is not None and b['hi'] is not None and number(b['lo']) >= number(b['hi']) for b in bands):
        raise ValueError('invalid frozen band width')
    centre, raw, sigma = map(number, (c['centre_c'],c['raw_forecast_c'],c['sigma_c']))
    if sigma <= 0:
        raise ValueError('nonpositive original uncertainty')
    floor, obs_age, peak_lead = None, None, None
    peak_status = 'not_applicable_pre_day'
    peak = packet.get('timing_reference')
    if not pre_day:
        if c.get('running_max_c') is None or not c.get('reading_at'):
            return None, 'missing_original_observation'
        reading = pp.timestamp(c['reading_at'])
        if reading > at or reading.astimezone(zone).date() != day:
            raise ValueError('observation is future or belongs to another local date')
        obs_age = (at-reading).total_seconds()/60
        if obs_age > MAX_OBS_AGE_MIN:
            return None, 'stale_original_observation'
        floor = number(c['running_max_c'])
        if not peak or not peak.get('computed_at'):
            # An original morning call is still a real clock-time forecast.
            # Do not invent a historical peak from today's table. Its scores
            # remain a separate, explicitly unverified-peak stratum.
            if c['checkpoint'] != 'morning':
                return None, 'missing_as_of_peak_reference'
            peak_status = 'original_morning_without_peak_proof'
        elif pp.timestamp(peak['computed_at']) > at:
            return None, 'peak_reference_computed_later'
        elif (peak.get('city_key') != c['city_key'] or peak.get('timezone') != city['timezone']
                or int(peak['month']) != day.month):
            raise ValueError('peak reference names a different city clock/month')
        else:
            peak_hour = number(peak['peak_hour_local'])
            if not 0 <= peak_hour < 24:
                raise ValueError('invalid local peak reference')
            # Elapsed UTC time respects a local DST transition. This is a
            # frozen seasonal reference, never the realized maximum time.
            peak_at = start+dt.timedelta(hours=peak_hour)
            peak_lead = (peak_at.astimezone(dt.timezone.utc)-frozen).total_seconds()/3600
            if peak_lead < MIN_PEAK_LEAD_H:
                return None, 'less_than_one_hour_before_reference_peak'
            peak_status = 'before_as_of_reference_peak'
        if c['checkpoint'] == 'morning':
            scheduled = dt.datetime.combine(day,dt.time(9),zone)
            if not scheduled <= at <= frozen <= scheduled+dt.timedelta(minutes=75):
                return None, 'not_on_time_original_morning'
    ids = [str(b['band_id']) for b in bands]
    source = dict(policy=POLICY, checkpoint_id=str(c['checkpoint_id']),city=c['city_key'],
                  date=c['target_date'],checkpoint=c['checkpoint'],station=c['station'],
                  timezone=city['timezone'],unit=city['unit'],as_of=c['decided_at'],frozen_at=c['frozen_at'],
                  forecast_issued_at=c['forecast_issued_at'],centre_c=centre,raw_c=raw,sigma_c=sigma,
                  floor_c=floor,obs_age_min=obs_age,peak_lead_h=peak_lead,
                  peak_reference=peak,peak_status=peak_status,contract=packet.get('contract') or 'unknown',
                  rules_sha256=packet.get('rules_sha256'),band_ids=ids,
                  bounds=[[b['lo'],b['hi']] for b in bands],incumbent={bid:probs[bid] for bid in ids},
                  incumbent_top=top,market=c.get('market'),input_packet_sha256=pp.digest(packet | {'fact':None}))
    source['feature_sha256'] = pp.digest(source)
    label = None
    reason = 'pending_label'
    if fact and fact.get('winner_band_id') and fact.get('settled_at') and fact.get('banked_at'):
        if (str(fact['checkpoint_id']) != source['checkpoint_id'] or fact['city_key'] != source['city']
                or str(fact['target_date']) != source['date'] or fact['checkpoint'] != source['checkpoint']):
            raise ValueError('settled label names a different original call')
        winner = str(fact['winner_band_id'])
        if winner not in ids:
            return None, 'winner_outside_frozen_ladder'
        settled, banked = map(pp.timestamp,(fact['settled_at'],fact['banked_at']))
        if not frozen < settled <= banked:
            raise ValueError('invalid outcome availability ordering')
        if banked <= cutoff and end <= cutoff:
            label = dict(winner=winner,available_at=fact['banked_at'],settled_at=fact['settled_at'])
            reason = None
    return dict(source,label=label), reason


def validate(record):
    f = {k:v for k,v in record.items() if k not in ('feature_sha256','label')}
    if record.get('policy') != POLICY or pp.digest(f) != record.get('feature_sha256'):
        raise ValueError('forecast-state input integrity mismatch')
    pp.distribution(record['incumbent'],record['band_ids'])


def state(record):
    validate(record)
    if record['floor_c'] is None:
        return 'evening_before'
    return 'observations_above_forecast' if record['floor_c'] > record['centre_c'] else 'forecast_not_overtaken'


def vector(record):
    """Explicit feature allowlist. No market, labels, city IDs or final peak."""
    validate(record)
    centre, sigma, floor = record['centre_c'],max(record['sigma_c'],.25),record['floor_c']
    def clip(v): return max(-4.,min(4.,v))
    over = clip((floor-centre)/sigma) if floor is not None else 0.
    raw_over = clip((floor-record['raw_c'])/sigma) if floor is not None else 0.
    before = min(record['peak_lead_h']/6,1.) if record['peak_lead_h'] is not None else 0.
    age = record['obs_age_min']/MAX_OBS_AGE_MIN if record['obs_age_min'] is not None else 0.
    prior = float(floor is None)
    correction = clip((centre-record['raw_c'])/sigma)
    rows=[]
    for bid,(lo,hi) in zip(record['band_ids'],record['bounds']):
        low,high = edge_c(lo,record['unit']),edge_c(hi,record['unit'])
        lower = clip((low-centre)/sigma) if low is not None else -4.
        upper = clip((high-centre)/sigma) if high is not None else 4.
        rows.append([lower,upper,float(lo is None),float(hi is None),
                     max(-12.,math.log(max(record['incumbent'][bid],1e-6))),
                     over*lower,over*upper,raw_over*lower,raw_over*upper,
                     before*lower,before*upper,age*lower,age*upper,
                     prior*lower,prior*upper,correction*lower,correction*upper])
    return rows


def unique(records):
    seen=set()
    for r in records:
        key=(r['city'],r['date'],r['checkpoint'])
        if key in seen:
            raise ValueError('duplicate city/date/checkpoint; first calls required')
        seen.add(key)


def source_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def fit(records, evidence_cutoff):
    """Bounded global residual logits around the unchanged forecast prior.

    This is retrospective development. `evidence_cutoff` purges labels not
    banked before the earliest evaluation decision; it cannot backdate this fit.
    """
    import numpy as np
    import scipy
    from scipy.optimize import minimize
    unique(records)
    counts=collections.Counter(r['date'] for r in records)
    if len(counts)<MIN_FIT_DATES or len({r['city'] for r in records})<MIN_FIT_CITIES or len(records)<MIN_FIT_ROWS:
        raise ValueError('insufficient shared training dates/cities/rows')
    cutoff=pp.timestamp(evidence_cutoff)
    if any(not r.get('label') or pp.timestamp(r['label']['available_at'])>=cutoff for r in records):
        raise ValueError('training label unavailable before evaluation decision')
    last=max(r['date'] for r in records)
    if any(pp.timestamp(r['frozen_at'])>=cutoff for r in records):
        raise ValueError('training call unavailable at evidence cutoff')
    started=dt.datetime.now(dt.timezone.utc)
    arrays=[np.asarray(vector(r),float) for r in records]
    priors=[np.asarray([r['incumbent'][bid] for bid in r['band_ids']],float) for r in records]
    winners=[r['band_ids'].index(r['label']['winner']) for r in records]
    # Dates get equal total weight. Multiple slots from one city/date share
    # that city/date's weight, rather than pretending to be independent days.
    slots=collections.Counter((r['city'],r['date']) for r in records)
    cities=collections.defaultdict(set)
    for r in records: cities[r['date']].add(r['city'])
    if any(len(values)<MIN_FIT_CITIES for values in cities.values()):
        raise ValueError('insufficient cities on a shared training date')
    weights=[1/(len(counts)*len(cities[r['date']])*slots[(r['city'],r['date'])]) for r in records]
    def objective(beta):
        loss=L2*float(beta@beta)/2
        gradient=L2*beta.copy()
        for x,p,y,w in zip(arrays,priors,winners,weights):
            if p[y] == 0:
                # A residual reweighting cannot revive an impossible event.
                # Keep its finite reporting penalty constant in this fit.
                loss-=w*math.log(1e-12)
                continue
            t=np.tanh(x@beta/MAX_LOGIT_SHIFT)
            logits=np.log(np.maximum(p,1e-6))+MAX_LOGIT_SHIFT*t
            logits-=logits.max();q=np.where(p>0,np.exp(logits),0.);q/=q.sum()
            loss-=w*math.log(max(float(q[y]),1e-12))
            error=q.copy();error[y]-=1
            gradient+=w*(x.T@(error*(1-t*t)))
        return loss,gradient
    solution=minimize(objective,np.zeros(len(FEATURES)),jac=True,method='L-BFGS-B',
                      bounds=[(-COEFFICIENT_BOUND,COEFFICIENT_BOUND)]*len(FEATURES),
                      options={'maxiter':200,'ftol':1e-10,'gtol':1e-7})
    if not solution.success or not np.isfinite(solution.x).all():
        raise ValueError('shared correction fit failed: '+str(solution.message))
    finished=dt.datetime.now(dt.timezone.utc)
    artifact=dict(policy=POLICY,coefficients=list(map(float,solution.x)),features=list(FEATURES),
                  l2=L2,coefficient_bound=COEFFICIENT_BOUND,max_logit_shift=MAX_LOGIT_SHIFT,
                  source_sha256=source_hash(),numpy=np.__version__,scipy=scipy.__version__,
                  training=dict(first=min(counts),through=last,dates=len(counts),rows=len(records),
                                cities=len({r['city'] for r in records}),evidence_sha256=pp.digest(records),
                                evidence_cutoff=evidence_cutoff),
                  fit_started_at=started.isoformat(),available_at=finished.isoformat(),
                  changes_serving=False,eligible_for_promotion=False)
    artifact['sha256']=pp.digest(artifact)
    return artifact


def predict(artifact,records):
    """Retrospective predictions only; never advertised as a frozen live call."""
    import numpy as np
    if (artifact.get('policy')!=POLICY or artifact.get('source_sha256')!=source_hash()
            or artifact.get('features')!=list(FEATURES) or artifact.get('l2')!=L2
            or artifact.get('max_logit_shift')!=MAX_LOGIT_SHIFT
            or artifact.get('coefficient_bound')!=COEFFICIENT_BOUND
            or pp.digest({k:v for k,v in artifact.items() if k!='sha256'})!=artifact.get('sha256')
            or artifact.get('changes_serving') is not False or artifact.get('eligible_for_promotion') is not False):
        raise ValueError('shared correction model identity mismatch')
    beta=np.asarray(artifact['coefficients'],float)
    if beta.shape!=(len(FEATURES),) or not np.isfinite(beta).all() or np.any(abs(beta)>COEFFICIENT_BOUND):
        raise ValueError('shared correction exceeds coefficient bounds')
    unique(records)
    out=[]
    for r in records:
        if r['date']<=artifact['training']['through'] or pp.timestamp(r['as_of'])<pp.timestamp(artifact['training']['evidence_cutoff']):
            raise ValueError('evaluation overlaps shared training period')
        shift=MAX_LOGIT_SHIFT*np.tanh(np.asarray(vector(r),float)@beta/MAX_LOGIT_SHIFT)
        p=np.asarray([r['incumbent'][bid] for bid in r['band_ids']])*np.exp(shift)
        p/=p.sum()
        out.append(dict(zip(r['band_ids'],map(float,p))))
    return out


def scores(records,predictions,tops=None):
    if len(records)!=len(predictions):
        raise ValueError('forecast-state score alignment mismatch')
    if tops is not None and len(tops)!=len(records):
        raise ValueError('favourite alignment mismatch')
    rows=[]
    for i,(r,p) in enumerate(zip(records,predictions)):
        if not r.get('label'):
            raise ValueError('cannot score a pending outcome')
        pp.distribution(p,r['band_ids'])
        winner=r['label']['winner']
        top=max(p,key=p.get) if tops is None else tops[i]
        if top not in p or p[top]<max(p.values())-1e-6:
            raise ValueError('scored favourite is not a maximum-probability band')
        rows.append(dict(hit=int(top==winner),
                         log_loss=-math.log(max(p[winner],1e-12)),
                         brier=sum((v-int(bid==winner))**2 for bid,v in p.items())))
    if not rows:return dict(calls=0,hits=0,hit_rate=None,log_loss=None,brier=None)
    return dict(calls=len(rows),hits=sum(r['hit'] for r in rows),
                **{key:sum(r[key]/len(rows) for r in rows) for key in ('log_loss','brier')},
                hit_rate=sum(r['hit'] for r in rows)/len(rows))


def market(record):
    """Captured complete, non-crossed quotes; never current market prices."""
    from wxpredict.live_reader import live_price
    quotes=record.get('market') or {};prices={}
    for bid in record['band_ids']:
        q=quotes.get(bid) or {};lo,hi=q.get('bid'),q.get('ask')
        if any(v is not None and (not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1) for v in (lo,hi)):
            raise ValueError('invalid original market quote')
        if lo is not None and hi is not None and lo>hi:return None
        price=live_price(lo,hi)
        if price is None:return None
        prices[bid]=max(price,1e-6)
    return {bid:p/sum(prices.values()) for bid,p in prices.items()}
