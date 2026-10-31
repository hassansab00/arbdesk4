"""Integration regressions for bounded stages, original evidence and shadow fitting."""
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


def test_primary_database_reads_share_the_actual_request_deadline(monkeypatch):
    import common as ops
    import predictive_primary as pp
    monkeypatch.setattr(sys, 'path', list(sys.path))
    sys.path.insert(0, str(ROOT/'tools'))
    from wxpredict import common, wxrd_live
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, 'sdyes_forward', SimpleNamespace())
    monkeypatch.setattr(common, '_OPS', None)
    assert common.ops() is ops
    monkeypatch.setenv('PREDICTIVE_PRIMARY', pp.POLICY)
    monkeypatch.setattr(pp.time, 'monotonic', lambda: 100.)
    seen = []
    def read(*args, **kwargs):
        seen.append(ops._request_kwargs({'timeout':90})['timeout'])
        raise TimeoutError('test exhausted primary input transport')
    monkeypatch.setattr(ops, 'rest_all', read)
    monkeypatch.setattr(wxrd_live, 'load_model', lambda *a: object())
    now=dt.datetime(2026,10,30,10,tzinfo=UTC)
    result=pp.prepare([dict(market_id='m',city_key='jeddah',resolution_date='2026-10-30')],{},
        [dict(city_key='jeddah',timezone='Asia/Riyadh',icao='OEJN')],now)
    assert seen and all(v <= pp.MAX_PREPARE_S/2 for v in seen)
    assert result['selected']==0 and 'TimeoutError' in result['error']
    assert ops._request_end.get() is None


def test_local_day_features_keep_direction_pressure_and_observed_units():
    import refresh_station_forecasts as refresh
    start=dt.datetime(2026,10,29,tzinfo=UTC)
    stamps=[start+dt.timedelta(hours=i) for i in range(96)]
    hourly=dict(time=[s.replace(tzinfo=None).isoformat() for s in stamps],
        temperature_2m=[30]*96,dew_point_2m=[20]*96,wind_speed_10m=[18.52]*96,
        wind_direction_10m=[90]*96,precipitation=[25.4]*96,cloud_cover=[50]*96,
        pressure_msl=[1000+(s.date()-start.date()).days for s in stamps])
    rows=refresh.forecast_features(dict(city_key='jeddah',timezone='Asia/Riyadh'),{'hourly':hourly},
                                  dt.datetime(2026,10,30,10,tzinfo=UTC))
    row=rows[0]
    assert row['for_date']=='2026-10-30' and row['n_hours']==24
    assert row['wind_mean']==pytest.approx(10) and row['wind_u_mean']==pytest.approx(-1)
    assert row['wind_v_mean']==pytest.approx(0,abs=1e-12)
    assert row['precip_total']==pytest.approx(24) and row['cloud_mean']==4
    assert row['pressure_change_24h_hpa']==1 and row['morning_humidity'] is None
    hourly['temperature_2m'][30]=None
    assert not any(r['for_date']=='2026-10-30' for r in refresh.forecast_features(
        dict(city_key='jeddah',timezone='Asia/Riyadh'),{'hourly':hourly},dt.datetime(2026,10,30,10,tzinfo=UTC)))


def test_later_receipt_never_replaces_the_first_original_for_learning():
    import predictive_validation as pv
    common=dict(city_key='city',target_date='2026-10-20',checkpoint='noon',
        raw_probs={'a':1},baseline_probs={'a':1},priced_from='wxrd3:example',
        predictive_context={'band_ids':['a'],'model_fit_through':'2026-10-11','local_hour':12,'clim_peak_hour':13})
    first=dict(common,checkpoint_id='first',decided_at='2026-10-20T12:00:00Z',frozen_at=None)
    later=dict(common,checkpoint_id='later',decided_at='2026-10-20T12:01:00Z',frozen_at='2026-10-20T12:01:01Z')
    facts=[dict(checkpoint_id=k,winner_band_id='a',settled_at='2026-10-21T00:01:00Z',banked_at='2026-10-21T00:02:00Z') for k in ('first','later')]
    rows=pv.learning_pairs([later,first],facts,[dict(city_key='city',timezone='UTC')])
    assert len(rows)==1 and rows[0]['checkpoint_id']=='first'
    diag={}
    assert not pv.cal.eligible_rows(rows,dt.date(2026,10,30),'wxrd3:example',diagnostics=diag)
    assert diag['exclusions']=={'missing_freeze_or_input_timestamp':1}


def test_stage_failure_and_timeout_are_durable_nonzero_outcomes(tmp_path, monkeypatch):
    import run_stage
    monkeypatch.setenv('RUNNER_TEMP',str(tmp_path))
    assert run_stage.run('failed',2,[sys.executable,'-c','raise SystemExit(7)'])==7
    assert json.loads((tmp_path/'ad4-stage-receipts/failed.json').read_text())['status']=='failed'
    assert run_stage.run('slow',.05,[sys.executable,'-c','import time; time.sleep(2)'])==124
    assert json.loads((tmp_path/'ad4-stage-receipts/slow.json').read_text())['status']=='timeout'


def test_bootstrap_registers_only_hash_checked_unvalidated_research(tmp_path, monkeypatch):
    monkeypatch.setattr(sys,'path',list(sys.path));sys.path.insert(0,str(ROOT/'tools'))
    from wxpredict import bootstrap_cycle as boot, model_artifacts, cycle_registry as registry
    hashes=[hashlib.sha256(str(s).encode()).hexdigest() for s in boot.cycle.SEEDS]
    sha=hashlib.sha256(''.join(hashes).encode()).hexdigest()
    report=dict(boot.plan(dt.date(2026,10,31),{},dict(fit_last='2026-10-11')),
        decision='awaiting forward validation',training_rows=boot.cycle.MIN_TRAIN_ROWS,training_dates=20,
        training_last='2026-10-29',candidate_model_sha256=sha)
    meta=dict(version='wxrd-auto-20261031-'+sha[:12],fit_last=report['fit_last'],model_sha256=sha,
        rows=report['training_rows'],seeds=list(boot.cycle.SEEDS),rounds=boot.cycle.ROUNDS,report_lag_s=480,
        evaluation_report='data/eval/wxpredict/cycles/bootstrap_2026-10-31.json',
        models=[dict(seed=seed,sha256=h,asset_url='checked') for seed,h in zip(boot.cycle.SEEDS,hashes)])
    checked=[]
    monkeypatch.setattr(model_artifacts,'ensure',lambda entry,root: checked.append(entry))
    boot.register(report,meta,str(tmp_path))
    state=registry.registry(str(tmp_path))
    assert len(checked)==5 and state['current']['variant']=='wxrd_auto'
    assert not state.get('forward_validated') and registry.selected(str(tmp_path))[1]==meta
    assert not (tmp_path/'data/models/wxrd/wxrd-v3_remaining.json').exists()
    with pytest.raises(ValueError,match='existing challenger'):
        boot.register(report,meta,str(tmp_path))
    assert boot.plan(dt.date(2026,11,1),state,dict(fit_last='2026-10-11'))['decision']=='wait'


def test_bootstrap_performs_real_five_seed_fit_without_claiming_accuracy(tmp_path,monkeypatch):
    pytest.importorskip('lightgbm')
    monkeypatch.setattr(sys,'path',list(sys.path));sys.path.insert(0,str(ROOT/'tools'))
    import pandas as pd
    from wxpredict import bootstrap_cycle as boot,features,build_table
    rows=[]
    for i in range(80):
        day=str(dt.date(2026,10,1)+dt.timedelta(days=i%20))
        row=dict.fromkeys(features.COLUMNS,0)
        row.update(date=day,unit='C',label_unit=20,rmax_unit=18,rmax_c=18,day_offset=0,local_hour=12,
            event_id=str(i),city_key='synthetic',source='venue',station='SYN',n_bands=2,
            bands_lo=';20',bands_hi='20;',mkt_prices='0.4;0.6',mkt_ages_min='1;1',
            fc_daily_source='synthetic',decision_local=day+'T12:00:00+00:00')
        rows.append(row)
    pd.DataFrame(rows).to_csv(tmp_path/'wxpredict_table.csv.gz',index=False,compression='gzip')
    monkeypatch.setattr(build_table,'build',lambda **kw:dict(sha256='synthetic',sources=[]))
    monkeypatch.setattr(boot.cycle,'MIN_TRAIN_ROWS',40)
    monkeypatch.setattr(boot.cycle,'ROUNDS',2)
    report,meta,paths=boot.fit(boot.plan(dt.date(2026,10,31),{},dict(fit_last='2026-10-11')),tmp_path)
    assert len(paths)==len(meta['models'])==5 and report['training_dates']==20
    assert not report['validation_passed'] and not report['changes_serving']
    assert not any(k in report for k in ('accuracy','hit_rate','validation'))
    for path,entry in zip(paths,meta['models']):
        assert hashlib.sha256(gzip.decompress(Path(path).read_bytes())).hexdigest()==entry['sha256']


def test_a_mismatched_contract_is_reported_without_repairing_its_geometry(tmp_path,monkeypatch):
    import csv
    monkeypatch.setattr(sys,'path',list(sys.path));sys.path.insert(0,str(ROOT/'tools'))
    from wxpredict import arrival_dataset as arrival,build_table,parity,live_reader
    event=dict(event_id='historical',city='city',date='2026-10-20',bands=[(None,20),(20,None)],station='SYN',unit='C')
    live=dict(event,event_id='live',band_ids=['a','b'],bands=[(None,21),(21,None)])
    table=tmp_path/'table.csv.gz'
    with gzip.open(table,'wt') as out:
        writer=csv.DictWriter(out,fieldnames=list(dict.fromkeys(['source','date','event_id',*parity.LABELS])))
        writer.writeheader()
        writer.writerow(dict(source='venue',date=event['date'],event_id=event['event_id']))
    monkeypatch.setattr(arrival.common,'cities',lambda:{})
    monkeypatch.setattr(arrival.common,'nightly_reports',lambda:[])
    monkeypatch.setattr(arrival.common,'market_rows',lambda *a:[])
    monkeypatch.setattr(build_table,'report_stations',lambda *a:{})
    monkeypatch.setattr(build_table,'load_events',lambda *a:([event],None,None))
    monkeypatch.setattr(live_reader,'read_definitions',lambda:([],[]))
    monkeypatch.setattr(live_reader,'live_events',lambda *a:[live])
    monkeypatch.setattr(parity,'event_crosswalk',lambda *a:{'live':'historical'})
    monkeypatch.setattr(arrival,'recorded',lambda *a:([dict(checkpoint_id='call',city_key='city',target_date='2026-10-20',top_band_id='a')],{'call':{'checkpoint_id':'call'}}))
    rows,coverage=arrival.build('2026-10-20','2026-10-20',str(table),0)
    assert not rows and coverage['first_calls']==1
    assert coverage['excluded']=={'live_settled_contract_mismatch':1}
    assert coverage['contract_mismatches'][0]['fields']==['bands']
    assert event['bands']!=live['bands']


def test_probability_workers_inherit_the_actual_budget_and_keep_order(monkeypatch):
    import common,probability_engine as engine,contract_inputs,predictive_primary,early_city_adaptation
    from types import SimpleNamespace
    import threading
    monkeypatch.setattr(engine,'get_cities',lambda **kw:[dict(city_key='city',unit='C')])
    monkeypatch.setattr(engine,'_upcoming_markets',lambda:[dict(market_id='m',city_key='city',resolution_date='2026-10-31')])
    monkeypatch.setattr(engine,'_bands_for_markets',lambda *a:[dict(market_id='m',band_id='a',band_label='20')])
    monkeypatch.setattr(engine,'_observed_floors',lambda:{})
    monkeypatch.setattr(contract_inputs,'floors',lambda *a:{})
    monkeypatch.setattr(engine,'_promoted_models',lambda:{})
    monkeypatch.setattr(engine,'_model_forecasts',lambda *a:{})
    monkeypatch.setattr(engine,'_warm_caches',lambda:None)
    monkeypatch.setattr(engine,'_warm_skill_cache',lambda:None)
    monkeypatch.setattr(early_city_adaptation,'prepare',lambda *a:None)
    monkeypatch.setattr(predictive_primary,'prepare',lambda *a:{})
    main_thread=threading.get_ident(); seen=[]
    def price(*args):
        seen.append((common._request_end.get(),threading.get_ident()))
        return [dict(band_id='a',calibrated_prob=1)],SimpleNamespace(label='test',confidence=1),[]
    monkeypatch.setattr(engine,'process_city_day',price)
    written=[]
    monkeypatch.setattr(engine,'insert',lambda table,rows:written.extend(rows))
    monkeypatch.setattr(engine,'log_run',lambda *a:None)
    with common.request_deadline(123456.):
        engine.main()
    assert seen==[(123456.,seen[0][1])] and seen[0][1]!=main_thread
    assert written==[dict(band_id='a',calibrated_prob=1)]


@pytest.mark.parametrize('complete,hit',[('True','False'),('False','True')])
def test_archived_outcome_strings_reach_the_original_scorer_as_typed_evidence(tmp_path,monkeypatch,complete,hit):
    import predictive_validation as pv
    monkeypatch.setattr(sys,'path',list(sys.path));sys.path.insert(0,str(ROOT/'tools'))
    from wxpredict import arrival_dataset
    call=dict(city_key='city',target_date='2026-09-30',checkpoint='noon',checkpoint_id='old',
        decided_at='2026-09-30T12:00:00Z',frozen_at='2026-09-30T12:01:00Z',
        local_decision_time='2026-09-30T12:00:00',engine_version='version',priced_from='wxrd3:old',
        probs=json.dumps({'a':.8,'b':.2}),raw_probs=json.dumps({'a':.8,'b':.2}),
        baseline_probs=json.dumps({'a':.5,'b':.5}),predictive_context='{}')
    fact=dict(checkpoint_id='old',winner_band_id='a',settled_at='2026-10-01T00:01:00Z',
        banked_at='2026-10-01T00:02:00Z',market_complete=complete,market_hit=hit,
        log_loss_market='1.5',brier_market='0.75')
    monkeypatch.setattr(arrival_dataset,'mirrored_rows',lambda table,*a,**kw:[dict(call)] if table=='prediction_checkpoints' else [dict(fact)])
    calls,facts=pv.older_predictor_calls(tmp_path,dt.date(2026,10,31),'2026-10-01')
    assert facts[0]['market_complete'] is (complete=='True')
    assert facts[0]['market_hit'] is (hit=='True')
    rows=pv.collect(calls,facts,[],[dict(city_key='city',timezone='UTC')],{},dt.datetime(2026,10,31,tzinfo=UTC))
    assert len(rows)==1 and rows[0]['metrics']['hit']==1
    if complete=='True':
        assert rows[0]['market_metrics']==dict(hit=0,log_loss=1.5,brier=.75)
    else:
        assert rows[0]['market_metrics'] is None
