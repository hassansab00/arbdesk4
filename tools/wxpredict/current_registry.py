"""Separate immutable lineage for the current-forecast challenger, shadow only."""
import datetime as dt
import gzip
import hashlib
import json
import os
import re

from wxpredict import common, current_calls, cycle_registry

STATE = 'data/models/wxrd/current_registry.json'
POINTER = 'data/models/wxrd/current-shadow.json'
REPORTS = 'data/eval/wxpredict/current_forecast'
CANDIDATES = 'data/models/wxrd/current-candidates'
VARIANT = 'wx_current'


def state(root=None):
    return cycle_registry.read(os.path.join(root or common.ROOT, STATE),
                               dict(schema=1, changes_serving=False, paused=False, history=[]))


def report_path(night, root=None):
    dt.date.fromisoformat(night)
    return os.path.join(root or common.ROOT, REPORTS, night+'.json')


def record(report, meta=None, root=None):
    root = root or common.ROOT
    path = report_path(report['night'], root)
    if os.path.exists(path):
        if cycle_registry.read(path) != report:
            raise ValueError('current research report already recorded; refusing rewrite')
        return
    s = state(root)
    if meta is not None:
        if report['decision'] != 'accept shadow' or s['paused'] or s.get('candidate'):
            raise ValueError('unaccepted, paused or already active current candidate')
        if (report.get('reasons') or report.get('nll_gain_ci90_by_date',[0])[0]<=0
                or report.get('model_sha256')!=meta['model_sha256'] or report.get('changes_serving') is not False):
            raise ValueError('current candidate lacks independent acceptance evidence')
        if (meta.get('recipe') != 'current-band-v1' or meta['fit_last'] >= report['evaluation_first']
                or not re.fullmatch(r'wxrd-auto-[0-9]{8}-[a-f0-9]{12}', meta['version'])
                or meta.get('evaluation_report')!=REPORTS+'/'+report['night']+'.json'):
            raise ValueError('invalid current candidate lineage')
        rel = CANDIDATES+'/'+meta['version']+'.json'
        metadata_path = os.path.join(root, rel)
        if os.path.exists(metadata_path) and cycle_registry.read(metadata_path) != meta:
            raise ValueError('current candidate metadata already exists')
        sha = cycle_registry.write(metadata_path, meta)
        s['candidate'] = dict(metadata=rel, metadata_sha256=sha, model_sha256=meta['model_sha256'],
                              available_at=dt.datetime.now(common.UTC).isoformat())
    cycle_registry.write(path, report)
    entry=dict(night=report['night'], decision=report['decision'],
                            report=REPORTS+'/'+report['night']+'.json',
                            report_sha256=current_calls.digest(report))
    if report['decision']=='forward rejected' and s.get('candidate'):
        entry['retired_candidate']=s.pop('candidate')
        s.pop('forward_validated',None)
    if report['decision']=='forward validated' and report.get('forward_evaluation_last'):
        s['forward_validated']=dict(report=entry['report'],report_sha256=entry['report_sha256'])
    s['history'].append(entry)
    cycle_registry.write(os.path.join(root, STATE), s)
    if meta is not None:
        cycle_registry.write(os.path.join(root,POINTER),s['candidate'])
    elif 'retired_candidate' in entry:
        cycle_registry.write(os.path.join(root,POINTER),dict(disabled=True))


def selected(root=None):
    root = root or common.ROOT
    s = state(root)
    pointer = s.get('candidate')
    if s['paused'] or not pointer:
        return None
    rel = pointer['metadata']
    if not rel.startswith(CANDIDATES+'/') or '..' in rel.split('/'):
        raise ValueError('current metadata outside its registry')
    with open(os.path.join(root, rel), 'rb') as fh:
        raw = fh.read()
    if hashlib.sha256(raw).hexdigest() != pointer['metadata_sha256']:
        raise ValueError('current candidate metadata hash mismatch')
    meta = json.loads(raw)
    if (meta['model_sha256'] != pointer['model_sha256'] or meta.get('recipe') != 'current-band-v1'
            or meta.get('blend') != .10):
        raise ValueError('current candidate weight or recipe mismatch')
    rel=meta.get('evaluation_report','')
    if not re.fullmatch(re.escape(REPORTS)+r'/[0-9]{4}-[0-9]{2}-[0-9]{2}\.json',rel):
        raise ValueError('current evaluation report outside its registry')
    entry=next((r for r in s['history'] if r['report']==rel),None)
    report=cycle_registry.read(os.path.join(root,rel))
    if (entry is None or report is None or current_calls.digest(report)!=entry['report_sha256']
            or report.get('model_sha256')!=meta['model_sha256'] or report.get('decision')!='accept shadow'):
        raise ValueError('current candidate evaluation evidence hash mismatch')
    from wxpredict import current_model
    if meta.get('recipe_sources_sha256')!=current_calls.digest(current_model.source_hashes()):
        raise ValueError('current candidate recipe code changed; re-evaluation required')
    return meta, pointer['available_at']


def load(meta, budget_s=10):
    import lightgbm as lgb
    from wxpredict import model_artifacts
    path = model_artifacts.ensure(meta, budget_s=budget_s)
    with gzip.open(path, 'rt') as fh:
        return lgb.Booster(model_str=fh.read())
