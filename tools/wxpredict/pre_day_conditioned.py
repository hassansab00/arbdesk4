"""One fixed weather-conditioned pre-day bucket model, offline only.

Uses received forecast temperatures and complete received hourly weather. No
market, incumbent probability, label or original-call metadata is a feature.
The recipe is fixed before its development evaluation; it cannot self-promote.
"""
import collections
import datetime as dt
import hashlib
import math
from pathlib import Path

from wxpredict import pre_day_pairs as pp, pre_day_weather_model as base, pre_day_weather_data as data
from wxpredict import pre_day_hourly as hourly

RECIPE='pre-day-weather-conditioned-v1'
PARAMS=dict(objective='binary',learning_rate=.03,num_leaves=15,min_data_in_leaf=200,
            lambda_l2=20.,feature_fraction=.8,bagging_fraction=.8,bagging_freq=1,
            num_threads=2,verbose=-1,deterministic=True,force_col_wise=True,seed=20261101)
ROUNDS=120
MIN_RICH_DATES,MIN_RICH_CITIES=10,20


def source_hashes():
    out=base.source_hashes()
    for name in ('pre_day_hourly.py','pre_day_conditioned.py'):
        out[name]=hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
    root=Path(__file__).resolve().parents[2]
    for name in ('contract_inputs.py','metar_contract.py'):
        out['scripts/'+name]=hashlib.sha256((root/'scripts'/name).read_bytes()).hexdigest()
    return out


def validate(r):
    data.validate(r);hourly.validate(r.get('hourly'),r['features'])
    rules=r.get('rules_context')
    if rules and (pp.timestamp(rules['received_at'])>pp.timestamp(r['features']['as_of'])
                  or rules['contract']!=r['observation_contract']
                  or rules.get('station') and rules['station']!=r['features']['station']):
        raise ValueError('noncausal or mismatched conditioned rules context')
    if pp.digest({k:v for k,v in r.items() if k!='research_sha256'})!=r.get('research_sha256'):
        raise ValueError('conditioned research row hash mismatch')


def matrix(records,labels=False):
    import numpy as np
    import pandas as pd
    base.unique(records)
    rows=[];selected=[];extras=[];target=[];weights=[];sizes=[]
    date_counts=collections.Counter(r['features']['date'] for r in records)
    for i,r in enumerate(records):
        validate(r);f=r['features'];providers=f['providers'];values=[p['value'] for p in providers.values()]
        anchor=sum(values)/len(values);spread=math.sqrt(sum((v-anchor)**2 for v in values)/len(values))
        age=[(pp.timestamp(f['as_of']).timestamp()-p['available_at'])/3600 for p in providers.values()]
        doy=dt.date.fromisoformat(f['date']).timetuple().tm_yday
        h=r.get('hourly');context=h['features'] if h else {}
        features=dict(station=f['station'],unit=f['unit'],contract=r['observation_contract'],
                      latitude=f.get('latitude'),doy_sin=math.sin(2*math.pi*doy/365.25),doy_cos=math.cos(2*math.pi*doy/365.25),
                      anchor_c=anchor,provider_n=len(values),provider_sd_c=spread,
                      provider_age_mean_h=sum(age)/len(age),provider_age_max_h=max(age),
                      hourly_available=int(h is not None),hourly_source=h['kind'] if h else 'missing')
        for provider in data.MODELS:
            features['delta_'+provider]=providers[provider]['value']-anchor if provider in providers else np.nan
        for key in hourly.FIELDS:features[key]=context.get(key,np.nan)
        features['hourly_minus_daily_c']=context['weather_tmax_c']-anchor if h else np.nan
        rows.append(features);sizes.append(len(r['band_ids']))
        def edge(v):return (float(v)-.5-32)*5/9 if f['unit']=='F' else float(v)-.5
        for bid,(lo,hi) in zip(r['band_ids'],r['bounds']):
            selected.append(i)
            extras.append(dict(lower_gap_c=edge(lo)-anchor if lo is not None else np.nan,
                               upper_gap_c=edge(hi)-anchor if hi is not None else np.nan,
                               band_width_c=edge(hi)-edge(lo) if lo is not None and hi is not None else np.nan,
                               open_low=int(lo is None),open_high=int(hi is None)))
            if labels:
                if not r.get('label'):raise ValueError('conditioned fit requires banked labels')
                target.append(int(bid==r['label']['winner']))
                weights.append(1/(len(date_counts)*date_counts[f['date']]*len(r['band_ids'])))
    frame=pd.DataFrame(rows)
    for key in ('station','unit','contract','hourly_source'):frame[key]=frame[key].astype('category')
    x=pd.concat([frame.iloc[selected].reset_index(drop=True),pd.DataFrame(extras)],axis=1)
    return x,target,weights,sizes


def fit(records):
    import lightgbm as lgb
    import numpy as np
    import pandas as pd
    started=base.utc_now();base.unique(records)
    dates=collections.Counter(r['features']['date'] for r in records)
    rich=collections.Counter(r['features']['date'] for r in records if r.get('hourly'))
    if len(dates)<base.MIN_DATES or min(dates.values())<base.MIN_CITIES:
        raise ValueError('insufficient conditioned training dates/cities')
    if sum(n>=MIN_RICH_CITIES for n in rich.values())<MIN_RICH_DATES:
        raise ValueError('insufficient complete received hourly training dates')
    if any(not r.get('label') or pp.timestamp(r['label']['available_at'])>=started for r in records):
        raise ValueError('conditioned labels unavailable at fit time')
    x,y,w,_=matrix(records,True)
    # Scaling weights to one average per call keeps L2 fixed across table size;
    # dates have equal total weight, each call equal within its date.
    w=np.asarray(w)*len(records)
    booster=lgb.train(PARAMS,lgb.Dataset(x,label=y,weight=w),num_boost_round=ROUNDS)
    finished=base.utc_now()
    if finished<started:raise ValueError('conditioned fit clock moved backwards')
    raw=booster.model_to_string()
    artifact=dict(recipe=RECIPE,params=PARAMS,rounds=ROUNDS,model_text=raw,
                  feature_names=list(x.columns),model_text_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                  source_sha256=source_hashes(),fit_started_at=started.isoformat(),available_at=finished.isoformat(),
                  training=dict(first=min(dates),through=max(dates),dates=len(dates),rows=len(records),
                                rich_dates=sum(n>=MIN_RICH_CITIES for n in rich.values()),
                                rich_rows=sum(rich.values()),evidence_sha256=pp.digest(records)),
                  dependencies=dict(lightgbm=lgb.__version__,numpy=np.__version__,pandas=pd.__version__),
                  changes_serving=False,eligible_for_promotion=False)
    artifact['sha256']=pp.digest(artifact)
    return artifact


def validate_model(artifact):
    if (artifact.get('recipe')!=RECIPE or artifact.get('params')!=PARAMS or artifact.get('rounds')!=ROUNDS
            or artifact.get('source_sha256')!=source_hashes()
            or hashlib.sha256(artifact['model_text'].encode()).hexdigest()!=artifact.get('model_text_sha256')
            or pp.digest({k:v for k,v in artifact.items() if k!='sha256'})!=artifact.get('sha256')
            or artifact.get('changes_serving') is not False or artifact.get('eligible_for_promotion') is not False):
        raise ValueError('conditioned model recipe or integrity mismatch')
    if pp.timestamp(artifact['available_at'])<pp.timestamp(artifact['fit_started_at']):
        raise ValueError('conditioned model availability precedes fit')


def predictions(artifact,records,forward=False):
    import lightgbm as lgb
    import numpy as np
    validate_model(artifact)
    if not records:return []
    if forward:
        now=base.utc_now()
        for r in records:
            f=r['features'];at=pp.timestamp(f['as_of'])
            from zoneinfo import ZoneInfo
            start=dt.datetime.combine(dt.date.fromisoformat(f['date']),dt.time(),ZoneInfo(f['timezone']))
            if not pp.timestamp(artifact['available_at'])<=at<=now<start or now-at>dt.timedelta(minutes=15) or f['date']<=artifact['training']['through']:
                raise ValueError('conditioned forward prediction is late or backdated')
    x,_,_,sizes=matrix(records)
    if list(x.columns)!=artifact['feature_names']:raise ValueError('conditioned feature order changed')
    booster=lgb.Booster(model_str=artifact['model_text'])
    scores=np.asarray(booster.predict(x,validate_features=True,num_threads=2),float)
    if scores.shape!=(sum(sizes),) or not np.isfinite(scores).all() or np.any((scores<0)|(scores>1)):
        raise ValueError('invalid conditioned model scores')
    out=[];offset=0
    for r,n in zip(records,sizes):
        p=np.maximum(scores[offset:offset+n],1e-6);p/=p.sum();offset+=n
        out.append(dict(probs=dict(zip(r['band_ids'],map(float,p)))))
    return out
