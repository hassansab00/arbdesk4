"""Shared weather: causal inputs, one fixed bounded fit and honest live clocks."""
import copy
import datetime as dt
import math
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from wxpredict import shared_weather as m,pre_day_hourly as h,pre_day_pairs as pp,shared_weather_live as live
from test_forecast_state import packet,DEFINITIONS,rehash


def evidence(**kwargs):
    p=packet(**kwargs);p['timing_reference']['computed_at']='2026-01-01T00:00:00Z'
    p['city']['display_name']='Synthetic city'
    cutoff=(dt.date.fromisoformat(p['call']['target_date'])+dt.timedelta(days=2)).isoformat()+'T00:00:00Z'
    r,_=m.base.attach(p,DEFINITIONS,cutoff);assert r is not None
    at=pp.timestamp(r['as_of'])-dt.timedelta(minutes=5)
    hours={dt.datetime.fromtimestamp(t,dt.timezone.utc).strftime('%Y-%m-%dT%H:%M'):
        [20+3*math.sin(i/24*math.pi),15,30,100,10,0,1010]
        for i,t in enumerate(h.target_hours(r['date'],r['timezone']))}
    weather=dict(capture_id=1,kind='hourly0',key=r['city']+'|snapshot',captured_at=at.isoformat(),written_at=at.isoformat(),
                 payload=dict(schema='nwp-current-snapshot-v1',hours=hours))
    context,_=h.History([weather]).select(r['city'],r['date'],r['timezone'],r['as_of'])
    return m.attach(r,context),p,weather


def artifact():
    a=dict(policy=m.POLICY,features=list(m.FEATURES),sources=m.source_hashes(),l2=m.L2,
        coefficient_bound=m.BOUND,max_logit_shift=m.LIMIT,coefficients=[0.]*len(m.FEATURES),
        training=dict(through='2026-02-01',evidence_cutoff='2026-02-02T00:00:00Z'),
        available_at='2026-02-03T00:00:00Z',changes_serving=False,eligible_for_promotion=False)
    a['sha256']=pp.digest(a);return a


@pytest.mark.parametrize('zone,day,hours',[('Asia/Riyadh','2026-08-01',24),('Asia/Kathmandu','2026-08-01',24),
    ('America/New_York','2026-11-01',25),('America/New_York','2026-03-08',23)])
def test_actual_city_day_and_received_weather_survive_inference(zone,day,hours):
    r,_,_=evidence(zone=zone,day=day);before=copy.deepcopy(r)
    assert len(r['hourly']['hours'])==hours
    assert len(m.vector(r)[0])==len(m.FEATURES)==35
    assert m.predict(artifact(),[r])[0]==pytest.approx(r['incumbent'],abs=1e-5)
    assert r==before


def test_weather_received_later_cannot_enter_an_earlier_prediction():
    r,_,_=evidence();r['hourly']['hours'][0]['written_at']='2026-08-02T00:00:00Z'
    r['hourly']['sha256']=pp.digest({k:v for k,v in r['hourly'].items() if k!='sha256'});rehash(r)
    with pytest.raises(ValueError,match='arrived late'):m.vector(r)


def test_quotes_labels_and_city_identity_do_not_enter_the_feature_vector():
    r,_,_=evidence();changed=copy.deepcopy(r);changed.update(city='another-city',market=None,label={'winner':'a'})
    rehash(changed);assert m.vector(r)==m.vector(changed)


def test_forward_call_requires_prior_registration_and_timely_capture():
    r,_,_=evidence();a=artifact();at=pp.timestamp(r['as_of'])
    assert len(m.predict(a,[r],forward=True,registered_at='2026-07-31T00:00:00Z',now=at.isoformat()))==1
    for registered,now in [(at+dt.timedelta(seconds=1),at),(at-dt.timedelta(days=1),at+dt.timedelta(seconds=301))]:
        with pytest.raises(ValueError,match='unavailable'):
            m.predict(a,[r],forward=True,registered_at=registered.isoformat(),now=now.isoformat())


def test_source_drift_and_unbounded_coefficients_are_refused():
    r,_,_=evidence();a=artifact();a['sources']['tools/wxpredict/shared_weather.py']='changed'
    a['sha256']=pp.digest({k:v for k,v in a.items() if k!='sha256'})
    with pytest.raises(ValueError,match='identity'):m.predict(a,[r])
    a=artifact();a['coefficients'][0]=m.BOUND+.01;a['sha256']=pp.digest({k:v for k,v in a.items() if k!='sha256'})
    with pytest.raises(ValueError,match='bounds'):m.predict(a,[r])


def test_structural_zeros_remain_zero_and_odds_correction_is_bounded():
    r,_,_=evidence();r['incumbent']['a']=0.;r['incumbent']['c']+=.15;rehash(r)
    a=artifact();a['coefficients']=[m.BOUND]*len(m.FEATURES);a['sha256']=pp.digest({k:v for k,v in a.items() if k!='sha256'})
    q=m.predict(a,[r])[0];assert q['a']==0 and sum(q.values())==pytest.approx(1.)
    assert .25<=(q['b']/q['c'])/(r['incumbent']['b']/r['incumbent']['c'])<=4.


def test_live_morning_requires_both_saved_seasonal_and_forecast_peak_lead():
    r,p,w=evidence();p['call']['predictive_context']={'timing_reference':p['timing_reference']}
    defs=[dict(b,label=bid) for bid,b in DEFINITIONS.items()]
    packet=dict(call=p['call'],city=p['city'],weather=w,bands=defs)
    prepared,why=live.prepare(packet,artifact(),'2026-07-31T00:00:00Z',r['as_of'])
    assert why is None and prepared['context']['weather_peak_lead_h']>1
    # A received forecast already peaking before the morning call is not
    # silently counted as an advance forecast, even with a later seasonal peak.
    for i,(hour,values) in enumerate(w['payload']['hours'].items()):values[0]=30-i
    assert live.prepare(packet,artifact(),'2026-07-31T00:00:00Z',r['as_of'])[1]=='less_than_one_hour_before_forecast_peak'
    p['call']['predictive_context']={}
    assert live.prepare(packet,artifact(),'2026-07-31T00:00:00Z',r['as_of'])[1]=='missing_original_morning_peak_proof'


def test_live_missing_weather_never_gets_a_substitute_prediction():
    _,p,_=evidence()
    assert live.prepare(dict(call=p['call'],city=p['city'],weather=None),artifact(),'2026-07-31T00:00:00Z',p['call']['decided_at'])[1]=='missing_received_current_weather'
