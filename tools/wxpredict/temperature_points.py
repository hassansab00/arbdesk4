"""Complete retained AWC point targets; a venue bucket is never its midpoint."""
import base64
import datetime as dt
import gzip
import hashlib
import io
import json
import collections
import random
from decimal import Decimal
from zoneinfo import ZoneInfo

from wxpredict import temperature_model as model, pre_day_pairs as pp


def measured_point(evidence, record, winner, now):
    import metar_contract
    if (evidence.get('record_status') != 'verified' or evidence.get('parser_version') != 'weather-outcomes-v6'
            or evidence.get('city_key') != record['city'] or str(evidence.get('for_date')) != record['date']
            or evidence.get('station_id') != record['station'] or pp.timestamp(evidence['captured_at']) > pp.timestamp(now)):
        raise ValueError('point lacks matching complete verified evidence')
    raw = evidence['raw_payload']
    if raw.get('encoding') != 'gzip+base64':
        raise ValueError('unsupported point encoding')
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(raw['data'], validate=True))) as stream:
        packed = stream.read(2_000_001)
    if len(packed) > 2_000_000 or hashlib.sha256(packed).hexdigest() != evidence['payload_sha256']:
        raise ValueError('point evidence fingerprint mismatch')
    p = json.loads(packed)
    if (p.get('schema') != 'awc-routine-metar-v2' or p.get('completeness') != 'bounded_windows_cross_checked'
            or p.get('station_id') != record['station'] or p.get('for_date') != record['date']
            or p.get('timezone') != record['timezone'] or p.get('display_unit') != record['unit']):
        raise ValueError('point payload contract mismatch')
    zone = ZoneInfo(record['timezone'])
    day = dt.date.fromisoformat(record['date'])
    start = dt.datetime.combine(day, dt.time(), zone).astimezone(dt.timezone.utc)
    end = dt.datetime.combine(day+dt.timedelta(days=1), dt.time(), zone).astimezone(dt.timezone.utc)
    covered = start
    reports = {}
    windows = p.get('supplementary_windows') or []
    if not 4 <= len(windows) <= 5:
        raise ValueError('point lacks complete local-day windows')
    for w in windows:
        begin, finish, received = map(pp.timestamp, (w['window_start'], w['window_end'], w['received_at']))
        if begin != covered or not begin < finish <= end or not finish <= received <= pp.timestamp(evidence['captured_at']):
            raise ValueError('point window chronology or coverage mismatch')
        covered = finish
        for r in metar_contract.raw_reports(w['response'].encode(), record['station'], finish):
            if r['routine'] and r['temp_c'] is not None and r['when'].astimezone(zone).date() == day:
                prior = reports.get(r['when'])
                if prior is not None and prior != r['raw']:
                    raise ValueError('point has conflicting routine reports')
                reports[r['when']] = r['raw']
    if covered != end or not reports:
        raise ValueError('point local day not covered')
    actual = max(metar_contract.temperature_c(r) for r in reports.values())
    if actual != Decimal(p['qualifying_max_c']):
        raise ValueError('point maximum differs from saved reports')
    rounded = metar_contract.rounded_unit(actual, record['unit'])
    lo, hi = record['bounds'][record['band_ids'].index(winner)]
    if (lo is not None and rounded < lo) or (hi is not None and rounded >= hi):
        raise ValueError('measured point contradicts confirmed venue winner')
    return float(actual)


def evaluate_points(items, points, now, expected=None):
    """Input points must be the current verified-reader rows from the export.

    The exporter excludes superseded/disputed originals. This validator still
    checks the retained receipts and actual routine maximum independently.
    """
    by_key = {}
    for p in points:
        key=(p['city_key'],str(p['for_date']))
        if key in by_key and by_key[key]!=p:
            raise ValueError('ambiguous current point evidence')
        by_key[key]=p
    groups = {}
    excluded = {}
    for item in items:
        c, s = item['call'], item.get('score')
        if not s:
            continue
        key = (c['city_key'], c['target_date'])
        point = by_key.get(key)
        if not point:
            excluded['missing_complete_verified_point'] = excluded.get('missing_complete_verified_point', 0)+1
            continue
        try:
            actual = measured_point(point, c['record'], s['winner_band_id'], now)
            p = c['prediction']
            metrics = model.point_metrics(p['centre_c'], p['sigma_c'], actual, c['record'].get('floor_c'))
            maximum,_=model.vector(c['record'])
            refs=dict(hourly_reference=model.point_metrics(maximum,model.PRIOR_SIGMA,actual,c['record'].get('floor_c')))
            raw=c.get('comparisons') or {}
            if raw.get('raw_forecast_c') is not None and raw.get('raw_sigma_c') is not None:
                refs['raw_normal_reference']=model.point_metrics(float(raw['raw_forecast_c']),float(raw['raw_sigma_c']),actual,c['record'].get('floor_c'))
        except (ValueError, KeyError, TypeError) as error:
            reason = 'invalid:'+str(error)[:100]
            excluded[reason] = excluded.get(reason, 0)+1
            continue
        groups.setdefault(c['checkpoint'], []).append(dict(metrics, city=c['city_key'], date=c['target_date'],
            comparisons=refs,covered=p['q10_c'] <= actual <= p['q90_c']))
    results={}
    for slot,rows in groups.items():
        summary=dict(calls=len(rows),dates=len({r['date'] for r in rows}),cities=len({r['city'] for r in rows}),
            mae_c=sum(r['absolute_error_c'] for r in rows)/len(rows),crps_c=sum(r['crps_c'] for r in rows)/len(rows),
            interval80_coverage=sum(r['covered'] for r in rows)/len(rows),comparisons={})
        for name in ('hourly_reference','raw_normal_reference'):
            paired=[r for r in rows if name in r['comparisons']]
            blocks=collections.defaultdict(list)
            for r in paired:
                b=r['comparisons'][name];blocks[r['date']].append((b['crps_c']-r['crps_c'],b['absolute_error_c']-r['absolute_error_c']))
            daily=[tuple(sum(v[j] for v in group)/len(group) for j in (0,1)) for _,group in sorted(blocks.items())]
            rng=random.Random(470)
            means=sorted(sum(rng.choice(daily)[0] for _ in daily)/len(daily) for _ in range(2000)) if daily else []
            lower=means[50] if means else None
            mae_gain=sum(v[1] for v in daily)/len(daily) if daily else None
            coverage=len(paired)/len(expected) if expected else 0
            passed=bool(expected and coverage>=.9 and len(paired)>=200 and len(blocks)>=20
                and len({r['city'] for r in paired})>=20 and lower is not None and lower>0 and mae_gain>=0)
            summary['comparisons'][name]=dict(pairs=len(paired),dates=len(blocks),coverage=coverage,
                crps_gain_lower_95=lower,mae_gain_c=mae_gain,passed=passed)
        summary['passed']=all(v['passed'] for v in summary['comparisons'].values())
        results[slot]=summary
    return dict(exclusions=excluded,by_horizon=results,point_gates_passed=all(slot in results and results[slot]['passed'] for slot in model.SLOTS))
