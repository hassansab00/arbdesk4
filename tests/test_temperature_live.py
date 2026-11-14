import copy
import datetime as dt
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import temperature_model as m,temperature_live as live,temperature_learning as learning,temperature_evaluation as evaluation,pre_day_pairs as pp
import temperature_register
from test_temperature_model import record,artifact
from test_awc_weather_contract import RULES


def packet():
    r=record(day='2026-10-23',city='nyc',zone='America/New_York',unit='F')
    context=r['hourly'];first=context['hours'][0]
    weather=dict(capture_id=1,kind='hourly0',key='nyc|snapshot',captured_at=first['captured_at'],written_at=first['written_at'],
        payload=dict(schema='nwp-current-snapshot-v1',request_location=dict(latitude=40.7,longitude=-73.9,timezone=r['timezone']),
        hours={dt.datetime.fromtimestamp(h['utc'],dt.timezone.utc).strftime('%Y-%m-%dT%H:%M'):h['values'] for h in context['hours']}))
    return dict(as_of=r['as_of'],checkpoint=r['checkpoint'],weather=weather,rules=dict(rules_text=RULES,received_at='2026-10-20T00:00:00Z'),
        market=dict(market_id='synthetic-market',city_key='nyc',resolution_date=r['date'],unit='F'),
        city=dict(city_key='nyc',icao='KLGA',latitude=40.7,longitude=-73.9,timezone=r['timezone']),
        bands=[dict(band_id=b,lo=lo,hi=hi) for b,(lo,hi) in zip(r['band_ids'],r['bounds'])],
        references=dict(raw_forecast_c=23,raw_sigma_c=1.5))


def test_live_packet_preserves_original_hourly_input_and_private_references():
    p=packet();before=copy.deepcopy(p)
    row,why=live.prepare(p,artifact(),'2026-01-02T00:00:00Z',p['as_of'])
    assert why is None and row['record']['station']=='KLGA'
    assert row['record']['forecast_issued_at'] is None
    assert row['record']['issuance_basis'].startswith('provider_initialization_not_supplied')
    assert row['record']['hourly'] and m.validate(row['record'])
    assert row['hourly_reference']==row['prediction']['probs']
    assert sum(row['raw_normal_reference'].values())==pytest.approx(1.)
    assert p==before


def test_exact_decimal_reference_receipt_survives_json_transport():
    from decimal import Decimal
    p=packet()
    receipt='{"raw_forecast_c":23,"raw_sigma_c":1.5,"market":{"a":0.18518518518518518519}}'
    p['references']=json.loads(receipt)
    p['references_receipt']=receipt
    row,why=live.prepare(p,artifact(),'2026-01-02T00:00:00Z',p['as_of'])
    sent=json.loads(json.dumps(row))
    assert why is None and 'references' not in sent
    assert sent['references_receipt']==receipt
    assert json.loads(sent['references_receipt'],parse_float=Decimal)['market']['a']==Decimal('0.18518518518518518519')
    p['references']['market']['a']=.5
    with pytest.raises(ValueError,match='receipt differs'):
        live.prepare(p,artifact(),'2026-01-02T00:00:00Z',p['as_of'])


@pytest.mark.parametrize('mutation,reason',[
    (lambda p:p['rules'].update(received_at='2027-01-01T00:00:00Z'),'missing_as_of_rules_receipt'),
    (lambda p:p['city'].update(icao='KJFK'),'unknown_or_mismatched_observation_contract'),
    (lambda p:p['market'].update(unit='C'),'dated_contract_unit_mismatch'),
    (lambda p:p['weather']['payload']['request_location'].update(latitude=42),'missing_or_mismatched_station_weather_location'),
    (lambda p:p.update(weather=None),'missing_received_weather'),
])
def test_live_contract_and_source_gaps_are_named_exclusions(mutation,reason):
    p=packet();mutation(p)
    assert live.prepare(p,artifact(),'2026-01-02T00:00:00Z',p['as_of'])==(None,reason)


def test_registration_roundtrips_private_coefficients_without_publication_or_capture():
    a=artifact();cohort=[f'city{i}' for i in range(20)];called=[]
    receipt=dict(model_sha256=a['sha256'],registered_at='2026-01-02T00:00:00Z',cohort=cohort)
    def rpc(name,params=None,**kw):
        called.append(name)
        if name=='temperature_register':
            assert params['p_protocol']==evaluation.specification();return receipt
        assert name=='temperature_inputs'
        return dict(artifact=json.loads(json.dumps(a)),registered_at=receipt['registered_at'],cohort=cohort,protocol=evaluation.specification())
    assert temperature_register.register(a,cohort,rpc)['roundtrip_verified']
    assert called==['temperature_register','temperature_inputs']


def test_first_original_is_chosen_before_label_or_feature_eligibility():
    r=record();c=dict(record=r,call_id='a',city_key=r['city'],target_date=r['date'],checkpoint=r['checkpoint'],frozen_at=r['as_of'])
    later=copy.deepcopy(c);later.update(call_id='b',frozen_at=(pp.timestamp(c['frozen_at'])+dt.timedelta(seconds=1)).isoformat())
    s=dict(winner_band_id='b',confirmed_at='2026-08-02T00:00:00Z',scored_at='2026-08-02T01:00:00Z')
    rows,detail=learning.prepare([dict(call=later,score=s),dict(call=c,score=None)],'2026-08-03T00:00:00Z')
    assert not rows and detail['first_calls']==1 and detail['exclusions']=={'pending_venue_outcome':1}


def test_insufficient_learning_history_records_health_without_a_fit_or_serving_write():
    calls=[]
    def rpc(name,params=None,**kw):
        calls.append(name)
        if name=='temperature_learning_inputs':return dict(state='ready',calls=[],cutoff='2026-08-03T00:00:00Z')
        assert name=='temperature_fit_report' and params['p_artifact'] is None
    result=learning.run(rpc)
    assert result['state']=='insufficient_legitimate_history' and result['reached_serving'] is False
    assert calls==['temperature_learning_inputs','temperature_fit_report']


@pytest.mark.parametrize('zone,cutoff', [
    ('America/Los_Angeles','2026-08-02T05:00:00Z'),
    ('Asia/Kathmandu','2026-08-01T18:00:00Z'),
    ('America/New_York','2026-11-02T04:30:00Z'),
])
def test_preparation_excludes_labels_before_the_city_local_day_ends(zone,cutoff):
    day='2026-11-01' if '11-02' in cutoff else '2026-08-01'
    r=record(day=day,zone=zone)
    c=dict(record=r,call_id='first',city_key=r['city'],target_date=day,
           checkpoint=r['checkpoint'],frozen_at=r['as_of'])
    at=pp.timestamp(cutoff)
    s=dict(winner_band_id='b',confirmed_at=(at-dt.timedelta(hours=2)).isoformat(),
           scored_at=(at-dt.timedelta(hours=1)).isoformat())
    before=copy.deepcopy((c,s))
    rows,detail=learning.prepare([dict(call=c,score=s)],cutoff)
    assert not rows and detail['eligible_rows']==0
    assert detail['exclusions']=={'invalid:temperature training label unavailable or not original':1}
    assert (c,s)==before
    training=dict(r,frozen_at=c['frozen_at'],label=dict(winner='b',
        settled_at=s['confirmed_at'],available_at=s['scored_at']))
    with pytest.raises(ValueError,match='temperature training label unavailable or not original'):
        m.fit([training],cutoff)


@pytest.mark.parametrize('seconds,expected', [(300,1),(301,0)])
def test_preparation_enforces_the_same_five_minute_freeze_bound_as_fitting(seconds,expected):
    r=record()
    c=dict(record=r,call_id='first',city_key=r['city'],target_date=r['date'],
           checkpoint=r['checkpoint'],frozen_at=(pp.timestamp(r['as_of'])+dt.timedelta(seconds=seconds)).isoformat())
    s=dict(winner_band_id='b',confirmed_at='2026-08-02T00:00:00Z',scored_at='2026-08-02T01:00:00Z')
    rows,detail=learning.prepare([dict(call=c,score=s)],'2026-08-03T00:00:00Z')
    assert len(rows)==detail['eligible_rows']==expected


def test_existing_fixed_zero_reference_keeps_exact_artifact_and_probabilities():
    current=artifact();old=copy.deepcopy(current)
    old['sources'].update(m.PRE_LABEL_GATE_SOURCES)
    old['sha256']=pp.digest({k:v for k,v in old.items() if k!='sha256'})
    before=copy.deepcopy(old)
    original_prediction=m.predict(old,record());current_prediction=m.predict(current,record())
    for field in ('adjustment_version','uncertainty_version'):
        assert original_prediction.pop(field)==old['sha256']
        assert current_prediction.pop(field)==current['sha256']
    assert original_prediction==current_prediction
    assert old==before
    old['sources']['tools/wxpredict/temperature_live.py']='f'*64
    old['sha256']=pp.digest({k:v for k,v in old.items() if k!='sha256'})
    with pytest.raises(ValueError,match='artifact identity'):m.model(old)


def test_legacy_source_exception_cannot_accept_fitted_or_nonzero_reference():
    a=artifact();a['sources'].update(m.PRE_LABEL_GATE_SOURCES)
    a['state']='fitted_candidate'
    a['sha256']=pp.digest({k:v for k,v in a.items() if k!='sha256'})
    with pytest.raises(ValueError,match='artifact identity'):m.model(a)
    a['state']='unfitted_reference';a['mean_coefficients'][0]=float(1).hex()
    a['sha256']=pp.digest({k:v for k,v in a.items() if k!='sha256'})
    with pytest.raises(ValueError,match='unfitted reference contains learned'):m.model(a)


def test_warmups_stay_preserved_but_cannot_block_the_declared_full_training_window():
    r=record();c=dict(record=r,call_id='a',city_key=r['city'],target_date=r['date'],checkpoint=r['checkpoint'],frozen_at=r['as_of'])
    rows,detail=learning.prepare([dict(call=c,score=None)],'2026-08-05T00:00:00Z',training_first='2026-08-03')
    assert rows==[] and detail['exclusions']=={'warmup_before_predeclared_training_start':1}


def test_reference_or_unfinished_trial_cannot_be_promoted():
    reg=dict(protocol=evaluation.specification(),cohort=[f'city{i}' for i in range(20)],evaluation_first='2026-10-01',evaluation_last='2026-10-20',
        state='unfitted_reference',registered_at='2026-09-28T00:00:00Z',model_sha256='b'*64)
    out=evaluation.evaluate_trial(reg,[],'2026-10-05T00:00:00Z')
    assert not out['window_finished'] and not out['eligible_for_promotion'] and not out['distribution_gates_passed']
    assert set(out['by_horizon'])==set(m.SLOTS)
    assert all(r['expected']==400 and r['coverage']==0 for r in out['by_horizon'].values())
    reg['protocol']['minimum_calls_per_horizon']=1
    with pytest.raises(ValueError,match='preregistration'):evaluation.evaluate_trial(reg,[],'2026-11-01T00:00:00Z')
