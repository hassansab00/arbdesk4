"""Synthetic evidence, temporal leakage and weather-mixture checks."""
import copy
import csv
import datetime as dt
import gzip
import hashlib
import io
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import pre_day_weather_data as d, pre_day_weather_model as m, pre_day_weather_study as s
from wxpredict import pre_day_pairs as pp


def fixture(day='2026-08-01',city='synthetic',zone='UTC',unit='C'):
    date=dt.date.fromisoformat(day)
    at=dt.datetime.combine(date-dt.timedelta(days=1),dt.time(18,37),ZoneInfo(zone))
    run=at-dt.timedelta(hours=6)
    rows=[dict(city_key=city,for_date=day,model=model,run_at=run.isoformat(),
               observed_at=(run+dt.timedelta(minutes=1)).isoformat(),forecast_max_c=str(20+i/10),
               source=d.SOURCE,lead_days='1') for i,model in enumerate(d.MODELS)]
    event=dict(city=city,date=day,station='TEST',unit=unit,band_ids=['low','mid','high'],
               bands=[[None,20],[20,22],[22,None]])
    probs={'low':.1,'mid':.8,'high':.1}
    call=dict(checkpoint_id=city+day,station='TEST',decided_at=at.isoformat(),frozen_at=at.isoformat(),
              local_decision_time=at.replace(hour=18,minute=0,tzinfo=None).isoformat(),inputs_ok=True,
              probs=probs,top_band_id='mid',market={k:{'bid':v,'ask':v} for k,v in probs.items()})
    end=dt.datetime.combine(date+dt.timedelta(days=1),dt.time(),ZoneInfo(zone))
    label=dict(winner='mid',settled_at=(end+dt.timedelta(minutes=1)).isoformat(),
               banked_at=(end+dt.timedelta(minutes=2)).isoformat(),available_at=(end+dt.timedelta(minutes=2)).isoformat())
    return rows,event,call,label,dict(timezone=zone,latitude=30),run.isoformat()


def record(day='2026-08-01',city='synthetic',zone='UTC'):
    rows,e,c,l,info,created=fixture(day,city,zone)
    r,reason=d.build_record(e,info,c,l,d.Forecasts(rows),created)
    assert reason is None
    return r


@pytest.mark.parametrize('zone',['UTC','Asia/Riyadh','America/New_York','Pacific/Auckland'])
def test_actual_local_pre_day_receipts_and_originals_are_immutable(zone):
    rows,e,c,l,info,created=fixture(zone=zone)
    before=copy.deepcopy((rows,e,c,l,info))
    r,reason=d.build_record(e,info,c,l,d.Forecasts(rows),created)
    assert reason is None and r['original'] and r['features']['as_of']==c['decided_at']
    assert r['features']['as_of']!=c['local_decision_time']
    assert (rows,e,c,l,info)==before


def test_forecast_selection_requires_actual_receipt_not_historical_run_claim():
    rows,e,c,*_=fixture()
    later=copy.deepcopy(rows[0]);later['run_at']='2026-07-31T18:36:00Z'
    later['observed_at']='2026-07-31T18:38:00Z';later['forecast_max_c']='55'
    historic=copy.deepcopy(rows[1]);historic['source']='open-meteo-previous-runs';historic['forecast_max_c']='60'
    f=d.Forecasts(rows+[later,historic])
    assert f.select(e['city'],e['date'],c['decided_at'])[d.MODELS[0]]['value']==20
    assert f.select(e['city'],e['date'],c['decided_at'])[d.MODELS[1]]['value']==20.1
    assert not f.select(e['city'],e['date'],'2026-07-31T10:00:00Z')
    assert not f.select(e['city'],e['date'],'2026-08-02T10:00:00Z')


def test_conflicting_same_run_is_quarantined_without_erasing_other_providers():
    rows,e,c,*_=fixture();bad=copy.deepcopy(rows[0]);bad['forecast_max_c']='25'
    f=d.Forecasts(rows+[bad])
    assert f.conflicts==1 and len(f.select(e['city'],e['date'],c['decided_at']))==6
    duplicate=copy.deepcopy(rows[1]);duplicate['observed_at']='2026-07-31T18:38:00Z'
    f=d.Forecasts(rows+[duplicate])
    assert d.MODELS[1] not in f.select(e['city'],e['date'],c['decided_at'])


def test_missing_providers_and_known_station_mismatch_are_explicit_exclusions():
    rows,e,c,l,info,created=fixture()
    assert d.build_record(e,info,c,l,d.Forecasts(rows[:3]),created)[1]=='missing_timely_current_forecasts'
    c['station']='OTHER'
    assert d.build_record(e,info,c,l,d.Forecasts(rows),created)[1]=='original_station_contract_mismatch'


def test_no_freeze_proof_is_training_input_replay_never_a_comparison():
    rows,e,c,l,info,created=fixture();c['frozen_at']=None
    r,reason=d.build_record(e,info,c,l,d.Forecasts(rows),created)
    assert reason is None and r['evidence']=='received_weather_input_replay'
    assert r['original'] is None and r['incumbent'] is None and r['market'] is None
    assert pp.timestamp(r['features']['as_of']).hour==18
    with pytest.raises(ValueError,match='replays'):s.comparison([r],[{'probs':{'low':.2,'mid':.6,'high':.2}}])


@pytest.mark.parametrize('field,value,message',[
    ('as_of','2026-08-01T00:00:00Z','pre-day'),('unit','K','contract'),
])
def test_feature_identity_and_pre_day_guards(field,value,message):
    r=record();r['features'][field]=value
    with pytest.raises(ValueError,match='hash'):d.validate(r)
    r['feature_sha256']=pp.digest(r['features'])
    with pytest.raises(ValueError,match=message):d.validate(r)


def test_lead_label_and_bucket_integrity():
    r=record();r['features']['providers'][d.MODELS[0]]['lead_days']=2
    r['feature_sha256']=pp.digest(r['features'])
    with pytest.raises(ValueError,match='lead'):d.validate(r)
    r=record();r['bounds'][1][0]=21
    with pytest.raises(ValueError,match='geometry'):d.validate(r)
    r=record();r['label']['available_at']=r['features']['as_of']
    with pytest.raises(ValueError,match='label'):d.validate(r)


def test_mirror_hash_and_row_counts_are_verified(tmp_path):
    path=tmp_path/'data/mirror/weather_forecast_models';path.mkdir(parents=True)
    buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=['value']);writer.writeheader();writer.writerow({'value':'1'})
    blob=gzip.compress(buf.getvalue().encode());(path/'synthetic.csv.gz').write_bytes(blob)
    entry=dict(file='synthetic.csv.gz',sha256=hashlib.sha256(blob).hexdigest(),rows=1,
               to='2026-08-02T00:00:00Z',**{'from':'2026-08-01T00:00:00Z'})
    manifest=dict(tables={'weather_forecast_models':dict(through=entry['to'],files=[entry])})
    read=lambda:list(d.verified_rows(tmp_path,manifest,'weather_forecast_models',0,pp.timestamp(entry['to']).timestamp(),lambda r:True))
    assert read()==[{'value':'1'}]
    entry['rows']=2
    with pytest.raises(ValueError,match='row count'):read()
    entry['sha256']='bad'
    with pytest.raises(ValueError,match='hash'):read()


def test_training_label_arrival_is_purged_across_city_dates():
    a,b,c=record('2026-08-01'),record('2026-08-02'),record('2026-08-04')
    b['label']['available_at']='2026-08-04T00:00:00Z'
    train,test,timing=s.split([a,b,c],'2026-08-02','2026-08-04','2026-08-04')
    assert train==[a] and test==[c] and timing['purged_training_labels']==1
    with pytest.raises(ValueError,match='overlap'):s.split([a,b,c],'2026-08-04','2026-08-04','2026-08-04')


def test_weather_features_and_predictions_do_not_use_market_incumbent_or_label():
    np=pytest.importorskip('numpy');pytest.importorskip('scipy')
    a=record();b=copy.deepcopy(a)
    b['market']={'low':1.,'mid':0.,'high':0.};b['incumbent']=dict(b['market'])
    b['label']['winner']='high';b['original']['top']='low'
    za,zb=m.matrix([a],['C:TEST']),m.matrix([b],['C:TEST'])
    assert all(np.array_equal(za[k],zb[k]) for k in za)


def test_analytic_gradient_matches_finite_differences_with_missing_providers():
    np=pytest.importorskip('numpy');pytest.importorskip('scipy')
    records=[record('2026-08-01','one'),record('2026-08-02','two')]
    del records[1]['features']['providers'][d.MODELS[0]]
    records[1]['feature_sha256']=pp.digest(records[1]['features'])
    stations=['C:TEST'];z=m.matrix(records,stations,True)
    p=np.array([.1]*(len(d.MODELS)+len(m.FEATURES)+1)+[1.5,.1,.25])
    w=np.array([.5,.5]);_,g=m.loss_and_gradient(p,z,stations,w)
    for i in range(len(p)):
        hi=p.copy();lo=p.copy();hi[i]+=1e-5;lo[i]-=1e-5
        fd=(m.loss_and_gradient(hi,z,stations,w)[0]-m.loss_and_gradient(lo,z,stations,w)[0])/2e-5
        assert fd==pytest.approx(g[i],abs=1e-7)
    weights,shift,sigma,*_=m.context(p,z,stations)
    assert weights[1,0]==0 and weights.sum(1)==pytest.approx([1,1])
    assert all(abs(shift)<=m.MAX_SHIFT_C) and all(sigma<=m.MAX_SIGMA_C)


def test_native_half_up_boundaries_include_negative_fahrenheit():
    pytest.importorskip('numpy')
    r=record();r['features']['unit']='F';r['feature_sha256']=pp.digest(r['features'])
    r['bounds']=[[None,-8],[-8,-6],[-6,None]];r['label']['winner']='low'
    z=m.matrix([r],[],True)
    assert z['hi'][0]==pytest.approx(-22.5) and z['lo'][0]==float('-inf')
    r=record();r['label']['winner']='mid';z=m.matrix([r],[],True)
    assert z['lo'][0]==19.5 and z['hi'][0]==21.5


def test_sparse_station_offsets_are_held_at_zero():
    bounds=m.parameter_bounds(['C:A','F:B'],{'C:A':20,'F:B':2})
    start=len(d.MODELS)+len(m.FEATURES)
    assert bounds[start]==(-3,3) and bounds[start+1]==(0,0)


def test_fit_artifact_is_research_only_and_forward_backdating_is_rejected(monkeypatch):
    pytest.importorskip('numpy');pytest.importorskip('scipy')
    records=[record((dt.date(2026,8,1)+dt.timedelta(days=i)).isoformat(),'synthetic'+str(j)) for i in range(20) for j in range(20)]
    candidate=m.fit(records)
    assert candidate['available_at']>=candidate['fit_started_at']
    assert candidate['changes_serving'] is False and candidate['eligible_for_promotion'] is False
    p=m.predictions(candidate,[records[0]])[0]
    assert sum(p['probs'].values())==pytest.approx(1)
    changed=copy.deepcopy(records[0]);changed['label']['winner']='high'
    changed['incumbent']={'low':1.,'mid':0.,'high':0.};changed['market']=None
    assert m.predictions(candidate,[changed])[0]==p
    b=copy.deepcopy(records[0]);b['features']['station']='UNSEEN';b['feature_sha256']=pp.digest(b['features'])
    assert sum(m.predictions(candidate,[b])[0]['probs'].values())==pytest.approx(1)
    with pytest.raises(ValueError,match='late|availability'):m.predictions(candidate,[records[0]],forward=True)
    bad=copy.deepcopy(candidate);bad['parameters'][0]+=.1
    with pytest.raises(ValueError,match='integrity'):m.predictions(bad,[records[0]])
    bad=copy.deepcopy(candidate);bad['bounds'][0]=[-100.,100.]
    bad['sha256']=pp.digest({k:v for k,v in bad.items() if k!='sha256'})
    with pytest.raises(ValueError,match='bounds'):m.predictions(bad,[records[0]])
    bad=copy.deepcopy(candidate);bad['eligible_for_promotion']=True;bad['sha256']=pp.digest({k:v for k,v in bad.items() if k!='sha256'})
    with pytest.raises(ValueError,match='integrity'):m.predictions(bad,[records[0]])
    with pytest.raises(ValueError,match='coverage'):m.fit(records[:20])
    future_date=(m.utc_now().date()+dt.timedelta(days=2)).isoformat()
    future=record(future_date);future['label']=None
    at=pp.timestamp(future['features']['as_of'])
    monkeypatch.setattr(m,'utc_now',lambda:at+dt.timedelta(minutes=1))
    assert m.predictions(candidate,[future],forward=True)[0]['probs']
    monkeypatch.setattr(m,'utc_now',lambda:at+dt.timedelta(minutes=16))
    with pytest.raises(ValueError,match='late'):m.predictions(candidate,[future],forward=True)


def test_comparisons_use_the_same_market_cohort_and_original_tie_choice():
    a,b=record(city='one'),record(city='two');b['market']=None
    a['original']['top']='low';a['incumbent']={'low':.5,'mid':.5,'high':0.}
    out=s.comparison([a,b],[{'probs':a['incumbent']},{'probs':b['incumbent']}])
    assert out['candidate']['n']==2 and out['market']['n']==1
    assert all(v['n']==1 for v in out['matched_market'].values())
    assert out['incumbent']['hits']==1
