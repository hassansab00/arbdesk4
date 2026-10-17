"""Score original served calls; fit bounded city calibration on prior labels.

Run nightly to learn and score, or --score-only after venue confirmation.
No reads of blinded shadow experiments; no rewriting a call or outcome.
"""
import argparse
import datetime as dt
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from common import get_cities, log_run, rest_all, upsert
from tick import CHECKPOINTS, decision_times
import wx_city_calibration as cal

JOB = 'P7.10_city_prediction_validation'
HORIZONS = ('day_ahead', *CHECKPOINTS)
OVERALL = tuple(h for h in HORIZONS if h != 'postpeak_1h')
ROOT = Path(__file__).resolve().parents[1]


def wilson(hits, n):
    if not n:
        return None
    p, z = hits/n, 1.95996398454
    d = 1+z*z/n
    mid, delta = (p+z*z/(2*n))/d, z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0,mid-delta),min(1,mid+delta)]


def collect(checkpoints, outcomes, day_ahead, cities, peaks, now):
    """A later perfect replacement never wins selection over an original miss."""
    city_of = {c['city_key']:c for c in cities}
    facts = {f['checkpoint_id']:f for f in outcomes}
    first = {}
    for p in checkpoints:
        key = (p['city_key'],p['target_date'],p['checkpoint'])
        if key not in first or (p['decided_at'],p['checkpoint_id']) < (first[key]['decided_at'],first[key]['checkpoint_id']):
            first[key] = p
    out = []
    for p in first.values():
        c = city_of.get(p['city_key'])
        if c is None:
            continue
        day = dt.date.fromisoformat(p['target_date'])
        end = dt.datetime.combine(day+dt.timedelta(days=1),dt.time(),ZoneInfo(c['timezone']))
        received = cal.timestamp(p.get('frozen_at') or p['decided_at'])
        times = decision_times(p['target_date'],c['timezone'],peaks.get((p['city_key'],day.month)))
        plan = times.get(p['checkpoint'])
        if (not plan or not plan[1] <= received <= plan[1]+dt.timedelta(minutes=75)
            or received >= end or cal.timestamp(p['decided_at']) > received
            or str(p['local_decision_time'])[:16] != plan[0].isoformat()[:16]):
            continue
        f = facts.get(p['checkpoint_id'])
        settled = (f and f.get('settled_at') and received < cal.timestamp(f['settled_at']) <= now
                   and cal.timestamp(f['banked_at']) <= now and now >= end)
        winner = f['winner_band_id'] if settled else None
        probs = p['probs']
        top = min(probs,key=lambda k:(-float(probs[k]),k))
        r = dict(city_key=p['city_key'],target_date=p['target_date'],checkpoint=p['checkpoint'],
                 version=p.get('priced_from') or p['engine_version'],settled=bool(settled),
                 top_prob=float(probs[top]),verified=p.get('frozen_at') is not None,
                 metrics=cal.metrics(probs,winner) if settled else None,
                 baseline=None,raw=None)
        if settled:
            for src,dest in [('baseline_probs','baseline'),('raw_probs','raw')]:
                q=p.get(src)
                if q and set(q)==set(probs):
                    r[dest]=cal.metrics(q,winner)
        out.append(r)
    for p in day_ahead:
        c=city_of.get(p['city_key'])
        if not c or not p.get('called_at') or p.get('hit') is None:
            continue
        start=dt.datetime.combine(dt.date.fromisoformat(p['for_date']),dt.time(),ZoneInfo(c['timezone']))
        if not cal.timestamp(p['called_at']) < start or now < start+dt.timedelta(days=1):
            continue
        prob=p.get('prob_on_winner')
        out.append(dict(city_key=p['city_key'],target_date=p['for_date'],checkpoint='day_ahead',
            version=p.get('engine_version') or 'day-ahead served',settled=True,verified=False,
            top_prob=float(p.get('predicted_pct') or 0)/100,
            metrics={'hit':int(p['hit']),'log_loss':-math.log(max(1e-6,float(prob))) if prob is not None else None,
                     'brier':None},baseline=None,raw=None))
    return out


def average_metrics(rows, key='metrics'):
    pairs=[r[key] for r in rows if r.get(key)]
    return dict(n=len(pairs),**{k:statistics.fmean(p[k] for p in pairs if p[k] is not None)
                if any(p[k] is not None for p in pairs) else None for k in ('hit','log_loss','brier')})


def summarize(rows, horizon):
    settled=[r for r in rows if r['settled']]
    hits=sum(r['metrics']['hit'] for r in settled)
    n=len(settled)
    interval=wilson(hits,n)
    if horizon=='overall' and settled:
        by_date=defaultdict(list)
        for r in settled:
            by_date[r['target_date']].append(r['metrics']['hit'])
        values=[statistics.fmean(v) for v in by_date.values()]
        rng=random.Random(20261017)
        boot=sorted(statistics.fmean(rng.choices(values,k=len(values))) for _ in range(1000))
        interval=[boot[25],boot[974]]
    bins=[]
    for lo in range(0,100,10):
        bucket=[r for r in settled if lo/100<=r['top_prob']<(lo+10)/100 or lo==90 and r['top_prob']==1]
        if bucket:
            bins.append(dict(lo=lo/100,n=len(bucket),stated=statistics.fmean(r['top_prob'] for r in bucket),
                             actual=statistics.fmean(r['metrics']['hit'] for r in bucket)))
    gap=sum(b['n']*abs(b['stated']-b['actual']) for b in bins)/n if n else None
    return dict(n=n,hits=hits,hit_rate=hits/n if n else None,interval95=interval,
                dates=len({r['target_date'] for r in settled}),pending=None if horizon in ('day_ahead','overall') else sum(not r['settled'] for r in rows),
                verified=sum(r['verified'] for r in settled),legacy=sum(not r['verified'] for r in settled),
                metrics=average_metrics(settled),baseline=average_metrics(settled,'baseline'),
                raw=average_metrics(settled,'raw'),calibration_gap=gap,reliability=bins,
                versions=dict(Counter(r['version'] for r in settled)))


def diagnostic(rows,reviews):
    misses={r['target_date'] for r in rows if r['settled'] and not r['metrics']['hit']}
    checked=[r for r in reviews if r['target_date'] in misses]
    weather=Counter()
    shifts=[]
    errors=[]
    for r in checked:
        for s in r.get('surprises') or []:
            if abs(float(s.get('z') or 0))>=1:
                weather[s['what']]+=1
        if r.get('peak_local_hour') is not None and r.get('usual_peak_hour') is not None:
            shifts.append(abs(float(r['peak_local_hour'])-float(r['usual_peak_hour'])))
        if r.get('forecast_error_c') is not None:
            errors.append(float(r['forecast_error_c']))
    return dict(missed_dates=len(misses),reviewed_dates=len(checked),weather=dict(weather.most_common()),
                mean_peak_shift_h=statistics.fmean(shifts) if shifts else None,
                public_forecast_mae_c=statistics.fmean(abs(x) for x in errors) if errors else None,
                public_forecast_bias_c=statistics.fmean(errors) if errors else None,
                interpretation='Observed associations on reviewed misses; not causal attribution to WXPredict.')


def reports(rows,cities,reviews,states,now,model_version=None):
    period_start=(now.date()-dt.timedelta(days=29)).isoformat()
    period_end=now.date().isoformat()
    rows=[r for r in rows if period_start<=r['target_date']<=period_end]
    result=[]
    for c in cities:
        city=c['city_key']
        for scope in ('served','wxrd3'):
            group=[r for r in rows if r['city_key']==city and (scope=='served' or r['version'].startswith('wxrd3:'))]
            for horizon in (*HORIZONS,'overall'):
                if horizon=='overall':
                    dates=[{r['target_date'] for r in group if r['checkpoint']==h and r['settled']} for h in OVERALL]
                    common=set.intersection(*dates) if dates else set()
                    chosen=[r for r in group if r['checkpoint'] in OVERALL and r['target_date'] in common]
                else:
                    chosen=[r for r in group if r['checkpoint']==horizon]
                report=summarize(chosen,horizon)
                report.update(city=city,display_name=c.get('display_name') or city,timezone=c['timezone'],
                    scope=scope,horizon=horizon,period_start=period_start,period_end=period_end,
                    why=diagnostic(chosen,[r for r in reviews if r['city_key']==city]),
                    learning=[dict(s,active=cal.usable(s,now.date().isoformat(),model_version),
                                   effective_params=s['params'] if cal.usable(s,now.date().isoformat(),model_version) else cal.IDENTITY)
                              for s in states if s['city_key']==city and s['model_version']==model_version])
                result.append(dict(city_key=city,checkpoint=horizon,scope=scope,as_of=now.isoformat(),report=report))
    return result


def run(score_only=False):
    now=dt.datetime.now(dt.timezone.utc)
    first=(now.date()-dt.timedelta(days=30)).isoformat()
    cities=get_cities(require_coords=False,extra=('display_name',))
    p=rest_all('prediction_checkpoints',[('select','*'),('target_date','gte.'+first)],order='checkpoint_id.asc',page_size=1000)
    outcomes=rest_all('fact_checkpoint_outcome',[('select','checkpoint_id,winner_band_id,settled_at,banked_at'),('target_date','gte.'+first)],order='checkpoint_id.asc',page_size=1000)
    da=rest_all('v_day_ahead_frozen_validation',[('select','*'),('for_date','gte.'+first)],order='city_key.asc,for_date.asc',page_size=1000)
    reviews=rest_all('derived_day_review',[('select','*'),('target_date','gte.'+first)],order='city_key.asc,target_date.asc',page_size=1000)
    peak_rows=rest_all('derived_weather_peak',[('select','city_key,month,peak_hour_local')],order='city_key.asc,month.asc',page_size=1000)
    peaks={(r['city_key'],int(r['month'])):r['peak_hour_local'] for r in peak_rows}
    states=[r['state'] for r in rest_all('derived_wx_city_calibration',[('select','state')],order='model_version.asc,city_key.asc,phase.asc',page_size=1000)]
    meta=json.loads((ROOT/'data/models/wxrd/wxrd-v3_remaining.json').read_text())
    # Same version identity as the serving/shadow readers; no ML dependency.
    import sys
    sys.path.insert(0,str(ROOT/'tools'))
    from wxpredict.wxrd_live import variant_version
    version=variant_version(meta,'wxrd3')
    detail={'score_only':score_only,'active_cities':len(cities)}
    if not score_only:
        learning=rest_all('v_wx_frozen_learning',[('select','*'),('target_date','gte.'+(now.date()-dt.timedelta(days=180)).isoformat())],order='city_key.asc,target_date.asc,checkpoint.asc',page_size=1000)
        fitted=cal.fit(learning,[c['city_key'] for c in cities],version,now.date(),states)
        path=ROOT/'data/eval/wxpredict/city_calibration'/f'{now.date()}.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(dict(as_of=now.isoformat(),policy=cal.POLICY,states=fitted),sort_keys=True,indent=2)+'\n')
        upsert('derived_wx_city_calibration',[dict(model_version=s['model_version'],city_key=s['city_key'],phase=s['phase'],as_of=s['as_of'],version=s['version'],state=s) for s in fitted], 'model_version,city_key,phase')
        states=[s for s in states if s['model_version']!=version]+fitted
        detail.update(calibration_cells=len(fitted),decisions=dict(Counter(s['decision'] for s in fitted)),eligible_learning_calls=len(cal.eligible_rows(learning,now.date(),version)))
    data=reports(collect(p,outcomes,da,cities,peaks,now),cities,reviews,states,now,version)
    n=upsert('derived_city_prediction_validation',data,'city_key,checkpoint,scope')
    log_run(JOB,'ok',n,detail)
    print(json.dumps(dict(rows=n,**detail)),flush=True)
    return detail


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--score-only',action='store_true')
    run(parser.parse_args().score_only)
