"""Prospective complete local-day model maxima from one received snapshot.

Raw current model inputs are a different research family from station-corrected
D-1 forecasts. Never fill a missing hour, borrow a later snapshot or change the
served prediction. The original snapshot remains in the immutable live record.
"""
import copy
import datetime as dt
import hashlib
import json
import math
import time
from common import request_deadline
from zoneinfo import ZoneInfo
from honest_record import MODELS

KIND='models0_raw_local_day'
_inputs,_reasons={},{}


def timestamp(value):
    at=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    return at.replace(tzinfo=dt.timezone.utc) if at.tzinfo is None else at


def pack(row,city,target,now):
    if row['key']!=city['city_key']+'|snapshot':raise ValueError('current model city identity mismatch')
    captured,written=timestamp(row['captured_at']),timestamp(row['written_at'])
    received=max(captured,written)
    if not now-dt.timedelta(hours=8)<=captured<=received<=now:
        raise ValueError('current model receipt stale or future')
    payload=row['payload']
    if payload.get('schema')!='nwp-current-snapshot-v1' or not isinstance(payload.get('hours'),dict):
        raise ValueError('current model snapshot schema unavailable')
    day=dt.date.fromisoformat(str(target));zone=ZoneInfo(city['timezone'])
    if now.astimezone(zone).date()!=day:raise ValueError('current model family requires the local target day')
    first=dt.datetime.combine(day,dt.time(),zone).astimezone(dt.timezone.utc)
    last=dt.datetime.combine(day+dt.timedelta(days=1),dt.time(),zone).astimezone(dt.timezone.utc)
    hour=first.replace(minute=0,second=0,microsecond=0)
    if hour<first:hour+=dt.timedelta(hours=1)
    expected=set()
    while hour<last:
        expected.add(hour);hour+=dt.timedelta(hours=1)
    hours={}
    for key,values in payload['hours'].items():
        stamp=timestamp(key)
        if first<=stamp<last:
            if stamp in hours:raise ValueError('ambiguous current model hour')
            hours[stamp]=values
    if set(hours)!=expected:raise ValueError('incomplete city-local model hours')
    if any(not isinstance(v,list) or len(v)!=len(MODELS) for v in hours.values()):
        raise ValueError('incomplete seven-model hourly vectors')
    if any(x is None or isinstance(x,bool) or not math.isfinite(float(x)) or not -90<=float(x)<=70 for v in hours.values() for x in v):
        raise ValueError('invalid original model temperature')
    values={'open_meteo_'+m:max(float(v[i]) for v in hours.values()) for i,m in enumerate(MODELS)}
    original={k:row[k] for k in ('capture_id','key','captured_at','written_at','payload')}
    sha=hashlib.sha256(json.dumps(original,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()
    return dict(source_kind=KIND,sources=values,inputs_oldest_received_at=received.isoformat(),
        inputs_newest_received_at=received.isoformat(),source_receipt=dict(
            capture_id=row['capture_id'],key=row['key'],captured_at=row['captured_at'],written_at=row['written_at'],
            sha256=sha,schema=payload['schema'],model_order=list(MODELS),timezone=city['timezone'],target_date=str(target),expected_hours=len(expected),
            request_location=copy.deepcopy(payload.get('request_location')),
            response_location=copy.deepcopy(payload.get('response_location'))))


def prepare(contexts,cities,now,read):
    global _inputs,_reasons
    _inputs,_reasons={},{}
    wanted={key for key,c in contexts.items() if c['checkpoint']=='morning' and c.get('contract')=='awc-routine-v1'}
    if not wanted:return
    city_of={c['city_key']:c for c in cities}
    keys=','.join(sorted({city+'|snapshot' for city,_ in wanted}))
    try:
        with request_deadline(time.monotonic()+8.):
            rows=read('live_capture',[('select','capture_id,key,captured_at,written_at,payload'),('kind','eq.models0'),
                ('key','in.('+keys+')'),('captured_at','gte.'+(now-dt.timedelta(hours=8)).isoformat()),
                ('captured_at','lte.'+now.isoformat()),('written_at','lte.'+now.isoformat())],
                order='capture_id.asc',page_size=500)
        latest={}
        for row in rows:
            if not row.get('written_at'):continue
            received=max(timestamp(row['captured_at']),timestamp(row['written_at']))
            if received>now:continue
            key=row['key'];identity=(received,timestamp(row['captured_at']),int(row['capture_id']))
            if key not in latest or identity>latest[key][0]:latest[key]=(identity,row)
        for key in wanted:
            city,target=key;row=latest.get(city+'|snapshot')
            if row is None:_reasons[key]='current seven-model snapshot unavailable';continue
            try:_inputs[key]=pack(row[1],city_of[city],target,now)
            except (KeyError,ValueError,TypeError,OverflowError) as exc:_reasons[key]=str(exc)
    except Exception as exc:
        _reasons={key:'current seven-model read failed: '+type(exc).__name__ for key in wanted}


def get(city,target):return copy.deepcopy(_inputs.get((city,str(target))))
def reason(city,target):return _reasons.get((city,str(target)),'current seven-model snapshot unavailable')
