"""One bounded weather-conditioned forecast correction shared by all cities.

Private coefficients; standard-library inference. No market, city ID, outcome,
later weather or realized peak in the feature vector. Research replays cannot
become original forward captures. Training and serving never edit old calls.
"""
import collections
import datetime as dt
import hashlib
import math
from pathlib import Path

from wxpredict import forecast_state as base, pre_day_hourly as hourly, pre_day_pairs as pp

POLICY='shared-early-weather-v1'
WEATHER=('day_gap','warming_innovation','remaining_rise','cloud','rain','wind','solar','peak_lead','dewpoint_gap')
FEATURES=base.FEATURES+tuple(name+'_'+edge for name in WEATHER for edge in ('lower','upper'))
LIMIT,L2,BOUND=math.log(2.),.1,2.
ROOT=Path(__file__).resolve().parents[2]
SOURCES=('tools/wxpredict/shared_weather.py','tools/wxpredict/forecast_state.py',
         'tools/wxpredict/pre_day_hourly.py','tools/wxpredict/pre_day_pairs.py',
         'tools/wxpredict/shared_weather_live.py','scripts/shared_weather_forward.py')


def source_hashes():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}


def attach(record,context):
    base.validate(record)
    if not context:return None
    hourly.validate(context,record)
    out=dict(record,hourly=context);out.pop('feature_sha256',None)
    out['feature_sha256']=pp.digest({k:v for k,v in out.items() if k!='label'})
    return out


def original(record):
    out={k:v for k,v in record.items() if k not in ('hourly','feature_sha256')}
    out['feature_sha256']=pp.digest({k:v for k,v in out.items() if k!='label'})
    base.validate(out);return out


def validate(record):
    if pp.digest({k:v for k,v in record.items() if k not in ('label','feature_sha256')})!=record.get('feature_sha256'):
        raise ValueError('shared weather feature integrity mismatch')
    if not record.get('hourly'):raise ValueError('complete received weather required')
    hourly.validate(record['hourly'],record)
    return original(record)


def vector(record):
    r=validate(record);context=record['hourly'];f=context['features'];hours=context['hours']
    sigma=max(r['sigma_c'],.25);at=pp.timestamp(r['as_of']).timestamp()
    before=[h['values'][0] for h in hours if h['utc']<=at]
    after=[h['values'][0] for h in hours if h['utc']>at]
    peak=max(hours,key=lambda h:h['values'][0])['utc']
    innovation=(r['floor_c']-max(before))/sigma if before and r['floor_c'] is not None else 0.
    rise=(max(after)-max(before))/sigma if before and after else 0.
    values=((f['weather_tmax_c']-r['centre_c'])/sigma,innovation,rise,
            f['weather_cloud_peak']/100,math.log1p(f['weather_rain_mm'])/3,
            f['weather_wind_peak']/30,f['weather_solar_to_peak']/5000,
            (peak-at)/21600,(f['weather_tmax_c']-f['weather_dewpoint_peak'])/10)
    values=[max(-4.,min(4.,base.number(v))) for v in values]
    return [row+[v*row[edge] for v in values for edge in (0,1)] for row in base.vector(r)]


def fit(records,evidence_cutoff):
    import numpy as np
    import scipy
    from scipy.optimize import minimize
    base.unique(records);dates={r['date'] for r in records};cities=collections.defaultdict(set)
    slots=collections.Counter((r['city'],r['date']) for r in records)
    for r in records:cities[r['date']].add(r['city'])
    if (len(dates)<base.MIN_FIT_DATES or len(records)<base.MIN_FIT_ROWS
            or any(len(c)<base.MIN_FIT_CITIES for c in cities.values())):
        raise ValueError('insufficient shared training dates/cities/rows')
    cutoff=pp.timestamp(evidence_cutoff)
    if any(not r.get('label') or pp.timestamp(r['label']['available_at'])>=cutoff
           or pp.timestamp(r['frozen_at'])>=cutoff for r in records):
        raise ValueError('training evidence unavailable before cutoff')
    started=dt.datetime.now(dt.timezone.utc)
    arrays=[np.asarray(vector(r),float) for r in records]
    priors=[np.asarray([r['incumbent'][b] for b in r['band_ids']],float) for r in records]
    winners=[r['band_ids'].index(r['label']['winner']) for r in records]
    weights=[1/(len(dates)*len(cities[r['date']])*slots[(r['city'],r['date'])]) for r in records]
    def objective(beta):
        loss=L2*float(beta@beta)/2;gradient=L2*beta.copy()
        for x,p,y,w in zip(arrays,priors,winners,weights):
            if p[y]==0:loss-=w*math.log(1e-12);continue
            t=np.tanh(x@beta/LIMIT);z=np.log(np.maximum(p,1e-6))+LIMIT*t;z-=z.max()
            q=np.where(p>0,np.exp(z),0.);q/=q.sum()
            loss-=w*math.log(max(float(q[y]),1e-12));error=q.copy();error[y]-=1
            gradient+=w*(x.T@(error*(1-t*t)))
        return loss,gradient
    result=minimize(objective,np.zeros(len(FEATURES)),jac=True,method='L-BFGS-B',
        bounds=[(-BOUND,BOUND)]*len(FEATURES),options={'maxiter':200,'ftol':1e-10,'gtol':1e-7})
    if not result.success or not np.isfinite(result.x).all():
        raise ValueError('shared weather fit failed: '+str(result.message))
    artifact=dict(policy=POLICY,coefficients=list(map(float,result.x)),features=list(FEATURES),
        l2=L2,coefficient_bound=BOUND,max_logit_shift=LIMIT,sources=source_hashes(),
        numpy=np.__version__,scipy=scipy.__version__,fit_started_at=started.isoformat(),
        available_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        training=dict(first=min(dates),through=max(dates),dates=len(dates),rows=len(records),
          cities=len(set.union(*cities.values())),evidence_sha256=pp.digest(records),evidence_cutoff=evidence_cutoff),
        changes_serving=False,eligible_for_promotion=False)
    artifact['sha256']=pp.digest(artifact);return artifact


def model(artifact):
    if (artifact.get('policy')!=POLICY or artifact.get('features')!=list(FEATURES)
        or artifact.get('sources')!=source_hashes() or artifact.get('l2')!=L2
        or artifact.get('coefficient_bound')!=BOUND or artifact.get('max_logit_shift')!=LIMIT
        or artifact.get('changes_serving') is not False or artifact.get('eligible_for_promotion') is not False
        or pp.digest({k:v for k,v in artifact.items() if k!='sha256'})!=artifact.get('sha256')):
        raise ValueError('shared weather model identity mismatch')
    beta=artifact['coefficients']
    if len(beta)!=len(FEATURES) or any(not math.isfinite(v) or abs(v)>BOUND for v in beta):
        raise ValueError('shared weather coefficient bounds exceeded')
    return beta


def predict(artifact,records,*,forward=False,registered_at=None,now=None):
    """Historical replay is research. Forward calls require real registration."""
    beta=model(artifact);base.unique(records);out=[]
    for r in records:
        if r['date']<=artifact['training']['through'] or pp.timestamp(r['as_of'])<pp.timestamp(artifact['training']['evidence_cutoff']):
            raise ValueError('evaluation overlaps training')
        if forward:
            if not registered_at or not now:raise ValueError('forward registration and clock required')
            available,registered,at,current=map(pp.timestamp,(artifact['available_at'],registered_at,r['as_of'],now))
            if not available<=registered<=at<=current or (current-at).total_seconds()>300:
                raise ValueError('model unavailable before this live decision')
        q={}
        for bid,x in zip(r['band_ids'],vector(r)):
            shift=LIMIT*math.tanh(sum(v*b for v,b in zip(x,beta))/LIMIT)
            q[bid]=r['incumbent'][bid]*math.exp(shift)
        total=sum(q.values());out.append({bid:p/total for bid,p in q.items()})
    return out
