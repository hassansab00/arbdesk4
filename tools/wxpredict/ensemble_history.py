"""Past-only city/station/horizon ensemble skill; no outcome reconstruction.

Ranks seven saved forecasts on the actual winning intervals. The weights are
bounded, recency-weighted and shrunk toward a compatible pool. A label can
influence a decision only after its recorded receipt, on a later target date.
This is a feature adapter for a separately validated candidate, not promotion.
"""
import collections
import datetime as dt
import math
from statistics import NormalDist
from wxpredict import features, current_calls

POLICY = 'city-ensemble-history-v1'
HALF_LIFE_DAYS, SHRINK_DAYS, WINDOW_DAYS = 14., 10., 60
MIN_WEIGHT_DATES = 10
MODELS = features.MODELS
LEARNED_SHARE = .20  # no single recent winner can own the ensemble
NORMAL = NormalDist()


def stamp(v):
    return dt.datetime.fromisoformat(str(v).replace('Z','+00:00')).timestamp()


def values(r):
    return {m:float(v) for m in MODELS if (v:=r['row'].get('fc_'+m+'_c')) is not None
            and not isinstance(v,bool) and math.isfinite(float(v))}


def pool_key(r):
    version=r['version']
    station=r['row']['station']
    if version.endswith(':awc-routine-v1:'+station):version=version.rsplit(':',1)[0]
    return '|'.join((version,r['checkpoint'],r['row']['unit']))


def losses(r):
    """Proper source log loss on the saved winner; never invent a point max."""
    if 'winner' not in r or not r.get('label_received_at'):return {}
    lo,hi=current_calls.bounds(r['row'])[r['winner']]
    if r['row']['unit']=='F':
        lo=(lo-.5-32)*5/9 if lo is not None else None
        hi=(hi-.5-32)*5/9 if hi is not None else None
    else:
        lo=lo-.5 if lo is not None else None
        hi=hi-.5 if hi is not None else None
    sd=max(.5,float(r['row'].get('current_models_sd_c') or 1.5))
    out={}
    for m,v in values(r).items():
        p=(NORMAL.cdf((hi-v)/sd) if hi is not None else 1.)-(NORMAL.cdf((lo-v)/sd) if lo is not None else 0.)
        out[m]=min(12.,-math.log(max(1e-6,p)))
    return out


def fit(records):
    """Private self-contained score history, one original per city/date/horizon."""
    first={}
    for r in records:
        key=(r['source_identity'],r['checkpoint'],r['date'])
        if key not in first or (r['frozen_at'],r['checkpoint_id'])<(first[key]['frozen_at'],first[key]['checkpoint_id']):first[key]=r
    rows=[]
    for r in first.values():
        loss=losses(r)
        if not loss:continue
        if not stamp(r['frozen_at'])<stamp(r['settled_at'])<=stamp(r['label_received_at']):
            raise ValueError('ensemble history label chronology invalid')
        rows.append(dict(identity=r['source_identity'],pool=pool_key(r),checkpoint=r['checkpoint'],
                         date=r['date'],available=r['label_received_at'],loss=loss))
    return dict(policy=POLICY,rows=sorted(rows,key=lambda r:(r['date'],r['identity'],r['checkpoint'])))


def context(r,state):
    if state.get('policy')=='city-ensemble-snapshot-v1':
        if r['date']<=state['fit_last'] or stamp(r['input_as_of'])<=stamp(state['available_through']):
            raise ValueError('ensemble artifact unavailable at this decision')
        cell=state['cells'].get(r['source_identity']+'|'+r['checkpoint']) or state['pools'].get(pool_key(r))
        forecasts=values(r);available=sorted(forecasts)
        if not available:raise ValueError('no finite original ensemble sources')
        skill=(cell or {}).get('source_losses') or {}
        neutral=sum(skill.values())/len(skill) if skill else 0.
        s={m:skill.get(m,neutral) for m in available};anchor=min(s.values())
        raw={m:math.exp(-(s[m]-anchor)) if (cell or {}).get('pool_dates',0)>=MIN_WEIGHT_DATES else 1. for m in available};z=sum(raw.values())
        weights={m:(1-LEARNED_SHARE)/len(available)+LEARNED_SHARE*raw[m]/z for m in available}
        centre=sum(weights[m]*forecasts[m] for m in available)
        return dict(policy=POLICY,weights=weights,ranking=sorted(available,key=lambda m:(s[m],m)),source_losses=s,
            local_dates=(cell or {}).get('local_dates',0),pool_dates=(cell or {}).get('pool_dates',0),
            centre_c=centre,spread_c=math.sqrt(sum(weights[m]*(forecasts[m]-centre)**2 for m in available)),
            sources=len(available),max_weight=max(weights.values()))
    if state.get('policy')!=POLICY:raise ValueError('unknown ensemble history policy')
    at=stamp(r['input_as_of']);day=dt.date.fromisoformat(r['date'])
    pool=collections.defaultdict(list);local=collections.defaultdict(list);dates=set();local_dates=set()
    for old in state['rows']:
        age=(day-dt.date.fromisoformat(old['date'])).days
        if not 0<age<=WINDOW_DAYS or stamp(old['available'])>=at or old['pool']!=pool_key(r):continue
        w=2**(-age/HALF_LIFE_DAYS);dates.add(old['date'])
        for m,loss in old['loss'].items():pool[m].append((w,float(loss)))
        if old['identity']==r['source_identity'] and old['checkpoint']==r['checkpoint']:
            local_dates.add(old['date'])
            for m,loss in old['loss'].items():local[m].append((w,float(loss)))
    def mean(xs):return sum(w*v for w,v in xs)/sum(w for w,_ in xs) if xs else None
    skill={}
    for m in MODELS:
        p=mean(pool[m]);mine=mean(local[m]);n=len(local[m])
        if p is not None:skill[m]=p if mine is None else (n*mine+SHRINK_DAYS*p)/(n+SHRINK_DAYS)
    forecasts=values(r);available=sorted(forecasts)
    if not available:raise ValueError('no finite original ensemble sources')
    # An unmeasured source retains the measured pool's mean, not a false zero loss.
    neutral=sum(skill.values())/len(skill) if skill else 0.
    s={m:skill.get(m,neutral) for m in available};anchor=min(s.values())
    raw={m:math.exp(-(s[m]-anchor)) if len(dates)>=MIN_WEIGHT_DATES else 1. for m in available};z=sum(raw.values())
    weights={m:(1-LEARNED_SHARE)/len(available)+LEARNED_SHARE*raw[m]/z for m in available}
    centre=sum(weights[m]*forecasts[m] for m in available)
    spread=math.sqrt(sum(weights[m]*(forecasts[m]-centre)**2 for m in available))
    order=sorted(available,key=lambda m:(s[m],m))
    return dict(policy=POLICY,weights=weights,ranking=order,source_losses=s,
                local_dates=len(local_dates),pool_dates=len(dates),centre_c=centre,spread_c=spread,
                sources=len(available),max_weight=max(weights.values()))


def numeric(r,state):
    c=context(r,state)
    out=dict(ensemble_weighted_c=c['centre_c'],ensemble_weighted_sd_c=c['spread_c'],
             ensemble_local_dates=c['local_dates'],ensemble_pool_dates=c['pool_dates'],
             ensemble_sources=c['sources'],ensemble_max_weight=c['max_weight'])
    for m in MODELS:out['ensemble_weight_'+m]=c['weights'].get(m,0.)
    return out


def snapshot(records,state):
    """Keep aggregate parameters, not individual training calls, in artifacts."""
    if not records:return dict(policy='city-ensemble-snapshot-v1',cells={},pools={},fit_last='',available_through='')
    available=max((r['label_received_at'] for r in records),key=stamp)
    last=max(r['date'] for r in records)
    future=max(dt.date.fromisoformat(last),dt.datetime.fromtimestamp(stamp(available),dt.timezone.utc).date())+dt.timedelta(days=1)
    cells={};pools={}
    for r in records:
        key=r['source_identity']+'|'+r['checkpoint'];pk=pool_key(r)
        if key in cells:continue
        probe=dict(r,date=str(future),input_as_of=dt.datetime.combine(future,dt.time(),dt.timezone.utc).isoformat())
        c=context(probe,state)
        cells[key]={k:c[k] for k in ('source_losses','local_dates','pool_dates')}
        if pk not in pools:
            probe['source_identity']='unseen-city'
            c=context(probe,state)
            pools[pk]={k:c[k] for k in ('source_losses','local_dates','pool_dates')}
    return dict(policy='city-ensemble-snapshot-v1',cells=cells,pools=pools,fit_last=last,available_through=available)
