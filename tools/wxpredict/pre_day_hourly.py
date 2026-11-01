"""Received pre-day hourly weather context; no historical publication guesses.

Current snapshots remain atomic. Change-only hourly archives retain the last
actually received value of each hour. A source is used only when all target
local-day UTC hours and all seven weather variables are present and timely.
"""
import bisect
import collections
import datetime as dt
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from wxpredict import pre_day_pairs as pp, pre_day_weather_data as data

POLICY = 'pre-day-received-hourly-v1'
KINDS = ('hourly0','hourly2','hourly')
FIELDS = ('weather_tmax_c','weather_range_c','weather_peak_hour','weather_cloud_peak',
          'weather_solar_to_peak','weather_wind_peak','weather_dewpoint_peak',
          'weather_rain_mm','weather_pressure_change','weather_receipt_age_h')
MAX_AGE_H = 36.


def hour(value):
    t=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.utcoffset() is None:t=t.replace(tzinfo=dt.timezone.utc) # source API is explicitly GMT
    t=t.astimezone(dt.timezone.utc)
    if t.minute or t.second or t.microsecond:raise ValueError('hourly input is not UTC-hour aligned')
    return int(t.timestamp())


def target_hours(day,timezone):
    date=dt.date.fromisoformat(day);zone=ZoneInfo(timezone)
    start=dt.datetime.combine(date,dt.time(),zone).timestamp()
    end=dt.datetime.combine(date+dt.timedelta(days=1),dt.time(),zone).timestamp()
    return list(range(math.ceil(start/3600)*3600,math.ceil(end/3600)*3600,3600))


class History:
    def __init__(self, rows):
        versions={};conflicts=set();self.missing_receipts=0
        for row in rows:
            kind=row['kind']
            if kind not in KINDS:continue
            if not row.get('written_at'):
                self.missing_receipts+=1;continue
            captured=pp.timestamp(row['captured_at']);written=pp.timestamp(row['written_at'])
            available=max(captured,written).timestamp()
            city,key=row['key'].split('|',1)
            payload=pp.obj(row['payload']);identity=(kind,city,key,row['captured_at'])
            item=dict(available=available,captured_at=row['captured_at'],written_at=row['written_at'],
                      payload=payload,sha256=pp.digest(row))
            if identity in versions and versions[identity]['payload']!=payload:conflicts.add(identity)
            elif identity not in versions or versions[identity]['available']<available:versions[identity]=item
        self.conflicts=len(conflicts);self.hours=collections.defaultdict(list);self.snapshots=collections.defaultdict(list)
        for (kind,city,key,_),item in versions.items():
            snapshot=kind=='hourly0' and isinstance(item['payload'],dict)
            if (kind,city,key,item['captured_at']) in conflicts:
                item=dict(item,payload=None,conflicted=True)
            if snapshot:
                if item.get('conflicted'):
                    self.snapshots[(kind,city)].append(item);continue
                if item['payload'].get('schema')!='nwp-current-snapshot-v1':raise ValueError('unknown received hourly schema')
                self.snapshots[(kind,city)].append(item)
            else:
                self.hours[(kind,city,hour(key))].append(item)
        for seq in [*self.hours.values(),*self.snapshots.values()]:seq.sort(key=lambda x:(x['available'],x['captured_at']))

    @staticmethod
    def last(seq,at):
        i=bisect.bisect_right([v['available'] for v in seq],at)-1
        return seq[i] if i>=0 else None

    def select(self, city, day, timezone, as_of):
        at=pp.timestamp(as_of).timestamp();expected=target_hours(day,timezone)
        attempts={}
        for kind in KINDS:
            selected={};location=None;returned=None
            snapshot=self.last(self.snapshots.get((kind,city),[]),at)
            if snapshot:
                if snapshot.get('conflicted'):
                    attempts[kind]=0;continue
                raw=snapshot['payload'].get('hours')
                if not isinstance(raw,dict):raise ValueError('received snapshot has no hourly map')
                location=snapshot['payload'].get('request_location')
                returned=snapshot['payload'].get('response_location')
                selected={hour(k):(v,snapshot) for k,v in raw.items() if hour(k) in expected}
            else:
                for stamp in expected:
                    item=self.last(self.hours.get((kind,city,stamp),[]),at)
                    if item:selected[stamp]=(item['payload'],item)
            complete={}
            for stamp,(values,receipt) in selected.items():
                if receipt.get('conflicted'):continue
                if not 0<=at-receipt['available']<=MAX_AGE_H*3600:continue
                if not isinstance(values,list) or len(values)!=7:raise ValueError('malformed seven-variable weather row')
                if any(v is None for v in values):continue
                vals=[float(v) for v in values]
                if not all(math.isfinite(v) for v in vals):raise ValueError('non-finite received weather')
                if not (-90<=vals[0]<=70 and -100<=vals[1]<=70 and 0<=vals[2]<=100
                        and vals[3]>=0 and vals[4]>=0 and vals[5]>=0 and 800<=vals[6]<=1100):
                    raise ValueError('received weather values outside declared units')
                complete[stamp]=(vals,receipt)
            attempts[kind]=len(complete)
            if len(complete)!=len(expected):continue
            ordered=[complete[t][0] for t in expected]
            peak=max(range(len(ordered)),key=lambda i:ordered[i][0]);peak_time=dt.datetime.fromtimestamp(expected[peak],ZoneInfo(timezone))
            around=ordered[max(0,peak-2):min(len(ordered),peak+3)]
            values=[max(v[0] for v in ordered),max(v[0] for v in ordered)-min(v[0] for v in ordered),
                    peak_time.hour+peak_time.minute/60,sum(v[2] for v in around)/len(around),
                    sum(v[3] for v in ordered[:peak+1]),sum(v[4] for v in around)/len(around),
                    sum(v[1] for v in around)/len(around),sum(v[5] for v in ordered),
                    ordered[-1][6]-ordered[0][6],max((at-v[1]['available'])/3600 for v in complete.values())]
            result=dict(policy=POLICY,kind=kind,expected_hours=len(expected),features=dict(zip(FIELDS,values)),
                        request_location=location,response_location=returned,
                        provenance='atomic_current_snapshot' if snapshot else 'latest_received_hourly_revisions',
                        hours=[dict(utc=stamp,values=complete[stamp][0],
                                    **{k:complete[stamp][1][k] for k in ('available','captured_at','written_at','sha256')}) for stamp in expected])
            result['sha256']=pp.digest(result)
            return result,attempts
        return None,attempts


def validate(context,features):
    if not context:return
    if (context.get('policy')!=POLICY or context.get('kind') not in KINDS
            or pp.digest({k:v for k,v in context.items() if k!='sha256'})!=context.get('sha256')):
        raise ValueError('hourly weather context hash mismatch')
    expected=target_hours(features['date'],features['timezone']);at=pp.timestamp(features['as_of']).timestamp()
    if [v['utc'] for v in context['hours']]!=expected or context['expected_hours']!=len(expected):
        raise ValueError('hourly weather local day is incomplete')
    if set(context['features'])!=set(FIELDS) or not all(math.isfinite(v) for v in context['features'].values()):
        raise ValueError('hourly feature contract mismatch')
    for r in context['hours']:
        available=max(pp.timestamp(r['captured_at']),pp.timestamp(r['written_at'])).timestamp()
        if available!=r['available'] or not 0<=at-available<=MAX_AGE_H*3600:
            raise ValueError('hourly weather arrived late or is stale')


class Rules:
    """Exact ladder identity and server-received rules; never current-text replay."""
    def __init__(self, markets, bands, receipts):
        grouped=collections.defaultdict(set)
        for b in bands:grouped[b['market_id']].add(b['band_id'])
        self.by_ids=collections.defaultdict(list);self.receipts=collections.defaultdict(list)
        for market in markets:
            if grouped[market['market_id']]:self.by_ids[frozenset(grouped[market['market_id']])].append(market)
        for row in receipts:self.receipts[row['market_id']].append(row)

    def select(self, record):
        candidates=self.by_ids.get(frozenset(record['band_ids']),[])
        if len(candidates)>1:raise ValueError('ambiguous rules market ladder identity')
        if not candidates:return None
        market=candidates[0];f=record['features']
        if market['city_key']!=f['city'] or market['resolution_date']!=f['date']:
            raise ValueError('rules ladder names another city/date')
        known=[r for r in self.receipts.get(market['market_id'],[]) if pp.timestamp(r['received_at'])<=pp.timestamp(f['as_of'])]
        if not known:return None
        latest=max(known,key=lambda r:pp.timestamp(r['received_at']))
        if len({r['rules_text'] for r in known if pp.timestamp(r['received_at'])==pp.timestamp(latest['received_at'])})>1:
            raise ValueError('conflicting rules at one receipt time')
        sys.path.insert(0,str(Path(data.common.ROOT)/'scripts'))
        import contract_inputs
        annotated=contract_inputs.with_rules({},latest['rules_text'])
        return dict(contract=annotated['observation_contract'],station=annotated['contract_station'],
                    rules_sha256=annotated['rules_sha256'],received_at=latest['received_at'],
                    market_id=market['market_id'],source_sha256=pp.digest(latest))


def enrich(records,through):
    root=Path(data.common.ROOT);path=root/'data/mirror/manifest.json';blob=path.read_bytes();manifest=json.loads(blob)
    begin=pp.timestamp(min(r['features']['date'] for r in records)+'T00:00:00Z').timestamp()-2*86400
    end=pp.timestamp(through).timestamp()
    history=History(data.verified_rows(root,manifest,'live_capture',begin,end,lambda r:r['kind'] in KINDS))
    first=min(v['features']['date'] for v in records)
    calls={r['checkpoint_id']:r for r in data.verified_rows(root,manifest,'prediction_checkpoints',begin,end,
           lambda r:r['checkpoint']=='d1_eve' and r['target_date']>=first)}
    receipts=[]
    if 'market_rule_receipts' in manifest['tables']:
        receipts=list(data.verified_rows(root,manifest,'market_rule_receipts',begin,end,lambda r:True))
    previous=data.common.READ;data.common.READ={}
    try:
        markets,bands=data.lr.read_definitions();rules=Rules(markets,bands,receipts)
        definitions=data.common.sources(data.common.READ)
    finally:data.common.READ=previous
    out=[];coverage=collections.Counter()
    for row in records:
        f=row['features'];context,attempts=history.select(f['city'],f['date'],f['timezone'],f['as_of'])
        call=calls.get((row.get('original') or {}).get('checkpoint_id'))
        packet=pp.obj((call or {}).get('predictive_context')) or {};audit=packet.get('early_forecast_audit') or {}
        contract=audit.get('contract') or 'unknown'
        if any(audit.get(k) and pp.timestamp(audit[k])>pp.timestamp(f['as_of']) for k in ('rules_received_at','rules_observed_at')):
            contract='unknown'
        if audit.get('unit') and audit['unit']!=f['unit']:raise ValueError('original unit differs from target contract')
        rules_context=rules.select(row)
        if rules_context:
            if rules_context['station'] and rules_context['station']!=f['station']:
                coverage['rules_station_mismatch']+=1;continue
            contract=rules_context['contract']
        attached=dict(row,hourly=context,hourly_attempts=attempts,observation_contract=contract,
                      rules_context=rules_context,
                      original_centre_c=data.number((call or {}).get('centre_c')),
                      raw_audit=audit if call else None)
        attached['research_sha256']=pp.digest(attached)
        out.append(attached);coverage[context['kind'] if context else 'missing_complete_hourly']+=1
    if path.read_bytes()!=blob:raise ValueError('mirror changed during hourly enrichment')
    return out,dict(counts=dict(coverage),conflicting_receipts=history.conflicts,missing_server_receipts=history.missing_receipts,
                    rule_definition_blobs=definitions)
