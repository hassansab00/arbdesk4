"""Receipt integrity, complete local days, model isolation and capture parity."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from wxpredict import pre_day_hourly as h, pre_day_conditioned as m, pre_day_pairs as pp
from wxpredict import pre_day_rich_study as study, live_reader as lr,current_forecasts
import live_capture as capture
from test_pre_day_weather import record

UTC=dt.timezone.utc


def weather(r,kind='hourly2'):
    f=r['features'];at=pp.timestamp(f['as_of'])-dt.timedelta(hours=1)
    rows=[]
    for stamp in h.target_hours(f['date'],f['timezone']):
        local=dt.datetime.fromtimestamp(stamp,ZoneInfo(f['timezone']))
        temp=28.-abs(local.hour+local.minute/60-12.)
        rows.append(dict(kind=kind,key=f['city']+'|'+dt.datetime.fromtimestamp(stamp,UTC).strftime('%Y-%m-%dT%H:%M'),
                    captured_at=at.isoformat(),written_at=(at+dt.timedelta(seconds=1)).isoformat(),
                    payload=[temp,12.,20.,400.,10.,0.,1010.]))
    return rows


def rich(day='2026-08-01',city='synthetic',zone='UTC'):
    r=record(day,city,zone);f=r['features']
    context,_=h.History(weather(r)).select(f['city'],f['date'],zone,f['as_of'])
    r.update(hourly=context,observation_contract='unknown',original_centre_c=20.6,raw_audit=None)
    r['research_sha256']=pp.digest(r)
    return r


@pytest.mark.parametrize('day,zone,expected',[
    ('2026-03-08','America/New_York',23),('2026-11-01','America/New_York',25),
    ('2026-08-01','Asia/Riyadh',24),('2026-08-01','Asia/Kolkata',24),
    ('2026-08-01','Pacific/Auckland',24)])
def test_received_weather_uses_the_entire_local_day_and_real_peak(day,zone,expected):
    r=rich(day,zone=zone);context=r['hourly'];h.validate(context,r['features'])
    assert context['expected_hours']==expected
    assert len(context['hours'])==len(set(x['utc'] for x in context['hours']))==expected
    assert context['features']['weather_peak_hour']==pytest.approx(12 if zone!='Asia/Kolkata' else 11.5)
    assert context['features']['weather_rain_mm']==0


def test_server_receipt_after_decision_cannot_fill_an_earlier_day():
    r=record();rows=weather(r);rows[-1]['written_at']='2026-07-31T18:38:00Z'
    assert h.History(rows).select('synthetic','2026-08-01','UTC',r['features']['as_of'])[0] is None
    rows=weather(r);rows[-1]['written_at']=None
    history=h.History(rows);assert history.missing_receipts==1
    assert history.select('synthetic','2026-08-01','UTC',r['features']['as_of'])[0] is None


def test_atomic_current_snapshot_does_not_borrow_an_omitted_hour_from_older_snapshot():
    r=record();rows=weather(r);f=r['features']
    hours={v['key'].split('|',1)[1]:v['payload'] for v in rows}
    def snap(at,values):return dict(kind='hourly0',key='synthetic|snapshot',captured_at=at,written_at=at,
                                   payload=dict(schema='nwp-current-snapshot-v1',hours=values))
    old=snap('2026-07-31T16:00:00Z',hours)
    partial=dict(hours);partial.pop(next(iter(partial)))
    newer=snap('2026-07-31T17:00:00Z',partial)
    assert h.History([old,newer]).select(f['city'],f['date'],'UTC',f['as_of'])[0] is None
    context,_=h.History([old,newer]+rows).select(f['city'],f['date'],'UTC',f['as_of'])
    assert context['kind']=='hourly2', 'a different complete recorded source is explicit'


def test_conflicting_latest_hour_is_quarantined_instead_of_using_earlier_value():
    r=record();rows=weather(r);previous=copy.deepcopy(rows[0])
    previous['captured_at']=previous['written_at']='2026-07-31T15:00:00Z'
    bad=copy.deepcopy(rows[0]);bad['payload'][0]+=1
    history=h.History([previous]+rows+[bad]);assert history.conflicts==1
    assert history.select('synthetic','2026-08-01','UTC',r['features']['as_of'])[0] is None


def test_hourly_context_and_research_row_are_hash_checked():
    r=rich();r['hourly']['features']['weather_tmax_c']+=1
    with pytest.raises(ValueError,match='hash'):m.validate(r)
    r=rich();r['hourly']['hours'][0]['written_at']='2026-08-01T00:00:00Z'
    r['hourly']['sha256']=pp.digest({k:v for k,v in r['hourly'].items() if k!='sha256'})
    with pytest.raises(ValueError,match='late'):m.validate(r)
    r=rich();r['observation_contract']='made-up'
    with pytest.raises(ValueError,match='research row'):m.validate(r)
    r=rich();r['rules_context']=dict(received_at='2026-07-31T18:38:00Z',contract='unknown',station=None)
    r['research_sha256']=pp.digest({k:v for k,v in r.items() if k!='research_sha256'})
    with pytest.raises(ValueError,match='noncausal'):m.validate(r)


def test_model_features_exclude_outcomes_market_and_incumbent_metadata():
    pytest.importorskip('numpy');pytest.importorskip('pandas')
    from pandas.testing import assert_frame_equal
    a=rich();b=copy.deepcopy(a)
    b['market']={'low':1.,'mid':0.,'high':0.};b['incumbent']=dict(b['market'])
    b['original']['top']='low';b['label']['winner']='high';b['original_centre_c']=60
    b['raw_audit']={'untrusted':'do not read as a feature'}
    b['research_sha256']=pp.digest({k:v for k,v in b.items() if k!='research_sha256'})
    assert_frame_equal(m.matrix([a])[0],m.matrix([b])[0])


def test_unknown_weather_is_explicit_and_does_not_become_zero_weather():
    pytest.importorskip('pandas');pytest.importorskip('numpy')
    r=rich();r['hourly']=None;r['research_sha256']=pp.digest({k:v for k,v in r.items() if k!='research_sha256'})
    x,_,_,_=m.matrix([r]);assert list(x.hourly_available)==[0,0,0]
    assert x.weather_cloud_peak.isna().all() and str(x.hourly_source.iloc[0])=='missing'


def test_conditioned_fit_and_forward_guards(monkeypatch):
    pytest.importorskip('lightgbm');pytest.importorskip('pandas');pytest.importorskip('numpy')
    rows=[rich((dt.date(2026,8,1)+dt.timedelta(days=i)).isoformat(),'c'+str(j)) for i in range(20) for j in range(20)]
    artifact=m.fit(rows);assert artifact['training']['rich_dates']==20
    assert artifact['changes_serving'] is False and artifact['eligible_for_promotion'] is False
    p=m.predictions(artifact,[rows[0]])[0];assert sum(p['probs'].values())==pytest.approx(1)
    loaded=json.loads(json.dumps(artifact));assert m.predictions(loaded,[rows[0]])==[p]
    with pytest.raises(ValueError,match='backdated'):m.predictions(artifact,[rows[0]],forward=True)
    bad=copy.deepcopy(artifact);bad['model_text']+='bad'
    with pytest.raises(ValueError,match='integrity'):m.predictions(bad,[rows[0]])
    future=rich((dt.datetime.now(UTC).date()+dt.timedelta(days=2)).isoformat());future['label']=None
    future['research_sha256']=pp.digest({k:v for k,v in future.items() if k!='research_sha256'})
    at=pp.timestamp(future['features']['as_of'])
    monkeypatch.setattr(m.base,'utc_now',lambda:at+dt.timedelta(minutes=1))
    assert m.predictions(artifact,[future],forward=True)[0]['probs']
    monkeypatch.setattr(m.base,'utc_now',lambda:at+dt.timedelta(minutes=16))
    with pytest.raises(ValueError,match='late'):m.predictions(artifact,[future],forward=True)
    for r in rows:
        r['hourly']=None;r['research_sha256']=pp.digest({k:v for k,v in r.items() if k!='research_sha256'})
    with pytest.raises(ValueError,match='complete received'):m.fit(rows)


def test_miss_distance_is_only_a_lower_bound_and_open_ends_are_valid():
    r=rich();assert study.interval_distance_c(20.,r)==0
    assert study.interval_distance_c(15.,r)==4.5
    r['label']['winner']='high';assert study.interval_distance_c(30.,r)==0
    assert study.interval_distance_c(None,r) is None


def test_rule_receipts_need_exact_identity_and_must_precede_the_decision():
    r=record();market=dict(market_id='market',city_key='synthetic',resolution_date='2026-08-01')
    bands=[dict(market_id='market',band_id=bid) for bid in r['band_ids']]
    receipt=dict(market_id='market',received_at='2026-07-31T18:38:00Z',rules_text='https://weather.gov/wrh/timeseries')
    rules=h.Rules([market],bands,[receipt]);assert rules.select(r) is None
    receipt['received_at']='2026-07-31T18:36:00Z'
    rules=h.Rules([market],bands,[receipt]);assert rules.select(r)['contract']=='legacy'
    assert rules.select(r)['rules_sha256']
    market['city_key']='different'
    with pytest.raises(ValueError,match='another city'):rules.select(r)


@pytest.mark.parametrize('zone,when',[
    ('Asia/Riyadh','2026-10-31T15:37:00Z'),('America/Los_Angeles','2026-11-01T01:37:00Z'),
    ('America/New_York','2026-10-31T22:37:00Z'),('Asia/Kolkata','2026-10-31T13:07:00Z')])
def test_current_capture_has_complete_today_and_tomorrow_without_extra_requests(monkeypatch,zone,when):
    now=pp.timestamp(when);city=dict(city_key='synthetic',latitude=1,longitude=2,timezone=zone)
    first,last=capture.current_window(city,now)
    stamps=[];at=first
    while at<=last:stamps.append(at.strftime('%Y-%m-%dT%H:%M'));at+=dt.timedelta(hours=1)
    asked=[]
    def ask(chunk,params,left):
        asked.append(params)
        return [{'latitude':1.1,'longitude':2.1,'elevation':10.,'hourly':{'time':stamps,**{capture.series_name(v,lead):[float(i%10+10) for i in range(len(stamps))]
                for v in capture.honest_record.HEATING for lead in capture.CAPTURE_HORIZONS}}}]
    monkeypatch.setattr(capture,'_ask',ask)
    batches,detail=capture.fetch_hourly([city],now,lambda:60)
    assert len(asked)==1 and detail['answered']==1
    snapshot=capture.current_snapshots(batches['hourly0'][0][1])[0]['payload']
    assert snapshot['request_location']=={k:city[k] for k in ('latitude','longitude','timezone')}
    assert snapshot['response_location']=={'latitude':1.1,'longitude':2.1,'elevation':10.}
    day=now.astimezone(ZoneInfo(zone)).date()
    recorded={h.hour(t) for t in snapshot['hours']}
    for date in (day,day+dt.timedelta(days=1)):
        assert set(h.target_hours(date.isoformat(),zone))<=recorded
    assert len(recorded) in (47,48,49), 'bounded two-day snapshot, including DST'


def test_current_window_does_not_change_same_day_context():
    now=pp.timestamp('2026-10-31T15:37:00Z');city=dict(city_key='synthetic',timezone='Asia/Riyadh')
    old_first,old_last=capture.hourly_window(now,24);first,last=capture.current_window(city,now)
    hours={};at=min(old_first,first)
    while at<=max(old_last,last):hours[at.isoformat()]=[20.,10.,50.,400.,10.,0.,1010.];at+=dt.timedelta(hours=1)
    def context(start,end):
        selected={t:v for t,v in hours.items() if start<=dt.datetime.fromisoformat(t)<=end}
        record=lr.Record([('hourly0','synthetic|snapshot',now.isoformat(),dict(schema='nwp-current-snapshot-v1',hours=selected))])
        return current_forecasts.context(record,{'city':'synthetic','date':'2026-10-31'},now.timestamp(),ZoneInfo(city['timezone']))
    assert context(first,last)==context(old_first,old_last)


def test_timezone_failure_is_isolated_and_snapshot_metadata_cannot_conflict():
    now=pp.timestamp('2026-10-31T15:37:00Z')
    assert capture.current_window({'timezone':'Unknown/zone'},now)==capture.hourly_window(now,24)
    rows=[dict(key='synthetic|2026-10-31T01:00',payload=[20],request_location={'latitude':1}),
          dict(key='synthetic|2026-10-31T02:00',payload=[20],request_location={'latitude':2})]
    with pytest.raises(ValueError,match='locations'):capture.current_snapshots(rows)
