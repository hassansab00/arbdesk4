"""One pooled weather-driven mixture: provider weights, season and station.

Fits venue bucket likelihood directly. No synthetic final temperature label,
incumbent probabilities, prices, city ranking or target-day observations.
Research artifact only: it cannot change a serving registry or approve itself.
"""
import collections
import datetime as dt
import hashlib
import math
from pathlib import Path

from wxpredict import pre_day_weather_data as data, pre_day_pairs as pp

RECIPE = 'pre-day-weather-mixture-v1'
FEATURES = ('intercept','season_sin','season_cos','latitude_season_sin',
            'latitude_season_cos','receipt_age_days','latitude_missing')
MIN_DATES, MIN_CITIES, MIN_STATION_DATES = 20,20,20
MAX_SHIFT_C, MAX_SIGMA_C = 5.,8.
SIGMA_PRIOR = (1.5,.1,.25)
PENALTIES = (.02,.05,.15,.02)  # provider logits, shared location, station, residual width


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def source_hashes():
    return {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ('pre_day_weather_data.py','pre_day_weather_model.py','pre_day_pairs.py',
                         'common.py','build_table.py','live_reader.py','parity.py','arrival_dataset.py')}


def unique(records):
    keys=set()
    for r in records:
        data.validate(r)
        key=(r['features']['city'],r['features']['date'])
        if key in keys:raise ValueError('duplicate city-date in weather dataset')
        keys.add(key)


def matrix(records, stations, labels=False):
    import numpy as np
    t,mask,x,spread,age,indices,lo,hi=[],[],[],[],[],[],[],[]
    index={s:i for i,s in enumerate(stations)}
    for r in records:
        f=r['features'];providers=f['providers'];as_of=pp.timestamp(f['as_of']).timestamp()
        values=[providers[m]['value'] if m in providers else 0. for m in data.MODELS]
        available=[m in providers for m in data.MODELS]
        xs=[v for v,ok in zip(values,available) if ok]
        mean=sum(xs)/len(xs)
        sd=math.sqrt(sum((v-mean)**2 for v in xs)/len(xs))
        ah=sum((as_of-p['available_at'])/3600 for p in providers.values())/len(providers)
        doy=dt.date.fromisoformat(f['date']).timetuple().tm_yday
        sn,cs=math.sin(2*math.pi*doy/365.25),math.cos(2*math.pi*doy/365.25)
        lat=f.get('latitude');lat0=float(lat)/90 if lat is not None else 0.
        if lat is not None and (not math.isfinite(lat0) or abs(lat0)>1):raise ValueError('invalid static latitude')
        x.append([1.,sn,cs,lat0*sn,lat0*cs,ah/24,int(lat is None)])
        t.append(values);mask.append(available);spread.append(sd);age.append(ah/24)
        indices.append(index.get(f['unit']+':'+f['station'],-1))
        if labels:
            win=r['band_ids'].index(r['label']['winner']);a,b=r['bounds'][win]
            def edge(v,negative):
                if v is None:return -np.inf if negative else np.inf
                boundary=float(v)-.5 # native integer bucket -> continuous forecast boundary
                return (boundary-32)*5/9 if f['unit']=='F' else boundary
            lo.append(edge(a,True));hi.append(edge(b,False))
    return dict(t=np.asarray(t,float),mask=np.asarray(mask,bool),x=np.asarray(x,float),
                spread=np.asarray(spread,float),age=np.asarray(age,float),station=np.asarray(indices,int),
                lo=np.asarray(lo,float),hi=np.asarray(hi,float))


def layout(stations):
    m,k=len(data.MODELS),len(FEATURES)
    return m,k,len(stations)


def context(parameters,z,stations):
    import numpy as np
    m,k,s=layout(stations);p=np.asarray(parameters,float)
    if p.shape!=(m+k+s+3,) or not np.isfinite(p).all():raise ValueError('invalid weather parameters')
    logits=p[:m];weights=np.exp(logits-logits.max())[None,:]*z['mask']
    weights/=weights.sum(1,keepdims=True)
    offsets=np.where(z['station']>=0,p[m+k+np.maximum(0,z['station'])] if s else 0.,0.)
    unbounded=z['x']@p[m:m+k]+offsets
    shift=np.clip(unbounded,-MAX_SHIFT_C,MAX_SHIFT_C)
    width=p[-3]+p[-2]*z['spread']+p[-1]*z['age']
    sigma=np.clip(width,.4,MAX_SIGMA_C)
    return weights,shift,sigma,unbounded,width


def interval_prob(lo,hi,means,sigma):
    import numpy as np
    from scipy.special import ndtr
    a,b=(lo[:,None]-means)/sigma[:,None],(hi[:,None]-means)/sigma[:,None]
    # Survival differences avoid cancellation for far positive buckets.
    p=np.where(a>0,ndtr(-a)-ndtr(-b),ndtr(b)-ndtr(a))
    return np.maximum(p,0.),a,b


def loss_and_gradient(parameters,z,stations,sample_weights):
    import numpy as np
    m,k,s=layout(stations);p=np.asarray(parameters,float)
    weights,shift,sigma,unbounded,width=context(p,z,stations)
    values,a,b=interval_prob(z['lo'],z['hi'],z['t']+shift[:,None],sigma)
    probability=(weights*values).sum(1)
    safe=np.maximum(probability,1e-12)
    active=(probability>1e-12)
    scale=-sample_weights*active/safe
    pa,pb=np.exp(-.5*a*a)/math.sqrt(2*math.pi),np.exp(-.5*b*b)/math.sqrt(2*math.pi)
    d_mean=(weights*(pa-pb)).sum(1)/sigma
    za=np.where(np.isfinite(a),a,0.);zb=np.where(np.isfinite(b),b,0.)
    d_sigma=(weights*(za*pa-zb*pb)).sum(1)/sigma
    gm=scale*d_mean*(np.abs(unbounded)<=MAX_SHIFT_C)
    gs=scale*d_sigma*((width>=.4)&(width<=MAX_SIGMA_C))
    grad=np.zeros_like(p)
    grad[:m]=(scale[:,None]*weights*(values-probability[:,None])).sum(0)
    grad[m:m+k]=z['x'].T@gm
    if s:
        known=z['station']>=0
        grad[m+k:m+k+s]=np.bincount(z['station'][known],weights=gm[known],minlength=s)
    grad[-3:]=[gs.sum(),gs@z['spread'],gs@z['age']]
    loss=float(-(sample_weights*np.log(safe)).sum())
    groups=[(slice(0,m),0.),(slice(m,m+k),0.),(slice(m+k,m+k+s),0.),(slice(-3,None),np.asarray(SIGMA_PRIOR))]
    for penalty,(section,prior) in zip(PENALTIES,groups):
        d=p[section]-prior
        if len(d):
            loss+=penalty*float((d*d).mean());grad[section]+=2*penalty*d/len(d)
    return loss,grad


def parameter_bounds(stations,station_dates):
    return ([(-3.,3.)]*len(data.MODELS)+[(-4.,4.)]+[(-2.,2.)]*(len(FEATURES)-1)
            +[(-3.,3.) if station_dates[s]>=MIN_STATION_DATES else (0.,0.) for s in stations]
            +[(.4,4.),(0.,1.),(0.,1.5)])


def fit(records):
    import numpy as np
    import scipy
    from scipy.optimize import minimize
    unique(records)
    now=utc_now()
    if any(not r.get('label') or pp.timestamp(r['label']['available_at'])>=now for r in records):
        raise ValueError('weather fit needs labels actually available before fitting')
    counts=collections.Counter(r['features']['date'] for r in records)
    if len(counts)<MIN_DATES or min(counts.values())<MIN_CITIES:
        raise ValueError('insufficient weather fit coverage: twenty dates and twenty cities per date required')
    stations=sorted({r['features']['unit']+':'+r['features']['station'] for r in records})
    station_dates={s:len({r['features']['date'] for r in records if r['features']['unit']+':'+r['features']['station']==s}) for s in stations}
    z=matrix(records,stations,labels=True)
    m,k,s=layout(stations)
    prior=np.asarray([0.]*(m+k+s)+list(SIGMA_PRIOR))
    weights=np.asarray([1/(len(counts)*counts[r['features']['date']]) for r in records])
    result=minimize(loss_and_gradient,prior,args=(z,stations,weights),jac=True,method='L-BFGS-B',
                    bounds=parameter_bounds(stations,station_dates),options=dict(maxiter=500,ftol=1e-10,gtol=1e-6))
    if not result.success or not math.isfinite(float(result.fun)):
        raise ValueError('weather mixture fit failed: '+str(result.message))
    finished=utc_now()
    if finished<now:raise ValueError('weather fit clock moved backwards')
    model=dict(recipe=RECIPE,models=list(data.MODELS),feature_names=list(FEATURES),stations=stations,
               parameters=result.x.tolist(),bounds=parameter_bounds(stations,station_dates),
               station_dates=station_dates,
               fit_started_at=now.isoformat(),available_at=finished.isoformat(),
               training=dict(first=min(counts),through=max(counts),dates=len(counts),rows=len(records),
                             input_replays=sum(r['original'] is None for r in records),evidence_sha256=pp.digest(records)),
               source_sha256=source_hashes(),dependencies=dict(numpy=np.__version__,scipy=scipy.__version__),
               optimizer=dict(converged=True,iterations=int(result.nit),objective=float(result.fun)),
               changes_serving=False,eligible_for_promotion=False)
    model['sha256']=pp.digest(model)
    return model


def validate_model(model):
    if (model.get('recipe')!=RECIPE or model.get('models')!=list(data.MODELS)
            or model.get('feature_names')!=list(FEATURES) or model.get('source_sha256')!=source_hashes()
            or pp.digest({k:v for k,v in model.items() if k!='sha256'})!=model.get('sha256')
            or model.get('changes_serving') is not False or model.get('eligible_for_promotion') is not False):
        raise ValueError('weather artifact integrity, recipe or source mismatch')
    stations=model['stations']
    expected=parameter_bounds(stations,model['station_dates'])
    if (stations!=sorted(set(stations)) or len(model['parameters'])!=sum(layout(stations))+3
            or [tuple(b) for b in model['bounds']]!=expected):
        raise ValueError('weather parameter layout or recipe bounds mismatch')
    if pp.timestamp(model['available_at'])<pp.timestamp(model['fit_started_at']):
        raise ValueError('weather artifact availability precedes fit')
    if len(model['bounds'])!=len(model['parameters']) or any(not math.isfinite(v) or not lo<=v<=hi for v,(lo,hi) in zip(model['parameters'],model['bounds'])):
        raise ValueError('weather parameters outside fitted bounds')


def predictions(model,records,forward=False):
    import numpy as np
    validate_model(model);unique(records)
    if not records:return []
    if forward:
        now=utc_now()
        for r in records:
            f=r['features'];at=pp.timestamp(f['as_of'])
            from zoneinfo import ZoneInfo
            start=dt.datetime.combine(dt.date.fromisoformat(f['date']),dt.time(),ZoneInfo(f['timezone']))
            if not pp.timestamp(model['available_at'])<=at<=now<start or now-at>dt.timedelta(minutes=15) or f['date']<=model['training']['through']:
                raise ValueError('forward weather prediction is late or precedes model availability')
    z=matrix(records,model['stations'])
    w,shift,sigma,_,_=context(model['parameters'],z,model['stations'])
    means=z['t']+shift[:,None]
    result=[]
    for i,r in enumerate(records):
        def edge(v,lower):
            if v is None:return -np.inf if lower else np.inf
            boundary=float(v)-.5
            return (boundary-32)*5/9 if r['features']['unit']=='F' else boundary
        lo=np.asarray([edge(a,True) for a,_ in r['bounds']]);hi=np.asarray([edge(b,False) for _,b in r['bounds']])
        p,_,_=interval_prob(lo,hi,np.repeat(means[i:i+1],len(lo),axis=0),np.repeat(sigma[i],len(lo)))
        q=np.maximum(p@w[i],1e-12);q/=q.sum()
        if not np.isfinite(q).all():raise ValueError('invalid weather distribution')
        centre=float(w[i]@means[i])
        result.append(dict(probs=dict(zip(r['band_ids'],map(float,q))),centre_c=centre,
                           sigma_c=float(math.sqrt(sigma[i]**2+np.sum(w[i]*(means[i]-centre)**2))),
                           provider_weights=dict(zip(data.MODELS,map(float,w[i])))))
    return result
