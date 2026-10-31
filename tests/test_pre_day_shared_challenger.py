"""Original receipt timing, immutable pairs and a shared research-only fit."""
import copy
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import pre_day_pairs as pp, pre_day_challenger as pc

UTC = dt.timezone.utc


def fixture(day='2026-08-01', city='synthetic', timezone='UTC'):
    from zoneinfo import ZoneInfo
    d = dt.date.fromisoformat(day)
    scheduled = dt.datetime.combine(d-dt.timedelta(days=1), dt.time(18), ZoneInfo(timezone))
    decided = scheduled+dt.timedelta(minutes=37)
    end = dt.datetime.combine(d+dt.timedelta(days=1), dt.time(), ZoneInfo(timezone))
    probs, raw = {'a':.2,'b':.8}, {'a':.7,'b':.3}
    packet = dict(policy='early-raw-centre-comparison-v1',raw_centre_c=20.,corrected_centre_c=21.,
                  sigma_c=1.,unit='C',raw_centre_probs=raw,corrected_probs=probs,
                  input_forecast_run=(scheduled-dt.timedelta(hours=6)).isoformat(),forecast_version='synthetic')
    call = dict(checkpoint_id=city+day,city_key=city,target_date=day,checkpoint='d1_eve',
                decided_at=decided.isoformat(),frozen_at=decided.isoformat(),
                local_decision_time=scheduled.replace(tzinfo=None).isoformat(),
                inputs_ok='True',predictive_context={'early_forecast_audit':packet},
                probs=probs,top_band_id='b',centre_c=21.,sigma_c=1.,station='TEST',
                market={'a':{'bid':.45,'ask':.55},'b':{'bid':.45,'ask':.55}})
    fact = dict(checkpoint_id=call['checkpoint_id'],city_key=city,target_date=day,winner_band_id='a',
                settled_at=(end+dt.timedelta(minutes=5)).isoformat(),
                banked_at=(end+dt.timedelta(minutes=6)).isoformat())
    return call, fact, end+dt.timedelta(days=2)


def record(day='2026-08-01', city='synthetic'):
    p, f, now = fixture(day, city)
    r, reason = pp.attach(p,f,'UTC',now)
    assert reason is None
    return r


def test_original_schedule_is_not_the_actual_receipt_and_city_clock_is_used():
    for zone in ('UTC', 'Asia/Riyadh', 'America/New_York', 'Pacific/Auckland'):
        p,f,now=fixture('2026-11-02',timezone=zone)
        before=copy.deepcopy((p,f))
        r,reason=pp.attach(p,f,zone,now)
        assert reason is None and r['label']['winner']=='a'
        assert r['checkpoint']=='d1_eve' and r['frozen_at']!=p['local_decision_time']
        pp.validate_record(r)
        assert (p,f)==before, 'no original prediction or settlement is rewritten'


@pytest.mark.parametrize('change,reason', [
    ({'frozen_at':None}, 'missing_server_freeze'),
    ({'local_decision_time':'2026-07-31T19:00:00'}, 'local_schedule_mismatch'),
    ({'frozen_at':'2026-08-01T00:00:00Z'}, 'not_on_time_pre_day_freeze'),
    ({'frozen_at':'2026-07-31T19:16:00Z'}, 'not_on_time_pre_day_freeze'),
    ({'inputs_ok':'False'}, 'original_inputs_not_ok'),
    ({'predictive_context':''}, 'missing_original_pair'),
    ({'checkpoint':'noon'}, 'not_evening_before'),
])
def test_ordinary_evidence_gaps_have_explicit_denominators(change, reason):
    p,f,now=fixture();p.update(change)
    assert pp.attach(p,f,'UTC',now)==(None,reason)


def test_corrupt_pairs_and_labels_are_not_silently_accepted():
    p,f,now=fixture()
    for field,value in [('sigma_c',2),('centre_c',22),('probs',{'a':.9,'b':.1}),('top_band_id','a')]:
        bad=copy.deepcopy(p);bad[field]=value
        with pytest.raises(ValueError): pp.attach(bad,f,'UTC',now)
    f['checkpoint_id']='different'
    with pytest.raises(ValueError,match='different'):pp.attach(p,f,'UTC',now)
    with pytest.raises(ValueError,match='timezone'):pp.timestamp('2026-08-01T00:00:00')


def test_pending_labels_are_never_scored_or_used_as_training_misses():
    p,f,now=fixture()
    r,reason=pp.attach(p,None,'UTC',now)
    assert r and r['label'] is None and reason=='pending_label'
    r,reason=pp.attach(p,f,'UTC',pp.timestamp('2026-08-02T00:05:30Z'))
    assert r['label'] is None and reason=='label_not_available_by_cutoff'
    f['winner_band_id']='outside'
    assert pp.attach(p,f,'UTC',now)==(None,'winner_outside_frozen_ladder')


@pytest.mark.parametrize('strength', [1.0, .5, 0.0])
def test_new_strength_packets_keep_their_actual_served_ladder(strength):
    import early_city_adaptation as early
    p,f,now=fixture();packet=p['predictive_context']['early_forecast_audit']
    raw,corrected=packet['raw_centre_probs'],packet['corrected_probs']
    candidates={str(b):{k:(1-b)*raw[k]+b*corrected[k] for k in raw} for b in early.GRID}
    packet.update(policy=early.POLICY,contract='legacy',station='TEST',recipe='synthetic',checkpoint='d1_eve',
                  timezone='UTC',strength=strength,candidates=candidates,served_probs=candidates[str(strength)],
                  raw_centre_c=20.012345,corrected_centre_c=21.123456,sigma_c=1.234567,
                  fitted_as_of='2026-07-31')
    packet['model_version']=early.family(packet['contract'],packet['station'],packet['unit'],packet['recipe'])
    p.update(probs=candidates[str(strength)],
             centre_c=round(20.012345+strength*(21.123456-20.012345),4),sigma_c=1.2346,
             top_band_id='a' if strength==0 else 'b')
    r,reason=pp.attach(p,f,'UTC',now)
    assert reason is None and r['incumbent']==candidates[str(strength)]
    for field in ('centre_c','sigma_c'):
        bad=copy.deepcopy(p);bad[field]+=.0001
        with pytest.raises(ValueError,match='temperature context'):pp.attach(bad,f,'UTC',now)
    packet['rules_received_at']='2026-08-01T00:00:00Z'
    assert pp.attach(p,f,'UTC',now)==(None,'rules_after_freeze')
    p,f,now=fixture();p['predictive_context']['early_forecast_audit']={'policy':'unknown'}
    assert pp.attach(p,f,'UTC',now)==(None,'unknown_pair_policy')


def test_chronological_dates_still_purge_late_training_labels():
    train=record('2026-08-01')
    early=record('2026-08-02')
    # Aug 1 labels arrive Aug 2 00:06, after the Aug 2 evening-before call on Aug 1.
    older,test,meta=pc.split([train,early],'2026-08-01','2026-08-02','2026-08-02')
    assert older==[] and test==[early]
    assert meta['training_labels_unavailable_at_first_evaluation']==1
    with pytest.raises(ValueError,match='overlap'):
        pc.split([train,early],'2026-08-02','2026-08-02','2026-08-02')
    with pytest.raises(ValueError,match='duplicate'):
        pc.split([train,train,early],'2026-08-01','2026-08-02','2026-08-02')


def test_prediction_features_have_no_labels_market_or_city_model_lookup():
    r=record();other=copy.deepcopy(r)
    other.update(city='another',label={'winner':'b'},market={'a':.99,'b':.01})
    assert pc.features(r)==pc.features(other)
    assert pc.predict_parameters(pc.PRIOR,r)==pc.predict_parameters(pc.PRIOR,other)
    assert 0 <= pc.weight(pc.PRIOR,r) <= .5
    bad=copy.deepcopy(r);bad['centre_c']+=1
    with pytest.raises(ValueError,match='hash'):pp.validate_record(bad)
    with pytest.raises(ValueError,match='bounds'):pc.weight([999]*4,r)


def test_fitted_candidate_is_shared_bounded_and_cannot_time_travel(monkeypatch):
    np=pytest.importorskip('numpy');pytest.importorskip('scipy')
    rows=[record(f'2026-08-{d:02}',f'city{i}') for d in range(1,6) for i in range(20)]
    monkeypatch.setattr(pc,'utc_now',lambda:pp.timestamp('2026-08-08T00:00:00Z'))
    model=pc.fit(rows)
    assert model['changes_serving'] is False and model['eligible_for_promotion'] is False
    assert model['optimizer']['converged']
    assert pc.weight(model['parameters'],rows[0])>pc.weight(pc.PRIOR,rows[0])
    q=pc.predict(model,rows[0])
    assert sum(q.values())==pytest.approx(1) and set(q)==set(rows[0]['incumbent'])
    assert pc.metrics(rows,model)['candidate']['log_loss']<pc.metrics(rows,model)['incumbent']['log_loss']
    with pytest.raises(ValueError,match='not available'):pc.predict(model,rows[0],forward=True)
    future=record('2026-08-10')
    monkeypatch.setattr(pc,'utc_now',lambda:pp.timestamp('2026-08-09T18:45:00Z'))
    assert pc.predict(model,future,forward=True)==pc.predict(model,future)
    monkeypatch.setattr(pc,'utc_now',lambda:pp.timestamp('2026-08-11T00:00:00Z'))
    with pytest.raises(ValueError,match='late'):pc.predict(model,future,forward=True)
    bad=copy.deepcopy(model);bad['parameters'][0]+=1
    with pytest.raises(ValueError,match='hash'):pc.predict(bad,future)
    changed=copy.deepcopy(rows)
    for r in changed:
        r['market']={'a':.01,'b':.99}
        r['input_sha256']=pp.digest({k:v for k,v in r.items() if k not in ('input_sha256','label')})
    other=pc.fit(changed)
    np.testing.assert_allclose(model['parameters'],other['parameters'],rtol=0,atol=0)
    changed[0]['market']=None
    changed[0]['input_sha256']=pp.digest({k:v for k,v in changed[0].items() if k not in ('input_sha256','label')})
    m=pc.metrics(changed,model)
    assert m['candidate']['n']==100 and m['market']['n']==99
    assert m['matched_market']['n']==99
    assert {m['matched_market'][name]['n'] for name in ('candidate','incumbent','market')}=={99}
    with pytest.raises(ValueError,match='coverage'):pc.fit(rows[:20])
    changed=copy.deepcopy(rows);changed[0]['label']=None
    with pytest.raises(ValueError,match='banked'):pc.fit(changed)


def mirror(root, table, rows, *, through='2026-08-10T00:00:00Z'):
    folder=root/'data/mirror'/table;folder.mkdir(parents=True,exist_ok=True)
    text=io.StringIO();writer=csv.DictWriter(text,fieldnames=list(rows[0]));writer.writeheader()
    writer.writerows({k:json.dumps(v) if isinstance(v,dict) else v for k,v in r.items()} for r in rows)
    blob=gzip.compress(text.getvalue().encode());(folder/'fixture.csv.gz').write_bytes(blob)
    return dict(through=through,files=[dict(file='fixture.csv.gz',sha256=hashlib.sha256(blob).hexdigest(),
                 rows=len(rows),**{'from':'2026-07-01T00:00:00Z','to':through})])


def test_real_verified_mirror_preserves_the_first_call_and_rejects_tampering(tmp_path):
    p,f,_=fixture()
    original=copy.deepcopy(p);original['frozen_at']=''
    later=copy.deepcopy(p);later.update(checkpoint_id='later',decided_at='2026-07-31T18:50:00Z',frozen_at='2026-07-31T18:50:00Z')
    state={table:mirror(tmp_path,table,rows) for table,rows in
           [('prediction_checkpoints',[original,later]),('fact_checkpoint_outcome',[f])]}
    folder=tmp_path/'data/mirror'
    (folder/'manifest.json').write_text(json.dumps({'tables':state}))
    cities=folder/'cities';cities.mkdir()
    with gzip.open(cities/'cities-fixture.csv.gz','wt') as fh:fh.write('city_key,timezone\nsynthetic,UTC\n')
    rows,coverage=pp.load('2026-08-01','2026-08-01','2026-08-10T00:00:00Z',root=tmp_path)
    assert rows==[] and coverage['first_calls']==1
    assert coverage['exclusions']=={'missing_server_freeze':1},'never replace the first miss/gap with a later call'
    (folder/'prediction_checkpoints/fixture.csv.gz').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='hash'):
        pp.load('2026-08-01','2026-08-01','2026-08-10T00:00:00Z',root=tmp_path)
