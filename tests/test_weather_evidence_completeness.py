"""Synthetic retrieval failures; no proprietary source responses or winners."""
import datetime as dt
import hashlib
import base64
import gzip
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

import metar_contract as metar
import weather_outcomes as outcomes
import weather_evidence_review as review
import forecast_postprocess as postprocess

UTC = dt.timezone.utc
DAY = dt.date(2026, 10, 12)
NOW = dt.datetime(2026, 10, 13, 2, tzinfo=UTC)


def reports(*pairs):
    return '\n'.join(f'METAR KAAA {stamp}Z {temp:02d}/10' for stamp, temp in pairs).encode()


def provider(monkeypatch, raw, *, omit=None, stale=False):
    calls = []
    def get(session, url, **kwargs):
        q = parse_qs(urlsplit(url).query)
        right = dt.datetime.fromisoformat(q['date'][0].replace('Z', '+00:00'))
        left = right - dt.timedelta(hours=int(q['hours'][0]))
        calls.append((left, right))
        rows = metar.raw_reports(raw, 'KAAA', right)
        chosen = [r['raw'] for r in rows if left <= r['when'] <= right and r['raw'] != omit]
        return SimpleNamespace(content=raw if stale else '\n'.join(chosen).encode())
    monkeypatch.setattr(outcomes, '_get', get)
    return calls


def complete(monkeypatch, all_raw, known_raw, **kw):
    calls = provider(monkeypatch, all_raw, **kw)
    known = metar.raw_reports(known_raw, 'KAAA', NOW)
    rows, receipts = outcomes._awc_complete_day(object(), 'KAAA', DAY, ZoneInfo('UTC'), NOW, known)
    return rows, receipts, calls


def test_separate_next_day_publication_does_not_prove_target_completeness(monkeypatch):
    partial = reports(('120100', 20), ('120700', 23))
    later = reports(('122200', 24), ('130100', 21))
    with pytest.raises(outcomes.OutcomeError, match='omits an independently') as e:
        complete(monkeypatch, partial, partial + b'\n' + later)
    assert e.value.status == 'incomplete_source'
    proof = e.value.detail['incomplete_retrieval']
    retained_reply = json.loads(gzip.decompress(base64.b64decode(proof['data'])))
    assert len(retained_reply['windows']) == 4
    assert all('received_at' in r for r in retained_reply['windows'])


def test_supplementary_windows_recover_the_temperature_centre_and_keep_receipts(monkeypatch):
    partial = reports(('120100', 20), ('120700', 23))
    available = partial + b'\n' + reports(('121300', 29), ('122200', 24), ('130100', 21))
    rows, receipts, calls = complete(monkeypatch, available, partial)
    assert max(r['temp_c'] for r in rows) == 29
    assert len(receipts) == len(calls) == 4
    assert all(r['received_at'] and r['response'] is not None for r in receipts)
    assert max(right for _, right in calls) <= NOW


def test_ignored_window_or_stale_response_is_not_a_successful_empty_window(monkeypatch):
    raw = reports(('120100', 20))
    with pytest.raises(outcomes.OutcomeError, match='requested window'):
        complete(monkeypatch, raw, raw, stale=True)


def test_successful_sparse_or_empty_windows_do_not_invent_airport_cadence(monkeypatch):
    rows, receipts, _ = complete(monkeypatch, reports(('121317', 23)), b'')
    assert len(rows) == 1
    assert sum(not r['response'] for r in receipts) == 3


def test_same_time_corrections_are_not_resolved_by_taking_the_hotter_version(monkeypatch):
    older = reports(('121300', 23))
    newer = b'METAR KAAA 121300Z COR 22/10'
    with pytest.raises(outcomes.OutcomeError, match='omits|disagree'):
        complete(monkeypatch, newer, older)


def test_additional_awc_requests_share_pacing_and_the_existing_deadline(monkeypatch):
    import common
    clock, waits = [100.0], []
    monkeypatch.setattr(outcomes.time, 'monotonic', lambda: clock[0])
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(common, '_budget_sleep', sleep)
    monkeypatch.setattr(common, '_request_kwargs', lambda kwargs: kwargs)
    session = SimpleNamespace(get=lambda *a, **k: SimpleNamespace(status_code=200))
    for n in range(3):
        outcomes._get(session, f'https://aviationweather.gov/api/data/metar?ids=KAAA&n={n}')
    assert waits == pytest.approx([outcomes.AWC_REQUEST_INTERVAL_S] * 2)


@pytest.mark.parametrize('day,zone,hours', [(dt.date(2026, 11, 1), 'America/New_York', 25),
                                         (dt.date(2026, 3, 8), 'America/New_York', 23)])
def test_windows_cover_dst_days_in_utc_without_assuming_twenty_four_hours(monkeypatch, day, zone, hours):
    calls = provider(monkeypatch, b'')
    _, receipts = outcomes._awc_complete_day(object(), 'KAAA', day, ZoneInfo(zone),
                                             dt.datetime(2026, 11, 3, tzinfo=UTC), [])
    first = dt.datetime.fromisoformat(receipts[0]['window_start'])
    last = dt.datetime.fromisoformat(receipts[-1]['window_end'])
    assert (last - first).total_seconds() == hours * 3600
    assert all(a['window_end'] == b['window_start'] for a, b in zip(receipts, receipts[1:]))


def retained(target, check):
    payload = {'schema': 'awc-routine-metar-v1', 'station_id': 'KAAA', 'for_date': str(DAY),
               'timezone': 'UTC', 'source_url': 'https://aviationweather.gov/api/data/metar?ids=KAAA&format=raw&date=2026-10-13T00:00:00Z&hours=27',
               'publication_check_url': 'https://aviationweather.gov/api/data/metar?ids=KAAA&format=raw&date=2026-10-13T02:00:00Z&hours=4',
               'target_response': target.decode(), 'publication_check_response': check.decode()}
    packed = json.dumps(payload).encode()
    return {'evidence_id': 'original', 'city_key': 'synthetic', 'for_date': str(DAY),
            'station_id': 'KAAA', 'observed_max_c': '23', 'unit': 'C', 'source_authority': 'AWC',
            'source_url': payload['source_url'], 'observed_at': None,
            'payload_sha256': hashlib.sha256(packed).hexdigest(),
            'raw_payload': outcomes._packed_raw(packed, 'application/json')}


def test_retained_contradiction_appends_a_dispute_without_overwriting_source():
    evidence = retained(reports(('120100', 20)), reports(('122200', 24), ('130100', 21)))
    before = json.dumps(evidence, sort_keys=True)
    verdict = review.review(evidence)
    appended = review.dispute(evidence, verdict)
    assert appended['record_status'] == 'disputed'
    assert appended['supersedes_evidence_id'] == evidence['evidence_id']
    assert 'captured_at' not in appended  # receipt comes from the database clock
    assert json.dumps(evidence, sort_keys=True) == before
    assert not verdict['certifies_complete_day']


def test_a_clean_narrow_review_cannot_certify_completeness_or_append_a_dispute():
    evidence = retained(reports(('122200', 24)), reports(('122200', 24), ('130100', 21)))
    verdict = review.review(evidence)
    assert not verdict['certifies_complete_day']
    with pytest.raises(ValueError, match='contradiction'):
        review.dispute(evidence, verdict)


def test_point_fitter_excludes_bucket_fallback_and_reports_counts(monkeypatch):
    import common
    def read(table, params, **kwargs):
        if table == 'v_verified_fact_forecast_outcome':
            return [dict(city_key=c, for_date='2026-10-12', model='synthetic', lead_days=1,
                         run_at='2026-10-11T00:00:00Z', forecast_max_c=24, observed_max_c=23)
                    for c in ('good', 'conflict', 'mismatch')]
        assert table == 'v_venue_truth'
        return [dict(city_key='good', for_date='2026-10-12', label_c=23, source='venue_reading'),
                dict(city_key='conflict', for_date='2026-10-12', label_c=25, source='winning_bucket'),
                dict(city_key='mismatch', for_date='2026-10-12', label_c=24, source='venue_reading')]
    monkeypatch.setattr(common, 'rest_all', read)
    counts = {}
    result = postprocess.load_evidence(eligibility=counts)
    assert set(result) == {('good', 1)}
    assert result[('good', 1)][0].observed_c == 23
    assert counts == dict(verified_fact_rows=3, accepted_point_rows=1,
                          excluded_bucket_or_missing_truth=1, excluded_point_mismatch=1)
