"""Read original, pre-day raw/served pairs from the verified mirror.

No forecast replay, new capture, database read/write or legacy receipt recovery.
This research reader does not change production learning eligibility.
"""
import collections
import datetime as dt
import hashlib
import json
import math
from zoneinfo import ZoneInfo

from wxpredict import arrival_dataset as arrival, common

POLICY = 'pre-day-original-pairs-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def timestamp(value):
    t = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if t.utcoffset() is None:
        raise ValueError('receipt timestamp has no timezone')
    return t


def obj(value):
    if value == '':
        return None  # PostgreSQL NULL in the verified CSV export
    return json.loads(value) if isinstance(value, str) else value


def distribution(value, ids=None):
    q = obj(value)
    if not isinstance(q, dict) or len(q) < 2 or ids is not None and set(q) != set(ids):
        raise ValueError('frozen ladder identity mismatch')
    p = {str(k): float(v) for k, v in q.items()}
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in p.values()) or abs(sum(p.values())-1) > 1e-4:
        raise ValueError('invalid frozen probability ladder')
    return p


def validate_record(record):
    if record.get('checkpoint') != 'd1_eve':
        raise ValueError('not an original pre-day research pair')
    original = {k:v for k,v in record.items() if k not in ('input_sha256', 'label')}
    if digest(original) != record.get('input_sha256'):
        raise ValueError('original pair input hash mismatch')
    day = dt.date.fromisoformat(record['date'])
    start = dt.datetime.combine(day, dt.time(), ZoneInfo(record['timezone']))
    if not timestamp(record['decided_at']) <= timestamp(record['frozen_at']) < start:
        raise ValueError('pair was not frozen before the target local day')
    distribution(record['raw'], distribution(record['incumbent']))


def attach(call, fact, timezone, as_of):
    """Return a receipt-backed research pair or an explicit exclusion reason.

    Malformed ladders/identity collisions are errors. Missing historical
    evidence is an exclusion. Missing labels remain pending, never a miss.
    """
    if call['checkpoint'] != 'd1_eve':
        return None, 'not_evening_before'
    if not call.get('frozen_at'):
        return None, 'missing_server_freeze'
    if not timezone:
        return None, 'missing_timezone'
    day = dt.date.fromisoformat(call['target_date'])
    tz = ZoneInfo(timezone)
    decided, frozen = timestamp(call['decided_at']), timestamp(call['frozen_at'])
    start = dt.datetime.combine(day, dt.time(), tz)
    end = dt.datetime.combine(day+dt.timedelta(days=1), dt.time(), tz)
    scheduled = dt.datetime.combine(day-dt.timedelta(days=1), dt.time(18), tz)
    if not scheduled <= decided <= frozen <= scheduled+dt.timedelta(minutes=75) or frozen >= start:
        return None, 'not_on_time_pre_day_freeze'
    local = dt.datetime.fromisoformat(str(call['local_decision_time']))
    # This column is the scheduled wall-clock checkpoint, not receipt time.
    if local.tzinfo is not None or local != scheduled.replace(tzinfo=None):
        return None, 'local_schedule_mismatch'
    if frozen > as_of:
        return None, 'freeze_after_research_cutoff'
    if str(call.get('inputs_ok')).lower() not in ('true', 't', '1'):
        return None, 'original_inputs_not_ok'
    context = obj(call.get('predictive_context')) or {}
    packet = context.get('early_forecast_audit')
    if not isinstance(packet, dict) or not packet.get('policy'):
        return None, 'missing_original_pair'
    if packet['policy'] not in ('early-raw-centre-comparison-v1','early-city-strength-v1'):
        return None, 'unknown_pair_policy'
    served = distribution(call['probs'])
    raw = distribution(packet.get('raw_centre_probs'), served)
    policy = packet['policy']
    if policy == 'early-raw-centre-comparison-v1':
        if obj(packet.get('corrected_probs')) != served:
            raise ValueError('raw pair does not reproduce the original served call')
    elif policy == 'early-city-strength-v1':
        import early_city_adaptation
        if not early_city_adaptation.valid_packet(packet, served):
            raise ValueError('invalid original strength-grid pair')
        if packet.get('checkpoint') != call['checkpoint'] or packet.get('station') != call.get('station'):
            raise ValueError('strength pair names a different original call')
        if packet.get('timezone') != timezone:
            return None, 'frozen_timezone_mismatch'
        if any(packet.get(k) and timestamp(packet[k]) > frozen for k in ('rules_observed_at','rules_received_at')):
            return None, 'rules_after_freeze'
        if packet.get('fitted_as_of') and dt.date.fromisoformat(packet['fitted_as_of']) >= day:
            return None, 'fit_after_pre_day_decision'
    else:
        return None, 'unknown_pair_policy'
    if not packet.get('input_forecast_run'):
        return None, 'missing_forecast_timestamp'
    if timestamp(packet['input_forecast_run']) > frozen:
        return None, 'forecast_after_freeze'
    if packet.get('unit') not in ('C', 'F') or not call.get('station'):
        return None, 'missing_frozen_unit_or_station'
    centre, raw_centre, sigma = (float(v) for v in
        (call['centre_c'], packet['raw_centre_c'], call['sigma_c']))
    if not all(math.isfinite(v) for v in (centre, raw_centre, sigma)) or sigma <= 0:
        raise ValueError('invalid frozen temperature context')
    expected_centre = float(packet['corrected_centre_c'])
    if policy == 'early-city-strength-v1':
        expected_centre = raw_centre+float(packet['strength'])*(expected_centre-raw_centre)
    if abs(float(packet['sigma_c'])-sigma) > 1e-6 or abs(expected_centre-centre) > 1e-6:
        raise ValueError('frozen temperature context differs from the paired ladder')
    top = str(call['top_band_id'])
    if top not in served or served[top] < max(served.values())-1e-6:
        raise ValueError('original favourite disagrees with frozen probabilities')
    quotes = obj(call.get('market')) or {}
    market, prices = None, {}
    from wxpredict.live_reader import live_price
    for bid in sorted(served):
        q = quotes.get(bid) or {}
        values = [q.get('bid'), q.get('ask')]
        if any(v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1) for v in values):
            raise ValueError('invalid original quote')
        if all(v is not None for v in values) and values[0] > values[1]:
            continue  # A crossed book cannot enter the market comparison.
        price = live_price(*values)
        if price is not None:
            prices[bid] = max(1e-6, price)
    if set(prices) == set(served):
        market = {k:v/sum(prices.values()) for k, v in prices.items()}
    source = dict(checkpoint_id=str(call['checkpoint_id']), city=call['city_key'], date=call['target_date'],
                  checkpoint='d1_eve', station=call['station'], unit=packet['unit'], timezone=timezone,
                  decided_at=call['decided_at'], frozen_at=call['frozen_at'], pair_policy=policy,
                  raw_centre_c=raw_centre, centre_c=centre, sigma_c=sigma,
                  raw=raw, incumbent=served, incumbent_top=top, market=market,
                  original_pair_sha256=digest(packet))
    source['input_sha256'] = digest(source)
    if not fact or not fact.get('winner_band_id'):
        return dict(source, label=None), 'pending_label'
    if str(fact['checkpoint_id']) != source['checkpoint_id'] or fact['city_key'] != source['city'] or str(fact['target_date']) != source['date']:
        raise ValueError('label names a different original call')
    winner = str(fact['winner_band_id'])
    if winner not in served:
        return None, 'winner_outside_frozen_ladder'
    if not fact.get('settled_at') or not fact.get('banked_at'):
        return dict(source, label=None), 'pending_label'
    settled, banked = timestamp(fact['settled_at']), timestamp(fact['banked_at'])
    if not frozen < settled <= banked <= as_of or end > as_of:
        return dict(source, label=None), 'label_not_available_by_cutoff'
    return dict(source, label=dict(winner=winner, settled_at=fact['settled_at'], banked_at=fact['banked_at'])), None


def load(first, last, through, root=None):
    root = root or common.ROOT
    from pathlib import Path
    manifest = Path(root)/'data/mirror/manifest.json'
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    cutoff = timestamp(through)
    start = timestamp(first+'T00:00:00Z').timestamp()-2*86400
    read = lambda table, where: arrival.mirrored_rows(table, start, cutoff.timestamp(), root=root, where=where)
    calls = arrival.first_calls(read('prediction_checkpoints',
        lambda r:first <= r['target_date'] <= last and r['checkpoint']=='d1_eve'), first, last, ('d1_eve',))
    ids = {str(p['checkpoint_id']) for p in calls}
    facts = {}
    for f in read('fact_checkpoint_outcome', lambda r:str(r['checkpoint_id']) in ids):
        key = str(f['checkpoint_id'])
        identity = ('checkpoint_id', 'city_key', 'target_date', 'winner_band_id', 'settled_at', 'banked_at')
        if key in facts and any(facts[key].get(k) != f.get(k) for k in identity):
            raise ValueError('conflicting original outcome rows')
        facts[key] = f
    # The archived timezone interprets the saved local schedule. It is not
    # invented as an original frozen metadata field or promotion proof.
    cities = common.cities() if os_root(root) == os_root(common.ROOT) else _cities_at(root)
    rows, excluded = [], collections.Counter()
    for p in calls:
        r, reason = attach(p, facts.get(str(p['checkpoint_id'])), (cities.get(p['city_key']) or {}).get('timezone'), cutoff)
        if r is not None:
            rows.append(r)
        if reason:
            excluded[reason] += 1
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != manifest_sha:
        raise ValueError('mirror manifest changed while reading the original pairs')
    return rows, dict(policy=POLICY, first_calls=len(calls), paired_calls=len(rows),
                      labeled=sum(r['label'] is not None for r in rows), exclusions=dict(excluded),
                      mirror_manifest_sha256=manifest_sha,
                      timezone_proof='archived city timezone interprets the original schedule; not an original timezone receipt')


def os_root(path):
    from pathlib import Path
    return str(Path(path).resolve())


def _cities_at(root):
    # Keep common's global paths unchanged while reading a separate test/export root.
    import csv, glob, gzip, os
    files = sorted(glob.glob(os.path.join(root, 'data/mirror/cities/cities-*.csv.gz')))
    if not files:
        raise ValueError('archived cities unavailable')
    with gzip.open(files[-1], 'rt') as fh:
        return {r['city_key']:r for r in csv.DictReader(fh)}
