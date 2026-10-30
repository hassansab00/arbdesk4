"""Prospective correction candidates, city clocks, chronology and bounded activation."""
import copy
import datetime as dt

import pytest
import early_city_adaptation as e
import probability_engine as pe
import predictive_validation as validation
import early_forecast_audit as audit
import tick
from test_the_correction_reaches_the_price import _price, BANDS

UTC = dt.timezone.utc
RECIPE = dict(source_model='nws', centre='measured_bias')
FAMILY = e.family('awc-routine-v1','KLGA','C',RECIPE)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(e,'_contexts',{})
    monkeypatch.setattr(e,'_states',{})
    monkeypatch.setattr(pe,'_calibration',{})


def history():
    as_of = dt.date(2026,10,30)
    rows=[]
    for offset in range(2,36):
        day=as_of-dt.timedelta(days=offset)
        qs={str(b):{'a':.1+.8*b,'b':.9-.8*b} for b in e.GRID}
        packet=dict(policy=e.POLICY,model_version=FAMILY,contract='awc-routine-v1',station='KLGA',
                    unit='C',recipe=RECIPE,checkpoint='morning',timezone='UTC',
                    scheduled_at=f'{day}T09:00:00Z',input_forecast_run=f'{day}T08:00:00Z',
                    candidates=qs,raw_centre_probs=qs['0.0'],corrected_probs=qs['1.0'],
                    served_probs=qs['1.0'],strength=1.,version=e.POLICY+':prior',fitted_as_of=None)
        rows.append(dict(city_key='city',target_date=str(day),checkpoint='morning',settled=True,verified=True,
                         probs=qs['1.0'],winner='b',timing={'before_expected_peak':True,'minutes_before_expected_peak':180},frozen_at=f'{day}T09:36:00Z',
                         settled_at=f'{day+dt.timedelta(days=1)}T01:00:00Z',
                         label_received_at=f'{day+dt.timedelta(days=1)}T01:01:00Z',
                         paired_context={'early_forecast_audit':packet}))
    return rows


def test_fit_moves_one_bounded_step_only_after_independent_unseen_dates_and_revalidates():
    rows=history(); original=copy.deepcopy(rows)
    state=e.fit(rows,'2026-10-30')[0]
    assert state['strength']==.9 and state['decision']=='applied'
    assert state['train_dates']>=20 and state['validation_dates']>=10
    assert state['validation']['gain_lower90']>0
    assert e.usable(state,'2026-10-30',FAMILY,'morning')
    rerun=e.fit(rows,'2026-10-30',[state])[0]
    assert rerun['strength']==state['strength'] and rerun['version']==state['version']
    assert rerun['decision']=='revalidated'
    assert not e.usable(state,'2026-11-04',FAMILY,'morning')
    assert not e.usable(state,'2026-10-30',FAMILY,'d1_eve')
    assert not e.usable(state,'2026-10-30','different source','morning')
    assert rows==original


def test_fresh_validation_rejection_restores_prior_immediately():
    rows=history(); old=e.fit(rows,'2026-10-30')[0]
    for row in rows:
        if row['target_date']>='2026-10-16':row['winner']='a'
    state=e.fit(rows,'2026-10-30',[old])[0]
    assert state['strength']==1 and state['decision']=='reverted'
    assert not e.usable(state,'2026-10-30',FAMILY,'morning')


@pytest.mark.parametrize('change',['future_label','future_input','late_freeze','unreceipted','legacy_recipe','wrong_station','new_contract','packet_mismatch','future_fit'])
def test_invalid_or_future_evidence_never_trains(change):
    row=history()[0];p=row['paired_context']['early_forecast_audit']
    if change=='future_label':row['label_received_at']='2026-10-30T00:00:00Z'
    if change=='future_input':p['input_forecast_run']='2026-10-30T00:00:00Z'
    if change=='late_freeze':row['frozen_at']='2026-10-30T00:00:00Z'
    if change=='unreceipted':row['verified']=False
    if change=='legacy_recipe':p['policy']=audit.POLICY
    if change=='wrong_station':p['station']='KJFK'
    if change=='new_contract':p['contract']='new'
    if change=='packet_mismatch':p['served_probs']={'a':.5,'b':.5}
    if change=='future_fit':p['fitted_as_of']='2026-11-01'
    assert not e.eligible([row],dt.date(2026,10,30))


def test_minimum_is_per_city_and_horizon_and_the_future_cannot_choose_the_training_candidate():
    rows=history(); original=e.fit(rows,'2026-10-30')[0]
    assert e.fit(rows[:10],'2026-10-30')[0]['strength']==1
    for row in rows:
        if row['target_date']>='2026-10-16':row['winner']='a'
    # The candidate was selected on training only, so held-out changes reject it.
    assert e.fit(rows,'2026-10-30')[0]['strength']==1
    assert original['strength']==.9
    for i,row in enumerate(rows):
        if i%2:row['city_key']='other'
    assert all(s['strength']==1 for s in e.fit(rows,'2026-10-30'))


def test_prepare_uses_city_day_and_makes_one_compact_read_and_handles_dst():
    calls=[]
    cities=[dict(city_key='city',timezone='America/New_York',icao='KLGA',unit='F')]
    markets=[dict(city_key='city',resolution_date='2026-11-01',observation_contract='awc-routine-v1',contract_station='KLGA',unit='F')]
    def read(*args,**kwargs):calls.append((args,kwargs));return []
    e.prepare(markets,cities,dt.datetime(2026,11,1,14,36,tzinfo=UTC),read)
    assert e._contexts[('city','2026-11-01')]['checkpoint']=='morning'
    assert e._contexts[('city','2026-11-01')]['scheduled_at'].endswith('-05:00')
    assert len(calls)==1 and calls[0][0][0]=='derived_wx_city_calibration'
    e.prepare(markets,cities,dt.datetime(2026,11,1,17,tzinfo=UTC),read)
    assert not e._contexts and len(calls)==1


@pytest.mark.parametrize('unit',['C','F'])
def test_real_floored_pricing_and_tick_freeze_candidates_before_labels(monkeypatch,unit):
    context=dict(checkpoint='morning',contract='awc-routine-v1',station='KLGA',unit=unit,timezone='UTC',scheduled_at='2026-09-19T09:00:00Z')
    monkeypatch.setattr(e,'_contexts',{('london','2026-09-19'):context})
    # Run the real engine to verify its selected ladder and its pre-calibration
    # raw ladder remain coherent, even when a validated strength moves a centre.
    if unit=='C':
        rows,reg,reasons=_price(monkeypatch,skill_bias=2)
        ctx=pe.forecast_context('london','2026-09-19',rows)
        packet=ctx['early_forecast_audit']
        family=packet['model_version']
        old=e.fit(history(),'2026-10-30')[0]
        state=dict(old,model_version=family,as_of='2026-09-19',validated_through='2026-09-18')
        monkeypatch.setattr(e,'_states',{('london','morning',family):state})
        # The helper deliberately disables registration; restore a real-shaped
        # successful registration after its setup and before the second price.
        real_process=pe.process_city_day
        def registered(*args,**kwargs):
            monkeypatch.setattr(pe,'model_version_id',lambda *a,**kw:'00000000-0000-0000-0000-000000000001')
            return real_process(*args,**kwargs)
        monkeypatch.setattr(pe,'process_city_day',registered)
        new,reg,why=_price(monkeypatch,skill_bias=2)
        assert new[0]['centre_c']==pytest.approx(18.2)
        assert any(e.POLICY in r for r in why if r.startswith('priced_from:'))
        call,_=tick.build_row('london','2026-09-19','morning',dt.datetime(2026,9,19,9),new,why,{},None,'v',bands=BANDS,unit='C')
        p=call['predictive_context']['early_forecast_audit']
        assert p['strength']==.9 and e.valid_packet(p,call['probs'])
        assert 'early_forecast_audit' not in call['predictive_context']['forecast_input']
        assert audit.score(p,call['probs'],'mid')
    bands=BANDS if unit=='C' else [dict(b,band_lo=None if b.get('band_lo') is None else b['band_lo']*1.8+32,
                                        band_hi=None if b.get('band_hi') is None else b['band_hi']*1.8+32) for b in BANDS]
    _,base=e.distribution(pe,18,1,unit,bands,20,0,0)
    rows=[dict(band_id=k,calibrated_prob=p,input_forecast_run='2026-09-19T08:00:00Z') for k,p in base.items()]
    packet,_=e.price(pe,'london','2026-09-19',rows,bands,unit,20,18,1,20,0,0,RECIPE)
    assert packet and e.valid_packet(packet,base)
    for q in packet['candidates'].values():
        assert sum(q.values())==pytest.approx(1,abs=1e-4)
        for k in pe.impossible_band_ids(20,unit,bands):assert q[k]<=pe.PROB_FLOOR+1e-6


def test_original_first_receipt_wins_and_score_only_reports_readiness_without_rewriting():
    rows=history();r=rows[0];day=r['target_date']
    p=dict(checkpoint_id='first',city_key='city',target_date=day,checkpoint='morning',local_decision_time=day+'T09:00:00',
           decided_at=r['frozen_at'],frozen_at=r['frozen_at'],engine_version='v',probs=r['probs'],predictive_context=dict(r['paired_context'],timing_reference=dict(source='derived_weather_peak',city_key='city',timezone='UTC',month=10,peak_hour_local=14,computed_at='2026-09-30T00:00:00Z',n_days=40)))
    late=dict(p,checkpoint_id='replacement',decided_at=day+'T09:50:00Z',probs={'a':0,'b':1})
    f=dict(checkpoint_id='first',winner_band_id='b',settled_at=r['settled_at'],banked_at=r['label_received_at'])
    cities=[dict(city_key='city',timezone='UTC')]
    captured=validation.collect([late,p],[f],[],cities,{},dt.datetime(2026,10,30,tzinfo=UTC))
    assert len(captured)==1 and captured[0]['probs']==r['probs']
    assert len(e.eligible(captured,dt.date(2026,10,30)))==1
    states=e.fit(captured,'2026-10-30')
    report=next(x['report'] for x in validation.reports(captured,cities,[],states,dt.datetime(2026,10,30,tzinfo=UTC))
                if x['scope']=='served' and x['checkpoint']=='morning')
    assert report['early_adaptation'][0]['effective_strength']==1
    assert not report['early_adaptation'][0]['active']
    assert report['early_adaptation'][0]['readiness']['counts'][0]['missing']==20


def test_verified_offload_extends_history_without_database_reads_and_rejects_corruption(tmp_path):
    import csv,gzip,hashlib,io,json
    manifest={'tables':{}}
    day='2026-09-25'
    call=dict(checkpoint_id='p',city_key='city',target_date=day,checkpoint='morning',
              probs=json.dumps({'a':.8,'b':.2}),predictive_context=json.dumps({'original':True}),frozen_at=day+'T09:36:00Z')
    fact=dict(checkpoint_id='p',winner_band_id='b',settled_at='2026-09-26T01:00:00Z',banked_at='2026-09-26T02:00:00Z')
    for table,rows in [('prediction_checkpoints',[call,dict(call,checkpoint_id='newer',target_date='2026-10-25')]),('fact_checkpoint_outcome',[fact])]:
        folder=tmp_path/'data/mirror'/table;folder.mkdir(parents=True)
        text=io.StringIO();writer=csv.DictWriter(text,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        blob=gzip.compress(text.getvalue().encode());(folder/'rows.csv.gz').write_bytes(blob)
        manifest['tables'][table]=dict(through='2026-10-30T00:00:00Z',files=[dict(file='rows.csv.gz',rows=len(rows),
            sha256=hashlib.sha256(blob).hexdigest(),**{'from':None,'to':'2026-10-30T00:00:00Z'})])
    (tmp_path/'data/mirror/manifest.json').write_text(json.dumps(manifest))
    calls,facts=e.older_mirrored_calls(tmp_path,dt.date(2026,10,30),'2026-09-30')
    assert len(calls)==len(facts)==1 and calls[0]['probs']=={'a':.8,'b':.2}
    assert calls[0]['predictive_context']=={'original':True}
    (tmp_path/'data/mirror/prediction_checkpoints/rows.csv.gz').write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash mismatch'):
        e.older_mirrored_calls(tmp_path,dt.date(2026,10,30),'2026-09-30')


@pytest.mark.parametrize('timing',[{}, {'before_expected_peak':False,'minutes_before_expected_peak':-30},
                                  {'before_expected_peak':True,'minutes_before_expected_peak':30}])
def test_morning_learning_excludes_missing_peak_evidence_and_nearly_finished_city_days(timing):
    row=history()[0];row['timing']=timing
    assert not e.eligible([row],dt.date(2026,10,30))
