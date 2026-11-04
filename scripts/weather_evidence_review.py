"""Review retained AWC replies without refetching or rewriting historical truth.

An original reply which omits reports independently present in its own saved
publication check cannot be a complete point-temperature label. A dispute is
an append-only evidence record, with the actual review receipt, original hash
and an explicit supersession link. It never estimates a replacement maximum.
"""
from __future__ import annotations

import base64
import datetime as dt
import gzip
import hashlib
import io
import json
from zoneinfo import ZoneInfo

import metar_contract

VERSION = "awc-evidence-review-v1"


def review(evidence):
    raw = evidence['raw_payload']
    if raw.get('encoding') != 'gzip+base64':
        raise ValueError('Unsupported retained evidence encoding')
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(raw['data'], validate=True))) as stream:
        packed = stream.read(2_000_001)
    if len(packed) > 2_000_000:
        raise ValueError('Retained AWC evidence exceeds review bound')
    if hashlib.sha256(packed).hexdigest() != evidence['payload_sha256']:
        raise ValueError('Retained evidence fingerprint mismatch')
    payload = json.loads(packed)
    if payload.get('schema') not in ('awc-routine-metar-v1', 'awc-routine-metar-v2'):
        raise ValueError('Unsupported AWC evidence schema')
    if payload['for_date'] != str(evidence['for_date']) or payload['station_id'] != evidence['station_id']:
        raise ValueError('Retained evidence identity mismatch')
    station = payload['station_id']
    _, target_at = metar_contract.source_parameters(payload['source_url'])
    _, check_at = metar_contract.source_parameters(payload['publication_check_url'])
    target = metar_contract.raw_reports(payload['target_response'].encode(), station, target_at)
    check = metar_contract.raw_reports(payload['publication_check_response'].encode(), station, check_at)
    zone = ZoneInfo(payload['timezone'])
    day = dt.date.fromisoformat(payload['for_date'])
    seen = {r['raw'] for r in target}
    missing = [r for r in check if r['routine'] and r['when'].astimezone(zone).date() == day
               and r['raw'] not in seen]
    # v2 explicitly retains recovery windows; those may legitimately supplement
    # the named target reply. Review their union, not just the original reply.
    for receipt in payload.get('supplementary_windows', []):
        at = dt.datetime.fromisoformat(receipt['window_end'])
        seen.update(r['raw'] for r in metar_contract.raw_reports(receipt['response'].encode(), station, at))
    missing = [r for r in missing if r['raw'] not in seen]
    return {'version': VERSION, 'evidence_id': evidence['evidence_id'],
            'original_payload_sha256': evidence['payload_sha256'],
            'verdict': 'disputed_incomplete_target' if missing else 'no_cross_response_omission_found',
            'missing_published_times': sorted(r['when'].isoformat() for r in missing),
            # Passing this narrow audit is not an assertion of full completeness.
            'certifies_complete_day': False}


def dispute(evidence, verdict):
    if verdict != review(evidence) or verdict['verdict'] != 'disputed_incomplete_target':
        raise ValueError('A measured contradiction is required to append a dispute')
    packed = json.dumps(verdict, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(packed).hexdigest()
    from weather_outcomes import _packed_raw
    return {k: evidence[k] for k in ('city_key', 'for_date', 'observed_max_c', 'unit',
                                     'source_authority', 'station_id', 'source_url', 'observed_at')} | {
        'evidence_id': hashlib.sha256((VERSION + '|' + evidence['evidence_id'] + '|' + digest).encode()).hexdigest(),
        'record_status': 'disputed', 'parser_version': VERSION,
        'payload_sha256': digest, 'raw_payload': _packed_raw(packed, 'application/json'),
        'supersedes_evidence_id': evidence['evidence_id']}
