import copy
import datetime as dt
import pytest
from day_max_prediction import choices, original_calls, signal_for, POLICY

NOW=dt.datetime(2026,10,30,12,40,tzinfo=dt.timezone.utc)

def fixture():
    config=dict(policy=POLICY,activated_at='2026-10-29T00:00:00Z',cities=[dict(city_key='city',checkpoint='noon',stake_usd=10)])
    p=dict(checkpoint_id='original',city_key='city',target_date='2026-10-30',checkpoint='noon',
        local_decision_time='2026-10-30T12:00:00',decided_at='2026-10-30T12:35:00Z',frozen_at='2026-10-30T12:35:00Z',
        inputs_ok=True,probs={'a':.7,'b':.3},top_band_id='a',predictive_context={'clim_peak_hour':14},
        forecast_issued_at='2026-10-30T06:00:00Z')
    return config,p,{'city':{'timezone':'UTC'}}

def test_first_original_call_survives_later_perfect_replacement():
    config,p,cities=fixture()
    newer={**p,'checkpoint_id':'replacement','decided_at':'2026-10-30T12:36:00Z','probs':{'a':0,'b':1},'top_band_id':'b'}
    rows,skips=original_calls([newer,p],config,cities,NOW)
    assert rows==[p] and not skips

@pytest.mark.parametrize('change',[
    {'frozen_at':None},{'frozen_at':'2026-10-30T13:00:00Z'},{'inputs_ok':False},
    {'predictive_context':{}},{'predictive_context':{'clim_peak_hour':12.5}},
    {'probs':{'a':float('nan')}},{'probs':{'a':.1,'b':.9}},
    {'decided_at':'2026-10-30T11:59:00Z'},
])
def test_bad_late_or_missing_original_is_not_a_trade(change):
    config,p,cities=fixture();p.update(change)
    rows,skips=original_calls([p],config,cities,NOW)
    assert rows==[] and skips

def test_before_activation_no_backfill_and_scope_is_fixed():
    config,p,cities=fixture();config['activated_at']=NOW.isoformat()
    assert not original_calls([p],config,cities,NOW)[0]
    config,p,cities=fixture();p['checkpoint']='morning'
    assert not original_calls([p],config,cities,NOW)[0]
    config['cities'].append(copy.deepcopy(config['cities'][0]))
    with pytest.raises(ValueError): choices(config)

def test_source_packet_keeps_forecast_time_and_probability():
    _,p,_=fixture()
    b=dict(band_id='a',market_id='m')
    m=dict(market_id='m',city_key='city',resolution_date=p['target_date'],unit='C',closed=False)
    packet=signal_for(p,b,m,NOW)['payload']['decision_inputs']['a']
    assert packet['model_prob_yes']==.7
    assert packet['decision_evidence']['forecast']['run_at']==p['forecast_issued_at']
    p['forecast_issued_at']='2026-10-30T13:00:00Z'
    with pytest.raises(ValueError,match='timestamp'): signal_for(p,b,m,NOW)


def test_day_ahead_uses_original_pre_midnight_input_not_current_forecast():
    config,p,cities=fixture();config['cities'][0]['checkpoint']='day_ahead'
    p.update(checkpoint='day_ahead',local_decision_time='2026-10-30T00:00:00',
        decided_at='2026-10-30T00:37:00Z',frozen_at='2026-10-30T00:37:00Z',
        forecast_issued_at='2026-10-29T18:00:00Z')
    p['predictive_context'].update(source_kind='day_max_day_ahead_calls',source_computed_at='2026-10-29T23:00:00Z',cutoff_at='2026-10-30T00:00:00Z')
    now=dt.datetime(2026,10,30,0,40,tzinfo=dt.timezone.utc)
    assert original_calls([p],config,cities,now)[0]==[p]
    for change in [dict(source_computed_at='2026-10-30T00:01:00Z'),dict(cutoff_at='2026-10-29T00:00:00Z'),dict(source_kind='rebuild')]:
        bad=copy.deepcopy(p);bad['predictive_context'].update(change)
        assert not original_calls([bad],config,cities,now)[0]
    config['selection_changed_at']='2026-10-30T00:38:00Z'
    assert not original_calls([p],config,cities,now)[0]


def test_new_server_day_ahead_receipt_is_eligible_in_the_same_worker_run(monkeypatch):
    import day_max_prediction as worker
    real_datetime=dt.datetime
    before=real_datetime(2026,10,30,0,37,tzinfo=dt.timezone.utc)
    after=before+dt.timedelta(seconds=2)
    ticks=iter([before,after])
    class Clock(real_datetime):
        @classmethod
        def now(cls,tz=None):return next(ticks)
    config,p,cities=fixture();config['cities'][0]['checkpoint']='day_ahead'
    p.update(checkpoint='day_ahead',local_decision_time='2026-10-30T00:00:00',
        decided_at='2026-10-30T00:37:01Z',frozen_at='2026-10-30T00:37:01Z',forecast_issued_at='2026-10-29T18:00:00Z')
    p['predictive_context'].update(source_kind='day_max_day_ahead_calls',source_computed_at='2026-10-29T23:00:00Z',cutoff_at='2026-10-30T00:00:00Z')
    def read(table,params):
        if table=='strategies':return [dict(enabled=True,extra=dict(day_max=config))]
        if table=='day_max_prediction_entries':return []
        if table=='paper_accounts':return [dict(mode='automatic',entries_paused=True)]
        raise AssertionError(table)
    captures=[]
    monkeypatch.setattr(worker.dt,'datetime',Clock)
    monkeypatch.setattr(worker,'get_cities',lambda **kw:[dict(city_key='city',timezone='UTC')])
    monkeypatch.setattr(worker,'rest',read)
    monkeypatch.setattr(worker,'rest_all',lambda *a,**kw:[p])
    monkeypatch.setattr(worker,'rpc',lambda name,body:captures.append(name) or dict(captured=True))
    monkeypatch.setattr(worker,'log_run',lambda *a:None)
    result=worker.run()
    assert captures==['capture_day_max_day_ahead']
    assert result['candidates']==1 and not result['skipped']
