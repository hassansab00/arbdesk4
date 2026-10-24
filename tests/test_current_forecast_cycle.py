"""The fresh-forecast build trains only real frozen calls and cannot rewrite them."""
import contextlib
import copy
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest
# Production scripts own the unqualified helper name. Legacy research CLI
# modules also contain a common.py and prepend their directory when imported.
import common as ops_common

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import current_calls as calls, current_cycle as cycle, current_model as model
from wxpredict import current_registry as registry, current_shadow, current_forecasts
from wxpredict import common, features, decision_inputs, live_reader, model_artifacts
from wxpredict import capture_history


def case(day='2026-10-24',city='synthetic',cp='noon'):
    stamp=day+'T12:00:00Z'
    row=dict.fromkeys(features.COLUMNS,0)
    row.update(event_id='event-'+city+day,city_key=city,station='SYN0',source='venue',date=day,
        unit='C',n_bands=3,bands_lo=';18;20',bands_hi='18;20;',rmax_c=18.,rmax_unit=18,
        day_offset=0,local_hour=12,decision_utc=int(calls.inputs.unix(stamp)),decision_local=stamp,
        fc_daily_source='synthetic',fc_models_n=7,fch_n_known=24,clim_peak_hour=14)
    row.update({k:None for k in decision_inputs.LABELS})
    version='wxrd3:v3:synthetic:awc-routine-v1:SYN0'
    packet=dict(policy=calls.INPUT_POLICY,version=version,as_of=stamp,history_commit='a'*40,
        history_committed_at=calls.inputs.unix(stamp)-100,model_fit_through='2026-10-11',
        timezone='UTC',band_ids=['a','b','c'],row=row,row_sha256=calls.digest(row))
    decision_inputs.add_current(packet,dict(zip(current_forecasts.FIELDS,[24,24,22.,22.,7,21.,1.])))
    packet['current_received_at']=dict(hourly0=day+'T11:00:00Z',models0=day+'T11:00:00Z')
    packet['current_receipts_sha256']=calls.digest(packet['current_received_at'])
    call=dict(checkpoint_id=city+'|'+day+'|'+cp,city_key=city,target_date=day,checkpoint=cp,
        decided_at=day+'T12:00:02Z',frozen_at=day+'T12:00:02Z',input_as_of=stamp,
        input_sha256=packet['row_sha256'],input_key=city+'|'+day,priced_from=version,
        raw_probs={'a':0.,'b':.5,'c':.5},probs={'a':0.,'b':.5,'c':.5},
        predictive_context=dict(band_ids=['a','b','c'],raw_model_version=version,timezone='UTC',
            current_forecasts_sha256=packet['current_forecasts_sha256'],
            current_receipts_sha256=packet['current_receipts_sha256']),
        market={k:dict(bid=.2,ask=.4) for k in ['a','b','c']},top_band_id='b',engine_version='synthetic')
    tomorrow=str(dt.date.fromisoformat(day)+dt.timedelta(days=1))
    outcome=dict(checkpoint_id=call['checkpoint_id'],city_key=city,target_date=day,checkpoint=cp,
        winner_band_id='c',ladder_has_winner=True,settled_at=tomorrow+'T00:10:00Z',banked_at=tomorrow+'T00:11:00Z')
    return call,packet,outcome


def attached(day='2026-10-24',city='synthetic',cp='noon'):
    call,packet,outcome=case(day,city,cp)
    out,why=calls.attach(call,packet,day+'T12:00:01Z',day+'T12:00:01Z',outcome,
        calls.inputs.unix(str(dt.date.fromisoformat(day)+dt.timedelta(days=2))+'T00:00:00Z'))
    assert why is None
    return out


def test_exact_primary_source_packet_and_independent_current_hash_are_required():
    call,p,truth=case()
    original=copy.deepcopy((call,p))
    row=attached()
    assert row['winner']==2 and row['row']['station']=='SYN0'
    assert row['row']['current_hourly_age_h']==1 and row['row']['current_models_age_h']==1
    assert row['input_sha256']==call['input_sha256'] and row['current_sha256']==p['current_forecasts_sha256']
    assert (call,p)==original
    assert calls.bounds(row['row'])[-1][1] is None,'an open-ended winner is never assigned a made-up temperature'
    for field in ('station','rmax_c'):
        bad=copy.deepcopy(p);bad['row'][field]='changed'
        with pytest.raises(ValueError,match='hash'):
            calls.attach(call,bad,'2026-10-24T12:00:01Z','2026-10-24T12:00:01Z')
    bad=copy.deepcopy(p);bad['current_forecasts']['current_max_c']=99.
    with pytest.raises(ValueError,match='hash'):
        calls.attach(call,bad,'2026-10-24T12:00:01Z','2026-10-24T12:00:01Z')
    bad=copy.deepcopy(p);bad['band_ids']=['b','a','c']
    with pytest.raises(ValueError,match='identity'):
        calls.attach(call,bad,'2026-10-24T12:00:01Z','2026-10-24T12:00:01Z')


@pytest.mark.parametrize('reason', ['late_packet','stale_forecast','future_forecast','missing_receipts',
                                    'missing_context','late_label','training_overlap'])
def test_late_inputs_labels_and_incomplete_forecasts_are_counted_not_rebuilt(reason):
    call,p,truth=case(); captured=written='2026-10-24T12:00:01Z'
    through=calls.inputs.unix('2026-10-26T00:00:00Z')
    if reason=='late_packet':written='2026-10-24T12:01:00Z'
    if reason in ('stale_forecast','future_forecast'):
        p['current_received_at']['hourly0']='2026-10-24T00:00:00Z' if reason=='stale_forecast' else '2026-10-24T13:00:00Z'
        p['current_receipts_sha256']=calls.digest(p['current_received_at'])
        call['predictive_context']['current_receipts_sha256']=p['current_receipts_sha256']
    if reason=='missing_receipts':p.pop('current_received_at')
    if reason=='missing_context':p.pop('current_forecasts')
    if reason=='late_label':truth['banked_at']='2026-10-27T00:00:00Z'
    if reason=='training_overlap':p['model_fit_through']='2026-10-24'
    row,why=calls.attach(call,p,captured,written,truth,through)
    assert row is None and why


def test_current_receipts_use_only_the_actual_snapshot_available_at_the_decision():
    first=calls.inputs.unix('2026-10-24T00:00:00Z')
    payload=dict(schema='nwp-current-snapshot-v1',hours={'2026-10-24T12:00:00Z':[22.]})
    record=live_reader.Record([(kind,'synthetic|snapshot',first+3600,payload)
        for kind in ('hourly0','models0')]+[('hourly0','synthetic|snapshot',first+14*3600,payload)])
    out=current_forecasts.receipts(record,{'city':'synthetic','date':'2026-10-24'},first+12*3600,common.UTC)
    assert set(out)=={'hourly0','models0'}
    assert set(out.values())=={'2026-10-24T01:00:00+00:00'}


def mirror(tmp_path,table,rows,through='2026-10-27T00:00:00Z'):
    folder=tmp_path/'data/mirror'/table;folder.mkdir(parents=True,exist_ok=True)
    keys=sorted({k for r in rows for k in r})
    text=io.StringIO();w=csv.DictWriter(text,fieldnames=keys);w.writeheader()
    for r in rows:
        w.writerow({k:json.dumps(v) if isinstance(v,(dict,list)) else v for k,v in r.items()})
    blob=gzip.compress(text.getvalue().encode(),mtime=0)
    (folder/'part.csv.gz').write_bytes(blob)
    manifest=tmp_path/'data/mirror/manifest.json'
    data=json.loads(manifest.read_text()) if manifest.exists() else {'tables':{}}
    data['tables'][table]=dict(through=through,files=[dict(file='part.csv.gz',rows=len(rows),
        sha256=hashlib.sha256(blob).hexdigest(),to=through)])
    manifest.write_text(json.dumps(data))


def test_verified_mirror_counts_missing_first_calls_and_never_uses_a_later_packet(tmp_path,monkeypatch):
    call,p,truth=case()
    other={**call,'checkpoint_id':'other','city_key':'other','input_key':'missing'}
    later={**call,'checkpoint_id':'later','decided_at':'2026-10-24T12:10:00Z'}
    mirror(tmp_path,'prediction_checkpoints',[call,later,other])
    mirror(tmp_path,'fact_checkpoint_outcome',[truth])
    captured=dict(capture_id=1,kind='wx_features',key=call['input_key'],payload=p,
                  captured_at='2026-10-24T12:00:01Z',written_at='2026-10-24T12:00:01Z')
    newer={**captured,'capture_id':2,'written_at':'2026-10-24T12:10:00Z',
           'captured_at':'2026-10-24T12:10:00Z','payload':{**p,'row_sha256':'wrong'}}
    mirror(tmp_path,'live_capture',[captured,newer])
    monkeypatch.setattr(common,'ROOT',str(tmp_path))
    rows,report=calls.build('2026-10-24','2026-10-24',calls.inputs.unix('2026-10-27T00:00:00Z'))
    assert len(rows)==1 and report['first_calls']==2 and report['excluded']=={'missing_primary_packet':1}
    assert rows[0]['input_sha256']==call['input_sha256']
    # Filtering cannot turn a wrong file count into a verified mirror.
    path=tmp_path/'data/mirror/manifest.json';m=json.loads(path.read_text())
    m['tables']['live_capture']['files'][0]['rows']=3;path.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='count mismatch'):
        calls.build('2026-10-24','2026-10-24',calls.inputs.unix('2026-10-27T00:00:00Z'))


def test_current_capture_availability_uses_server_receipt_without_changing_legacy_reads(tmp_path):
    row=dict(kind='hourly0',key='synthetic|snapshot',captured_at='2026-10-24T11:00:00Z',
             written_at='2026-10-24T12:30:00Z',payload={'hours':{}})
    mirror(tmp_path,'live_capture',[row])
    first=calls.inputs.unix('2026-10-24T10:00:00Z')
    through=calls.inputs.unix('2026-10-24T12:00:00Z')
    assert len(capture_history.read(first,through,root=str(tmp_path)))==1
    assert capture_history.read(first,through,root=str(tmp_path),receipt_kinds=('hourly0',))==[]
    later=capture_history.read(first,through+3600,root=str(tmp_path),receipt_kinds=('hourly0',))
    assert later[0][2]=='2026-10-24T12:30:00Z'


def records(first,dates,cities=20):
    return [attached(str(dt.date.fromisoformat(first)+dt.timedelta(days=i)),str(city),cp)
            for i in range(dates) for city in range(cities) for cp in calls.CHECKPOINTS]


def ready_fixture():
    p=cycle.window('2026-11-29')
    train=records('2026-10-24',20);test=records(p['evaluation_first'],14)
    tc=dict(first_calls=len(train));vc=dict(first_calls=len(test))
    p.update(decision='ready',training_availability=cycle.availability(train,tc,p['train_first'],p['fit_last'],True),
             evaluation_availability=cycle.availability(test,vc,p['evaluation_first'],p['evaluation_last']))
    return p,train,test


def test_training_and_evaluation_require_whole_date_coverage_and_keep_missing_calls():
    p,train,test=ready_fixture()
    assert p['fit_last']<'2026-11-13'<p['evaluation_first']
    assert p['training_availability']['ready'] and p['evaluation_availability']['ready']
    short=cycle.availability(test[1:],{'first_calls':len(test)},p['evaluation_first'],p['evaluation_last'])
    assert not short['ready'] and short['complete_dates']==13
    assert not cycle.availability(test,{'first_calls':len(test)/.79},p['evaluation_first'],p['evaluation_last'])['ready']
    assert not cycle.availability(train[:-80],{'first_calls':len(train)},p['train_first'],p['fit_last'],True)['ready']
    with pytest.raises(ValueError,match='coverage'):
        cycle.fit_and_evaluate({**p,'decision':'wait'},train,test,'unused')


def test_real_fresh_forecast_fit_and_inference_preserve_labels_and_probability_bound(tmp_path,monkeypatch):
    pytest.importorskip('lightgbm')
    monkeypatch.setattr(sys,'path',list(sys.path))
    p,train,test=ready_fixture()
    report,meta,path=cycle.fit_and_evaluate(p,train,test,str(tmp_path))
    assert report['decision']=='accept shadow'
    assert report['maximum_bucket_change']<=cycle.legacy.MAX_BUCKET_CHANGE+1e-12
    assert meta['fit_last']<p['evaluation_first'] and meta['recipe']==model.RECIPE
    assert model_artifacts.validate_blob(pathlib.Path(path).read_bytes(),meta['model_sha256'])
    assert any('current_' in name for name in meta['features'])
    assert not any('mkt_' in name or name in decision_inputs.LABELS for name in meta['features'])
    import lightgbm as lgb
    booster=lgb.Booster(model_str=gzip.decompress(pathlib.Path(path).read_bytes()).decode())
    old=model.predict(booster,test[:1])
    changed=copy.deepcopy(test[:1]);changed[0]['winner']=0
    changed[0]['market_probs']={'a':1.,'b':0.,'c':0.}
    changed[0]['row']['future_outcome']=99
    changed[0]['row']['label_unit']=-99
    assert model.predict(booster,changed)==old
    assert old[0]['a']==0 and sum(old[0].values())==pytest.approx(1)


def test_registry_is_separate_immutable_and_refuses_corruption(tmp_path):
    sha='a'*64
    report=dict(night='2026-11-29',decision='accept shadow',evaluation_first='2026-11-14',
                model_sha256=sha,changes_serving=False,reasons=[],nll_gain_ci90_by_date=[.01,.02])
    meta=dict(version='wxrd-auto-20261129-'+sha[:12],recipe=model.RECIPE,model_sha256=sha,
              blend=model.BLEND,fit_last='2026-11-12',features=['synthetic'],
              recipe_sources_sha256=calls.digest(model.source_hashes()),
              evaluation_report=registry.REPORTS+'/2026-11-29.json')
    registry.record(report,meta,str(tmp_path))
    loaded,_=registry.selected(str(tmp_path));assert loaded==meta
    registry.record(report,meta,str(tmp_path))
    with pytest.raises(ValueError,match='rewrite'):
        registry.record({**report,'decision':'reject'},root=str(tmp_path))
    assert not (tmp_path/'data/models/wxrd/auto_registry.json').exists()
    path=tmp_path/registry.CANDIDATES/(meta['version']+'.json');path.write_text('{}')
    with pytest.raises(ValueError,match='metadata hash'):
        registry.selected(str(tmp_path))


def test_failed_public_weight_readback_cannot_register_a_candidate(tmp_path,monkeypatch):
    p,train,test=ready_fixture()
    monkeypatch.setattr(common,'ROOT',str(tmp_path))
    monkeypatch.setattr(cycle,'prepare',lambda p:(dict(p,decision='ready'),train,test))
    monkeypatch.setattr(cycle,'fit_and_evaluate',lambda *a:(
        {**p,'decision':'accept shadow'},dict(version='synthetic',model_sha256='a'*64),'unused'))
    monkeypatch.setattr(model_artifacts,'publish',lambda *a:(_ for _ in ()).throw(ValueError('readback refused')))
    with pytest.raises(ValueError,match='readback'):
        cycle.main([])
    assert registry.selected() is None and not (tmp_path/registry.STATE).exists()


def test_active_shadow_requires_a_model_available_before_the_original_call(monkeypatch):
    call,p,truth=case()
    writes=[]
    def rest(table,*a,**k):
        return [call] if table=='prediction_checkpoints' else [] if table=='variant_shadow_checkpoints' else [
            dict(capture_id=1,key=call['input_key'],payload=p,captured_at='2026-10-24T12:00:01Z',written_at='2026-10-24T12:00:01Z')]
    fake=SimpleNamespace(request_deadline=lambda *a:contextlib.nullcontext(),rest_all=rest,
                         upsert=lambda *a:writes.append(a) or 1,log_run=lambda *a:None)
    monkeypatch.setattr(common,'ops',lambda:fake)
    monkeypatch.setattr(registry,'selected',lambda:(dict(fit_last='2026-10-11'),'2026-10-24T12:01:00Z'))
    monkeypatch.setattr(registry,'load',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not load')))
    result=current_shadow.run(dt.datetime(2026,10,24,12,5,tzinfo=common.UTC))
    assert not writes and result['selected']==0
    row=attached()
    meta=dict(version='wxrd-auto-20261023-'+'a'*12,model_sha256='a'*64,fit_last='2026-10-11',
              training_dates={row['source_identity']+'|noon':20})
    monkeypatch.setattr(registry,'selected',lambda:(meta,'2026-10-23T00:00:00Z'))
    monkeypatch.setattr(registry,'load',lambda *a,**k:object())
    monkeypatch.setattr(model,'predict',lambda *a:[{'a':0.,'b':.45,'c':.55}])
    result=current_shadow.run(dt.datetime(2026,10,24,12,5,tzinfo=common.UTC))
    assert result['written']==1 and len(writes)==1
    frozen=writes[0][1][0]
    assert frozen['variant']=='wx_current' and frozen['decided_at']=='2026-10-24T12:05:00+00:00'
    assert frozen['station']=='SYN0' and frozen['probs']=={'a':0.,'b':.45,'c':.55}
    writes.clear()
    monkeypatch.setattr(model,'predict',lambda *a:[{'a':0.,'b':0.,'c':1.}])
    result=current_shadow.run(dt.datetime(2026,10,24,12,5,tzinfo=common.UTC))
    assert not writes and result['attention']
    assert result['excluded']=={'prediction correction exceeds its bound':1}


def test_no_active_current_model_adds_no_live_database_work(monkeypatch):
    monkeypatch.setattr(registry,'selected',lambda:None)
    monkeypatch.setattr(common,'ops',lambda:(_ for _ in ()).throw(AssertionError('no database read')))
    assert current_shadow.run()['selected']==0


def test_forward_validation_uses_frozen_predictions_and_keeps_missing_calls(monkeypatch):
    rows=records('2026-11-03',14)
    available='2026-11-01T00:00:00Z'
    meta=dict(version='wxrd-auto-20261101-'+'a'*12,model_sha256='a'*64,
              training_dates={r['source_identity']+'|'+r['checkpoint']:20 for r in rows})
    shadows=[dict(city_key=r['city'],target_date=r['date'],checkpoint=r['checkpoint'],
        variant=registry.VARIANT,variant_version=registry.VARIANT+':'+meta['version'],
        decided_at=r['date']+'T12:05:00Z',frozen_at=r['date']+'T12:05:01Z',
        probs={'a':0.,'b':.45,'c':.55}) for r in rows]
    monkeypatch.setattr(calls,'build',lambda *a:(rows,dict(first_calls=len(rows))))
    monkeypatch.setattr(calls.arrival,'mirrored_rows',lambda *a,**k:shadows)
    good=cycle.forward(meta,available,'2026-11-18')
    assert good['decision']=='forward validated' and not good['changes_serving']
    assert all(r['supported'] for r in good['city_horizons'])
    shadows[0]['frozen_at']='2026-11-04T00:12:00Z'
    bad=cycle.forward(meta,available,'2026-11-18')
    assert bad['decision']=='forward rejected' and bad['coverage']<1
    assert bad['expected_first_calls']==len(rows)
    assert cycle.forward(meta,available,'2026-11-05')['decision']=='wait forward'
