"""Explicit, append-only recovery of disputed AWC point evidence.

No network or database writes occur here. Every retained reply must replay
through the current complete-day collector before a replacement is proposed.
"""
import copy
import datetime as dt
import json
from decimal import Decimal
from types import SimpleNamespace

import weather_evidence_review as review
import weather_outcomes as outcomes

VERSION = 'awc-reviewed-recovery-v1'


def _time(value):
    at = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if at.tzinfo is None:
        raise ValueError('Recovery requires a timezone-aware receipt')
    return at


def _without_processing_receipts(payload):
    value = copy.deepcopy(payload)
    value.pop('read_at', None)
    for window in value.get('supplementary_windows', []):
        window.pop('received_at', None)
    return value


def recover(prior, candidate, rules_text, market_unit, timezone, *, now):
    """Return a proposed verified supersession; the database supplies receipt time.

    The caller must separately guard that `prior` is still the current decision.
    A verified original, guessed timestamp or new numerical claim is not silently
    substituted. This review does not rewrite older forecast/settlement facts.
    """
    if prior.get('record_status') != 'disputed':
        raise ValueError('Recovery requires an explicitly disputed current decision')
    spec = outcomes.parse_rule_spec(rules_text, market_unit)
    if (spec.adapter != 'aviation_weather_metar' or spec.station_id != prior['station_id']
            or spec.source_url != prior['source_url']):
        raise ValueError('Recovery rules differ from the disputed source identity')
    retained = dict(prior, payload_sha256=candidate['payload_sha256'], raw_payload=candidate['raw_payload'])
    payload = review._retained_payload(retained)
    if (payload['schema'] != 'awc-routine-metar-v2' or payload['timezone'] != timezone
            or payload['source_url'] != spec.source_url
            or payload.get('completeness') != 'bounded_windows_cross_checked'):
        raise ValueError('Recovery lacks an identified complete-day source packet')
    read_at = _time(payload['read_at'])
    if not _time(prior['captured_at']) < read_at <= _time(now):
        raise ValueError('Recovery receipt does not follow dispute or is in the future')
    windows = payload.get('supplementary_windows')
    if not isinstance(windows, list) or not 1 <= len(windows) <= 5:
        raise ValueError('Recovery window bound is invalid')
    replies = {}
    def keep(url, text):
        if not isinstance(text, str) or len(text.encode()) > 100_000:
            raise ValueError('Recovery reply exceeds its bound')
        if url in replies and replies[url] != text:
            raise ValueError('Conflicting replies for one recovery request')
        replies[url] = text
    keep(payload['source_url'], payload['target_response'])
    keep(payload['publication_check_url'], payload['publication_check_response'])
    for window in windows:
        if not _time(prior['captured_at']) < _time(window['received_at']) <= read_at:
            raise ValueError('Recovery window has an invalid receipt')
        keep(window['source_url'], window['response'])
    class RetainedSession:
        def __init__(self):
            self.asked = []
        def get(self, url, **kwargs):
            if url not in replies or len(self.asked) >= 8:
                raise ValueError('Recovery replay requested an unretained response')
            self.asked.append(url)
            return SimpleNamespace(status_code=200, content=replies[url].encode())
    session = RetainedSession()
    try:
        replayed = outcomes.collect_awc(session, spec, dt.date.fromisoformat(prior['for_date']), timezone, read_at)
    except outcomes.OutcomeError as error:
        raise ValueError('Recovery candidate did not replay: '+str(error)) from error
    replay_payload = review._retained_payload(dict(retained, payload_sha256=replayed.payload_sha256,
                                                 raw_payload=replayed.raw_payload))
    if (set(session.asked) != set(replies)
            or _without_processing_receipts(payload) != _without_processing_receipts(replay_payload)
            or Decimal(str(candidate['observed_max_c'])) != replayed.observed_max_c
            or _time(candidate['observed_at']) != _time(replayed.observed_at)):
        raise ValueError('Recovery claim differs from complete retained-source replay')
    proof = dict(version=VERSION, supersedes_evidence_id=prior['evidence_id'],
                 prior_payload_sha256=prior['payload_sha256'], candidate_payload_sha256=candidate['payload_sha256'],
                 certifies_complete_day=True, changes_originals=False)
    raw = copy.deepcopy(candidate['raw_payload'])
    raw['recovery_review'] = proof
    return dict(evidence_id=outcomes._id(prior['city_key'], prior['for_date'], outcomes.PARSER_VERSION,
                                        candidate['payload_sha256']),
                city_key=prior['city_key'], for_date=prior['for_date'],
                observed_max_c=str(replayed.observed_max_c), unit='C',
                source_authority=spec.source_authority, station_id=spec.station_id,
                source_url=spec.source_url, record_status='verified', observed_at=replayed.observed_at,
                parser_version=outcomes.PARSER_VERSION, payload_sha256=candidate['payload_sha256'],
                raw_payload=raw, supersedes_evidence_id=prior['evidence_id'])
