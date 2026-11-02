"""Synthetic evidence contracts for the private forecast-state experiment."""
import copy
import datetime as dt
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import forecast_state as m, forecast_state_study as study, pre_day_pairs as pp

DEFINITIONS={
    'a':dict(band_id='a',lo=None,hi=20),
    'b':dict(band_id='b',lo=20,hi=22),
    'c':dict(band_id='c',lo=22,hi=None)}
THROUGH='2026-08-30T00:00:00Z'


def packet(day='2026-08-01',city='synthetic',zone='UTC',slot='morning'):
    d=dt.date.fromisoformat(day);tz=ZoneInfo(zone)
    start=dt.datetime.combine(d,dt.time(),tz)
    at=start+dt.timedelta(hours=9,minutes=37)
    if slot=='d1_eve':at=start-dt.timedelta(hours=5,minutes=23)
    end=dt.datetime.combine(d+dt.timedelta(days=1),dt.time(),tz)
    c=dict(checkpoint_id=city+day+slot,city_key=city,target_date=day,checkpoint=slot,
           decided_at=at.isoformat(),frozen_at=at.isoformat(),station='TEST',inputs_ok=True,
           probs={'a':.15,'b':.55,'c':.3},top_band_id='b',centre_c=21.,raw_forecast_c=20.,sigma_c=1.5,
           running_max_c=20.4,reading_at=(at-dt.timedelta(minutes=30)).isoformat(),
           forecast_issued_at=(at-dt.timedelta(hours=1)).isoformat(),
           market={bid:dict(bid=p-.01,ask=p+.01) for bid,p in {'a':.2,'b':.5,'c':.3}.items()})
    fact=dict(checkpoint_id=c['checkpoint_id'],city_key=city,target_date=day,checkpoint=slot,
              winner_band_id='b',settled_at=(end+dt.timedelta(minutes=5)).isoformat(),
              banked_at=(end+dt.timedelta(minutes=6)).isoformat())
    peak=dict(city_key=city,timezone=zone,month=d.month,peak_hour_local=12.,computed_at='2026-07-01T00:00:00Z')
    return dict(call=c,city=dict(timezone=zone,unit='C',latitude=0.),fact=fact,
                timing_reference=peak,contract='synthetic-test',rules_sha256='synthetic-rules')


def row(**kwargs):
    r,why=m.attach(packet(**kwargs),DEFINITIONS,THROUGH)
    assert why is None
    return r


def rehash(record):
    record['feature_sha256']=pp.digest({k:v for k,v in record.items() if k not in ('feature_sha256','label')})
    return record


@pytest.mark.parametrize('zone',['UTC','Asia/Riyadh','Asia/Kathmandu','America/New_York','Pacific/Auckland'])
def test_local_clocks_and_original_freezes_are_preserved(zone):
    p=packet(zone=zone);before=copy.deepcopy(p)
    r,why=m.attach(p,DEFINITIONS,THROUGH)
    assert why is None and r['peak_lead_h']==pytest.approx(2+23/60)
    assert r['peak_status']=='before_as_of_reference_peak'
    assert p==before
    m.validate(r)


def test_dst_uses_actual_local_peak_and_elapsed_utc_hours():
    p=packet(day='2026-11-01',zone='America/New_York')
    r,why=m.attach(p,DEFINITIONS,'2026-11-04T00:00:00Z')
    assert why is None and r['peak_lead_h']==pytest.approx(2+23/60)


def test_jeddah_noon_cannot_be_scored_as_an_advance_call_after_reference_peak():
    p=packet(zone='Asia/Riyadh',slot='noon')
    at=dt.datetime.fromisoformat(p['call']['decided_at'])+dt.timedelta(hours=3)
    p['call'].update(decided_at=at.isoformat(),frozen_at=at.isoformat(),
                     reading_at=(at-dt.timedelta(minutes=30)).isoformat())
    assert m.attach(p,DEFINITIONS,THROUGH)==(None,'less_than_one_hour_before_reference_peak')


def test_missing_peak_is_honestly_stratified_only_for_original_morning():
    p=packet();p['timing_reference']=None
    r,why=m.attach(p,DEFINITIONS,THROUGH)
    assert why is None and r['peak_lead_h'] is None
    assert r['peak_status']=='original_morning_without_peak_proof'
    p['call']['checkpoint']='noon'
    assert m.attach(p,DEFINITIONS,THROUGH)==(None,'missing_as_of_peak_reference')


@pytest.mark.parametrize('change,reason',[
    ({'frozen_at':None},'missing_server_freeze'),
    ({'inputs_ok':False},'original_inputs_not_ok'),
    ({'frozen_at':'2026-08-01T09:45:00+00:00'},'late_or_backdated_freeze'),
    ({'forecast_issued_at':None},'missing_forecast_timestamp'),
    ({'reading_at':None},'missing_original_observation'),
    ({'reading_at':'2026-08-01T07:00:00Z'},'stale_original_observation'),
])
def test_evidence_gaps_are_excluded_with_named_reasons(change,reason):
    p=packet();p['call'].update(change)
    assert m.attach(p,DEFINITIONS,THROUGH)==(None,reason)


@pytest.mark.parametrize('change',[
    {'reading_at':'2026-08-01T10:00:00Z'},
    {'reading_at':'2026-07-31T23:59:00Z'},
    {'forecast_issued_at':'2026-08-01T10:00:00Z'},
    {'sigma_c':float('nan')},{'sigma_c':0.},
    {'top_band_id':'a'}, {'probs':{'a':.2,'b':.6,'c':.3}},
])
def test_contradictory_evidence_is_an_error_not_a_silent_exclusion(change):
    p=packet();p['call'].update(change)
    with pytest.raises(ValueError):m.attach(p,DEFINITIONS,THROUGH)


def test_evening_before_does_not_use_an_observation_from_another_day():
    r=row(slot='d1_eve')
    assert r['floor_c'] is None and r['obs_age_min'] is None
    assert m.state(r)=='evening_before'
    p=packet(slot='d1_eve');p['call']['frozen_at']='2026-08-01T00:00:00Z'
    assert m.attach(p,DEFINITIONS,THROUGH)[0] is None


def test_identity_bounds_and_outcome_timing_are_checked():
    p=packet();p['fact']['city_key']='other'
    with pytest.raises(ValueError,match='different original'):m.attach(p,DEFINITIONS,THROUGH)
    p=packet();p['fact']['banked_at']='2026-08-01T00:00:00Z'
    with pytest.raises(ValueError,match='availability'):m.attach(p,DEFINITIONS,THROUGH)
    bad=copy.deepcopy(DEFINITIONS);bad['a']['hi']=19
    with pytest.raises(ValueError,match='gap or overlap'):m.attach(packet(),bad,THROUGH)
    assert m.attach(packet(),{'a':DEFINITIONS['a']},THROUGH)[1]=='missing_exact_band_geometry'


def test_pending_labels_and_unknown_rules_are_never_fabricated():
    p=packet();p['fact']=None;p['contract']=None
    r,why=m.attach(p,DEFINITIONS,THROUGH)
    assert why=='pending_label' and r['label'] is None and r['contract']=='unknown'
    with pytest.raises(ValueError,match='pending'):m.scores([r],[r['incumbent']])


def test_the_other_live_model_path_is_not_misdiagnosed_as_a_broken_forecast():
    p=packet();p['call'].update(model_path='wxpredict',forecast_issued_at=None)
    assert m.attach(p,DEFINITIONS,THROUGH)==(None,'different_wxpredict_serving_path')


def test_label_and_market_cannot_enter_prediction_features():
    r=row();before=m.vector(r)
    r['label']['winner']='c'
    assert m.vector(r)==before
    r['market']={'malicious':'not a predictor'};rehash(r)
    r['city']='different-id';rehash(r)
    assert m.vector(r)==before
    r['centre_c']+=1
    with pytest.raises(ValueError,match='integrity'):m.vector(r)


def test_first_call_selection_precedes_evidence_exclusion():
    a=packet();a['call']['frozen_at']=None
    b=copy.deepcopy(a);b['call'].update(checkpoint_id='retry',decided_at='2026-08-01T09:38:00Z',frozen_at='2026-08-01T09:38:00Z')
    records,coverage=study.prepare([b,a],DEFINITIONS,THROUGH)
    assert not records and coverage['first_calls']==1
    assert coverage['excluded']=={'missing_server_freeze':1}
    bad=copy.deepcopy(a);bad['call']['centre_c']=25
    with pytest.raises(ValueError,match='conflicting'):study.prepare([a,bad],DEFINITIONS,THROUGH)


def test_complete_captured_market_only_and_probability_scoring():
    r=row();p=m.market(r)
    assert sum(p.values())==pytest.approx(1.)
    assert m.scores([r],[r['incumbent']])['hits']==1
    r['market']['a']['ask']=.01;rehash(r)
    assert m.market(r) is None
    with pytest.raises(ValueError,match='alignment'):m.scores([r],[])
    with pytest.raises(ValueError,match='alignment'):study.compare([r],[])


def test_original_tied_favourite_is_preserved_in_baseline_scoring():
    r=row();r['incumbent']={'a':.4,'b':.4,'c':.2};r['incumbent_top']='b';rehash(r)
    scores=study.compare([r],[r['incumbent']])
    assert scores['incumbent']['hits']==1 and scores['candidate']['hits']==0


def test_date_bootstrap_is_paired_and_retains_day_groups():
    records=[row(day='2026-08-01'),row(day='2026-08-02')]
    p=[r['incumbent'] for r in records]
    r=study.paired_date_interval(records,p,50)
    assert r['dates']==2 and r['hit_delta_90']==[0.,0.] and r['log_loss_delta_90']==[0.,0.]


def test_private_output_cannot_overwrite_or_target_repository(tmp_path):
    kwargs=dict(packets=[],bands=DEFINITIONS,through=THROUGH,fit_last='2026-08-01',eval_first='2026-08-02',eval_last='2026-08-03')
    with pytest.raises(ValueError,match='private output'):study.run(out=tmp_path,**kwargs)
    with pytest.raises(ValueError,match='private output'):study.run(out=Path(m.__file__).parents[2]/'private',**kwargs)


def test_one_shared_fit_is_bounded_hashed_and_cannot_backdate_or_activate(tmp_path):
    pytest.importorskip('numpy');pytest.importorskip('scipy')
    records=[row(day=f'2026-08-{day:02d}',city=f'synthetic-{city}') for day in range(1,9) for city in range(25)]
    cutoff='2026-08-12T00:00:00Z'
    artifact=m.fit(records,cutoff)
    assert artifact['changes_serving'] is False and artifact['eligible_for_promotion'] is False
    assert artifact['training']['cities']==25 and artifact['training']['dates']==8
    test=[row(day='2026-08-15')];p=m.predict(artifact,test)[0]
    assert sum(p.values())==pytest.approx(1.)
    original=test[0]['incumbent']
    for a in p:
        for b in p:assert (p[a]/p[b])/(original[a]/original[b])<=math.exp(2*m.MAX_LOGIT_SHIFT)+1e-9
    test[0]['label']['winner']='c'
    assert m.predict(artifact,test)[0]==p
    with pytest.raises(ValueError,match='overlaps'):m.predict(artifact,[records[0]])
    records[0]['label']['available_at']=cutoff
    with pytest.raises(ValueError,match='unavailable'):m.fit(records,cutoff)
    damaged=copy.deepcopy(artifact);damaged['coefficients'][0]+=.01
    with pytest.raises(ValueError,match='identity'):m.predict(damaged,test)
    test[0]['incumbent']={'a':0.,'b':.6,'c':.4};rehash(test[0])
    assert m.predict(artifact,test)[0]['a']==0., 'structural zero probabilities remain zero'
