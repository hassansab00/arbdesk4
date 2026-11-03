"""Private development replay and actual-time forward fit; no database writes."""
import argparse
import collections
import datetime as dt
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wxpredict import shared_weather as model,pre_day_hourly as hourly,pre_day_weather_data as data
from wxpredict import pre_day_pairs as pp,forecast_state_study as study


def run(input_path,output_path,through):
    out=Path(output_path).resolve();root=Path(__file__).resolve().parents[2]
    if out.exists() or out==root or out.is_relative_to(root):raise ValueError('use a new private output directory outside the repository')
    now=dt.datetime.now(dt.timezone.utc)
    if pp.timestamp(through)>now:raise ValueError('future evidence cutoff')
    original=json.loads(Path(input_path).read_text())['rows']
    for r in original:model.base.validate(r)
    manifest=json.loads((root/'data/mirror/manifest.json').read_text())
    first=pp.timestamp(min(r['date'] for r in original)+'T00:00:00Z').timestamp()-2*86400
    history=hourly.History(data.verified_rows(root,manifest,'live_capture',first,pp.timestamp(through).timestamp(),
                                            lambda r:r['kind'] in hourly.KINDS))
    records=[];counts=collections.Counter()
    for r in original:
        context,_=history.select(r['city'],r['date'],r['timezone'],r['as_of'])
        if not context:counts['missing_complete_received_weather']+=1;continue
        records.append(model.attach(r,context));counts[context['kind']]+=1
    # Previously inspected dates: development only, never a forward freeze.
    test=[r for r in records if '2026-10-27'<=r['date']<='2026-11-01' and r.get('label')]
    boundary=min(pp.timestamp(r['as_of']) for r in test)
    possible=[r for r in records if r['date']<='2026-10-25']
    train=[r for r in possible if r.get('label') and pp.timestamp(r['label']['available_at'])<boundary]
    development=model.fit(train,boundary.isoformat());predictions=model.predict(development,test)
    comparisons={s:study.compare([r for r in test if r['checkpoint']==s],
                                [p for r,p in zip(test,predictions) if r['checkpoint']==s])
                 for s in sorted({r['checkpoint'] for r in test})}
    future_train=[r for r in records if r.get('label') and pp.timestamp(r['label']['available_at'])<now
                  and pp.timestamp(r['frozen_at'])<now]
    forward=model.fit(future_train,now.isoformat())
    report=dict(policy=model.POLICY,generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        interpretation='Retrospective development only; fixed future captures must validate this candidate.',changes_serving=False,
        coverage=dict(original=len(original),enriched=len(records),kinds=dict(counts),conflicts=history.conflicts,
                      missing_receipts=history.missing_receipts),
        development_training=development['training'],purged_labels=len(possible)-len(train),by_checkpoint=comparisons,
        total=study.compare(test,predictions),date_block_interval=study.paired_date_interval(test,predictions),
        forward_training=forward['training'],forward_artifact_sha256=forward['sha256'])
    out.mkdir(parents=True)
    for name,value in [('dataset.json',records),('development_artifact.json',development),
                       ('forward_artifact.json',forward),('development_report.json',report)]:
        (out/name).write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--out',required=True);p.add_argument('--through',required=True)
    args=p.parse_args();print(json.dumps(run(args.input,args.out,args.through),indent=2))
