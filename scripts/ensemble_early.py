"""Prospective seven-source early ensemble, learned per settlement station.

Original source ladders, incumbent and market freeze together. Working weights
stay in the existing service-only calibration store. Candidates are shadow:
production promotion needs later frozen evidence against both benchmarks.
"""
import collections
import datetime as dt
import hashlib
import json
import math
import random
from zoneinfo import ZoneInfo

POLICY='early-seven-source-v1'
PREFIX=POLICY+':'
MIN_TRAIN, MIN_VALIDATE, VALIDATION_DAYS = 20,10,14
MIN_WEIGHT_DATES, MAX_WEIGHT_STEP = 10, .02
MODELS=('ecmwf_ifs025','gfs_seamless','icon_seamless','ukmo_seamless','jma_seamless','gem_seamless','meteofrance_seamless')
_states={}


def timestamp(v):return dt.datetime.fromisoformat(str(v).replace('Z','+00:00'))
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()
def family(station,unit):return PREFIX+'awc-routine-v1:'+station+':'+unit


def prepare(read,now,rows=None):
    global _states
    _states={}
    try:
        if rows is None:
            rows=read('derived_wx_city_calibration',[('select','state'),('model_version','like.'+PREFIX+'*'),
                ('computed_at','lte.'+now.isoformat())],order='model_version.asc,city_key.asc,phase.asc',page_size=1000)
        _states={(r['state']['city_key'],r['state']['phase'],r['state']['model_version']):r['state'] for r in rows if r['state'].get('policy')==POLICY}
    except Exception:
        pass  # no learned weights is the explicitly recorded uniform prior


def saved_market(quotes,ids):
    """Same bounded mark as Tick; incomplete markets never gate source capture."""
    from tick import market_price
    market={}
    try:
        for bid in ids:
            q=quotes.get(bid) or {}
            clean={k:None if q.get(k) is None else float(q[k]) for k in ('bid','ask','last')}
            if any(v is not None and (not math.isfinite(v) or not 0<=v<=1) for v in clean.values()):
                return None,'invalid saved quote'
            if clean['bid'] is None and clean['ask'] is None:
                return None,'missing saved quoted band'
            if clean['bid'] is not None and clean['ask'] is not None and clean['bid']>clean['ask']:
                return None,'crossed saved quote'
            value=market_price(clean)
            if value is None:return None,'saved quote has no comparable mark'
            market[bid]=value
        z=sum(market.values())
        if z<=0:return None,'zero saved market mass'
        return {k:v/z for k,v in market.items()},'complete saved ladder using bounded Tick marks'
    except (TypeError,ValueError,KeyError,OverflowError):
        return None,'malformed saved quote'


def canonical_sources(sources):
    """Accept the recorded provider names without changing values or identity."""
    values={}; names={}
    for name,value in sources.items():
        canonical=name.removeprefix('open_meteo_')
        if canonical not in MODELS or canonical in values:
            return None,None
        values[canonical]=value
        names[canonical]=name
    return (values,names) if set(values)==set(MODELS) else (None,None)


def capture(row,bands,unit,context,now=None,diagnostics=None):
    import probability_engine as pe
    def refuse(reason):
        if diagnostics is not None:diagnostics.update(status='unavailable',reason=reason)
        return None
    if row['checkpoint'] not in ('day_ahead','d1_eve','morning'):return None
    if row.get('inputs_ok',True) is not True:return refuse('original pricing inputs ineligible')
    if not context:return refuse('dated city checkpoint context unavailable')
    if context.get('contract')!='awc-routine-v1':return refuse('supported dated station resolution contract unavailable')
    now=now or dt.datetime.now(dt.timezone.utc)
    inputs=((row.get('predictive_context') or {}).get('forecast_input') or {}).get('station_combination') or {}
    sources,source_names=canonical_sources(inputs.get('sources') or {})
    if sources is None:return refuse('seven original station-corrected sources unavailable')
    try:
        if row.get('station')!=context.get('station') or not context.get('rules_received_at') or timestamp(context['rules_received_at'])>now or len(context.get('rules_sha256') or '')!=64:return refuse('dated station or rule receipt mismatch')
        if any(not math.isfinite(float(v)) or not -90<=float(v)<=70 for v in sources.values()):return refuse('invalid source temperature')
        oldest=timestamp(inputs['inputs_oldest_run_at']);newest=timestamp(inputs['inputs_newest_run_at'])
        if not 0<=(now-oldest).total_seconds()<=8*3600 or not oldest<=newest<=now:return refuse('source run stale or future')
        sigma=float(row['sigma_c'])
        if not math.isfinite(sigma) or sigma<=0:return refuse('invalid original uncertainty')
        components={m:dict(pe.compute_band_probabilities(float(sources[m]),sigma,unit,bands,row.get('running_max_c'))) for m in MODELS}
        ids=set(row['probs'])
        market,market_reason=saved_market(row.get('market') or {},ids)
        version=family(context['station'],unit)
        state=_states.get((row['city_key'],row['checkpoint'],version),{})
        age=(dt.date.fromisoformat(row['target_date'])-dt.date.fromisoformat(state.get('as_of',row['target_date']))).days
        usable=state.get('policy')==POLICY and 0<=age<=3 and state.get('as_of','9999')<=row['target_date']
        weights=state['weights'] if usable else {m:1/7 for m in MODELS}
        weighted={k:sum(weights[m]*components[m][k] for m in MODELS) for k in ids}
        # Magnitudes are bounded; one last-cycle winner never replaces the blend.
        candidate={k:(.8 if market else .9)*row['probs'][k]+.1*weighted[k]+(.1*market[k] if market else 0.) for k in ids}
        return dict(policy=POLICY,model_version=version,station=context['station'],unit=unit,
            rules_sha256=context['rules_sha256'],rules_received_at=context['rules_received_at'],
            captured_at=now.isoformat(),input_oldest=oldest.isoformat(),input_newest=newest.isoformat(),
            source_probs=components,source_values_c=dict(sources),source_names=source_names,incumbent_probs=dict(row['probs']),
            market_probs=market,market_comparable=market is not None,market_reason=market_reason,
            candidate_probs=candidate,weight_version=state.get('version') if usable else 'uniform-prior',
            training_dates=state.get('learning_dates',0) if usable else 0,weights_sha256=digest(weights),changes_serving=False)
    except (KeyError,ValueError,TypeError,OverflowError) as exc:return refuse('invalid original input: '+type(exc).__name__)


def metrics(q,winner):
    return dict(log_loss=-math.log(max(1e-6,q[winner])),brier=sum((p-int(k==winner))**2 for k,p in q.items()),
                hit=int(min(q,key=lambda k:(-q[k],k))==winner))


def eligible(rows,as_of):
    out=[];first={}
    for r in rows:
        key=(r['city_key'],r['target_date'],r['checkpoint'])
        if key not in first or (r.get('frozen_at') or '9999')<(first[key].get('frozen_at') or '9999'):first[key]=r
    for r in first.values():
        p=r.get('ensemble_packet') or {};winner=r.get('winner')
        try:
            if p.get('policy')!=POLICY or r.get('inputs_ok',True) is not True or not r.get('verified') or not r.get('settled') or not winner:continue
            if winner not in r['probs'] or p['station']!=r.get('station') or len(p.get('rules_sha256',''))!=64 or timestamp(p['rules_received_at'])>timestamp(p['captured_at']):continue
            if p['model_version']!=family(p['station'],p['unit']) or set(p['source_probs'])!=set(MODELS):continue
            if p['incumbent_probs']!=r['probs'] or not (timestamp(p['input_oldest'])<=timestamp(p['input_newest'])<=timestamp(p['captured_at'])<=timestamp(r['frozen_at'])<timestamp(r['settled_at'])<=timestamp(r['label_received_at'])<as_of):continue
            if (timestamp(p['captured_at'])-timestamp(p['input_oldest'])).total_seconds()>8*3600:continue
            if dt.date.fromisoformat(r['target_date'])>=as_of.date():continue
            if dt.date.fromisoformat(r['target_date'])<as_of.date()-dt.timedelta(days=90):continue
            for q in [*p['source_probs'].values(),p['candidate_probs'],*([p['market_probs']] if p.get('market_probs') is not None else [])]:
                if set(q)!=set(r['probs']) or abs(sum(q.values())-1)>1e-4 or any(not math.isfinite(v) or not 0<=v<=1 for v in q.values()):raise ValueError('bad original ladder')
            out.append(dict(r,packet=p))
        except (KeyError,ValueError,TypeError):continue
    return out


def fit(rows,as_of,previous=()):
    cutoff=dt.datetime.combine(as_of,dt.time(),dt.timezone.utc)
    prior={(s['city_key'],s['phase'],s['model_version']):s for s in previous if s.get('policy')==POLICY}
    good=eligible(rows,cutoff);groups=collections.defaultdict(list)
    for r in good:groups[(r['city_key'],r['checkpoint'],r['packet']['model_version'])].append(r)
    states=[];split=as_of-dt.timedelta(days=VALIDATION_DAYS)
    for (city,cp,version),mine in sorted(groups.items()):
        train=[r for r in mine if r['target_date']<str(split)]
        val=[r for r in mine if r['target_date']>=str(split) and r['packet']['training_dates']>=MIN_TRAIN and r['packet'].get('market_probs') is not None]
        losses={};local_dates=len({r['target_date'] for r in train})
        for m in MODELS:
            # Next-call shadow weights may use every label already received.
            # Promotion is judged only on the older/later forward cohorts below.
            xs=[(2**(-(as_of-dt.date.fromisoformat(r['target_date'])).days/14),metrics(r['packet']['source_probs'][m],r['winner'])['log_loss']) for r in mine]
            losses[m]=sum(w*min(12.,v) for w,v in xs)/sum(w for w,_ in xs) if xs else 0.
        anchor=min(losses.values());soft={m:math.exp(-(v-anchor)) for m,v in losses.items()};z=sum(soft.values())
        learning_dates=len({r['target_date'] for r in mine})
        desired={m:.8/7+.2*soft[m]/z if learning_dates>=MIN_WEIGHT_DATES else 1/7 for m in MODELS}
        old=prior.get((city,cp,version),{})
        base=old.get('weights') or {m:1/7 for m in MODELS}
        # A retry on the same nightly cutoff cannot walk the step repeatedly.
        if old.get('as_of')==str(as_of):
            weights=dict(base)
        else:
            step=max(abs(desired[m]-base[m]) for m in MODELS)
            alpha=min(1.,MAX_WEIGHT_STEP/step) if step else 1.
            weights={m:base[m]+alpha*(desired[m]-base[m]) for m in MODELS}
        evidence={};passed=local_dates>=MIN_TRAIN and len({r['target_date'] for r in val})>=MIN_VALIDATE
        for ref in ('incumbent','market'):
            a=[metrics(r['packet']['candidate_probs'],r['winner']) for r in val]
            b=[metrics(r['packet'][ref+'_probs'],r['winner']) for r in val]
            gains=[y['log_loss']-x['log_loss'] for x,y in zip(a,b)]
            rng=random.Random(20261111)
            lo=sorted(sum(rng.choices(gains,k=len(gains)))/len(gains) for _ in range(1000))[49] if gains else None
            evidence[ref]=dict(n=len(a),gain_lower90=lo,candidate_hits=sum(x['hit'] for x in a),reference_hits=sum(y['hit'] for y in b))
            passed=passed and lo is not None and lo>0 and sum(x['hit'] for x in a)>=sum(y['hit'] for y in b) and sum(x['brier'] for x in a)<=sum(y['brier'] for y in b)
        state=dict(policy=POLICY,model_version=version,city_key=city,phase=cp,as_of=str(as_of),weights=weights,
            source_ranking=sorted(MODELS,key=lambda m:(losses[m],m)),source_log_losses=losses,
            train_dates=local_dates,validation_dates=len({r['target_date'] for r in val}),evidence=evidence,
            learning_dates=learning_dates,minimum_weight_dates=MIN_WEIGHT_DATES,maximum_weight_step=MAX_WEIGHT_STEP,
            decision='validated_shadow' if passed else 'held',changes_serving=False,
            reason='original forward calls passed both benchmarks' if passed else 'insufficient original dates or gains against incumbent and saved market unproven')
        state['version']=PREFIX+digest(state)[:16];states.append(state)
    return states


def summary(state):
    return {k:v for k,v in state.items() if k not in ('weights','source_log_losses')}
