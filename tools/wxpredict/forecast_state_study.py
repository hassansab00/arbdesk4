"""Local-only development evaluation of the shared forecast-state correction.

Input is a private export of first calls and exact band geometry. This script
has no database client, deployment switch or scheduler. It refuses overwrites.
"""
import argparse
import collections
import datetime as dt
import json
import math
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wxpredict import forecast_state as model, pre_day_pairs as pp


def prepare(packets,bands,through):
    """First-call identity is checked before any exclusions are applied."""
    chosen={};seen={}
    for packet in packets:
        c=packet['call'];identity=str(c['checkpoint_id']);digest=pp.digest(packet)
        if identity in seen and seen[identity]!=digest:
            raise ValueError('conflicting versions of an original call')
        seen[identity]=digest
        key=(c['city_key'],c['target_date'],c['checkpoint'])
        order=(pp.timestamp(c['decided_at']),identity)
        if key not in chosen or order<chosen[key][0]:chosen[key]=(order,packet)
    records=[];excluded=collections.Counter()
    for _,packet in sorted(chosen.values(),key=lambda v:v[0]):
        r,reason=model.attach(packet,bands,through)
        if r is not None:records.append(r)
        if reason:excluded[reason]+=1
    model.unique(records)
    return records,dict(first_calls=len(chosen),included=len(records),
                        excluded=dict(excluded),pending=sum(r['label'] is None for r in records))


def compare(records,predictions):
    if len(records)!=len(predictions):raise ValueError('comparison alignment mismatch')
    original=[r['incumbent'] for r in records]
    quotes=[model.market(r) for r in records]
    selected=[i for i,p in enumerate(quotes) if p is not None]
    matched=[records[i] for i in selected]
    candidate=[predictions[i] for i in selected]
    incumbent=[original[i] for i in selected]
    market=[quotes[i] for i in selected]
    return dict(candidate=model.scores(records,predictions),
                incumbent=model.scores(records,original,tops=[r['incumbent_top'] for r in records]),
                market_matched=dict(candidate=model.scores(matched,candidate),
                                    incumbent=model.scores(matched,incumbent,tops=[r['incumbent_top'] for r in matched]),
                                    market=model.scores(matched,market)),
                gained_hits=sum(max(p,key=p.get)==r['label']['winner'] and r['incumbent_top']!=r['label']['winner'] for r,p in zip(records,predictions)),
                lost_hits=sum(max(p,key=p.get)!=r['label']['winner'] and r['incumbent_top']==r['label']['winner'] for r,p in zip(records,predictions)))


def paired_date_interval(records,predictions,repetitions=2000):
    """Paired date-block uncertainty, not independent city/slot resampling."""
    import random
    if len(records)!=len(predictions) or repetitions<20:
        raise ValueError('invalid date-block comparison')
    groups=collections.defaultdict(list)
    for r,p in zip(records,predictions):
        winner=r['label']['winner'];q=r['incumbent']
        groups[r['date']].append((int(max(p,key=p.get)==winner)-int(r['incumbent_top']==winner),
                                 -math.log(max(p[winner],1e-12))+math.log(max(q[winner],1e-12))))
    dates=sorted(groups);rng=random.Random(20261102);samples=[[],[]]
    if len(dates)<2:return dict(dates=len(dates),hit_delta_90=None,log_loss_delta_90=None)
    for _ in range(repetitions):
        chosen=[rng.choice(dates) for _ in dates]
        count=sum(len(groups[d]) for d in chosen)
        for i in range(2):samples[i].append(sum(sum(v[i] for v in groups[d]) for d in chosen)/count)
    for values in samples:values.sort()
    index=lambda v:[v[int(.05*(repetitions-1))],v[int(.95*(repetitions-1))]]
    return dict(dates=len(dates),hit_delta_90=index(samples[0]),log_loss_delta_90=index(samples[1]),
                interpretation='Development-date uncertainty only; inspected dates and short histories cannot approve activation.')


def run(packets,bands,through,fit_last,eval_first,eval_last,out):
    destination=Path(out).resolve()
    repository=Path(__file__).resolve().parents[2]
    if destination==repository or destination.is_relative_to(repository) or destination.exists():
        raise ValueError('use a new private output directory outside the repository')
    if pp.timestamp(through)>dt.datetime.now(dt.timezone.utc):raise ValueError('future research cutoff')
    if not fit_last<eval_first<=eval_last:raise ValueError('invalid chronological windows')
    records,coverage=prepare(packets,bands,through)
    test=[r for r in records if eval_first<=r['date']<=eval_last and r['label']]
    if not test:raise ValueError('no labeled evaluation calls')
    boundary=min((pp.timestamp(r['as_of']) for r in test)).isoformat()
    possible=[r for r in records if r['date']<=fit_last]
    train=[r for r in possible if r['label'] and pp.timestamp(r['label']['available_at'])<pp.timestamp(boundary)
           and pp.timestamp(r['frozen_at'])<pp.timestamp(boundary)]
    artifact=model.fit(train,boundary)
    predictions=model.predict(artifact,test)
    groups={}
    for field,values in (('checkpoint',sorted({r['checkpoint'] for r in test})),
                         ('state',sorted({model.state(r) for r in test})),
                         ('peak_status',sorted({r['peak_status'] for r in test})),
                         ('city',sorted({r['city'] for r in test})),
                         ('date',sorted({r['date'] for r in test}))):
        groups[field]={}
        for value in values:
            ids=[i for i,r in enumerate(test) if (model.state(r) if field=='state' else r[field])==value]
            groups[field][value]=compare([test[i] for i in ids],[predictions[i] for i in ids])
            if field in ('checkpoint','state'):
                groups[field][value]['date_block_uncertainty']=paired_date_interval(
                    [test[i] for i in ids],[predictions[i] for i in ids])
    report=dict(policy=model.POLICY,evidence='Retrospective development; no prospective or trading claim.',
                coverage=coverage,training=artifact['training'],purged_training_labels=len(possible)-len(train),
                evaluation=[eval_first,eval_last],scores=compare(test,predictions),groups=groups,
                date_block_uncertainty=paired_date_interval(test,predictions),
                evaluation_sha256=pp.digest(test),model_sha256=artifact['sha256'],
                changes_serving=False,eligible_for_promotion=False,
                limitations=['A seasonal peak reference is not the realized peak or proof the maximum was not already reached.',
                    'Morning calls without saved peak evidence are a separate stratum, never claimed pre-peak.',
                    'No exact temperature error is inferred from a winning bracket.',
                    'Unknown historical rule context stays unknown; it cannot qualify future activation.',
                    'This calibrates existing weather opinions; it does not repair cold weather inputs or add new weather information.',
                    'One fixed shared model and bounded logits; no city model selection or tuning on evaluation dates.',
                    'Fitting today cannot create an original historical freeze or an independent future validation.',
                    'Six or fewer development dates cannot establish seasonal robustness.'])
    report['development_holds']=['retrospective_only_no_activation_evidence']
    for slot,score in groups['checkpoint'].items():
        c,b=score['candidate'],score['incumbent']
        if c['hits']<b['hits']:report['development_holds'].append(slot+':hit_regression')
        if c['log_loss']>b['log_loss']:report['development_holds'].append(slot+':log_loss_regression')
        if c['brier']>b['brier']:report['development_holds'].append(slot+':brier_regression')
        matched=score['market_matched']
        if matched['candidate']['hits']<matched['market']['hits']:
            report['development_holds'].append(slot+':below_captured_market_hit_rate')
    destination.mkdir(parents=True,exist_ok=False)
    files={'dataset.json':dict(rows=records,coverage=coverage),'candidate.json':artifact,'evaluation.json':report,
           'development_predictions.json':dict(model_sha256=artifact['sha256'],evidence=report['evidence'],
                 calls=[dict(checkpoint_id=r['checkpoint_id'],city=r['city'],date=r['date'],checkpoint=r['checkpoint'],
                             as_of=r['as_of'],feature_sha256=r['feature_sha256'],probs=p) for r,p in zip(test,predictions)])}
    for name,data in files.items():(destination/name).write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calls',nargs='+',required=True)
    for field in ('bands','through','fit-last','eval-first','eval-last','out'):parser.add_argument('--'+field,required=True)
    a=parser.parse_args(argv)
    packets=[r for name in a.calls for r in json.loads(Path(name).read_text())['rows']]
    bands={str(b['band_id']):b for b in json.loads(Path(a.bands).read_text())['rows']}
    report=run(packets,bands,a.through,a.fit_last,a.eval_first,a.eval_last,a.out)
    print(json.dumps({k:v for k,v in report.items() if k!='groups'},indent=2))


if __name__=='__main__':main()
