"""Start one fixed five-seed research challenger, awaiting future validation.

This repairs the cold-start deadlock: insufficient historic frozen validation
must prevent promotion, not prevent fitting a forward-only shadow candidate.
No production pointer, capital setting, or historical prediction is changed.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wxpredict import common, candidate_cycle as cycle, cycle_registry as registry

POLICY = 'remaining-five-seed-forward-bootstrap-v1'
MIN_TRAIN_DATES = 20


def plan(today, state, incumbent):
    through = str(today-dt.timedelta(days=2))
    reason = ('paused by rollback' if state.get('paused') else
              'fixed challenger already registered' if state.get('current') else
              'no new completed training date' if through <= incumbent['fit_last'] else None)
    return dict(night=str(today), fit_last=through, policy=POLICY,
                decision='wait' if reason else 'fit for forward shadow', reason=reason,
                changes_serving=False, validation_passed=False)


def fit(p, work):
    import pandas as pd
    from wxpredict import build_table, remaining, walkforward as wf
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    built = build_table.build(last=p['fit_last'], out_dir=str(work), write_meta=False, report_lag_s=480)
    table = work/'wxpredict_table.csv.gz'
    frame = pd.read_csv(table, low_memory=False)
    train = frame[(frame.date <= p['fit_last']) & frame.label_unit.notna() & remaining.eligible(frame)].reset_index(drop=True)
    del frame  # do not retain a second full research table while fitting
    if len(train) < cycle.MIN_TRAIN_ROWS or train.date.nunique() < MIN_TRAIN_DATES:
        raise ValueError('insufficient independent completed training dates/rows')
    x = remaining.features(train, wf.features(train))
    models = [remaining.fit(x, remaining.target(train), seed, cycle.ROUNDS) for seed in cycle.SEEDS]
    raw = [model.model_to_string().encode() for model in models]
    hashes = [hashlib.sha256(blob).hexdigest() for blob in raw]
    sha = hashlib.sha256(''.join(hashes).encode()).hexdigest()
    model_id = 'wxrd-auto-'+p['night'].replace('-', '')+'-'+sha[:12]
    report = dict(p, decision='awaiting forward validation', training_rows=len(train),
        training_dates=int(train.date.nunique()), training_first=str(train.date.min()),
        training_last=str(train.date.max()), candidate_model_sha256=sha,
        table_content_sha256=built['sha256'], table_sources=built['sources'],
        table_gzip_sha256=hashlib.sha256(table.read_bytes()).hexdigest(),
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=common.ROOT,text=True).strip(),
        recipe='remaining-v2; fixed five seeds; future original-call validation only')
    meta = dict(version=model_id, model_sha256=sha, fit_last=p['fit_last'], report_lag_s=480,
        features=models[0].feature_name(), rows=len(train), seeds=list(cycle.SEEDS), rounds=cycle.ROUNDS,
        parameters=remaining.PARAMS, recipe=report['recipe'],
        evaluation_report='data/eval/wxpredict/cycles/bootstrap_'+p['night']+'.json',
        models=[dict(seed=seed,file=model_id+f'_s{seed}.txt.gz',sha256=digest)
                for seed,digest in zip(cycle.SEEDS,hashes)])
    paths = []
    for entry, blob in zip(meta['models'], raw):
        path = work/entry['file']
        path.write_bytes(gzip.compress(blob, compresslevel=9, mtime=0))
        paths.append(str(path))
    return report, meta, paths


def register(report, meta, root=None):
    """Atomic research-only registration, after every weight has been verified."""
    from wxpredict import model_artifacts
    root = root or common.ROOT
    state = registry.registry(root)
    if state.get('paused') or state.get('current'):
        raise ValueError('paused or existing challenger cannot be replaced by bootstrap')
    if (report.get('policy') != POLICY or report.get('decision') != 'awaiting forward validation'
        or report.get('changes_serving') is not False or report.get('validation_passed') is not False
        or report.get('training_rows',0) < cycle.MIN_TRAIN_ROWS or report.get('training_dates',0) < MIN_TRAIN_DATES
        or meta.get('rows') != report['training_rows'] or meta.get('fit_last') != report.get('fit_last')
        or report.get('training_last','9999') > meta['fit_last']
        or meta['fit_last'] > str(dt.date.fromisoformat(report['night'])-dt.timedelta(days=2))
        or meta.get('seeds') != list(cycle.SEEDS) or meta.get('rounds') != cycle.ROUNDS
        or meta.get('report_lag_s') != 480 or meta.get('model_sha256') != report.get('candidate_model_sha256')):
        raise ValueError('invalid forward bootstrap lineage')
    entries = meta.get('models') or []
    if len(entries) != len(cycle.SEEDS) or [r['seed'] for r in entries] != list(cycle.SEEDS):
        raise ValueError('incomplete five-seed bootstrap')
    hashes = []
    for entry in entries:
        model_artifacts.ensure(dict(asset_url=entry['asset_url'],model_sha256=entry['sha256']), root)
        hashes.append(entry['sha256'])
    if hashlib.sha256(''.join(hashes).encode()).hexdigest() != meta['model_sha256']:
        raise ValueError('bootstrap ensemble hash mismatch')
    relative = 'data/models/wxrd/candidates/'+meta['version']+'.json'
    if '/' in meta['version'] or '..' in meta['version'] or meta.get('evaluation_report') != 'data/eval/wxpredict/cycles/bootstrap_'+report['night']+'.json':
        raise ValueError('invalid bootstrap path')
    def immutable(path, value):
        path = os.path.join(root,path)
        if os.path.exists(path):
            if registry.read(path) != value:
                raise ValueError('refusing to rewrite immutable bootstrap evidence')
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()
        return registry.write(path,value)
    digest = immutable(relative,meta)
    immutable(meta['evaluation_report'],report)
    pointer = dict(schema=1,variant='wxrd_auto',metadata=relative,metadata_sha256=digest,
                   model_sha256=meta['model_sha256'],available_at=dt.datetime.now(common.UTC).isoformat())
    state['current'] = pointer
    state.pop('forward_validated', None)
    state['history'].append(dict(action='bootstrap', night=report['night'], decision=report['decision'],
        report=meta['evaluation_report'], model_id=meta['version'], fitted=True))
    registry.write(os.path.join(root,registry.REGISTRY),state)
    registry.write(os.path.join(root,registry.POINTER),pointer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    _path, incumbent = cycle.incumbent()
    p = plan(dt.datetime.now(common.UTC).date(), registry.registry(), incumbent)
    ready = p['decision'] == 'fit for forward shadow'
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as out:
            out.write('fit='+str(ready).lower()+'\n')
    if ready and not args.plan:
        from wxpredict import model_artifacts
        report, meta, paths = fit(p, os.path.join(common.ROOT,'.cache/wxpredict-bootstrap',p['night']))
        for entry, path in zip(meta['models'], paths):
            artifact_id = 'wxrd-auto-'+p['night'].replace('-','')+'-'+entry['sha256'][:12]
            entry['asset_url'] = model_artifacts.publish(path,artifact_id,entry['sha256'],validated=False)
        register(report,meta)
        p = report
    print(json.dumps(p,indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
