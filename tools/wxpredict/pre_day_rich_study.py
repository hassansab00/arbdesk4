"""Private miss diagnostics and fixed received-weather development experiment."""
import argparse
import collections
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import pre_day_pairs as pp, pre_day_weather_data as data
from wxpredict import pre_day_weather_model as simple, pre_day_weather_study as study
from wxpredict import pre_day_hourly as hourly, pre_day_conditioned as model


def interval_distance_c(value,record):
    """Minimum error allowed by the winning interval, never an exact MAE."""
    if value is None:return None
    lo,hi=record['bounds'][record['band_ids'].index(record['label']['winner'])]
    def edge(v):return (v-.5-32)*5/9 if record['features']['unit']=='F' else v-.5
    return max(0.,edge(lo)-value if lo is not None else 0.,value-edge(hi) if hi is not None else 0.)


def diagnose(records,predictions):
    if len(records)!=len(predictions):raise ValueError('diagnostic prediction count mismatch')
    sys.path.insert(0,str(Path(data.common.ROOT)/'scripts'))
    import early_forecast_audit
    cases=[]
    for r,p in zip(records,predictions):
        f=r['features'];winner=r['label']['winner'];candidate=max(p['probs'],key=p['probs'].get)
        old=r['original']['top'];distance=interval_distance_c(r.get('original_centre_c'),r)
        audit=early_forecast_audit.score(r.get('raw_audit') or {},r['incumbent'],winner)
        correction=None
        if audit:
            raw=audit['raw_centre']['hit'];served=audit.get('served',audit['corrected'])['hit']
            correction='helped' if served>raw else 'hurt' if served<raw else 'same_hit_result'
        cases.append(dict(city=f['city'],date=f['date'],as_of=f['as_of'],checkpoint_id=r['original']['checkpoint_id'],
             winner=winner,candidate_top=candidate,incumbent_top=old,candidate_hit=int(candidate==winner),
             incumbent_hit=int(old==winner),candidate_bucket_distance=abs(r['band_ids'].index(candidate)-r['band_ids'].index(winner)),
             incumbent_centre_minimum_error_c=distance,
             centre_inside_winning_interval_with_wrong_top=bool(old!=winner and distance==0),
             verified_correction_effect=correction,complete_hourly=bool(r.get('hourly')),
             hourly_source=r['hourly']['kind'] if r.get('hourly') else None,
             provider_receipt_max_age_h=max((pp.timestamp(f['as_of']).timestamp()-v['available_at'])/3600 for v in f['providers'].values()),
             rules_context=r['observation_contract'],
             request_coordinates_retained=bool((r.get('hourly') or {}).get('request_location')),
             feature_sha256=r['feature_sha256'],research_sha256=r['research_sha256']))
    summary=dict(calls=len(cases),candidate_only_hits=sum(c['candidate_hit']>c['incumbent_hit'] for c in cases),
        incumbent_only_hits=sum(c['candidate_hit']<c['incumbent_hit'] for c in cases),
        wrong_top_with_centre_inside_winner=sum(c['centre_inside_winning_interval_with_wrong_top'] for c in cases),
        correction_effect=dict(collections.Counter(c['verified_correction_effect'] or 'no_verified_pair' for c in cases)),
        missing_rule_context=sum(c['rules_context']=='unknown' for c in cases),
        missing_request_coordinates=sum(not c['request_coordinates_retained'] for c in cases),
        interpretation='Mechanical evidence and associations only; forecast weather does not establish the physical cause of a miss.')
    return dict(summary=summary,cases=cases)


def run(first,fit_last,eval_first,eval_last,through,out):
    destination=Path(out).resolve()
    if destination.is_relative_to(Path(data.common.ROOT).resolve()) or destination.exists():
        raise ValueError('use a new private output directory outside the repository')
    if pp.timestamp(through)>simple.utc_now():raise ValueError('future evidence cutoff')
    if not first<=fit_last<eval_first<=eval_last:raise ValueError('invalid chronological windows')
    records,coverage=data.build(first,eval_last,through)
    records,hourly_coverage=hourly.enrich(records,through)
    train,test,timing=study.split(records,fit_last,eval_first,eval_last)
    candidate=model.fit(train);pred=model.predictions(candidate,test)
    baseline=simple.fit(train);baseline_pred=simple.predictions(baseline,test)
    scores=study.comparison(test,pred)
    scores['simple_weather_baseline']=study.comparison(test,baseline_pred)['candidate']
    diagnostic=diagnose(test,pred)
    report=dict(recipe=model.RECIPE,evidence='retrospective development; these dates have already been inspected; no prospective validation',
        training=candidate['training'],evaluation=[eval_first,eval_last],timing=timing,coverage=coverage,
        hourly_coverage=hourly_coverage,scores=scores,miss_diagnostics=diagnostic['summary'],
        by_day={day:study.comparison([r for r in test if r['features']['date']==day],
              [p for r,p in zip(test,pred) if r['features']['date']==day]) for day in sorted({r['features']['date'] for r in test})},
        model_sha256=candidate['sha256'],dataset_sha256=pp.digest(records),changes_serving=False,eligible_for_promotion=False,
        limitations=['Historical provider initialization and request coordinates cannot be invented.',
                     'Complete received hourly2 forecasts are actual prior receipts, not a replay of forecasts fetched after outcomes.',
                     'Missing hourly inputs remain missing with an explicit feature flag; existing calls are never rewritten.',
                     'A short development period cannot approve activation or establish seasonal skill.',
                     'Exact station-temperature error and causal weather attribution require separate verified observations.'])
    destination.mkdir(parents=True,exist_ok=False)
    artifacts={'dataset.json':dict(rows=records,sha256=report['dataset_sha256']),
               'candidate.json':candidate,'simple_baseline.json':baseline,'evaluation.json':report,
               'miss_diagnostics.json':diagnostic,
               'development_predictions.json':dict(evidence=report['evidence'],model_sha256=candidate['sha256'],
                   calls=[dict(city=r['features']['city'],date=r['features']['date'],as_of=r['features']['as_of'],
                               research_sha256=r['research_sha256'],**p) for r,p in zip(test,pred)])}
    for name,value in artifacts.items():(destination/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('first','fit-last','eval-first','eval-last','through','out'):parser.add_argument('--'+name,required=True)
    a=parser.parse_args(argv);r=run(a.first,a.fit_last,a.eval_first,a.eval_last,a.through,a.out)
    summary={k:v for k,v in r.items() if k not in ('coverage','by_day','hourly_coverage')}
    summary['hourly_coverage']={k:v for k,v in r['hourly_coverage'].items() if k!='rule_definition_blobs'}
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
