import datetime as dt
import pytest
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


def test_strict_timing_uses_actual_receipt_and_earliest_saved_peak_not_checkpoint_name():
    import copy
    p=call(checkpoint='prepeak_1h',local_decision_time='2026-10-16T11:00:00',
           decided_at='2026-10-16T12:10:00Z',frozen_at='2026-10-16T12:10:00Z',
           predictive_context=dict(clim_peak_hour=13,fch_peak_local_hour=12))
    original=copy.deepcopy(p)
    t=v.capture_timing(p,CITIES[0])
    assert not t['before_expected_peak'] and t['reason']=='at_or_after_expected_peak'
    assert t['cutoff_local']=='2026-10-16T12:00:00+00:00' and t['delay_minutes']==70
    assert p==original, 'reporting must never rewrite the scheduled time or prediction'
    assert not v.capture_timing(dict(p,frozen_at='2026-10-16T12:00:00Z'),CITIES[0])['before_expected_peak']
    assert v.capture_timing(dict(p,frozen_at='2026-10-16T11:59:59Z'),CITIES[0])['before_expected_peak']


def test_strict_early_calls_unknown_peaks_and_legacy_are_separate():
    early=call(checkpoint='d1_eve',local_decision_time='2026-10-15T18:00:00',
               frozen_at='2026-10-15T18:36:00Z')
    assert v.capture_timing(early,CITIES[0])['before_expected_peak']
    assert v.capture_timing(call(),CITIES[0])['reason']=='missing_server_receipt'
    assert v.capture_timing(call(frozen_at='2026-10-16T12:36:00Z'),CITIES[0])['reason']=='missing_saved_peak'
    assert v.capture_timing(call(checkpoint='postpeak_1h',frozen_at='2026-10-16T12:36:00Z',
        predictive_context=dict(clim_peak_hour=20)),CITIES[0])['reason']=='explicit_postpeak_call'
    # A city's early peak makes a noon call a late-information call.
    assert v.capture_timing(call(frozen_at='2026-10-16T12:36:00Z',
        predictive_context=dict(clim_peak_hour=11)),CITIES[0])['reason']=='at_or_after_expected_peak'


def test_strict_timing_city_clock_handles_dst_and_keeps_missing_evidence_unknown():
    c=dict(city_key='city',timezone='Europe/Paris')
    p=call(target_date='2026-10-25',local_decision_time='2026-10-25T12:00:00',
           decided_at='2026-10-25T11:37:00Z',frozen_at='2026-10-25T11:37:00Z',
           predictive_context=dict(clim_peak_hour=14,fch_peak_local_hour=float('nan')))
    t=v.capture_timing(p,c)
    assert t['received_local']=='2026-10-25T12:37:00+01:00'
    assert t['delay_minutes']==37 and t['before_expected_peak']
    p['predictive_context']={'clim_peak_hour':'invalid','fch_peak_local_hour':None}
    assert v.capture_timing(p,c)['reason']=='missing_saved_peak'


def test_fixed_clock_uses_only_its_own_saved_monthly_peak():
    import copy
    ref=dict(city_key='city',timezone='Asia/Riyadh',source='derived_weather_peak',month=10,
             peak_hour_local=11,computed_at='2026-10-16T04:00:00Z',n_days=25)
    p=call(frozen_at='2026-10-16T09:37:00Z',decided_at='2026-10-16T09:37:00Z',
           predictive_context={'timing_reference':ref})
    c=dict(city_key='city',timezone='Asia/Riyadh')
    original=copy.deepcopy(p)
    t=v.capture_timing(p,c)
    assert t['reason']=='at_or_after_expected_peak' and t['minutes_before_expected_peak']==-97
    assert t['cutoff_local']=='2026-10-16T11:00:00+03:00'
    assert p==original
    for bad in [dict(ref,city_key='other'),dict(ref,timezone='UTC'),dict(ref,month=9),
                dict(ref,source='live'),dict(ref,computed_at='2026-10-17T00:00:00Z'),
                dict(ref,peak_hour_local=float('nan'))]:
        assert v.capture_timing(dict(p,predictive_context={'timing_reference':bad}),c)['reason']=='missing_saved_peak'


def test_reviewed_peak_split_preserves_scores_and_excludes_missing_or_mismatched_evidence():
    import copy
    p=call(frozen_at='2026-10-16T12:36:00Z',predictive_context={'clim_peak_hour':14})
    rows=v.collect([p],[outcome()],[],CITIES,{},NOW)
    original=copy.deepcopy(rows)
    review=dict(city_key='city',target_date='2026-10-16',winner_band_id='b',
                peak_local_hour=11,computed_at='2026-10-17T03:00:00Z')
    def report(reviews):
        return next(r['report'] for r in v.reports(rows,CITIES,reviews,[],NOW)
                    if r['checkpoint']=='noon' and r['scope']=='served')
    r=report([review]); split=r['observed_peak_timing']
    assert r['hits']==0 and r['n']==1
    assert split['at_or_after']['n']==1 and split['at_or_after']['hits']==0
    assert split['before']['n']==0
    recent=r['capture_timing']['recent_calls'][0]['observed_timing']
    assert recent['minutes_before_peak']==-96 and recent['relation']=='at_or_after'
    assert report([dict(review,peak_local_hour=15)])['observed_peak_timing']['before']['n']==1
    for reviews in [[],[dict(review,winner_band_id='different')],
                    [dict(review,computed_at='2026-10-18T00:00:00Z')],
                    [dict(review,peak_local_hour=float('nan'))]]:
        assert report(reviews)['observed_peak_timing']['unknown_calls']==1
    assert rows==original, 'hindsight timing must not change original calls or fitting inputs'
    legacy=dict(rows[0],verified=False)
    assert v.observed_peak_timing(legacy,CITIES[0],review,NOW)['relation']=='unknown'
    assert v.observed_peak_timing(dict(rows[0],settled=False),CITIES[0],review,NOW)['relation']=='pending'


def test_strict_scores_expose_denominator_and_never_replace_an_original_miss():
    p=call(frozen_at='2026-10-16T12:36:00Z',predictive_context=dict(clim_peak_hour=14))
    late=call('later',decided_at='2026-10-16T12:55:00Z',frozen_at='2026-10-16T12:55:00Z',
              predictive_context=dict(clim_peak_hour=14),probs={'a':.01,'b':.99})
    rows=v.collect([late,p],[outcome(),outcome('later')],[],CITIES,{},NOW)
    report=next(r['report'] for r in v.reports(rows,CITIES,[],[],NOW) if r['checkpoint']=='noon' and r['scope']=='served')
    t=report['capture_timing']
    assert report['n']==1 and t['strict']['n']==1 and t['strict']['hits']==0
    assert t['eligible_calls']==1 and t['excluded_calls']==0
    assert t['recent_calls'][0]['frozen_at']=='2026-10-16T12:36:00+00:00'
    p['predictive_context']['clim_peak_hour']=11
    rows=v.collect([p],[outcome()],[],CITIES,{},NOW)
    t=v.timing_report(rows,'noon')
    assert t['strict']['n']==0 and t['calls']==1 and t['excluded_calls']==1
    assert t['exclusions']=={'at_or_after_expected_peak':1}
    assert t['recent_calls'][0]['hit']==0, 'an excluded miss stays visible'


def test_strict_overall_requires_common_eligible_dates_without_cherry_picking():
    base=dict(target_date='2026-10-16',settled=True,verified=True,top_prob=.9,
              metrics=dict(hit=1,log_loss=.1,brier=.01),version='v',baseline=None,raw=None)
    rows=[dict(base,checkpoint='morning',timing=dict(before_expected_peak=True,reason='eligible')),
          dict(base,checkpoint='noon',timing=dict(before_expected_peak=False,reason='at_or_after_expected_peak'))]
    t=v.timing_report(rows,'overall')
    assert t['strict']['n']==0 and t['excluded_calls']==2
    assert t['exclusions']==dict(incomplete_strict_date=1,at_or_after_expected_peak=1)


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
        if row['checkpoint'] in ('day_ahead','d1_eve','morning'):
            assert row['report']['learning']==[], 'noon/peak calibration must not be displayed as early-horizon learning'
            continue
        assert len(row['report']['learning'])==1
        assert row['report']['learning'][0]['model_version']=='current'
        assert row['report']['learning'][0]['active']


@pytest.mark.parametrize('contract_station',[None,'KDEN'])
def test_repeat_runs_replace_derived_answers_and_preserve_original_calls(tmp_path,monkeypatch,contract_station):
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
    input_version='wxrd3:v3:'+('a'*12)
    if contract_station:
        input_version+=':awc-routine-v1:'+contract_station
        original['predictive_context']={'raw_model_version':input_version}
    preserved=copy.deepcopy(original)
    facts=[]
    saved={}
    modes=[]
    def read(table,*a,**kw):
        if table=='prediction_checkpoints': return [original]
        if table=='v_checkpoint_outcome': return list(facts)
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
    assert all(s['model_version']==input_version for s in states)
    assert second['report']['active_model_version']==input_version
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
        p=call(checkpoint=h,local_decision_time='2026-10-16T12:00:00',frozen_at='2026-10-16T12:36:00Z',
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


def test_displayed_active_parameters_expire_on_the_city_calendar():
    import city_model_selection as selector
    now=dt.datetime(2026,10,18,12,tzinfo=dt.timezone.utc)  # Wellington is already on 19 October.
    city=dict(city_key='city',timezone='Pacific/Auckland')
    fit=dict(policy=selector.POLICY,baseline_policy=selector.BASELINE_POLICY,model_version='current',
        city_key='city',checkpoint='noon',selected='baseline',version='validated',as_of='2026-10-18',
        fit_at='2026-10-18T08:00:00Z',validated_through='2026-10-15',train_dates=20,validation_dates=10,
        train_through='2026-09-30',validation_from='2026-10-06',validation=dict(passed=True),reason='validated')
    calibration=dict(city_key='city',phase='before_peak',model_version='current',as_of='2026-10-18',
        validated_through='2026-10-15',params=dict(temperature=1.05,tilt=.025),version='fit')
    row=next(r['report'] for r in v.reports([],[city],[],[calibration],now,'current',[fit])
             if r['checkpoint']=='noon' and r['scope']=='served')
    assert not row['model_selection']['effective']['active']
    assert not row['learning'][0]['active'] and row['learning'][0]['effective_params']==v.cal.IDENTITY
