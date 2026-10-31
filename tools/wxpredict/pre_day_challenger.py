"""One shared, bounded early calibration challenger; research only.

Learn from actual original pre-day pairs, not a reconstructed weather model.
The incumbent always retains at least half the mass. No city-specific model,
market input, serving integration, registry write, or automatic promotion.
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
from wxpredict import pre_day_pairs as pairs

RECIPE = 'pre-day-shared-blend-v1'
FEATURES = ('intercept', 'signed_correction_sigma', 'correction_size_sigma', 'log_sigma_c')
MAX_RAW_WEIGHT = .5
PRIOR = (math.log(.2/.8), 0., 0., 0.)
BOUNDS = ((-6., 2.), (-2., 2.), (-2., 2.), (-2., 2.))
PENALTY = .02
MIN_FIT_DATES, MIN_CITIES_PER_DATE = 5, 20


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def features(record):
    """Only frozen temperature context. Labels and city identities cannot enter."""
    centre, raw, sigma = (float(record[k]) for k in ('centre_c', 'raw_centre_c', 'sigma_c'))
    if not all(math.isfinite(v) for v in (centre, raw, sigma)) or sigma <= 0:
        raise ValueError('invalid frozen model context')
    delta = max(-3., min(3., (centre-raw)/sigma))
    return [1., delta, abs(delta), max(-2., min(2., math.log(sigma)))]


def weight(parameters, record):
    if len(parameters) != len(FEATURES) or any(not math.isfinite(float(p)) or not lo <= p <= hi for p, (lo, hi) in zip(parameters, BOUNDS)):
        raise ValueError('shared challenger parameters outside declared bounds')
    z = sum(float(p)*x for p, x in zip(parameters, features(record)))
    return MAX_RAW_WEIGHT/(1+math.exp(-z))


def predict_parameters(parameters, record):
    incumbent = pairs.distribution(record['incumbent'])
    raw = pairs.distribution(record['raw'], incumbent)
    a = weight(parameters, record)
    q = {k:(1-a)*incumbent[k]+a*raw[k] for k in incumbent}
    total = sum(q.values())
    return {k:v/total for k, v in q.items()}


def unique(records):
    seen = set()
    for r in records:
        pairs.validate_record(r)
        key = (r['city'], r['date'])
        if key in seen:
            raise ValueError('duplicate city-date; later calls cannot replace the first')
        seen.add(key)
        if r.get('checkpoint') != 'd1_eve':
            raise ValueError('shared early challenger accepts evening-before calls only')


def source_hashes():
    import hashlib
    root = Path(__file__).resolve().parents[2]
    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in
            ('tools/wxpredict/pre_day_pairs.py', 'tools/wxpredict/pre_day_challenger.py',
             'scripts/early_city_adaptation.py')}


def fit(records):
    import numpy as np
    from scipy.optimize import minimize
    unique(records)
    if any(not r.get('label') for r in records):
        raise ValueError('training requires original banked labels')
    counts = collections.Counter(r['date'] for r in records)
    if len(counts) < MIN_FIT_DATES or min(counts.values()) < MIN_CITIES_PER_DATE:
        raise ValueError('insufficient development coverage: five dates, twenty cities per date')
    now = utc_now()
    if any(pairs.timestamp(r['label']['banked_at']) > now or pairs.timestamp(r['frozen_at']) >= now for r in records):
        raise ValueError('training evidence not available before candidate creation')
    x = np.asarray([features(r) for r in records])
    base, raw = [], []
    for r in records:
        incumbent = pairs.distribution(r['incumbent'])
        alternative = pairs.distribution(r['raw'], incumbent)
        win = r['label']['winner']
        if win not in incumbent:
            raise ValueError('winner outside original training ladder')
        # Each proposed mixture is normalized, including original rounding error.
        base.append((incumbent[win], sum(incumbent.values())))
        raw.append((alternative[win], sum(alternative.values())))
    base, raw = np.asarray(base), np.asarray(raw)
    weights = np.asarray([1/(len(counts)*counts[r['date']]) for r in records])
    prior = np.asarray(PRIOR)

    def objective(theta):
        a = MAX_RAW_WEIGHT/(1+np.exp(-(x@theta)))
        pw = ((1-a)*base[:,0]+a*raw[:,0])/((1-a)*base[:,1]+a*raw[:,1])
        return float(-(weights*np.log(np.maximum(1e-6, pw))).sum()+PENALTY*((theta-prior)**2).sum())

    result = minimize(objective, prior, method='L-BFGS-B', bounds=BOUNDS,
                      options=dict(maxiter=300, ftol=1e-12, gtol=1e-8))
    if not result.success or not math.isfinite(float(result.fun)):
        raise ValueError('shared challenger fit did not converge: '+str(result.message))
    finished = utc_now()
    if finished < now:
        raise ValueError('candidate creation clock moved backwards')
    model = dict(recipe=RECIPE, feature_names=list(FEATURES), parameters=result.x.tolist(),
                 max_raw_weight=MAX_RAW_WEIGHT, penalty=PENALTY,
                 fit_started_at=now.isoformat(), available_at=finished.isoformat(), source_sha256=source_hashes(),
                 training=dict(first=min(counts), through=max(counts), dates=len(counts), rows=len(records),
                               evidence_sha256=pairs.digest(records)),
                 optimizer=dict(converged=bool(result.success), objective=float(result.fun), iterations=int(result.nit)),
                 changes_serving=False, eligible_for_promotion=False)
    model['sha256'] = pairs.digest(model)
    return model


def validate_model(model):
    if model.get('recipe') != RECIPE or model.get('feature_names') != list(FEATURES) or model.get('max_raw_weight') != MAX_RAW_WEIGHT:
        raise ValueError('unknown shared challenger recipe')
    if pairs.digest({k:v for k, v in model.items() if k != 'sha256'}) != model.get('sha256'):
        raise ValueError('candidate artifact hash mismatch')
    if model.get('changes_serving') is not False or model.get('eligible_for_promotion') is not False:
        raise ValueError('research artifact cannot approve serving')
    if model['source_sha256'] != source_hashes():
        raise ValueError('candidate source changed; use the original version')
    pairs.timestamp(model['available_at'])


def predict(model, record, *, forward=False):
    validate_model(model)
    pairs.validate_record(record)
    if forward and (record['date'] <= model['training']['through'] or pairs.timestamp(record['decided_at']) < pairs.timestamp(model['available_at'])):
        raise ValueError('candidate was not available before this original decision')
    if forward:
        from zoneinfo import ZoneInfo
        start = dt.datetime.combine(dt.date.fromisoformat(record['date']),dt.time(),ZoneInfo(record['timezone']))
        now, frozen = utc_now(), pairs.timestamp(record['frozen_at'])
        if not frozen <= now < start or now-frozen > dt.timedelta(minutes=15):
            raise ValueError('forward prediction is late, post-day, or uses a future capture')
    return predict_parameters(model['parameters'], record)


def split(records, fit_last, first, last):
    unique(records)
    if not dt.date.fromisoformat(fit_last) < dt.date.fromisoformat(first) <= dt.date.fromisoformat(last):
        raise ValueError('chronological windows overlap')
    test = [r for r in records if first <= r['date'] <= last and r.get('label')]
    if not test:
        raise ValueError('no banked evaluation calls')
    first_decision = min(pairs.timestamp(r['decided_at']) for r in records if first <= r['date'] <= last)
    candidates = [r for r in records if r['date'] <= fit_last and r.get('label')]
    # Date separation alone leaks labels for next-day evening calls. Purge by
    # actual banked time across all city timezones, before the earliest test call.
    train = [r for r in candidates if pairs.timestamp(r['label']['banked_at']) < first_decision]
    return train, test, dict(training_labels_unavailable_at_first_evaluation=len(candidates)-len(train),
                             first_evaluation_decision=first_decision.isoformat())


def metrics(records, model):
    from wx_city_calibration import metrics as score
    validate_model(model)
    labeled = [r for r in records if r.get('label')]
    unique(labeled)
    predicted = {r['checkpoint_id']:predict_parameters(model['parameters'],r) for r in labeled}
    out = {}
    for name in ('candidate', 'incumbent', 'raw', 'market'):
        scored = []
        for r in labeled:
            p = predicted[r['checkpoint_id']] if name == 'candidate' else r.get(name)
            if p is not None:
                s = score(p, r['label']['winner'])
                if name == 'incumbent':
                    s['hit'] = int(r['incumbent_top'] == r['label']['winner'])
                scored.append(s)
        n = len(scored)
        out[name] = dict(n=n, hits=sum(s['hit'] for s in scored),
                         hit_rate=sum(s['hit'] for s in scored)/n if n else None,
                         log_loss=sum(s['log_loss'] for s in scored)/n if n else None,
                         brier=sum(s['brier'] for s in scored)/n if n else None)
    paired = [r for r in labeled if r.get('market') is not None]
    # Model/market figures must not use different pending/missing-quote cohorts.
    out['matched_market'] = dict(n=len(paired))
    for name in ('candidate','incumbent','market'):
        scores = [score(predicted[r['checkpoint_id']] if name=='candidate' else r[name],r['label']['winner']) for r in paired]
        if name=='incumbent':
            for s,r in zip(scores,paired):s['hit']=int(r['incumbent_top']==r['label']['winner'])
        n=len(scores)
        out['matched_market'][name]=dict(n=n,hits=sum(s['hit'] for s in scores),
            log_loss=sum(s['log_loss'] for s in scores)/n if n else None,
            brier=sum(s['brier'] for s in scores)/n if n else None)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('first', 'fit-last', 'eval-first', 'eval-last', 'through', 'out'):
        ap.add_argument('--'+name, required=True)
    args = ap.parse_args(argv)
    if not dt.date.fromisoformat(args.first) <= dt.date.fromisoformat(args.fit_last) < dt.date.fromisoformat(args.eval_first) <= dt.date.fromisoformat(args.eval_last):
        raise ValueError('invalid chronological windows')
    records, coverage = pairs.load(args.first, args.eval_last, args.through)
    train, test, timing = split(records, args.fit_last, args.eval_first, args.eval_last)
    model = fit(train)
    report = dict(recipe=RECIPE, evidence='retrospective development from original pre-day frozen pairs; not forward predictions',
                  coverage=coverage, timing=timing, training=model['training'],
                  evaluation=[args.eval_first, args.eval_last],
                  evaluation_dates=len({r['date'] for r in test}), scores=metrics(test, model),
                  by_city={city:metrics([r for r in test if r['city']==city],model) for city in sorted({r['city'] for r in test})},
                  model_sha256=model['sha256'], changes_serving=False, eligible_for_promotion=False,
                  caveat='Pilot samples are short; full future validation and serving integration remain separate.')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    (out/'candidate.json').write_text(json.dumps(model,indent=2,allow_nan=False)+'\n')
    (out/'evaluation.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='by_city'},indent=2))
    return report


if __name__ == '__main__':
    main()
