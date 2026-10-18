import datetime as dt
import predictive_validation as v

NOW=dt.datetime(2026,10,17,12,tzinfo=dt.timezone.utc)
CITIES=[dict(city_key='city',timezone='UTC',display_name='City')]


def call(id='first',**kw):
    return dict(dict(checkpoint_id=id,city_key='city',target_date='2026-10-16',checkpoint='noon',
        local_decision_time='2026-10-16T12:00:00',decided_at='2026-10-16T12:36:00Z',
        engine_version=id,probs={'a':.9,'b':.1}),**kw)


def outcome(id='first'):
    return dict(checkpoint_id=id,winner_band_id='b',settled_at='2026-10-17T01:00:00Z',banked_at='2026-10-17T02:00:00Z')


def test_original_miss_survives_perfect_later_engine_and_backfill_rejected():
    p=call(); later=call('later',decided_at='2026-10-16T12:55:00Z',probs={'a':.01,'b':.99})
    rows=v.collect([later,p],[outcome(),outcome('later')],[],CITIES,{},NOW)
    assert len(rows)==1 and rows[0]['metrics']['hit']==0
    for stamp in ['2026-10-16T11:59:00Z','2026-10-16T14:00:00Z','2026-10-17T02:00:00Z']:
        assert not v.collect([call(decided_at=stamp)],[outcome()],[],CITIES,{},NOW)
    assert not v.collect([call(frozen_at='2026-10-17T02:00:00Z')],[outcome()],[],CITIES,{},NOW)
    early=outcome();early['settled_at']='2026-10-16T12:00:00Z'
    assert not v.collect([p],[early],[],CITIES,{},NOW)[0]['settled']


def test_day_ahead_local_midnight_and_common_horizon_overall():
    da=dict(city_key='city',for_date='2026-10-16',called_at='2026-10-16T00:01:00Z',hit=True,predicted_pct=99)
    assert not v.collect([],[],[da],CITIES,{},NOW)
    da['called_at']='2026-10-15T23:59:00Z'
    rows=v.collect([],[],[da],CITIES,{},NOW)
    reports=v.reports(rows,CITIES,[],[],NOW)
    assert len(reports)==24
    overall=next(r for r in reports if r['checkpoint']=='overall' and r['scope']=='served')['report']
    assert overall['n']==0, 'one horizon cannot masquerade as complete overall coverage'
    assert next(r for r in reports if r['checkpoint']=='day_ahead' and r['scope']=='served')['report']['hits']==1


def test_missing_cities_are_retained_and_postpeak_does_not_inflate_overall():
    rows=[]
    for h in v.HORIZONS:
        rows.append(dict(city_key='city',target_date='2026-10-16',checkpoint=h,version='wxrd3:v3:x',settled=True,
            verified=True,top_prob=.9,metrics=dict(hit=int(h=='postpeak_1h'),log_loss=2,brier=1),baseline=None,raw=None))
    out=v.reports(rows,CITIES+[dict(city_key='empty',timezone='UTC')],[],[],NOW)
    overall=next(r['report'] for r in out if r['city_key']=='city' and r['checkpoint']=='overall' and r['scope']=='served')
    assert overall['hits']==0 and overall['n']==6 and overall['dates']==1
    assert all(r['report']['n']==0 for r in out if r['city_key']=='empty')


def test_learning_activity_only_names_current_serving_model():
    import wx_city_calibration as c
    state=dict(city_key='city',phase='before_peak',model_version='old',as_of='2026-10-17',
               validated_through='2026-10-16',params=dict(temperature=1.05,tilt=.025),version='fit')
    reports=v.reports([],CITIES,[],[state,dict(state,model_version='current')],NOW,'current')
    for row in reports:
        assert len(row['report']['learning'])==1
        assert row['report']['learning'][0]['model_version']=='current'
        assert row['report']['learning'][0]['active']


def test_repeat_runs_replace_derived_answers_and_preserve_original_calls(tmp_path,monkeypatch):
    """Exercise the real HTTP write helpers, including duplicate handling."""
    import copy
    import json
    import pathlib
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
    metadata=tmp_path/'data/models/wxrd/wxrd-v3_remaining.json'
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps(dict(version='wxrd-v3',model_sha256='a'*64,fit_last='2026-10-11')))
    monkeypatch.setattr(v,'ROOT',tmp_path)
    original=call()
    preserved=copy.deepcopy(original)
    facts=[]
    saved={}
    modes=[]
    def read(table,*a,**kw):
        if table=='prediction_checkpoints': return [original]
        if table=='fact_checkpoint_outcome': return list(facts)
        if table=='derived_wx_city_calibration':
            return list(saved.get(table,{}).values())
        if table=='derived_city_prediction_validation':
            return [r for r in saved.get(table,{}).values() if r['scope']=='served']
        return []
    def post(url,*,headers,params,data,**kw):
        table=url.rsplit('/',1)[-1]
        mode=headers['Prefer']
        modes.append((table,mode))
        target=saved.setdefault(table,{})
        for row in json.loads(data):
            key=tuple(row[k] for k in params['on_conflict'].split(','))
            if key not in target or 'merge-duplicates' in mode:
                target[key]=copy.deepcopy(row)
        return SimpleNamespace(status_code=201)
    monkeypatch.setattr(v,'get_cities',lambda **kw:CITIES)
    monkeypatch.setattr(v,'rest_all',read)
    monkeypatch.setattr(v,'log_run',lambda *a:None)
    # The suite loads common under several identities; patch the globals of
    # the actual bound writer rather than a separately imported module.
    wire=v.upsert_replace.__globals__
    monkeypatch.setitem(wire,'_cfg',lambda:{'url':'https://fixture.invalid'})
    monkeypatch.setitem(wire,'_headers',lambda:{})
    monkeypatch.setitem(wire,'_post',post)
    key=('city','noon','served')
    v.run(now=NOW)
    first=saved['derived_city_prediction_validation'][key]
    assert first['report']['n']==0 and first['report']['pending']==1
    facts.append(outcome())
    v.run(now=NOW+dt.timedelta(days=1))
    second=saved['derived_city_prediction_validation'][key]
    assert second['as_of']!=first['as_of'] and second['report']['n']==1 and second['report']['hits']==0
    assert len(saved['derived_city_prediction_validation'])==24
    states=list(saved['derived_wx_city_calibration'].values())
    assert len(states)==3 and all(s['as_of']=='2026-10-18' for s in states)
    selection_fit=second['report']['model_selection']['fit_at']
    v.run(score_only=True,now=NOW+dt.timedelta(days=2))
    assert saved['derived_city_prediction_validation'][key]['as_of'].startswith('2026-10-19')
    assert all(s['as_of']=='2026-10-18' for s in saved['derived_wx_city_calibration'].values())
    assert saved['derived_city_prediction_validation'][key]['report']['model_selection']['fit_at']==selection_fit
    assert set(saved)=={'derived_city_prediction_validation','derived_wx_city_calibration'}
    assert all('merge-duplicates' in mode for _,mode in modes)
    assert original==preserved
    assert len(list((tmp_path/'data/eval/wxpredict/city_calibration').glob('*.json')))==2


def test_raw_comparison_keeps_misses_when_baseline_is_the_served_predictor():
    p=call(frozen_at='2026-10-16T12:36:00Z',priced_from='corrected-baseline',
        probs={'a':.1,'b':.9},raw_probs={'a':.8,'b':.2},baseline_probs={'a':.1,'b':.9},
        predictive_context=dict(raw_model_version='wxrd3:v3:checked',band_ids=['a','b'],obs_age_min=43))
    rows=v.collect([p],[outcome()],[],CITIES,{},NOW)
    reports=v.reports(rows,CITIES,[],[],NOW)
    by={r['scope']:r['report'] for r in reports if r['checkpoint']=='noon'}
    assert by['served']['hits']==1 and by['wxrd3']['n']==0
    assert by['wxrd3_raw']['n']==1 and by['wxrd3_raw']['hits']==0
    assert by['served']['paired_audit']['baseline_only_hits']==1
    assert by['served']['paired_audit']['raw_cooler_misses']==1


def test_raw_overall_requires_its_three_prepeak_supported_calls_and_never_counts_postpeak():
    rows=[]
    for h in ('noon','prepeak_2h','prepeak_1h','postpeak_1h'):
        p=call(checkpoint=h,local_decision_time='2026-10-16T12:00:00Z',frozen_at='2026-10-16T12:36:00Z',
            priced_from='corrected-baseline',probs={'a':.1,'b':.9},raw_probs={'a':.8,'b':.2},
            baseline_probs={'a':.1,'b':.9},predictive_context=dict(raw_model_version='wxrd3:v3:checked',band_ids=['a','b']))
        # The source clock guards are covered in collect's tests; test aggregation here.
        r=v.collect([dict(p,checkpoint='noon')],[outcome()],[],CITIES,{},NOW)[0]
        r['checkpoint']=h
        if h=='postpeak_1h':r['raw']=dict(hit=1,log_loss=.1,brier=.01)
        rows.append(r)
    raw=next(r['report'] for r in v.reports(rows,CITIES,[],[],NOW) if r['checkpoint']=='overall' and r['scope']=='wxrd3_raw')
    assert raw['n']==3 and raw['hits']==0 and raw['dates']==1
    assert next(r['report'] for r in v.reports(rows[:-2],CITIES,[],[],NOW) if r['checkpoint']=='overall' and r['scope']=='wxrd3_raw')['n']==0
