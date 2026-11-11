"""Production gates shared by the nightly publisher and live pricing reader.

Only original calls from a fixed later trial qualify. A shadow pass alone is
never an authorization to serve, and a later refit cannot inherit that pass.
"""
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

POLICY = 'validated-product-v1'
HORIZONS = ('day_ahead', 'd1_eve', 'morning', 'noon', 'prepeak_2h', 'prepeak_1h')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def source_recipe_hash():
    root=Path(__file__).resolve().parents[1]
    return digest({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in
        ('scripts/ensemble_early.py','scripts/product_policy.py','scripts/probability_engine.py')})


def distribution(value):
    if not value or any(not math.isfinite(v) or not 0 <= v <= 1 for v in value.values()):
        raise ValueError('invalid product distribution')
    if abs(sum(value.values())-1) > 1e-4:
        raise ValueError('invalid product mass')
    # This is also the prospective trial recipe: score the values actually
    # writable to the production six-decimal columns, including residual mass.
    result = {k: round(v, 6) for k, v in value.items()}
    top = min(result, key=lambda k: (-result[k], k))
    result[top] = round(result[top]+1-sum(result.values()), 6)
    if any(not 0 <= v <= 1 for v in result.values()):
        raise ValueError('invalid rounded product distribution')
    return result


def qualified(proof):
    """Return a reason instead of silently treating an incomplete proof as true."""
    try:
        if proof['policy'] != POLICY or proof['original_frozen_calls'] is not True:
            return 'missing original-call proof'
        if not proof['fit_last'] < proof['validation_first'] <= proof['validation_last'] < proof['as_of']:
            return 'overlapping or unfinished validation'
        if proof['training_dates'] < 20 or proof['dates'] < 10 or proof['dates'] > proof['calls']:
            return 'insufficient independent dates'
        if proof['calls'] > proof['expected_calls'] or proof['calls']/proof['expected_calls'] < .9:
            return 'incomplete fixed trial coverage'
        if proof['candidate_hits']/proof['calls'] < .6:
            return 'hit-rate target unproven'
        for ref in ('incumbent', 'market'):
            if not (0 <= proof[ref+'_hits'] < proof['candidate_hits'] <= proof['calls']
                    and 0 <= proof['candidate_brier'] < proof[ref+'_brier']
                    and 0 <= proof['candidate_nll'] < proof[ref+'_nll']
                    and proof[ref+'_gain_lower90'] > 0):
                return 'proper-score and bucket-hit improvement unproven against '+ref
        if any(not math.isfinite(v) for v in proof.values() if isinstance(v, (int, float))):
            return 'nonfinite production proof'
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 'incomplete production proof'
    return None


def source_trial(rows, as_of, state, old, original_rows=None):
    """Pin weights before the first of fourteen future local dates.

    The existing adaptive shadow experiment continues independently. Its later
    weights cannot replace the pinned weights evaluated here. Rejection keeps
    the original window; a retry or another bad day cannot reset it.
    """
    import ensemble_early as ensemble
    trial = old.get('production_trial')
    if trial is None:
        if state['learning_dates'] < 20:
            return dict(decision='held', reason='twenty original training dates required')
        first = as_of+dt.timedelta(days=2)
        last = first+dt.timedelta(days=13)
        trial = dict(policy=POLICY, weights=state['weights'], fit_last=str(as_of-dt.timedelta(days=1)),
                     recipe_sha256=source_recipe_hash(),
                     city_key=state['city_key'], checkpoint=state['phase'],
                     training_dates=state['learning_dates'], validation_first=str(first),
                     validation_last=str(last), model_version=state['model_version'])
        trial['id'] = digest(trial)
    trial = dict(trial)
    if trial.get('decision') == 'approved':
        return trial  # exact proof and expiry survive retries and later refits
    if 'id' not in trial:
        return source_trial(rows, as_of, state, {})
    val = [r for r in rows if trial['validation_first'] <= r['target_date'] <= trial['validation_last']
           and r['packet'].get('production_trial_id') == trial['id']
           and r['packet'].get('production_weights_sha256') == digest(trial['weights'])
           and r['packet'].get('production_candidate_probs') is not None
           and r['packet'].get('market_probs') is not None]
    if str(as_of) <= trial['validation_last']:
        return dict(trial, decision='waiting', reason='fixed future trial is incomplete')
    import random
    proof = dict(policy=POLICY, original_frozen_calls=True, as_of=str(as_of),
                 fit_last=trial['fit_last'], training_dates=trial['training_dates'],
                 validation_first=trial['validation_first'], validation_last=trial['validation_last'],
                 dates=len({r['target_date'] for r in val}), calls=len(val), expected_calls=14)
    recorded=set()
    for r in original_rows or []:
        p=r.get('ensemble_packet') or {}
        try:
            if (r.get('verified') and r.get('inputs_ok') is True
                    and trial['validation_first']<=r['target_date']<=trial['validation_last']
                    and p.get('production_trial_id')==trial['id']
                    and p.get('production_weights_sha256')==digest(trial['weights'])
                    and p.get('station')==r.get('station')
                    and p.get('production_candidate_probs') is not None
                    and p.get('market_probs') is not None
                    and ensemble.timestamp(p['input_oldest'])<=ensemble.timestamp(p['input_newest'])
                    <=ensemble.timestamp(p['captured_at'])<=ensemble.timestamp(r['frozen_at'])
                    <dt.datetime.combine(as_of,dt.time(),dt.timezone.utc)):
                recorded.add(r['target_date'])
        except (KeyError,ValueError,TypeError):continue
    proof.update(recorded_calls=len(recorded) if original_rows is not None else len(val),
                 pending_calls=max(0,len(recorded)-len(val)))
    if original_rows is not None and len(recorded)>=13 and len(val)<13:
        return dict(trial,proof=proof,decision='pending_evidence',
                    reason='original fixed calls exist; authoritative outcomes still pending')
    if val:
        for name in ('candidate', 'incumbent', 'market'):
            key = 'production_candidate_probs' if name == 'candidate' else name+'_probs'
            scores = [ensemble.metrics(r['packet'][key], r['winner']) for r in val]
            proof[name+'_hits'] = sum(s['hit'] for s in scores)
            proof[name+'_brier'] = sum(s['brier'] for s in scores)/len(scores)
            proof[name+'_nll'] = sum(s['log_loss'] for s in scores)/len(scores)
        for ref in ('incumbent', 'market'):
            gains = [ensemble.metrics(r['packet'][ref+'_probs'], r['winner'])['log_loss']
                     - ensemble.metrics(r['packet']['production_candidate_probs'], r['winner'])['log_loss'] for r in val]
            rng = random.Random(20261111)
            proof[ref+'_gain_lower90'] = sorted(sum(rng.choices(gains, k=len(gains)))/len(gains)
                                                    for _ in range(2000))[99]
    reason = qualified(proof)
    return dict(trial, proof=proof, decision='rejected' if reason else 'approved',
                reason=reason or 'fixed original trial beat incumbent and saved market')
