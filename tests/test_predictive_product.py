"""Actual serving rows, immutable benchmarks, pinned trials and refusal gates."""
import copy
import datetime as dt
import json
import sys
from pathlib import Path
import time
from types import SimpleNamespace
import pytest
import common
import ensemble_early as ensemble
import predictive_primary as primary
import predictive_product as product
import product_policy as policy
import probability_engine as pe
import tick
import current_ladder
from test_ensemble_early import original,scored,BANDS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_current_forecast_cycle import case
from wxpredict import current_calls,current_model,current_registry


@pytest.fixture(autouse=True)
def runtime_common(monkeypatch):
    # Legacy contracts replace/remove sys.modules['common']. Bind the same
    # runtime module that the product imports inside its bounded calls.
    import importlib
    monkeypatch.setitem(globals(),'common',importlib.import_module('common'))


def proof():
    return dict(policy=policy.POLICY,original_frozen_calls=True,fit_last='2026-09-01',
        validation_first='2026-09-03',validation_last='2026-09-16',as_of='2026-09-18',
        training_dates=20,dates=14,calls=14,expected_calls=14,candidate_hits=10,
        incumbent_hits=7,market_hits=8,candidate_brier=.2,incumbent_brier=.3,market_brier=.25,
        candidate_nll=.3,incumbent_nll=.5,market_nll=.4,incumbent_gain_lower90=.1,market_gain_lower90=.05)


@pytest.mark.parametrize('field,value',[
    ('training_dates',19),('dates',9),('calls',12),('original_frozen_calls',False),
    ('candidate_hits',8),('candidate_brier',.25),('candidate_nll',.4),
    ('market_gain_lower90',0),('fit_last','2026-09-03'),('as_of','2026-09-16'),('candidate_nll',float('nan'))])
def test_shadow_or_incomplete_proof_cannot_qualify(field,value):
    p=proof();assert policy.qualified(p) is None
    p[field]=value;assert policy.qualified(p)


def trial():
    return dict(id='a'*64,policy=policy.POLICY,model_version=ensemble.family('TEST','C'),
                recipe_sha256=policy.source_recipe_hash(),
                weights={m:1/7 for m in ensemble.MODELS},fit_last='2026-09-01',training_dates=20,
                validation_first='2026-09-03',validation_last='2026-09-16',decision='approved',proof=proof())


def setup_source(monkeypatch):
    row,ctx,now=original()
    state=dict(production_trial=trial())
    r=product.release('early_ensemble','city','d1_eve','TEST','C',ensemble.family('TEST','C'),state,proof())
    receipt=dict(receipt_id=19,decision='activate',recorded_at='2026-09-18T00:01:00Z',
                 expires_at='2026-10-05T00:00:00Z',release=r)
    monkeypatch.setattr(common,'rpc',lambda name,p:[receipt])
    monkeypatch.setattr(common,'model_version_id',lambda *a,**k:'registered')
    monkeypatch.setattr(pe,'forecast_context',lambda *a:row['predictive_context']['forecast_input'])
    import early_city_adaptation as early
    monkeypatch.setattr(early,'capture_context',lambda *a:dict(ctx,checkpoint='d1_eve'))
    monkeypatch.setattr(primary,'_lineage',{})
    product.prepare([dict(city_key='city')],now)
    result=([dict(band_id=k,calibrated_prob=v,raw_prob=v,sigma_c=1.,centre_c=21.,pricing_eligible=True,
                  observed_floor_c=None,computed_at=now.isoformat()) for k,v in row['probs'].items()],SimpleNamespace(label='test'),['priced_from:incumbent'])
    return row,ctx,now,receipt,result


def test_real_rows_card_ladder_and_frozen_call_share_applied_distribution(monkeypatch):
    row,ctx,now,receipt,base=setup_source(monkeypatch);before=copy.deepcopy(base[0])
    served=product.apply('city',row['target_date'],base,BANDS,'C',row['market'],'TEST',checkpoint='d1_eve',now=now)
    assert base[0]==before and served[0]!=base[0], (product._status,served[2])
    q={r['band_id']:r['calibrated_prob'] for r in served[0]}
    assert sum(q.values())==pytest.approx(1)
    assert all(r['centre_c'] is None and r['sigma_c'] is None for r in served[0])
    frozen,why=tick.build_row('city',row['target_date'],'d1_eve',now,served[0],served[2],row['market'],None,'test',station='TEST',bands=BANDS,unit='C')
    assert why is None and frozen['probs']==q
    frozen['frozen_at']=(now+dt.timedelta(seconds=1)).isoformat()
    assert frozen['predictive_context']['ensemble_early']['incumbent_probs']==row['probs']
    assert product.original_incumbent(frozen)==row['probs']
    assert frozen['predictive_context']['product_release']['receipt_id']==19
    ladder,why=current_ladder.ladder_row('city',row['target_date'],'market',served[0],served[2],'checkpoint','d1_eve','test')
    assert not why and ladder['ladder']==q
    assert ladder['priced_from'].startswith('predictive-product:early_ensemble:')
    # The learner consumes the retained incumbent, never its own served answer.
    s=scored();s.update(probs=q,predictive_context=frozen['predictive_context'],ensemble_packet=frozen['predictive_context']['ensemble_early'])
    assert ensemble.eligible([s],dt.datetime(2026,11,1,tzinfo=dt.timezone.utc))
    bad=copy.deepcopy(frozen);bad['probs']['a']+=.01
    with pytest.raises(ValueError,match='provenance'):product.original_incumbent(bad)
    late=copy.deepcopy(frozen);p=late['predictive_context']['product_release']
    p['recorded_at']=(now+dt.timedelta(seconds=2)).isoformat()
    p['binding_sha256']=policy.digest({k:v for k,v in p.items() if k!='binding_sha256'})
    with pytest.raises(ValueError,match='received after'):product.original_incumbent(late)
    archived=copy.deepcopy(frozen);archived['predictive_context']=json.dumps(archived['predictive_context']);archived['probs']=json.dumps(archived['probs'])
    assert product.original_incumbent(archived)==row['probs']


@pytest.mark.parametrize('fault',['expired','revoked','code','payload','future','proof','identity','quotes','deadline','ineligible','version'])
def test_every_unsafe_release_keeps_actual_incumbent_rows(monkeypatch,fault):
    row,ctx,now,receipt,base=setup_source(monkeypatch)
    if fault=='expired':receipt['expires_at']='2026-09-01T00:00:00Z'
    if fault=='revoked':receipt['decision']='revoke'
    if fault=='code':receipt['release']['code_sha256']='b'*64
    if fault=='payload':receipt['release']['payload']['production_trial']['weights'][ensemble.MODELS[0]]=.9
    if fault=='future':receipt['recorded_at']='2026-11-01T00:00:00Z'
    if fault=='proof':receipt['release']['proof']['training_dates']=19
    if fault=='identity':receipt['release']['station']='OTHER'
    if fault in ('expired','revoked','code','payload','future','proof'):product.prepare([dict(city_key='city')],now)
    if fault=='deadline':monkeypatch.setattr(product,'_DEADLINE',0)
    if fault=='ineligible':base[0][0]['pricing_eligible']=False
    if fault=='version':monkeypatch.setattr(common,'model_version_id',lambda *a,**k:None)
    quotes={} if fault=='quotes' else row['market']
    got=product.apply('city',row['target_date'],base,BANDS,'C',quotes,'TEST',checkpoint='d1_eve',now=now)
    assert got[0]==base[0] and product._status['held']


def test_pinned_trial_uses_original_calls_only_and_never_refits_its_weights(monkeypatch):
    monkeypatch.setattr(ensemble,'_states',{})
    rows=[scored(str(dt.date(2026,8,1)+dt.timedelta(days=i))) for i in range(20)]
    state=ensemble.fit(rows,dt.date(2026,8,22))[0];t=state['production_trial']
    assert t['validation_first']=='2026-08-24' and t['validation_last']=='2026-09-06'
    assert t['fit_last']<t['validation_first']
    retry=ensemble.fit(rows,dt.date(2026,8,23),[state])[0]
    assert retry['production_trial']['id']==t['id'] and retry['production_trial']['weights']==t['weights']
    assert retry['production_trial']['validation_first']==t['validation_first']
    later=ensemble.fit(rows,dt.date(2026,9,9),[retry])[0]['production_trial']
    assert later['decision']=='rejected' and later['proof']['calls']==0 and later['proof']['expected_calls']==14
    assert later['id']==t['id']
    public=json.dumps(ensemble.summary(state));assert '"weights"' not in public


def test_current_live_adapter_requires_actual_server_receipt_and_original_primary_packet(monkeypatch):
    call,packet,_=case()
    now=dt.datetime(2026,10,24,12,0,2,tzinfo=dt.timezone.utc)
    identity='|'.join(('synthetic','SYN0','legacy',packet['version']))
    meta=dict(fit_last='2026-09-01',recipe_sources_sha256=current_calls.digest(current_model.source_hashes()))
    r=product.release('current_forecast','synthetic','noon','SYN0','C',identity,dict(metadata=meta),proof())
    receipt=dict(receipt_id=20,decision='activate',recorded_at='2026-10-24T11:00:00Z',expires_at='2026-11-01T00:00:00Z',release=r)
    monkeypatch.setattr(common,'rpc',lambda *a:[receipt])
    monkeypatch.setattr(common,'rest_all',lambda *a,**k:[dict(key=call['input_key'],captured_at='2026-10-24T12:00:01Z',written_at='2026-10-24T12:00:01Z')])
    monkeypatch.setattr(common,'model_version_id',lambda *a,**k:'registered')
    monkeypatch.setattr(primary,'_predictions',{('synthetic','2026-10-24',frozenset(call['probs'])):dict(input_key=call['input_key'],product_input_packet=packet)})
    monkeypatch.setattr(primary,'checkpoint_lineage',lambda *a,**k:{k:call[k] for k in ('input_as_of','input_sha256','input_key','raw_probs','predictive_context')})
    monkeypatch.setattr(current_registry,'load',lambda *a,**k:'native-artifact')
    def predict(booster,records):
        assert booster=='native-artifact' and records[0]['incumbent_probs']==call['probs']
        assert records[0]['primary_packet_written_at']=='2026-10-24T12:00:01Z'
        assert records[0]['frozen_at'] is None and records[0]['prospective_at']==now.isoformat()
        return [dict(a=0.,b=.2,c=.8)]
    monkeypatch.setattr(current_model,'predict',predict)
    product.prepare([dict(city_key='synthetic')],now)
    base=([dict(band_id=k,calibrated_prob=v,pricing_eligible=True) for k,v in call['probs'].items()],None,['priced_from:wxrd3:old'])
    got=product.apply('synthetic','2026-10-24',base,BANDS,'C',call['market'],'SYN0',checkpoint='noon',now=now)
    assert {r['band_id']:r['calibrated_prob'] for r in got[0]}==dict(a=0.,b=.2,c=.8), (product._status,got[2])
    monkeypatch.setattr(product,'_receipts',{})
    assert product.apply('synthetic','2026-10-24',base,BANDS,'C',call['market'],'SYN0',checkpoint='noon',now=now)[0]==base[0]


def test_fixed_source_trial_can_approve_only_its_exact_frozen_weight_candidate(monkeypatch):
    monkeypatch.setattr(ensemble,'_states',{})
    tr=trial();tr.pop('proof');tr['decision']='waiting'
    rows=[]
    for i in range(14):
        r=scored(str(dt.date(2026,9,3)+dt.timedelta(days=i)))
        r['packet']=r['ensemble_packet']
        r['packet'].update(production_trial_id=tr['id'],production_weights_sha256=policy.digest(tr['weights']),
                           incumbent_probs=dict(a=.6,b=.2,c=.2),
                           production_candidate_probs=dict(a=.05,b=.9,c=.05))
        rows.append(r)
    state=dict(city_key='city',phase='d1_eve',model_version=ensemble.family('TEST','C'),weights={m:1/7 for m in ensemble.MODELS},learning_dates=20)
    approved=policy.source_trial(rows,dt.date(2026,9,18),state,dict(production_trial=tr))
    assert approved['decision']=='approved' and policy.qualified(approved['proof']) is None
    assert approved['proof']['calls']==approved['proof']['expected_calls']==14
    other=copy.deepcopy(rows)
    for r in other:r['packet']['production_weights_sha256']='b'*64
    assert policy.source_trial(other,dt.date(2026,9,18),state,dict(production_trial=tr))['decision']=='rejected'
    # A later refit/retry cannot change weights, window, proof cutoff or expiry.
    state['weights'][ensemble.MODELS[0]]=.4
    assert policy.source_trial([],dt.date(2026,10,1),state,dict(production_trial=approved))==approved


def test_market_blend_cannot_revive_a_bucket_below_the_observed_station_maximum(monkeypatch):
    row,ctx,now=original();row['running_max_c']=21.
    row['probs']=dict(a=0.,b=.5,c=.5)
    p=ensemble.capture(row,BANDS,'C',ctx,now,state_override=dict(production_trial=trial()))
    assert p and p['production_candidate_probs']['a']==0
    assert sum(p['production_candidate_probs'].values())==pytest.approx(1)


def test_scheduled_probability_engine_inserts_product_rows_used_by_edges(monkeypatch):
    row,ctx,now,receipt,base=setup_source(monkeypatch)
    # Keep the prepared receipt clock deterministic while running the real
    # main orchestration and the real production adapter.
    prepare=product.prepare
    monkeypatch.setattr(product,'prepare',lambda markets:prepare(markets,now))
    monkeypatch.setattr(product,'pricing_books',lambda *a:row['market'])
    apply=product.apply
    monkeypatch.setattr(product,'apply',lambda *a,**k:apply(*a,**k,now=now))
    import early_city_adaptation as early,contract_inputs
    monkeypatch.setattr(early,'prepare',lambda *a:None)
    monkeypatch.setattr(primary,'prepare',lambda *a:{})
    monkeypatch.setattr(contract_inputs,'floors',lambda *a:{})
    monkeypatch.setattr(pe,'get_cities',lambda **k:[dict(city_key='city',unit='C',icao='TEST')])
    monkeypatch.setattr(pe,'_upcoming_markets',lambda:[dict(city_key='city',market_id='m',resolution_date=row['target_date'],unit='C',contract_station='TEST')])
    monkeypatch.setattr(pe,'_bands_for_markets',lambda *a:[dict(b,market_id='m') for b in BANDS])
    for name in ('_observed_floors','_promoted_models','_model_forecasts','_warm_caches','_warm_skill_cache'):
        monkeypatch.setattr(pe,name,lambda *a:{})
    base[1].confidence=.5
    monkeypatch.setattr(pe,'process_city_day',lambda *a:base)
    inserted=[];logs=[]
    monkeypatch.setattr(pe,'insert',lambda table,rows:inserted.append((table,copy.deepcopy(rows))))
    monkeypatch.setattr(pe,'log_run',lambda *a:logs.append(a))
    pe.main()
    assert len(inserted)==1 and inserted[0][0]=='band_probabilities'
    q={r['band_id']:r['calibrated_prob'] for r in inserted[0][1]}
    assert q!=row['probs'] and logs[0][-1]['product']['applied']==1
    assert all(r['forecast_version']=='registered' for r in inserted[0][1])


def test_current_publisher_requires_hashed_fixed_forward_and_local_development_gates(tmp_path,monkeypatch):
    from wxpredict import product_promotion as promotion,cycle_registry
    today=dt.datetime.now(dt.timezone.utc).date();first=today-dt.timedelta(days=14)
    available=(first-dt.timedelta(days=2)).isoformat()+'T05:00:00Z'
    identity='synthetic|SYN0|awc-routine-v1|wxrd3:fixed'
    local=dict(source_identity=identity,city='synthetic',checkpoint='noon',supported=True,
               calls=14,dates=14,training_dates=20,candidate_hits=10,incumbent_hits=7,market_hits=8,
               candidate_brier=.2,incumbent_brier=.3,market_brier=.25,candidate_nll=.3,incumbent_nll=.5,
               market_nll=.4,log_loss_gain_lower90=.1,market_log_loss_gain_lower90=.05)
    report=dict(decision='forward validated',model_sha256='a'*64,night=str(today),reasons=[],
                forward_first=str(first),forward_evaluation_last=str(today-dt.timedelta(days=1)),city_horizons=[local])
    development=dict(decision='accept shadow',city_horizons=[copy.deepcopy(local)])
    meta=dict(model_sha256='a'*64,fit_last=str(first-dt.timedelta(days=20)),evaluation_report='dev.json',training_dates={identity+'|noon':20})
    (tmp_path/'forward.json').write_text(json.dumps(report));(tmp_path/'dev.json').write_text(json.dumps(development))
    state=dict(paused=False,forward_validated=dict(report='forward.json',report_sha256=current_calls.digest(report)))
    monkeypatch.setattr(current_registry,'state',lambda root:state)
    monkeypatch.setattr(current_registry,'selected',lambda root:(meta,available))
    monkeypatch.setattr(promotion.common,'ops',lambda:SimpleNamespace(get_cities=lambda **k:[dict(city_key='synthetic',unit='C')]))
    sent=[]
    result=promotion.publish(lambda name,p:sent.append((name,p)),str(tmp_path))
    assert result['published']==1 and sent[0][0]=='publish_predictive_product'
    assert sent[0][1]['p_release']['payload']['metadata']==meta
    # Even a globally validated artifact cannot authorize a losing city cell.
    development['city_horizons'][0]['market_hits']=10
    (tmp_path/'dev.json').write_text(json.dumps(development))
    assert promotion.publish(lambda *a:pytest.fail('losing city published'),str(tmp_path))['published']==0
    report['forward_first']=str(first+dt.timedelta(days=1));(tmp_path/'forward.json').write_text(json.dumps(report))
    with pytest.raises(ValueError,match='proof mismatch'):promotion.publish(lambda *a:None,str(tmp_path))
