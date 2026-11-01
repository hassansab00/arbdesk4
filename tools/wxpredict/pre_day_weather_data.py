"""Receipt-aware pre-day weather inputs, independent of served probabilities.

Historical input replays can train a weather model without pretending they were
our frozen predictions. Only original server-frozen calls qualify as incumbent
comparisons. No database writes, source requests, backfills or guessed maxima.
"""
import bisect
import collections
import datetime as dt
import gzip
import hashlib
import io
import csv
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from wxpredict import common, build_table as bt, live_reader as lr, parity
from wxpredict import arrival_dataset as arrival, pre_day_pairs as pp

POLICY = 'pre-day-received-weather-v1'
MODELS = tuple('open_meteo_'+m for m in bt.MODELS)
SOURCE = 'open-meteo-models-current'
MAX_RECEIPT_AGE_H, MIN_MODELS = 36., 4


def number(value):
    if value in (None, ''):
        return None
    x = float(value)
    if not math.isfinite(x):
        raise ValueError('non-finite weather input')
    return x


def verified_rows(root, manifest, table, first, through, where):
    """One bounded verified read per source table, with original CSV types."""
    state = manifest['tables'][table]
    if pp.timestamp(state['through']).timestamp() < through:
        raise ValueError(table+' mirror does not cover the evidence cutoff')
    for e in state['files']:
        if e.get('from') and pp.timestamp(e['from']).timestamp() >= through or pp.timestamp(e['to']).timestamp() <= first:
            continue
        if Path(e['file']).name != e['file']:
            raise ValueError('invalid mirror filename')
        path = Path(root)/'data/mirror'/table/e['file']
        blob = path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != e['sha256']:
            raise ValueError(table+' mirror hash mismatch')
        count = 0
        for r in csv.DictReader(io.StringIO(gzip.decompress(blob).decode())):
            count += 1
            if where(r):
                yield r
        if count != e['rows']:
            raise ValueError(table+' mirror row count mismatch')


class Forecasts:
    def __init__(self, rows):
        original, conflicts = {}, set()
        for r in rows:
            if r.get('source') != SOURCE or r.get('model') not in MODELS:
                continue
            key = (r['city_key'], r['for_date'], r['model'], r['run_at'])
            value = number(r['forecast_max_c'])
            if value is None or not -90 <= value <= 70:
                raise ValueError('invalid recorded Celsius forecast')
            received = pp.timestamp(r['observed_at'])
            run = pp.timestamp(r['run_at'])  # producer fetch time, NOT NWP initialization
            available = max(received, run).timestamp()
            item = dict(value=value, available_at=available, observed_at=r['observed_at'],
                        run_at=r['run_at'], lead_days=int(r['lead_days']), source_sha256=pp.digest(r))
            if key in original and (original[key]['value'],original[key]['lead_days']) != (value,item['lead_days']):
                conflicts.add(key)
            elif key not in original or original[key]['available_at'] < available:
                original[key] = item  # later receipt is the conservative duplicate availability
        self.conflicts = len(conflicts)
        self.by = collections.defaultdict(list)
        for key, item in original.items():
            if key not in conflicts:
                self.by[key[:3]].append(item)
        for values in self.by.values():
            values.sort(key=lambda x:(x['available_at'],x['run_at']))

    def select(self, city, date, as_of):
        out = {}
        t = pp.timestamp(as_of).timestamp()
        for model in MODELS:
            values = self.by.get((city,date,model), [])
            index = bisect.bisect_right([r['available_at'] for r in values],t)-1
            if index < 0:
                continue
            chosen = values[index]
            if t-chosen['available_at'] <= MAX_RECEIPT_AGE_H*3600:
                out[model] = dict(chosen)
        return out


def original_call(call, event, timezone):
    """The first call either qualifies or stays unavailable. Never choose a retry."""
    if not call:
        return None, 'no_original_call'
    if not call.get('frozen_at'):
        return None, 'no_original_server_receipt'
    zone = ZoneInfo(timezone)
    day = dt.date.fromisoformat(event['date'])
    scheduled = dt.datetime.combine(day-dt.timedelta(days=1),dt.time(18),zone)
    decided, frozen = pp.timestamp(call['decided_at']), pp.timestamp(call['frozen_at'])
    if not scheduled <= decided <= frozen <= scheduled+dt.timedelta(minutes=75):
        return None, 'original_call_outside_window'
    if str(call.get('inputs_ok')).lower() not in ('true','t','1'):
        return None, 'original_inputs_not_ok'
    if dt.datetime.fromisoformat(call['local_decision_time']) != scheduled.replace(tzinfo=None):
        return None, 'original_schedule_mismatch'
    if not call.get('station'):
        return None, 'original_station_unknown'
    if call['station'] != event['station']:
        return None, 'original_station_contract_mismatch'
    q = pp.distribution(call['probs'],event['band_ids'])
    top = str(call['top_band_id'])
    if top not in q or q[top] < max(q.values())-1e-6:
        raise ValueError('original favourite differs from its saved ladder')
    return dict(checkpoint_id=call['checkpoint_id'], decided_at=call['decided_at'],
                frozen_at=call['frozen_at'], probs=q, top=top), None


def market_of(call, ids):
    if not call:
        return None
    book = pp.obj(call.get('market')) or {}
    out = {}
    for bid in ids:
        q = book.get(bid) or {}
        b,a = q.get('bid'),q.get('ask')
        if any(v is not None and (not isinstance(v,(float,int)) or not math.isfinite(v) or not 0 <= v <= 1) for v in (b,a)):
            raise ValueError('invalid original market quote')
        if b is not None and a is not None and b > a:
            return None
        price = lr.live_price(b,a)
        if price is None:
            return None
        out[bid] = max(1e-6,price)
    return {k:v/sum(out.values()) for k,v in out.items()}


def validate(record):
    features = record['features']
    if pp.digest(features) != record['feature_sha256']:
        raise ValueError('weather feature hash mismatch')
    day = dt.date.fromisoformat(features['date'])
    start = dt.datetime.combine(day,dt.time(),ZoneInfo(features['timezone']))
    as_of = pp.timestamp(features['as_of'])
    if as_of >= start or as_of.astimezone(start.tzinfo).date() != day-dt.timedelta(days=1):
        raise ValueError('weather inputs are not pre-day')
    if features['unit'] not in ('C','F') or not features['station']:
        raise ValueError('missing contract unit or station')
    providers = features['providers']
    if not MIN_MODELS <= len(providers) <= len(MODELS) or not set(providers) <= set(MODELS):
        raise ValueError('insufficient recorded weather providers')
    for r in providers.values():
        available = max(pp.timestamp(r['observed_at']), pp.timestamp(r['run_at']))
        age = (as_of-available).total_seconds()/3600
        if available.timestamp() != r['available_at'] or not 0 <= age <= MAX_RECEIPT_AGE_H:
            raise ValueError('weather input arrived after decision or is stale')
        lead = (day-pp.timestamp(r['run_at']).astimezone(start.tzinfo).date()).days
        if r['lead_days'] != lead or lead not in (1,2):
            raise ValueError('weather provider lead/date mismatch')
        if not -90 <= float(r['value']) <= 70:
            raise ValueError('invalid forecast value')
    ids, bounds = record['band_ids'], record['bounds']
    if (len(ids) != len(set(ids)) or len(ids) != len(bounds) or len(ids)<2
            or bounds[0][0] is not None or bounds[-1][1] is not None
            or any(lo is not None and (not math.isfinite(lo) or hi is not None and lo>=hi)
                   or hi is not None and not math.isfinite(hi) for lo,hi in bounds)
            or any(a[1] is None or a[1]!=b[0] for a,b in zip(bounds,bounds[1:]))):
        raise ValueError('invalid venue bucket geometry')
    label = record.get('label')
    if label and label['winner'] not in ids:
        raise ValueError('winner outside weather target ladder')
    if label:
        end = dt.datetime.combine(day+dt.timedelta(days=1),dt.time(),start.tzinfo)
        settled, banked, available = (pp.timestamp(label[k]) for k in ('settled_at','banked_at','available_at'))
        if not as_of < settled <= banked <= available or available < end:
            raise ValueError('invalid weather label availability')


def build_record(event, city, first_call, label, forecasts, created_at):
    zone = ZoneInfo(city['timezone'])
    day = dt.date.fromisoformat(event['date'])
    if first_call and first_call.get('station') and first_call['station'] != event['station']:
        return None, 'original_station_contract_mismatch'
    own, reason = original_call(first_call,event,city['timezone'])
    if reason == 'original_station_contract_mismatch':
        return None, reason
    scheduled = dt.datetime.combine(day-dt.timedelta(days=1),dt.time(18),zone)
    as_of = pp.timestamp(own['decided_at']) if own else scheduled
    if label and pp.timestamp(label['settled_at']) <= as_of:
        return None, 'market_already_settled_at_feature_cutoff'
    if pp.timestamp(created_at)>as_of:
        return None, 'event_not_open_at_feature_cutoff'
    providers = forecasts.select(event['city'],event['date'],as_of.isoformat())
    if len(providers)<MIN_MODELS:
        return None, 'missing_timely_current_forecasts'
    # Static location and deterministic season are not weather observations.
    features = dict(city=event['city'],date=event['date'],station=event['station'],
                    timezone=city['timezone'],unit=event['unit'],latitude=number(city.get('latitude')),
                    as_of=as_of.isoformat(),providers=providers)
    context = pp.obj((first_call or {}).get('predictive_context')) or {}
    row = dict(features=features,feature_sha256=pp.digest(features),band_ids=event['band_ids'],
               bounds=event['bands'],label=label,original=own,
               incumbent=own['probs'] if own else None,
               market=market_of(first_call,event['band_ids']) if own else None,
               evidence='original_pre_day_input_comparison' if own else 'received_weather_input_replay',
               comparison_exclusion=reason,
               components=context.get('forecast_input') if own else None)
    validate(row)
    return row, None


def build(first, last, through):
    cutoff = pp.timestamp(through)
    root = Path(common.ROOT)
    manifest_path = root/'data/mirror/manifest.json'
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    begin = pp.timestamp(first+'T00:00:00Z').timestamp()-2*86400
    read = lambda table,where:verified_rows(root,manifest,table,begin,cutoff.timestamp(),where)
    all_calls = list(read('prediction_checkpoints',lambda r:first<=r['target_date']<=last))
    first_calls = arrival.first_calls(all_calls,first,last,('d1_eve',))
    call_by = {(r['city_key'],r['target_date']):r for r in first_calls}
    by_label = collections.defaultdict(list)
    for r in read('fact_checkpoint_outcome',lambda r:first<=r['target_date']<=last):
        if r.get('winner_band_id') and r.get('banked_at') and r.get('settled_at'):
            by_label[(r['city_key'],r['target_date'])].append(r)
    forecasts = Forecasts(read('weather_forecast_models',lambda r:first<=r['for_date']<=last and r['source']==SOURCE))
    old_read = common.READ
    common.READ = {}
    try:
        cities = common.cities()
        venue_rows, venue_bands = list(common.market_rows('events')), list(common.market_rows('bands'))
        events, event_exclusions, _ = bt.load_events({r['station_icao'] for r in venue_rows},first,last)
        by_event = {e['event_id']:e for e in events}
        created = {r['event_id']:r['created_at'] for r in venue_rows}
        markets,bands = lr.read_definitions()
        cross = parity.event_crosswalk(markets,venue_rows,bands,venue_bands)
        matched = {}
        for live in lr.live_events(markets,bands,cities,first,last):
            e = by_event.get(cross.get(live['event_id']))
            if not e:
                continue
            if live['unit']!=e['unit'] or live['bands']!=e['bands']:
                continue
            if e['event_id'] in matched:
                raise ValueError('ambiguous original market identity')
            matched[e['event_id']] = dict(e,band_ids=live['band_ids'])
        out, exclusions = [], collections.Counter()
        for e in events:
            event = matched.get(e['event_id'])
            if event is None:
                exclusions['missing_exact_market_identity']+=1;continue
            day = dt.date.fromisoformat(e['date'])
            end = dt.datetime.combine(day+dt.timedelta(days=1),dt.time(),ZoneInfo(cities[e['city']]['timezone']))
            labels = [r for r in by_label[(e['city'],e['date'])] if r['winner_band_id'] in event['band_ids']]
            winner = event['band_ids'][e['winner']]
            if any(r['winner_band_id']!=winner for r in labels):
                raise ValueError('banked label disagrees with exact venue winner')
            labels = [r for r in labels if pp.timestamp(r['settled_at'])<=pp.timestamp(r['banked_at'])<=cutoff]
            if not labels or end>cutoff:
                exclusions['missing_banked_label_by_cutoff']+=1;continue
            f = min(labels,key=lambda r:pp.timestamp(r['banked_at']))
            label = dict(winner=winner,settled_at=f['settled_at'],banked_at=f['banked_at'],
                         available_at=max(end,pp.timestamp(f['banked_at'])).isoformat())
            row,reason = build_record(event,cities[e['city']],call_by.get((e['city'],e['date'])),label,forecasts,created[e['event_id']])
            if reason:exclusions[reason]+=1
            else:out.append(row)
        sources = common.sources(common.READ)
    finally:
        common.READ = old_read
    if manifest_path.read_bytes()!=manifest_bytes:
        raise ValueError('mirror changed during weather dataset read')
    coverage = dict(policy=POLICY,eligible_venue_events=len(events),rows=len(out),
        verified_original_comparisons=sum(r['original'] is not None for r in out),
        input_replays=sum(r['original'] is None for r in out),event_exclusions=event_exclusions,
        exclusions=dict(exclusions),forecast_key_conflicts=forecasts.conflicts,
        mirror_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),definition_git_blobs=sources,
        station_scope='exact venue station; known original mismatches excluded; provider request coordinates not retained',
        daily_scope='recorded daily summaries; hourly completeness and NWP initialization times are not retained',
        source='actual observed current forecasts, not historical previous-run publication assumptions')
    return out,coverage
