"""Early ensemble is frozen before outcomes, service-state only, never served."""
import copy
import datetime as dt
import pytest
import ensemble_early as e

BANDS=[dict(band_id='a',band_lo=None,band_hi=20,open_low=True,open_high=False),
       dict(band_id='b',band_lo=20,band_hi=22,open_low=False,open_high=False),
       dict(band_id='c',band_lo=22,band_hi=None,open_low=False,open_high=True)]


def original(day='2026-10-02'):
    now=dt.datetime.combine(dt.date.fromisoformat(day)-dt.timedelta(days=1),dt.time(18,36),dt.timezone.utc)
    inputs=dict(sources={m:21. if i==0 else 26. for i,m in enumerate(e.MODELS)},
                inputs_oldest_run_at=(now-dt.timedelta(hours=1)).isoformat(),inputs_newest_run_at=now.isoformat())
    row=dict(city_key='city',target_date=day,checkpoint='d1_eve',station='TEST',sigma_c=1.,
             probs={'a':.2,'b':.4,'c':.4},market={k:dict(bid=.1,ask=.5) for k in ('a','b','c')},
             predictive_context=dict(forecast_input=dict(station_combination=inputs)))
    context=dict(contract='awc-routine-v1',station='TEST',rules_sha256='a'*64,rules_received_at=(now-dt.timedelta(days=1)).isoformat())
    return row,context,now


def scored(day='2026-10-02'):
    row,ctx,now=original(day);packet=e.capture(row,BANDS,'C',ctx,now)
    assert packet
    return dict(row,ensemble_packet=packet,verified=True,settled=True,winner='b',
                frozen_at=(now+dt.timedelta(seconds=1)).isoformat(),
                settled_at=day+'T23:58:00+00:00',label_received_at=day+'T23:59:00+00:00')


def test_original_sources_and_market_freeze_without_changing_served_call(monkeypatch):
    monkeypatch.setattr(e,'_states',{})
    row,ctx,now=original();before=copy.deepcopy(row)
    p=e.capture(row,BANDS,'C',ctx,now)
    assert row==before and p['changes_serving'] is False and p['training_dates']==0
    assert set(p['source_probs'])==set(e.MODELS)
    assert sum(p['candidate_probs'].values())==pytest.approx(1)
    assert max(abs(p['candidate_probs'][k]-row['probs'][k]) for k in row['probs'])<=.2
    assert p['incumbent_probs']==row['probs']


@pytest.mark.parametrize('bad',['station','future_rules','stale','missing_source','nan','ineligible'])
def test_missing_stale_or_mismatched_evidence_is_not_a_candidate(bad):
    r,c,n=original();inp=r['predictive_context']['forecast_input']['station_combination']
    if bad=='ineligible':r['inputs_ok']=False
    if bad=='station':r['station']='OTHER'
    if bad=='future_rules':c['rules_received_at']=(n+dt.timedelta(seconds=1)).isoformat()
    if bad=='stale':inp['inputs_oldest_run_at']=(n-dt.timedelta(hours=9)).isoformat()
    if bad=='missing_source':inp['sources'].pop(e.MODELS[0])
    if bad=='nan':inp['sources'][e.MODELS[0]]=float('nan')
    assert e.capture(r,BANDS,'C',c,n) is None


def test_weights_learn_older_calls_and_private_coefficients_are_redacted(monkeypatch):
    monkeypatch.setattr(e,'_states',{})
    rs=[scored(str(dt.date(2026,10,2)+dt.timedelta(days=i))) for i in range(20)]
    states=e.fit(rs,dt.date(2026,11,20));assert len(states)==1
    s=states[0]
    assert s['train_dates']==20 and s['decision']=='held' and s['validation_dates']==0
    assert s['source_ranking'][0]==e.MODELS[0]
    assert s['weights'][e.MODELS[0]]>s['weights'][e.MODELS[1]]
    assert 'weights' not in e.summary(s) and 'source_log_losses' not in e.summary(s)
    assert s['changes_serving'] is False


def test_post_outcome_or_altered_incumbent_cannot_train():
    r=scored();r['frozen_at']=r['settled_at']
    assert not e.eligible([r],dt.datetime(2026,11,20,tzinfo=dt.timezone.utc))
    r=scored();r['ensemble_packet']['incumbent_probs']={'a':0,'b':1,'c':0}
    assert not e.eligible([r],dt.datetime(2026,11,20,tzinfo=dt.timezone.utc))


def test_shadow_weights_update_from_recent_labels_without_claiming_validation(monkeypatch):
    monkeypatch.setattr(e,'_states',{})
    rows=[scored('2026-10-02'),scored('2026-10-03')]
    s=e.fit(rows,dt.date(2026,10,5))[0]
    assert s['learning_dates']==2 and s['train_dates']==0
    assert all(w==1/7 for w in s['weights'].values())
    assert s['decision']=='held' and not s['changes_serving']


def test_weight_steps_are_bounded_and_same_night_retry_is_idempotent(monkeypatch):
    monkeypatch.setattr(e,'_states',{})
    rows=[scored(str(dt.date(2026,10,2)+dt.timedelta(days=i))) for i in range(20)]
    day=dt.date(2026,11,20)
    first=e.fit(rows,day)[0]
    assert max(abs(w-1/7) for w in first['weights'].values())<=e.MAX_WEIGHT_STEP+1e-12
    again=e.fit(rows,day,[first])[0]
    assert again==first
    next_=e.fit(rows,day+dt.timedelta(days=1),[first])[0]
    assert max(abs(next_['weights'][m]-first['weights'][m]) for m in e.MODELS)<=e.MAX_WEIGHT_STEP+1e-12


def test_one_sided_quotes_use_the_existing_tick_mark_without_discarding_sources():
    r,c,n=original();r['market']['a']=dict(bid=None,ask=.001,last=.999)
    packet=e.capture(r,BANDS,'C',c,n)
    assert packet['market_comparable'] and set(packet['source_probs'])==set(e.MODELS)
    assert packet['market_probs']['a']==pytest.approx(.001/(.001+.3+.3))


def test_missing_market_does_not_discard_source_learning_or_become_validation():
    rows=[]
    for i in range(20):
        day=str(dt.date(2026,10,2)+dt.timedelta(days=i))
        r,c,n=original(day);r['market']['a']=dict(bid=None,ask=.001,last=None)
        packet=e.capture(r,BANDS,'C',c,n)
        assert packet and packet['market_probs'] is None and not packet['market_comparable']
        assert sum(packet['candidate_probs'].values())==pytest.approx(1)
        packet['training_dates']=20
        rows.append(dict(scored(day),ensemble_packet=packet))
    assert len(e.eligible(rows,dt.datetime(2026,11,1,tzinfo=dt.timezone.utc)))==20
    s=e.fit(rows,dt.date(2026,11,1))[0]
    assert s['learning_dates']==20 and s['validation_dates']==0 and s['decision']=='held'


@pytest.mark.parametrize('quotes',[{'bid':.5,'ask':.1,'last':.2}, {'bid':None,'ask':None,'last':.2}, {'bid':float('nan'),'ask':.4}])
def test_invalid_or_unquoted_market_stays_uncomparable(quotes):
    r,c,n=original();r['market']['a']=quotes
    packet=e.capture(r,BANDS,'C',c,n)
    assert packet and packet['market_probs'] is None and not packet['market_comparable']


def test_live_provider_names_keep_all_seven_original_sources(monkeypatch):
    monkeypatch.setattr(e,'_states',{})
    r,c,n=original();before=copy.deepcopy(r)
    baseline=e.capture(r,BANDS,'C',c,n)
    inp=r['predictive_context']['forecast_input']['station_combination']
    inp['sources']={'open_meteo_'+k:v for k,v in inp['sources'].items()}
    saved=copy.deepcopy(r)
    packet=e.capture(r,BANDS,'C',c,n)
    assert packet and r==saved
    assert packet['source_values_c']==baseline['source_values_c']
    assert packet['source_probs']==baseline['source_probs']
    assert packet['candidate_probs']==baseline['candidate_probs']
    assert packet['source_names']=={m:'open_meteo_'+m for m in e.MODELS}
    assert baseline['source_names']=={m:m for m in e.MODELS}
    assert before['probs']==r['probs']


@pytest.mark.parametrize('bad',['duplicate','unknown','missing'])
def test_provider_aliases_cannot_merge_or_invent_source_evidence(bad):
    r,c,n=original()
    src=r['predictive_context']['forecast_input']['station_combination']['sources']
    if bad=='duplicate':src['open_meteo_'+e.MODELS[0]]=src[e.MODELS[0]]
    if bad=='unknown':src['other_provider']=21.
    if bad=='missing':src.pop(e.MODELS[0])
    assert e.capture(r,BANDS,'C',c,n) is None


@pytest.mark.parametrize('receipt',['past','missing','future'])
def test_known_rules_receipt_and_provider_payload_reach_capture_together(monkeypatch,receipt):
    import contract_inputs as ci
    import early_city_adaptation as adaptation
    monkeypatch.setattr(adaptation,'_contexts',{})
    monkeypatch.setattr(adaptation,'_states',{})
    monkeypatch.setattr(e,'_states',{})
    r,_,n=original()
    inp=r['predictive_context']['forecast_input']['station_combination']
    inp['sources']={'open_meteo_'+k:v for k,v in inp['sources'].items()}
    stamp=(n+dt.timedelta(hours=1) if receipt=='future' else n-dt.timedelta(hours=1)).isoformat() if receipt!='missing' else None
    rule='https://aviationweather.gov/api/data/metar?ids=TEST&format=raw&date=2026-10-03T00:00:00Z&hours=27'
    def read(table,params):
        assert table=='markets' and 'rules_fetched_at' in dict(params)['select']
        return [dict(market_id='market',event_slug='event',rules_text=rule,rules_fetched_at=stamp)]
    markets=ci.annotate([dict(market_id='market',city_key='city',resolution_date=r['target_date'],unit='C')],read)
    assert markets[0]['rules_received_at']==stamp
    adaptation.prepare(markets,[dict(city_key='city',timezone='UTC',unit='C',icao='TEST')],now=n,read=lambda *a,**k:[])
    packet=e.capture(r,BANDS,'C',adaptation.capture_context('city',r['target_date']),n)
    if receipt=='past':
        assert packet and packet['rules_received_at']==stamp
        assert packet['source_names']=={m:'open_meteo_'+m for m in e.MODELS}
    else:
        assert packet is None
