"""Morning research retains complete original current model snapshots only."""
import copy
import datetime as dt
from zoneinfo import ZoneInfo
import pytest
import morning_ensemble as m
import ensemble_early as e
from test_ensemble_early import original,BANDS

UTC=dt.timezone.utc

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(m,'_inputs',{})
    monkeypatch.setattr(m,'_reasons',{})
    monkeypatch.setattr(e,'_states',{})


def case(day='2026-10-02',zone='UTC'):
    city=dict(city_key='city',timezone=zone)
    d=dt.date.fromisoformat(day)
    first=dt.datetime.combine(d,dt.time(),ZoneInfo(zone)).astimezone(UTC)
    last=dt.datetime.combine(d+dt.timedelta(days=1),dt.time(),ZoneInfo(zone)).astimezone(UTC)
    now=dt.datetime.combine(d,dt.time(9,36),ZoneInfo(zone)).astimezone(UTC)
    hour=first.replace(minute=0,second=0,microsecond=0)
    if hour<first:hour+=dt.timedelta(hours=1)
    hours={}
    while hour<last:
        hours[hour.isoformat()]=[20.+i/10+len(hours)/100 for i in range(7)]
        hour+=dt.timedelta(hours=1)
    row=dict(capture_id=1,key='city|snapshot',captured_at=(now-dt.timedelta(hours=1)).isoformat(),
        written_at=(now-dt.timedelta(minutes=59)).isoformat(),payload=dict(schema='nwp-current-snapshot-v1',hours=hours,
        request_location=dict(latitude=1.,longitude=2.),response_location=dict(latitude=1.1,longitude=2.1)))
    return city,row,now


@pytest.mark.parametrize('day,zone,hours',[('2026-10-02','UTC',24),('2026-10-25','Europe/Paris',25),
    ('2026-03-29','Europe/Paris',23),('2026-10-02','Asia/Kolkata',24),('2026-10-02','Asia/Kathmandu',24)])
def test_complete_local_day_and_fractional_clocks_preserve_original_receipts(day,zone,hours):
    city,row,now=case(day,zone);before=copy.deepcopy(row)
    packet=m.pack(row,city,day,now)
    assert row==before and packet['source_receipt']['expected_hours']==hours
    assert packet['source_receipt']['request_location']==row['payload']['request_location']
    assert packet['inputs_newest_received_at']==row['written_at']
    assert packet['sources']['open_meteo_'+m.MODELS[0]]==20.+(hours-1)/100


@pytest.mark.parametrize('bad',['hour','model','nan','future','stale','schema','written'])
def test_missing_or_invalid_original_input_is_not_filled(bad):
    city,row,now=case();hour=next(iter(row['payload']['hours']))
    if bad=='hour':row['payload']['hours'].pop(hour)
    if bad=='model':row['payload']['hours'][hour][0]=None
    if bad=='nan':row['payload']['hours'][hour][0]=float('nan')
    if bad=='future':row['written_at']=(now+dt.timedelta(seconds=1)).isoformat()
    if bad=='stale':row['captured_at']=(now-dt.timedelta(hours=9)).isoformat()
    if bad=='schema':row['payload']['schema']='unknown'
    if bad=='written':row['written_at']=None
    with pytest.raises((ValueError,TypeError)):m.pack(row,city,'2026-10-02',now)


def test_latest_received_snapshot_never_borrows_old_hours_and_read_is_scoped():
    city,row,now=case();new=copy.deepcopy(row);new['capture_id']=2
    new['written_at']=(now-dt.timedelta(minutes=1)).isoformat()
    new['payload']['hours'].pop(next(iter(new['payload']['hours'])))
    contexts={('city','2026-10-02'):dict(checkpoint='morning',contract='awc-routine-v1')}
    seen=[]
    def read(table,params,**kwargs):seen.append((table,params));return [row,new]
    m.prepare(contexts,[city],now,read)
    assert len(seen)==1 and seen[0][0]=='live_capture'
    assert dict(seen[0][1])['key']=='in.(city|snapshot)'
    assert m.get('city','2026-10-02') is None and 'incomplete' in m.reason('city','2026-10-02')
    m.prepare({},[city],now,read)
    assert len(seen)==1


@pytest.mark.parametrize('unit',['C','F'])
def test_morning_packet_is_a_separate_raw_family_and_never_changes_served_call(unit):
    city,snapshot,now=case()
    m.prepare({('city','2026-10-02'):dict(checkpoint='morning',contract='awc-routine-v1')},[city],now,lambda *a,**k:[snapshot])
    row,ctx,_=original();row['checkpoint']='morning';row['predictive_context']={};row['sigma_c']=.01
    bands=BANDS if unit=='C' else [dict(band_id='a',band_lo=None,band_hi=68,open_low=True,open_high=False),dict(band_id='b',band_lo=68,band_hi=70,open_low=False,open_high=False),dict(band_id='c',band_lo=70,band_hi=None,open_low=False,open_high=True)]
    before=copy.deepcopy(row)
    packet=e.capture(row,bands,unit,ctx,now)
    assert packet['source_probs'][e.MODELS[0]]['b']>.99
    assert packet and row==before
    assert packet['source_kind']==m.KIND and packet['input_timestamp_basis']=='server_receipt'
    assert packet['model_version']!=e.family('TEST','C') and packet['changes_serving'] is False
    assert set(packet['source_probs'])==set(e.MODELS)==set(m.MODELS)
    assert tuple(packet['source_receipt']['model_order'])==e.MODELS==m.MODELS
    scored=dict(row,ensemble_packet=packet,verified=True,settled=True,winner='b',
        frozen_at=(now+dt.timedelta(seconds=1)).isoformat(),settled_at='2026-10-02T23:58:00Z',label_received_at='2026-10-02T23:59:00Z')
    assert len(e.eligible([scored],dt.datetime(2026,10,3,tzinfo=UTC)))==1
    broken=copy.deepcopy(scored);broken['ensemble_packet']['source_receipt']['target_date']='2026-10-01'
    assert not e.eligible([broken],dt.datetime(2026,10,3,tzinfo=UTC))


def test_morning_preparation_wires_the_actual_snapshot_reader():
    import early_city_adaptation as a
    city,snapshot,now=case();city.update(icao='TEST',unit='C')
    market=dict(city_key='city',resolution_date='2026-10-02',unit='C',observation_contract='awc-routine-v1',
        contract_station='TEST',rules_sha256='a'*64,rules_received_at=(now-dt.timedelta(hours=2)).isoformat())
    def read(table,*args,**kwargs):return [snapshot] if table=='live_capture' else []
    a.prepare([market],[city],now,read)
    assert m.get('city','2026-10-02') and a.capture_context('city','2026-10-02')['checkpoint']=='morning'


def test_wrong_city_future_target_and_failed_reads_cannot_collect():
    city,row,now=case();bad=copy.deepcopy(row);bad['key']='other|snapshot'
    with pytest.raises(ValueError,match='city identity'):m.pack(bad,city,'2026-10-02',now)
    with pytest.raises(ValueError,match='local target day'):m.pack(row,city,'2026-10-03',now)
    def unavailable(*args,**kwargs):raise TimeoutError('bounded read')
    m.prepare({('city','2026-10-02'):dict(checkpoint='morning',contract='awc-routine-v1')},[city],now,unavailable)
    assert m.get('city','2026-10-02') is None and 'read failed' in m.reason('city','2026-10-02')
    m.prepare({('city','2026-10-02'):dict(checkpoint='morning',contract='unknown')},[city],now,unavailable)
    assert not m._reasons
