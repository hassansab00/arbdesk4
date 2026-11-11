"""Validated releases replace actual pricing rows, never historical calls.

Private weights/artifacts are fetched once; absence, failure, expiry, changed
code, station/rule or geometry mismatch keep the incumbent with a reason.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import time

from product_policy import POLICY, HORIZONS, digest, distribution, qualified,source_recipe_hash

_releases, _receipts, _lineage, _models = {}, {}, {}, {}
_status = {}
_DEADLINE = 0


def code_hash():
    root=Path(__file__).resolve().parents[1]
    names=('scripts/product_policy.py','scripts/predictive_product.py','scripts/ensemble_early.py',
           'scripts/probability_engine.py','scripts/predictive_primary.py','scripts/tick.py',
           'tools/wxpredict/current_calls.py','tools/wxpredict/current_model.py','tools/wxpredict/current_recipe.py',
           'tools/wxpredict/product_promotion.py','tools/wxpredict/current_registry.py')
    return digest({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in names})


def release(kind,city,checkpoint,station,unit,identity,payload,proof):
    reason=qualified(proof)
    if reason:raise ValueError(reason)
    r=dict(policy=POLICY,kind=kind,city_key=city,checkpoint=checkpoint,station=station,
           unit=unit,identity=identity,payload=payload,payload_sha256=digest(payload),
           proof=proof,proof_sha256=digest(proof),code_sha256=code_hash())
    r['release_id']=digest(r)
    return r


def publish_sources(states, rpc):
    result=dict(approved=0,published=0,held=0,trials_archived=0)
    for state in states:
        for trial in (state.get('completed_production_trial'),state.get('production_trial')):
            if not trial or not trial.get('id'):continue
            rpc('record_predictive_product_trial',dict(p_city=state['city_key'],p_checkpoint=state['phase'],
                p_identity=state['model_version'],p_trial=trial,p_snapshot_sha256=digest(trial)))
            result['trials_archived']+=1
            if trial.get('decision')!='approved':result['held']+=1;continue
            if trial.get('recipe_sha256')!=source_recipe_hash():result['held']+=1;continue
            result['approved']+=1
            # Never re-date an old proof to extend its expiry or undo a rollback.
            if dt.date.fromisoformat(trial['proof']['as_of'])<dt.datetime.now(dt.timezone.utc).date()-dt.timedelta(days=2):continue
            version=state['model_version'];parts=version.split(':')
            r=release('early_ensemble',state['city_key'],state['phase'],parts[2],parts[3],version,
                      dict(production_trial=trial),trial['proof'])
            rpc('publish_predictive_product',dict(p_release=r));result['published']+=1
    return result


def original_incumbent(row):
    context=row.get('predictive_context') or row.get('paired_context') or {}
    if isinstance(context,str):context=json.loads(context)
    p=context.get('product_release')
    if not p:return row['probs']
    actual=json.loads(row['probs']) if isinstance(row['probs'],str) else row['probs']
    if (p.get('policy')!=POLICY or p.get('served_probs')!=actual
            or p.get('city_key')!=row['city_key'] or p.get('target_date')!=row['target_date']
            or p.get('checkpoint')!=row['checkpoint'] or not p.get('receipt_id')
            or p.get('binding_sha256')!=digest({k:v for k,v in p.items() if k!='binding_sha256'})):
        raise ValueError('product incumbent provenance mismatch')
    q=p['incumbent_probs']
    if row.get('frozen_at') and dt.datetime.fromisoformat(p['recorded_at'].replace('Z','+00:00'))>dt.datetime.fromisoformat(row['frozen_at'].replace('Z','+00:00')):
        raise ValueError('product release received after original freeze')
    if set(q)!=set(actual):raise ValueError('product incumbent ladder mismatch')
    distribution(q)
    return q


def prepare(markets, now=None, deadline=None):
    global _releases,_receipts,_lineage,_models,_status,_DEADLINE
    _releases,_receipts,_lineage,_models={},{},{},{}
    _status=dict(approved=0,applied=0,held={})
    now=now or dt.datetime.now(dt.timezone.utc)
    _DEADLINE=min(deadline or time.monotonic()+8,time.monotonic()+8)
    import common
    try:
        with common.request_deadline(_DEADLINE):
            rows=common.rpc('predictive_product_inputs',dict(p_cities=sorted({m['city_key'] for m in markets})))
            for row in rows or []:
                r=row['release'];proof=r.get('proof') or {}
                if (row['decision']!='activate' or not dt.datetime.fromisoformat(row['recorded_at'].replace('Z','+00:00'))<=now
                        <dt.datetime.fromisoformat(row['expires_at'].replace('Z','+00:00'))
                        or qualified(proof) or r.get('code_sha256')!=code_hash()
                        or r.get('payload_sha256')!=digest(r.get('payload'))
                        or r.get('release_id')!=digest({k:v for k,v in r.items() if k!='release_id'})):
                    continue
                _releases[(r['city_key'],r['checkpoint'])]=row
            # Actual database receipts for the already prepared primary inputs.
            keys=[]
            import predictive_primary as primary
            if any(r['release']['kind']=='current_forecast' for r in _releases.values()):
                keys=sorted({p['input_key'] for p in primary._predictions.values() if p.get('input_key')})
            for start in range(0,len(keys),100):
                for row in common.rest_all('live_capture',[
                        ('select','key,captured_at,written_at'),('kind','eq.wx_features'),
                        ('key','in.('+','.join(keys[start:start+100])+')')],order='written_at.asc,key.asc'):
                    _receipts[row['key']]=row
        _status['approved']=len(_releases)
    except Exception as exc:
        _releases={};_status['error']=type(exc).__name__
    return _status


def pricing_books(band_ids, now):
    """Only needed by ordinary pricing when an approved release exists."""
    if not _releases:return {}
    import common
    out={}
    with common.request_deadline(min(_DEADLINE,time.monotonic()+3)):
        for start in range(0,len(band_ids),100):
            rows=common.rest_all('v_latest_book',[
                ('select','band_id,best_bid,best_ask,observed_at'),
                ('band_id','in.('+','.join(band_ids[start:start+100])+')'),
                ('observed_at','gte.'+(now-dt.timedelta(minutes=75)).isoformat()),
                ('observed_at','lte.'+now.isoformat())],order='band_id.asc')
            for r in rows:out[str(r['band_id'])]=dict(bid=r['best_bid'],ask=r['best_ask'],last=None)
    return out


def apply(city,target,result,bands,unit,books,station,checkpoint=None,now=None):
    if result is None:return result
    rows,reg,reasons=result
    import early_city_adaptation as early
    import predictive_primary as primary
    context=early.capture_context(city,target)
    if checkpoint is None:
        checkpoint=(context or {}).get('checkpoint')
        if checkpoint is None:
            p=primary._predictions.get((city,str(target),frozenset(str(r['band_id']) for r in rows)))
            checkpoint=(p or {}).get('horizon')
    def hold(reason):
        counts=_status.setdefault('held',{});counts[reason]=counts.get(reason,0)+1
        if reason=='no_approved_release':return result
        return rows,reg,[*reasons,'product_held:'+reason]
    receipt=_releases.get((city,checkpoint))
    if receipt is None:return hold('no_approved_release')
    if time.monotonic()>=_DEADLINE:return hold('shared_deadline')
    r=receipt['release'];now=now or dt.datetime.now(dt.timezone.utc)
    q={str(x['band_id']):float(x['calibrated_prob']) for x in rows}
    lineage=primary.checkpoint_lineage(city,target,q,checkpoint)
    if not lineage and checkpoint is not None:lineage=primary.checkpoint_lineage(city,target,q)
    try:
        if (r['station']!=station or r['unit']!=unit or checkpoint not in HORIZONS
                or r['proof']['validation_last']>=str(target)
                or any(x.get('pricing_eligible',True) is not True for x in rows)):
            return hold('ineligible_station_unit_or_horizon')
        packet=None;serving_packet=None
        if r['kind']=='early_ensemble':
            import ensemble_early as ensemble
            import probability_engine as pe
            if not context or context['checkpoint']!=checkpoint:return hold('missing_exact_local_checkpoint')
            row=dict(city_key=city,target_date=str(target),checkpoint=checkpoint,station=station,inputs_ok=True,
                     sigma_c=rows[0].get('sigma_c'),running_max_c=rows[0].get('observed_floor_c'),probs=q,market=books,
                     predictive_context=dict(forecast_input=pe.forecast_context(city,target,rows)))
            packet=ensemble.capture(row,bands,unit,context,now,state_override=r['payload'])
            if not packet or packet['model_version']!=r['identity'] or packet.get('production_trial_id')!=r['payload']['production_trial']['id']:
                return hold('missing_original_sources_or_rule')
            served=packet.get('production_candidate_probs')
            if served is None:return hold('incomplete_saved_market')
            serving_packet=packet
            # Freeze the independently registered NEXT trial too. Its score
            # benchmark is the incumbent actually served, while its inputs
            # retain the same original pre-product weather prior.
            packet=ensemble.capture(row,bands,unit,context,now) or serving_packet
            packet=dict(packet,production_reference_probs=dict(served))
        else:
            from wxpredict import current_calls as calls,current_model,current_registry
            payload=primary.product_input(city,target,q)
            if not lineage or not payload:return hold('missing_original_primary_input')
            source_receipt=_receipts.get(lineage['input_key'])
            if not source_receipt:return hold('missing_server_input_receipt')
            call=dict(lineage,city_key=city,target_date=str(target),checkpoint=checkpoint,probs=q,market=books)
            record,why=calls.attach(call,payload,source_receipt['captured_at'],source_receipt['written_at'],decision_at=now.isoformat())
            if why:return hold(why)
            if record['source_identity']!=r['identity']:return hold('source_identity_changed')
            meta=r['payload']['metadata']
            if meta['fit_last']>=str(target):return hold('model_fit_not_before_target')
            if meta.get('recipe_sources_sha256')!=calls.digest(current_model.source_hashes()):return hold('current_recipe_changed')
            artifact=meta['model_sha256']
            if artifact not in _models:
                left=_DEADLINE-time.monotonic()
                if left<2:return hold('model_load_budget')
                _models[artifact]=current_registry.load(meta,budget_s=min(4,left/2))
            served=distribution(current_model.predict(_models[artifact],[record])[0])
        if set(served)!=set(q):return hold('ladder_mismatch')
        from common import model_version_id,request_deadline
        label='predictive-product:'+r['kind']+':'+r['release_id']
        with request_deadline(_DEADLINE):
            version=model_version_id('forecast',label,config=dict(policy=POLICY,release_id=r['release_id'],
                                    receipt_id=receipt['receipt_id']),structural=False)
        if not version:return hold('version_not_registered')
        if time.monotonic()>=_DEADLINE:return hold('shared_deadline')
        out=[]
        for baseline in rows:
            v=dict(baseline);p=served[str(v['band_id'])]
            v.update(raw_prob=p,calibrated_prob=p,forecast_version=version,calibration_version=None,
                     computed_at=now.isoformat(),centre_c=None,sigma_c=None,forecast_max_c=None,
                     forecast_sigma_c=None,station_width_c=None,bias_applied_c=0,lattice_applied=False)
            out.append(v)
        provenance=dict(policy=POLICY,city_key=city,target_date=str(target),checkpoint=checkpoint,
                        receipt_id=receipt['receipt_id'],release_id=r['release_id'],kind=r['kind'],
                        recorded_at=receipt['recorded_at'],incumbent_probs=q,served_probs=served,
                        incumbent_priced_from=next((s[len('priced_from:'):] for s in reasons if s.startswith('priced_from:')),None),
                        incumbent_calibration_version=(lineage.get('calibration') or {}).get('version'),
                        proof_sha256=digest(r['proof']),payload_sha256=r['payload_sha256'])
        provenance['binding_sha256']=digest(provenance)
        ctx=dict(lineage.get('predictive_context') or {},product_release=provenance)
        if packet:ctx['ensemble_early']=packet
        if serving_packet:
            ctx['product_ensemble_serving']={k:serving_packet[k] for k in (
                'policy','model_version','production_trial_id','production_weights_sha256',
                'production_candidate_probs','incumbent_probs','station','unit','captured_at',
                'input_oldest','input_newest','rules_received_at','rules_sha256')}
        _lineage[(city,str(target),checkpoint)]=dict(lineage,calibration=None,predictive_context=ctx,served_probs=served)
        _status['applied']=_status.get('applied',0)+1
        clean=[v for v in reasons if not v.startswith(('priced_from:','calibrated:','postprocessed:','station_correction:',
                    'station_width:','measurement_layer:','trajectory:','same-day-advance-width-prior-v1:'))]
        return out,reg,[*clean,'priced_from:'+label]
    except Exception as exc:
        return hold('refused_'+type(exc).__name__)


def checkpoint_lineage(city,target,probs,checkpoint):
    p=_lineage.get((city,str(target),checkpoint))
    if not p or p['served_probs']!=probs:return {}
    return {k:v for k,v in p.items() if k!='served_probs'}
