"""Build a private, receipt-aware weather candidate and development comparison.

Read-only research entry point. No serving hooks, network calls, database writes
or automatic promotion. Use a private output directory outside the repository.
"""
import argparse
import collections
import datetime as dt
import json
import math
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import pre_day_weather_data as data, pre_day_weather_model as model, pre_day_pairs as pp


def split(records, fit_last, first, last):
    model.unique(records)
    if not dt.date.fromisoformat(fit_last) < dt.date.fromisoformat(first) <= dt.date.fromisoformat(last):
        raise ValueError('weather study chronological windows overlap')
    test = [r for r in records if first<=r['features']['date']<=last and r.get('label') and r.get('original')]
    if not test:
        raise ValueError('no verified original evaluation calls')
    earliest = min(pp.timestamp(r['features']['as_of']) for r in test)
    candidates = [r for r in records if r['features']['date']<=fit_last and r.get('label')]
    train = [r for r in candidates if pp.timestamp(r['label']['available_at'])<earliest]
    return train,test,dict(first_evaluation_decision=earliest.isoformat(),
                          purged_training_labels=len(candidates)-len(train),
                          excluded_evaluation_replays=sum(first<=r['features']['date']<=last and not r.get('original') for r in records))


def score(q, winner, top=None):
    q = pp.distribution(q)
    if winner not in q:
        raise ValueError('winner missing from scored distribution')
    return dict(hit=int((top or max(q,key=q.get))==winner),
                log_loss=-math.log(max(1e-12,q[winner])),
                brier=sum((p-int(k==winner))**2 for k,p in q.items()))


def aggregate(scores):
    n=len(scores)
    return dict(n=n,hits=sum(s['hit'] for s in scores),
                hit_rate=sum(s['hit'] for s in scores)/n if n else None,
                log_loss=sum(s['log_loss'] for s in scores)/n if n else None,
                brier=sum(s['brier'] for s in scores)/n if n else None)


def comparison(records, predicted):
    if len(records)!=len(predicted):raise ValueError('weather prediction count mismatch')
    if any(not r.get('original') or not r.get('label') for r in records):
        raise ValueError('input replays cannot count as original prediction accuracy')
    out={}
    for name in ('candidate','incumbent','market'):
        scores=[]
        for r,p in zip(records,predicted):
            q=p['probs'] if name=='candidate' else r.get(name)
            if q is not None:
                scores.append(score(q,r['label']['winner'],r['original']['top'] if name=='incumbent' else None))
        out[name]=aggregate(scores)
    matched=[(r,p) for r,p in zip(records,predicted) if r.get('market') is not None]
    out['matched_market']={name:aggregate([score(p['probs'] if name=='candidate' else r[name],r['label']['winner'],
        r['original']['top'] if name=='incumbent' else None) for r,p in matched]) for name in ('candidate','incumbent','market')}
    return out


def run(first,fit_last,eval_first,eval_last,through,out):
    if not dt.date.fromisoformat(first)<=dt.date.fromisoformat(fit_last)<dt.date.fromisoformat(eval_first)<=dt.date.fromisoformat(eval_last):
        raise ValueError('invalid weather study windows')
    if pp.timestamp(through)>model.utc_now():raise ValueError('future weather evidence cutoff')
    destination=Path(out).resolve()
    if destination.is_relative_to(Path(data.common.ROOT).resolve()):
        raise ValueError('private weather artifacts must be outside the repository')
    if destination.exists():raise ValueError('weather output already exists; frozen artifacts cannot be replaced')
    records,coverage=data.build(first,eval_last,through)
    train,test,timing=split(records,fit_last,eval_first,eval_last)
    candidate=model.fit(train)
    predicted=model.predictions(candidate,test)
    daily={day:comparison([r for r in test if r['features']['date']==day],
                         [p for r,p in zip(test,predicted) if r['features']['date']==day])
           for day in sorted({r['features']['date'] for r in test})}
    cities={city:comparison([r for r in test if r['features']['city']==city],
                           [p for r,p in zip(test,predicted) if r['features']['city']==city])
            for city in sorted({r['features']['city'] for r in test})}
    age=[(pp.timestamp(r['features']['as_of']).timestamp()-p['available_at'])/3600
         for r in train+test for p in r['features']['providers'].values()]
    report=dict(recipe=model.RECIPE,evidence='retrospective development only; candidate did not exist at these decisions',
        evaluation=[eval_first,eval_last],training=candidate['training'],timing=timing,coverage=coverage,
        scores=comparison(test,predicted),by_day=daily,by_city=cities,
        input_quality=dict(max_receipt_age_hours=max(age),mean_receipt_age_hours=sum(age)/len(age),
                           provider_leads=dict(collections.Counter(str(p['lead_days']) for r in train+test for p in r['features']['providers'].values()))),
        model_sha256=candidate['sha256'],dataset_sha256=pp.digest(records),
        changes_serving=False,eligible_for_promotion=False,
        limitations=['Short, previously inspected period; no independent forward validation or profit claim.',
                     'Venue winners are interval labels, not invented exact temperature observations.',
                     'Daily maximum features cannot describe intraday wind, cloud, humidity or weather shifts.',
                     'Autumn-only training does not establish seasonal generalization.',
                     'Provider coordinates, exact hourly coverage and historical contract-family metadata are incomplete.',
                     'No fitted candidate can activate itself; future validation and serving approval are separate.'])
    destination.mkdir(parents=True,exist_ok=False)
    artifacts={'dataset.json':dict(policy=data.POLICY,rows=records,coverage=coverage,sha256=pp.digest(records)),
               'candidate.json':candidate,'evaluation.json':report,
               'development_predictions.json':dict(evidence=report['evidence'],model_sha256=candidate['sha256'],
                   calls=[dict(city=r['features']['city'],date=r['features']['date'],as_of=r['features']['as_of'],
                               feature_sha256=r['feature_sha256'],checkpoint_id=r['original']['checkpoint_id'],**p)
                          for r,p in zip(test,predicted)])}
    for filename,value in artifacts.items():
        (destination/filename).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('first','fit-last','eval-first','eval-last','through','out'):
        parser.add_argument('--'+key,required=True)
    args=parser.parse_args(argv)
    report=run(args.first,args.fit_last,args.eval_first,args.eval_last,args.through,args.out)
    print(json.dumps({k:v for k,v in report.items() if k not in ('coverage','by_day','by_city')},indent=2))


if __name__=='__main__':main()
