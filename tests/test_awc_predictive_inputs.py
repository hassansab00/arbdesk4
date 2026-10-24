import copy
import datetime as dt
import hashlib
import sys
from pathlib import Path

import pytest

import contract_inputs as ci

sys.path.insert(0,str(Path(__file__).parents[1]/'tools'))
from wxpredict import live_reader as lr, features

NOW=dt.datetime(2026,10,23,17,tzinfo=dt.timezone.utc)
CITY={'city_key':'nyc','icao':'KLGA','timezone':'America/New_York','unit':'F'}
RULES='https://aviationweather.gov/api/data/metar?ids=KLGA&format=raw&date=2026-10-24T04:00:00Z&hours=27'
MARKET={'city_key':'nyc','resolution_date':'2026-10-23','unit':'F','market_id':'market',
        'observation_contract':ci.CONTRACT,'rules_text':RULES,'rules_sha256':ci.fingerprint(RULES)}
RAW=b'METAR KLGA 231551Z 18/10 RMK T01830100\nSPECI KLGA 231610Z 30/10 RMK T03000100\nKLGA 231617Z 19/10 RMK T01940100'


def packet(raw=RAW,at=NOW):
    return ci.pack(MARKET,CITY,raw,'https://aviationweather.gov/api/data/metar?ids=KLGA&format=raw',at)


def test_authority_max_is_routine_and_precise_while_raw_special_is_preserved():
    p=packet()
    q=ci.qualifying(p['payload'],p['captured_at'],MARKET,CITY,NOW)
    assert q['rmax_c']==19.4 and q['rmax_unit']==67 and q['n_qualifying']==2
    assert p['payload']['qualifying_max_c']=='19.4'
    assert p['payload']['routine_count']==2


def test_later_correction_cannot_rewrite_the_input_available_at_freeze():
    old=packet()
    changed=packet(RAW.replace(b'T01940100',b'T02110100'),NOW+dt.timedelta(minutes=20))
    record=lr.Record([(ci.KIND,p['key'],p['captured_at'],p['payload']) for p in (old,changed)])
    receipt,payload=record.authority(old['key'],NOW.timestamp())
    assert ci.qualifying(payload,dt.datetime.fromtimestamp(receipt,dt.timezone.utc).isoformat(),MARKET,CITY,NOW)['rmax_c']==19.4
    assert record.authority(old['key'],NOW.timestamp()-1) is None
    assert ci.qualifying(changed['payload'],changed['captured_at'],MARKET,CITY,NOW)=={}


@pytest.mark.parametrize('change', ['rules','station','date','age','checksum'])
def test_wrong_or_stale_authority_input_does_not_become_a_floor(change):
    p=packet(); market=dict(MARKET); at=NOW
    if change=='rules': market['rules_sha256']='0'*64
    if change=='station': p['payload']['station']='KJFK'
    if change=='date': market['resolution_date']='2026-10-22'
    if change=='age': at=NOW+dt.timedelta(hours=2)
    if change=='checksum':
        p['payload']['raw_sha256']='0'*64
        with pytest.raises(ValueError,match='checksum'):
            ci.qualifying(p['payload'],p['captured_at'],market,CITY,at)
        return
    assert ci.qualifying(p['payload'],p['captured_at'],market,CITY,at)=={}


def test_missing_authority_or_rules_cannot_inherit_the_old_special_report_floor():
    read=lambda *a,**k:[]
    old={'nyc':('2026-10-23',30)}
    assert ci.floors([MARKET],[CITY],old,read,NOW)=={}
    unknown=dict(MARKET,observation_contract='unknown')
    assert ci.floors([unknown],[CITY],old,read,NOW)=={}
    legacy=dict(MARKET,observation_contract='legacy')
    assert ci.floors([legacy],[CITY],old,read,NOW)==old
    p=packet()
    read=lambda *a,**k:[p]
    assert ci.floors([MARKET],[CITY],old,read,NOW)=={'nyc':('2026-10-23',19.4)}


def test_input_regime_has_its_own_calibration_version():
    import wx_city_calibration as cal
    v='wxrd3:v3:fixture'
    newer=ci.version(v,ci.CONTRACT)
    assert newer!=v and ci.version(v,'legacy')==v
    state=dict(model_version=v,as_of='2026-10-22',validated_through='2026-10-21',params={'x':1},version='fit')
    assert cal.usable(state,'2026-10-23',v)
    assert not cal.usable(state,'2026-10-23',newer)
    assert ci.version(v,ci.CONTRACT,'KDEN')!=ci.version(v,ci.CONTRACT,'KBKF')


def test_contract_station_overrules_a_different_legacy_city_airport():
    market=dict(MARKET,city_key='denver',contract_station='KDEN',rules_text=RULES.replace('KLGA','KDEN'))
    market['rules_sha256']=ci.fingerprint(market['rules_text'])
    city=dict(CITY,city_key='denver',icao='KBKF')
    p=ci.pack(market,city,RAW.replace(b'KLGA',b'KDEN'),'https://aviationweather.gov/api/data/metar?ids=KDEN',NOW)
    assert p['payload']['station']=='KDEN'
    assert ci.qualifying(p['payload'],p['captured_at'],market,city,NOW)['station']=='KDEN'


def test_malformed_observation_does_not_cancel_valid_report_floors():
    p=packet(RAW+b'\nMETAR KLGA 23120Z 30/10')
    q=ci.qualifying(p['payload'],p['captured_at'],MARKET,CITY,NOW)
    assert q['rmax_c']==19.4 and q['malformed_reports']==1


def test_unavailable_rule_lookup_is_explicitly_unknown():
    def unavailable(*a): raise RuntimeError('unavailable')
    annotated=ci.annotate([{'market_id':'m','city_key':'nyc'}],unavailable)
    assert annotated[0]['observation_contract']=='unknown'


def test_qualifying_override_keeps_weather_temperature_and_removes_six_hour_extreme(monkeypatch):
    def observations(v,*a):
        v.update(rmax_c=30,rmax_unit=86,rmax_bucket=3,rmax_age_min=2,rmax6_c=35,t_now_c=30)
    monkeypatch.setattr(features,'obs_features',observations)
    monkeypatch.setattr(features,'fc_features',lambda *a:None)
    monkeypatch.setattr(features,'bias_features',lambda *a:None)
    monkeypatch.setattr(features,'market_features',lambda *a:None)
    from zoneinfo import ZoneInfo
    day=dt.date(2026,10,23)
    e=dict(event_id='m',source='venue',city='nyc',station='KLGA',date=str(day),unit='F',winner=None)
    ctx=dict(day=day,bands=[(None,66),(66,68),(68,None)],unit='F',d0=0,winner_lo=None,winner_hi=None,
             station_max_unit=None,station_max_c=None,station_in_winner=None,station_reports_day=0,
             label_unit=None,clim_with=(1,1,1,1),clim_without=(1,1,1,1),dm2_known_from=0)
    args=(e,ctx,0,NOW.astimezone(ZoneInfo(CITY['timezone'])),None,{}, {},{},{},[],ZoneInfo(CITY['timezone']))
    q=ci.qualifying(packet()['payload'],NOW.isoformat(),MARKET,CITY,NOW)
    row=features.decision_row(*args,qualifying=q)
    assert row['t_now_c']==30 and row['rmax_c']==19.4 and row['rmax_unit']==67
    assert row['rmax6_c'] is None
    missing=features.decision_row(*args,qualifying={})
    assert missing['t_now_c']==30 and missing['rmax_c'] is None
