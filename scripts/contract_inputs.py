"""As-received AWC observations for the contract's own routine-report maximum.

Only the existing hourly tick captures. Pricing readers reuse that record;
they never fetch revised history to repair an already frozen prediction.
"""
from __future__ import annotations

import base64
import datetime as dt
import gzip
import hashlib
import math
import time
from concurrent.futures import ThreadPoolExecutor, wait
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

import metar_contract as metar

KIND = "awc_metar"
CONTRACT = "awc-routine-v1"
SCHEMA = "awc-routine-day-v1"
MAX_AGE_S = 90 * 60
CAPTURE_BUDGET_S = 16
MAX_STATIONS = 60
TIMEOUT_S = 10
BATCH_SIZE = 4


def fingerprint(rules):
    return hashlib.sha256((rules or '').encode()).hexdigest()


def version(base, contract, station=None):
    return base + ':' + CONTRACT + (':'+station if station else '') if contract == CONTRACT else base


def annotate(markets, read):
    """Read the saved rules; an unavailable/unknown contract gets no old floor."""
    if not markets:
        return markets
    ids = ','.join(str(m['market_id']) for m in markets)
    try:
        rules = {str(r['market_id']):r for r in read('markets', [
            ('select','market_id,rules_text,event_slug'),('market_id','in.('+ids+')')])}
    except Exception:
        rules = {}
    return [with_rules(dict(row,event_slug=rules.get(str(row['market_id']),{}).get('event_slug') or row.get('event_slug')),
                       rules.get(str(row['market_id']),{}).get('rules_text')) for row in markets]


def with_rules(row, text):
    """Classify only the supplied, received rules; never infer from settlement."""
    contract = 'legacy' if text and ('weather.gov/wrh/timeseries' in text
                                    or 'hong kong observatory' in text.lower()) else 'unknown'
    if metar.source_url(text or ''):
        contract = CONTRACT
    station=None
    if contract==CONTRACT:
        try:
            station=metar.source_parameters(metar.source_url(text))[0]
        except ValueError:
            contract='unknown'
    return dict(row,rules_text=text,observation_contract=contract,rules_sha256=fingerprint(text),
                contract_station=station)


def pack(market, city, raw, source_url, received):
    station = market.get('contract_station') or metar.source_parameters(metar.source_url(market['rules_text']))[0]
    skipped=[]
    rows = metar.raw_reports(raw,station,received,strict=False,skipped=skipped)
    if len(rows)>=400 or len(raw)>100000:
        raise ValueError('AWC observation response may be truncated or oversized')
    if any(r['when']>received for r in rows):
        raise ValueError('AWC observation timestamp is in the future')
    day=str(market['resolution_date'])
    zone=ZoneInfo(city['timezone'])
    eligible=[r for r in rows if r['routine'] and r['temp_c'] is not None
              and r['when'].astimezone(zone).date().isoformat()==day]
    high=max((r['temp_c'] for r in eligible),default=None)
    latest=max(eligible,key=lambda r:r['when']) if eligible else None
    return {'key':market['city_key']+'|'+str(market['resolution_date']),
            'captured_at':received.isoformat(),'payload':{
                'schema':SCHEMA,'city_key':market['city_key'],'date':str(market['resolution_date']),
                'station':station,'timezone':city['timezone'],'rules_sha256':market['rules_sha256'],
                'source_received_at':received.isoformat(),
                'contract_source_url':metar.source_url(market['rules_text']),
                'source_url':source_url,'raw_sha256':hashlib.sha256(raw).hexdigest(),
                'raw_gzip':base64.b64encode(gzip.compress(raw,mtime=0)).decode(),
                'qualifying_max_c':str(high) if high is not None else None,
                'routine_count':len(eligible),
                'malformed_reports':len(skipped),
                'latest_eligible_at':latest['when'].isoformat() if latest else None,
                'latest_eligible_c':str(latest['temp_c']) if latest else None}}


def fetch_batch(batch,now):
    # Validate the contract's station and local-day anchor before asking.
    import weather_outcomes
    stations={}
    hours=1
    for market,city in batch:
        spec=weather_outcomes.parse_rule_spec(market['rules_text'],market.get('unit'))
        station,anchor=metar.validate_window(spec.source_url,dt.date.fromisoformat(str(market['resolution_date'])),city['timezone'])
        end=dt.datetime.combine(dt.date.fromisoformat(str(market['resolution_date']))+dt.timedelta(days=1),
                                dt.time(),ZoneInfo(city['timezone']))
        if spec.unit!=(market.get('unit') or city['unit']):
            raise ValueError('AWC contract date/unit does not match the market')
        stations[station]=(market,city)
        start=end-dt.timedelta(days=1)
        hours=max(hours,math.ceil((now-start).total_seconds()/3600)+1)
    url='https://aviationweather.gov/api/data/metar?'+urlencode({
        'ids':','.join(sorted(stations)),'format':'raw','hours':hours})
    response=requests.get(url,headers={'User-Agent':'ArbDesk4 contract observations'},timeout=TIMEOUT_S)
    response.raise_for_status()
    received=dt.datetime.now(dt.timezone.utc)
    grouped={station:[] for station in stations}
    lines=response.content.decode('utf-8-sig').splitlines()
    if len(lines)>=400 and len(batch)>1:
        # A capped multi-station answer is not a complete day. Smaller queries
        # are bounded by the same worker/request timeout; never use truncation.
        rows,errors=[],[]
        for item in batch:
            got,bad=fetch_batch([item],now)
            rows.extend(got);errors.extend(bad)
        return rows,errors
    for line in lines:
        if not line.strip(): continue
        m=metar.STATION_PREFIX.match(line.strip())
        if m is None or m[1] not in grouped:
            raise ValueError('AWC response contains an unrecognized report or wrong station')
        grouped[m[1]].append(line)
    packed,errors=[],[]
    for station,(market,city) in stations.items():
        try:
            packed.append(pack(market,city,'\n'.join(grouped[station]).encode(),url,received))
        except Exception as exc:
            errors.append(market['city_key']+': '+type(exc).__name__+': '+str(exc)[:160])
    return packed,errors


def capture(markets,cities,now=None,dry_run=False,budget_s=CAPTURE_BUDGET_S):
    """Bounded capture, isolated failures, actual response receipt per snapshot."""
    from common import rpc
    now=now or dt.datetime.now(dt.timezone.utc)
    cities={c['city_key']:c for c in cities}
    chosen={}
    for m in markets:
        c=cities.get(m['city_key'])
        if (m.get('observation_contract')==CONTRACT and c and c.get('timezone') and c.get('icao')
                and str(m['resolution_date'])==now.astimezone(ZoneInfo(c['timezone'])).date().isoformat()):
            chosen.setdefault(m['city_key'],(m,c))
    if len(chosen)>MAX_STATIONS:
        return {'captured':0,'error':'AWC station budget exceeded'}
    started=time.monotonic()
    pool=ThreadPoolExecutor(max_workers=6)
    # Rotate the bounded queue so a persistently slow provider cannot starve
    # the same last cities. Batches cut requests without accepting capped data.
    ordered=sorted(chosen.items())
    offset=int(now.timestamp()//3600)%len(ordered) if ordered else 0
    ordered=ordered[offset:]+ordered[:offset]
    batches=[ordered[i:i+BATCH_SIZE] for i in range(0,len(ordered),BATCH_SIZE)]
    futures={pool.submit(fetch_batch,[pair for _,pair in batch],now):','.join(key for key,_ in batch) for batch in batches}
    done,pending=wait(futures,timeout=max(0,budget_s-2))
    rows,errors=[],[]
    for f in done:
        try:
            got,bad=f.result()
            rows.extend(got);errors.extend(bad)
        except Exception as e:
            errors.append(futures[f]+': '+type(e).__name__+': '+str(e)[:160])
    for f in pending:
        f.cancel()
    pool.shutdown(wait=False,cancel_futures=True)
    captured=0
    if rows and not dry_run:
        try:
            result=rpc('capture_awc_metar',{'p_rows':rows},timeout=max(2,budget_s-(time.monotonic()-started)))
            captured=int(result.get('new') or 0)
        except Exception as e:
            errors.append(type(e).__name__+': '+str(e)[:160])
    return {'requested':len(chosen),'requests':len(batches),'received':len(rows),'captured':captured,
            'deferred':sum(len(name.split(',')) for f,name in futures.items() if f in pending),
            'errors':errors,'seconds':round(time.monotonic()-started,2),'dry_run':dry_run}


def qualifying(payload,received,market,city,as_of):
    """An as-of feature/floor only; never a final outcome or a later correction."""
    if not payload or payload.get('schema')!=SCHEMA:
        return {}
    receipt=dt.datetime.fromisoformat(str(received).replace('Z','+00:00'))
    if not 0<=(as_of-receipt).total_seconds()<=MAX_AGE_S:
        return {}
    station=market.get('contract_station') or city['icao'].upper()
    expected=(market['city_key'],str(market['resolution_date']),station,city['timezone'],market['rules_sha256'])
    actual=tuple(payload.get(k) for k in ('city_key','date','station','timezone','rules_sha256'))
    if actual!=expected:
        return {}
    raw=gzip.decompress(base64.b64decode(payload['raw_gzip']))
    if len(raw)>100000 or hashlib.sha256(raw).hexdigest()!=payload['raw_sha256']:
        raise ValueError('AWC receipt payload checksum mismatch')
    zone=ZoneInfo(city['timezone'])
    skipped=[]
    rows=metar.raw_reports(raw,station,receipt,strict=False,skipped=skipped)
    eligible=[r for r in rows if r['routine'] and r['temp_c'] is not None and r['when']<=receipt
              and r['when'].astimezone(zone).date().isoformat()==str(market['resolution_date'])]
    if not eligible:
        return {}
    high=max(r['temp_c'] for r in eligible)
    at=min(r['when'] for r in eligible if r['temp_c']==high)
    unit=market.get('unit') or city['unit']
    return {'rmax_c':float(high),'rmax_unit':metar.rounded_unit(high,unit),
            'rmax_age_min':(as_of-at).total_seconds()/60,'n_qualifying':len(eligible),
            'captured_at':receipt.isoformat(),'raw_sha256':payload['raw_sha256'],
            'source_url':payload['source_url'],'basis':CONTRACT,
            'station':station,
            'malformed_reports':len(skipped),
            'latest_reading_at':max(r['when'] for r in eligible).isoformat()}


def snapshot_rows(read,now):
    return read('live_capture',[
        ('select','key,captured_at,payload'),('kind','eq.'+KIND),
        ('captured_at','gte.'+(now-dt.timedelta(seconds=MAX_AGE_S)).isoformat()),
        ('captured_at','lte.'+now.isoformat())],order='captured_at.asc,capture_id.asc',page_size=1000)


def floors(markets,cities,legacy,read,now=None,details=None):
    """Keep old-rule floors. AWC/unknown rules never inherit an all-report floor."""
    now=now or dt.datetime.now(dt.timezone.utc)
    cities={c['city_key']:c for c in cities}
    out=dict(legacy)
    active={}
    for m in markets:
        c=cities.get(m['city_key'])
        if c and str(m['resolution_date'])==now.astimezone(ZoneInfo(c['timezone'])).date().isoformat():
            if m.get('observation_contract','legacy')!='legacy':
                out.pop(m['city_key'],None)
            if m.get('observation_contract')==CONTRACT:
                active[m['city_key']]=m
    if not active:
        return out
    try:
        latest={r['key']:r for r in snapshot_rows(read,now)}
        for key,m in active.items():
            row=latest.get(key+'|'+str(m['resolution_date']))
            if row:
                q=qualifying(row['payload'],row['captured_at'],m,cities[key],now)
                if q:
                    out[key]=(str(m['resolution_date']),q['rmax_c'])
                    if details is not None:
                        details[key]=q
    except Exception:
        # Do not restore a disallowed source after a read/parse failure.
        pass
    return out
