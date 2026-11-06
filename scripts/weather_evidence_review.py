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
import re
from zoneinfo import ZoneInfo

import metar_contract

VERSION = "awc-evidence-review-v1"
CLOCK_VERSION = "awc-clock-evidence-review-v1"


def _retained_payload(evidence):
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
    return payload


def review(evidence):
    payload = _retained_payload(evidence)
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


def review_malformed_clocks(evidence):
    """Prove a specific unreadable routine-report clock, never repair it.

    A valid station prefix plus a clock-like token is required. Wrong stations,
    generic garbage and SPECI-only defects cannot be relabeled this way. No
    calendar day or replacement maximum is inferred from the broken token.
    """
    payload = _retained_payload(evidence)
    station = payload['station_id']
    if evidence['source_url'] != payload['source_url']:
        raise ValueError('Retained evidence source URL mismatch')
    named, _ = metar_contract.validate_window(payload['source_url'],
        dt.date.fromisoformat(payload['for_date']), payload['timezone'])
    check_station, _ = metar_contract.source_parameters(payload['publication_check_url'])
    if named != station or check_station != station:
        raise ValueError('Retained AWC source station mismatch')
    windows = payload.get('supplementary_windows', [])
    if not isinstance(windows, list) or len(windows) > 64:
        raise ValueError('Retained AWC review window bound exceeded')
    parts = [('target_response', payload['target_response']),
             ('publication_check_response', payload['publication_check_response'])]
    parts += [(f'supplementary_windows[{i}]', r['response']) for i, r in enumerate(windows)]
    defects = []
    for part, text in parts:
        if not isinstance(text, str) or len([x for x in text.splitlines() if x.strip()]) >= 400:
            raise ValueError('Unsupported or truncated retained AWC response')
        for line_number, raw_line in enumerate(text.splitlines(), 1):
            line = raw_line.strip().rstrip('=').strip()
            if not line:
                continue
            prefix = re.match(r'^(?:(?:METAR|SPECI)\s+)?(?:COR\s+)?([A-Z0-9]{4})\s+(?:COR\s+)?(\S+)(?:\s|$)', line)
            if not prefix or prefix[1] != station:
                raise ValueError('Unknown report or wrong station in retained response')
            if line.startswith('SPECI '):
                continue  # An excluded report alone cannot invalidate routine point truth.
            header = metar_contract.HEADER.match(line)
            if header:
                day, hour, minute = map(int, header.group(3, 4, 5))
                if 1 <= day <= 31 and 0 <= hour < 24 and 0 <= minute < 60:
                    continue
                reason = 'out_of_range_clock_fields'
            else:
                if not re.fullmatch(r'[0-9Z]{1,16}', prefix[2]) or not any(c.isdigit() for c in prefix[2]):
                    raise ValueError('Generic parse failure is not a demonstrated clock defect')
                reason = 'malformed_clock_token'
            defects.append(dict(response=part, line_number=line_number,
                report_sha256=hashlib.sha256(line.encode()).hexdigest(),
                time_token=prefix[2], reason=reason))
    return dict(version=CLOCK_VERSION, evidence_id=evidence['evidence_id'],
        original_payload_sha256=evidence['payload_sha256'],
        verdict='disputed_unreadable_report_time' if defects else 'no_unreadable_routine_time_found',
        defects=defects, certifies_complete_day=False, numeric_maximum_reassessed=False)


def dispute(evidence, verdict):
    if isinstance(verdict, dict) and verdict.get('version') == CLOCK_VERSION:
        expected, required = review_malformed_clocks(evidence), 'disputed_unreadable_report_time'
    else:
        expected, required = review(evidence), 'disputed_incomplete_target'
    if verdict != expected or verdict['verdict'] != required:
        raise ValueError('A measured contradiction is required to append a dispute')
    packed = json.dumps(verdict, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(packed).hexdigest()
    from weather_outcomes import _packed_raw
    return {k: evidence[k] for k in ('city_key', 'for_date', 'observed_max_c', 'unit',
                                     'source_authority', 'station_id', 'source_url', 'observed_at')} | {
        'evidence_id': hashlib.sha256((verdict['version'] + '|' + evidence['evidence_id'] + '|' + digest).encode()).hexdigest(),
        'record_status': 'disputed', 'parser_version': verdict['version'],
        'payload_sha256': digest, 'raw_payload': _packed_raw(packed, 'application/json'),
        'supersedes_evidence_id': evidence['evidence_id']}
