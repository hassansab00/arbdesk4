"""Exact primary inputs and immutable venue labels, from the verified mirror.

No weather replay, station substitution, inferred temperature or later current
forecast can fill a missing first call. Open-ended winners remain real classes.
"""
import collections
import datetime as dt
import hashlib
import json
import math
from zoneinfo import ZoneInfo

from wxpredict import arrival_dataset as arrival, capture_history, common
from wxpredict import current_dataset, decision_inputs as inputs

POLICY = 'wx-primary-current-calls-v1'
INPUT_POLICY = 'wxrd3-horizons-v1'
CHECKPOINTS = current_dataset.CHECKPOINTS
MAX_CURRENT_AGE_HOURS = 8
MIN_CURRENT_MODELS = 4


def object_of(value):
    return json.loads(value) if isinstance(value, str) else value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def bounds(row):
    def parse(s):
        return [None if v == '' else float(v) for v in s.split(';')]
    low,high=parse(row['bands_lo']),parse(row['bands_hi'])
    if len(low)!=len(high):
        raise ValueError('primary ladder edge count mismatch')
    edges = list(zip(low,high))
    if len(edges) != int(row['n_bands']):
        raise ValueError('primary ladder size mismatch')
    if (not edges or edges[0][0] is not None or edges[-1][1] is not None
            or any(a is not None and not math.isfinite(a) or b is not None and not math.isfinite(b)
                   or a is not None and b is not None and a >= b for a, b in edges)
            or any(edges[i][1] is None or edges[i][1] != edges[i+1][0] for i in range(len(edges)-1))):
        raise ValueError('primary ladder must be continuous with open ends')
    return edges


def distribution(value, ids):
    p = object_of(value)
    if (not isinstance(p, dict) or set(p) != set(ids)
            or any(not math.isfinite(float(v)) or not 0 <= float(v) <= 1 for v in p.values())
            or abs(sum(map(float, p.values()))-1) > 1e-4):
        raise ValueError('invalid frozen probability distribution')
    return {k: float(p[k]) for k in ids}


def attach(call, packet, captured_at, written_at, outcome=None, through=None):
    """Return the original features, or a counted exclusion reason."""
    if packet.get('policy') != INPUT_POLICY:
        return None, 'missing_primary_packet'
    row = packet['row']
    # A wrong hash/identity is corrupt evidence, rather than an ordinary gap.
    if digest(row) != packet['row_sha256'] or packet['row_sha256'] != call.get('input_sha256'):
        raise ValueError('frozen primary input hash mismatch')
    context = object_of(call.get('predictive_context')) or {}
    version = context.get('raw_model_version') or call.get('priced_from')
    ids = packet['band_ids']
    if (not ids or len(set(ids)) != len(ids) or ids != context.get('band_ids')
            or packet['version'] != version or row['city_key'] != call['city_key']
            or row['date'] != call['target_date'] or row['unit'] not in ('C', 'F')
            or int(row['decision_utc']) != int(inputs.unix(packet['as_of']))
            or inputs.unix(packet['as_of']) != inputs.unix(call['input_as_of'])
            or any(row.get(k) is not None for k in inputs.LABELS)):
        raise ValueError('frozen primary packet identity mismatch')
    edges = bounds(row)
    if len(edges) != len(ids):
        raise ValueError('frozen bucket IDs differ from geometry')
    frozen = inputs.unix(call['frozen_at'])
    as_of = inputs.unix(packet['as_of'])
    if (not as_of <= inputs.unix(captured_at) <= inputs.unix(written_at) <= frozen
            or frozen-as_of > 15*60 or packet['history_committed_at'] > as_of
            or packet['model_fit_through'] >= call['target_date']):
        return None, 'noncausal_primary_packet'
    timezone = packet.get('timezone')
    if not timezone:
        return None, 'missing_frozen_timezone'
    day_end = common.local_day_bounds(dt.date.fromisoformat(call['target_date']), ZoneInfo(timezone))[1]
    if frozen >= day_end:
        return None, 'late_frozen_call'
    current = inputs.current_context(packet)
    merged = {**row, **(current or {}), 'current_context_available': int(current is not None)}
    if not current_dataset.complete(merged) or int(row['day_offset']) != 0 or float(row['local_hour']) < 9:
        return None, 'missing_or_incomplete_current_context'
    if not context.get('current_forecasts_sha256') or not context.get('current_receipts_sha256'):
        return None, 'missing_frozen_current_hashes'
    if (context['current_forecasts_sha256']!=packet.get('current_forecasts_sha256')
            or context['current_receipts_sha256']!=packet.get('current_receipts_sha256')
            or context.get('timezone')!=timezone):
        raise ValueError('current forecast differs from frozen context hash')
    receipts = packet.get('current_received_at')
    if not isinstance(receipts,dict) or set(receipts)!={'hourly0','models0'}:
        return None, 'missing_current_source_receipts'
    if digest(receipts) != packet.get('current_receipts_sha256'):
        raise ValueError('current source receipt hash mismatch')
    ages = {kind:(as_of-inputs.unix(stamp))/3600 for kind,stamp in receipts.items()}
    if any(not 0<=age<=MAX_CURRENT_AGE_HOURS for age in ages.values()):
        return None, 'stale_current_forecasts'
    if (int(current['current_models_n'])<MIN_CURRENT_MODELS
            or any(v is None or not math.isfinite(float(v)) for v in
                   (current['current_models_mean_c'],current['current_models_sd_c']))
            or float(current['current_models_sd_c'])<0):
        return None, 'insufficient_current_ensemble'
    merged.update(current_hourly_age_h=ages['hourly0'],current_models_age_h=ages['models0'])
    raw = distribution(call['raw_probs'], ids)
    served = distribution(call['probs'], ids)
    quotes = object_of(call.get('market')) or {}
    prices = []
    for bid in ids:
        q = quotes.get(bid) or {}
        bid_price, ask = q.get('bid'), q.get('ask')
        if any(v is not None and (not isinstance(v, (float, int)) or not 0 <= v <= 1)
               for v in (bid_price, ask)):
            raise ValueError('invalid first-call market')
        price = arrival.lr.live_price(bid_price, ask)
        if price is None:
            return None, 'missing_first_call_market'
        prices.append(price)
    if not sum(prices) or row.get('rmax_unit') is None or row.get('rmax_c') is None:
        return None, 'missing_first_call_market_or_floor'
    source = row.get('observation_contract') or 'legacy'
    identity = '|'.join((call['city_key'], row['station'], source, version))
    record = dict(checkpoint_id=str(call['checkpoint_id']), city=call['city_key'],
                  date=call['target_date'], checkpoint=call['checkpoint'], timezone=timezone,
                  source_identity=identity, version=version, row=merged, band_ids=ids,
                  incumbent_probs=served, raw_probs=raw,
                  market_probs=dict(zip(ids, (v/sum(prices) for v in prices))),
                  frozen_at=call['frozen_at'], input_sha256=packet['row_sha256'],
                  current_sha256=packet['current_forecasts_sha256'],
                  input_as_of=packet['as_of'],history_commit=packet['history_commit'],
                  history_committed_at=packet['history_committed_at'],
                  primary_packet_receipt=captured_at,primary_packet_written_at=written_at)
    if outcome is not None:
        if (str(outcome['checkpoint_id']) != record['checkpoint_id']
                or outcome['city_key'] != record['city'] or outcome['target_date'] != record['date']
                or outcome['checkpoint'] != record['checkpoint']):
            raise ValueError('settled label belongs to another first call')
        winner = outcome['winner_band_id']
        if (winner not in ids or not arrival.lr.truthy(outcome['ladder_has_winner'])
                or not frozen < inputs.unix(outcome['settled_at']) <= inputs.unix(outcome['banked_at']) < through
                or day_end > through):
            return None, 'missing_or_late_venue_label'
        record.update(winner=ids.index(winner), label_received_at=outcome['banked_at'],settled_at=outcome['settled_at'])
    return record, None


def build(first, last, through):
    start = inputs.unix(first+'T00:00:00Z')-2*86400
    source_end=min(through,inputs.unix(str(dt.date.fromisoformat(last)+dt.timedelta(days=2))+'T00:00:00Z'))
    calls = arrival.first_calls(arrival.mirrored_rows('prediction_checkpoints', start, source_end),
                                first, last, CHECKPOINTS)
    outcomes = {str(r['checkpoint_id']): r for r in arrival.mirrored_rows(
        'fact_checkpoint_outcome', start, through,
        where=lambda r:first<=r['target_date']<=last)}
    packets = collections.defaultdict(list)
    keys = {c.get('input_key') for c in calls if c.get('input_key')}
    for r in arrival.mirrored_rows('live_capture', start, source_end,
            where=lambda r:r['kind'] == inputs.KIND and r['key'] in keys):
        if r['kind'] == inputs.KIND:
            packets[r['key']].append(r)
    rows, excluded = [], collections.Counter()
    for call in calls:
        if not call.get('frozen_at') or not call.get('input_key') or not call.get('raw_probs'):
            excluded['missing_frozen_lineage'] += 1
            continue
        versions = [r for r in packets.get(call['input_key'], ())
                    if r.get('written_at') and inputs.unix(r['written_at']) <= inputs.unix(call['frozen_at'])]
        if not versions:
            excluded['missing_primary_packet'] += 1
            continue
        receipt = max(versions, key=lambda r: (inputs.unix(r['captured_at']), int(r['capture_id'])))
        outcome = outcomes.get(str(call['checkpoint_id']))
        if outcome is None:
            excluded['missing_venue_label'] += 1
            continue
        row, reason = attach(call, object_of(receipt['payload']), receipt['captured_at'],
                             receipt['written_at'], outcome, through)
        if reason:
            excluded[reason] += 1
        else:
            rows.append(row)
    return rows, dict(policy=POLICY, first_calls=len(calls), paired_rows=len(rows),
                      excluded=dict(excluded), first=first, last=last, through=through,
                      dataset_sha256=digest(rows), uses_reconstructed_features=False)
