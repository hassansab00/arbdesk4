"""Horizon mismatch, input causality and evidence-subset regressions."""
import copy
import datetime as dt
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import forecast_uncertainty as u
import probability_engine as pe
import predictive_validation as validation
import wx_city_calibration as cal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import candidate_cycle as cycle, candidate_forward as forward


def test_same_day_prior_widens_only_from_qualified_advance_errors():
    evidence=dict(lead_days=1,n_days=30,mae_c=1,verified=True,computed_at='2026-10-01T05:00:00Z')
    original=copy.deepcopy(evidence)
    sigma,packet=u.guard(.4,0,evidence,pe.MAE_TO_SIGMA)
    assert sigma==pe.MAE_TO_SIGMA and packet['applied']
    assert u.guard(2,0,evidence,pe.MAE_TO_SIGMA)[0]==2
    assert u.guard(.4,1,evidence,pe.MAE_TO_SIGMA)[0]==.4
    for bad in [dict(evidence,verified=False),dict(evidence,lead_days=0),dict(evidence,n_days=9),
                dict(evidence,mae_c=float('nan')),dict(evidence,mae_c=-1),None]:
        assert u.guard(.4,0,bad,pe.MAE_TO_SIGMA)[0]==.4
    assert evidence==original


def test_engine_applies_prior_without_borrowing_the_advance_bias_or_moving_the_centre(monkeypatch):
    monkeypatch.setattr(pe,'_forecast_for',lambda *a:dict(lead_days=0,forecast_max_c=25,model='public',run_at='2026-10-01T08:00:00Z'))
    monkeypatch.setattr(pe,'_skill_for',lambda city,lead,*a:dict(lead_days=lead,n_days=30,mae_c=.3 if lead==0 else 1,
                       bias_c=.1 if lead==0 else 10,verified=True))
    monkeypatch.setattr(pe.regime,'classify',lambda *a:SimpleNamespace(reasons=[],confidence=1,sigma_multiplier=1,label='NORMAL'))
    monkeypatch.setattr(pe,'_postprocess_for',lambda *a:None)
    monkeypatch.setattr(pe,'_station_corrected_for',lambda *a:None)
    monkeypatch.setattr(pe,'_trajectory_for',lambda *a:(None,None,None))
    monkeypatch.setattr(pe,'_divergence_for',lambda *a:(1,None))
    monkeypatch.setattr(pe,'_calibration_for',lambda *a:(1,None))
    monkeypatch.setattr(pe,'_calibration_map',lambda:None)
    monkeypatch.setattr(pe,'model_version_id',lambda *a,**kw:None)
    bands=[dict(band_id='lo',band_lo=None,band_hi=25,open_low=True,open_high=False),
           dict(band_id='hi',band_lo=25,band_hi=None,open_low=False,open_high=True)]
    rows,_,reasons=pe.process_city_day('synthetic','2026-10-01','C',bands,{},use_primary=False)
    assert any(r.startswith('priced_from:') and u.POLICY in r for r in reasons)
    assert all(r['centre_c']==24.9 and r['sigma_c']==pytest.approx(pe.MAE_TO_SIGMA,abs=1e-4) for r in rows)
    assert sum(r['calibrated_prob'] for r in rows)==pytest.approx(1,abs=1e-5)
    packet=pe.forecast_context('synthetic','2026-10-01',rows)
    assert packet['uncertainty']['applied'] and packet['source_model']=='public'
    packet['source_model']='mutated'
    assert pe.forecast_context('synthetic','2026-10-01',rows)['source_model']=='public'
    assert pe.forecast_context('synthetic','2026-10-01',[dict(rows[0],computed_at='other')]) is None


def test_future_station_readings_are_not_fresh():
    now=dt.datetime(2026,10,1,12,tzinfo=dt.timezone.utc)
    assert pe._fresh('2026-10-01T11:59:00Z',now)
    assert not pe._fresh('2026-10-01T12:01:00Z',now)
    assert not pe._fresh('2026-10-01T12:00:00',now)


def test_calibration_readiness_cannot_approve_a_thin_contract():
    r=cal.readiness(0,4,pool_rows=0,pool_dates=0)
    assert not r['sample_ready']
    assert r['counts'][0]['missing']==cal.PRIORS['min_train_dates']
    assert cal.readiness(20,10,pool_rows=200,pool_dates=20)['sample_ready']


def test_real_lead_subsets_and_market_pairs_do_not_count_unknown_or_missing_as_losses():
    def row(minutes,hit=1,market=True):
        return dict(target_date='2026-10-01',checkpoint='noon',version='test',settled=True,verified=True,
            top_prob=.6,metrics=dict(hit=hit,log_loss=.5,brier=.3),
            market_metrics=dict(hit=0,log_loss=1,brier=.6) if market else None,
            observed_timing=dict(relation='before',minutes_before_peak=minutes),baseline=None,raw=None)
    rows=[row(30),row(60),row(120,0,False)]
    original=copy.deepcopy(rows)
    result=validation.observed_peak_report(rows,'noon')
    assert result['before_60m']['n']==2 and result['before_60m']['hits']==1
    assert result['before_120m']['n']==1 and result['before_120m']['hits']==0
    assert result['before_60m']['market_comparison']['n']==1
    assert result['before_60m']['market_comparison']['model']['hit']==1
    assert result['before_120m']['market_comparison']['n']==0
    assert rows==original


def test_refit_cooldown_counts_fits_but_not_waiting_nights():
    history=[dict(action='cycle',night='2026-10-01',fitted=True),dict(action='cycle',night='2026-10-07',fitted=False)]
    assert not cycle.refit_due(history,'2026-10-07')
    assert cycle.refit_due(history,'2026-10-08')


def test_local_brier_regression_cannot_hide_behind_pooled_hits():
    rows=[dict(city='synthetic',date=f'2026-10-{i:02}',checkpoint='noon',candidate_nll=.4,incumbent_nll=.5,
               candidate_brier=.3,incumbent_brier=.2,candidate_hit=True,incumbent_hit=False) for i in range(1,11)]
    result=cycle.probability_quality(rows)
    assert result['quality_reasons'] and result['city_horizons'][0]['regressed']


def test_forward_scores_require_the_correct_fixed_model_and_a_causal_server_receipt():
    p=dict(checkpoint_id='call',city_key='synthetic',target_date='2026-10-01',checkpoint='noon',station='SYN',
           decided_at='2026-10-01T12:37:00Z',frozen_at='2026-10-01T12:37:00Z',probs={'a':.4,'b':.6},
           market={'a':{'bid':.3,'ask':.5},'b':{'bid':.5,'ask':.7}})
    o=dict(checkpoint_id='call',winner_band_id='b',settled_at='2026-10-02T01:00:00Z',banked_at='2026-10-02T02:00:00Z')
    s=dict(city_key='synthetic',target_date='2026-10-01',checkpoint='noon',variant='wxrd_auto',
           variant_version='fixed',station='SYN',decided_at=p['decided_at'],frozen_at='2026-10-01T12:38:00Z',probs={'a':.35,'b':.65})
    def collect(shadow):
        return forward.collect([p],[o],[shadow],{'synthetic':'UTC'},'fixed','2026-09-28T12:00:00Z',
                               '2026-10-01','2026-10-01',dt.datetime(2026,10,3,tzinfo=dt.timezone.utc).timestamp())
    assert len(collect(s)[0])==1
    for bad in [dict(s,variant_version='other'),dict(s,frozen_at=None),dict(s,frozen_at='2026-10-02T02:00:00Z'),
                dict(s,frozen_at='2026-10-01T12:36:00Z'),dict(s,probs={'a':1}),
                dict(s,station='OTHER'),dict(s,decided_at='2026-10-01T12:36:00Z')]:
        scores,n=collect(bad)
        assert not scores and n==1


def test_forward_cycle_reviews_the_registered_candidate_not_the_serving_incumbent(monkeypatch):
    incumbent=dict(fit_last='2026-09-01',model_sha256='incumbent')
    candidate=dict(fit_last='2026-10-01',model_sha256='candidate')
    state=dict(paused=False,history=[],current=dict(available_at='2026-10-02T12:00:00Z'))
    monkeypatch.setattr(cycle,'incumbent',lambda:('incumbent.json',incumbent))
    monkeypatch.setattr(cycle.registry,'registry',lambda:state)
    monkeypatch.setattr(cycle.registry,'selected',lambda:('candidate.json',candidate))
    seen=[]
    monkeypatch.setattr(forward,'review',lambda meta,available,night:
                        seen.append(meta) or dict(night=night,decision='wait forward'))
    monkeypatch.setattr(cycle.registry,'record',lambda report:None)
    assert cycle.main(['--plan'])==0
    assert seen==[candidate]
