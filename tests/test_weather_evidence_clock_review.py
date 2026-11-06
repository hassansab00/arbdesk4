"""Synthetic retained replies only: clock defects cannot become point truth."""
import base64
import copy
import gzip
import hashlib
import json

import pytest

import weather_evidence_review as review
from test_weather_evidence_completeness import retained, reports


def repack(evidence, change):
    e = copy.deepcopy(evidence)
    payload = json.loads(gzip.decompress(base64.b64decode(e['raw_payload']['data'])))
    change(payload)
    raw = json.dumps(payload).encode()
    e['payload_sha256'] = hashlib.sha256(raw).hexdigest()
    e['raw_payload']['data'] = base64.b64encode(gzip.compress(raw)).decode()
    return e


@pytest.mark.parametrize('token', ['12000Z', '121210Z0Z', '121200', '122460Z'])
def test_unreadable_clock_with_known_station_gets_a_specific_append_only_review(token):
    evidence = retained(f'METAR KAAA {token} 19/10'.encode(), reports(('130100', 21)))
    before = copy.deepcopy(evidence)
    verdict = review.review_malformed_clocks(evidence)
    assert verdict['verdict'] == 'disputed_unreadable_report_time'
    assert verdict['defects'][0]['time_token'] == token
    assert verdict['defects'][0]['response'] == 'target_response'
    assert verdict['defects'][0]['line_number'] == 1
    assert len(verdict['defects'][0]['report_sha256']) == 64
    assert verdict['numeric_maximum_reassessed'] is False
    assert verdict['certifies_complete_day'] is False
    appended = review.dispute(evidence, verdict)
    assert appended['record_status'] == 'disputed'
    assert appended['supersedes_evidence_id'] == evidence['evidence_id']
    assert appended['observed_max_c'] == evidence['observed_max_c']
    assert appended['observed_at'] == evidence['observed_at']
    assert 'captured_at' not in appended
    assert review.dispute(evidence, verdict) == appended
    assert evidence == before
    forged = copy.deepcopy(verdict); forged['defects'][0]['time_token'] = 'guessed'
    with pytest.raises(ValueError):
        review.dispute(evidence, forged)


def test_supplementary_and_publication_clocks_are_reviewed_without_assigning_a_day():
    evidence = retained(reports(('120100', 20)), b'METAR KAAA 13000Z 21/10')
    evidence = repack(evidence, lambda p: p.update(schema='awc-routine-metar-v2',
        supplementary_windows=[dict(window_end='2026-10-13T00:00:00+00:00',
            response='METAR KAAA 12200Z 18/10')]))
    v = review.review_malformed_clocks(evidence)
    assert {d['response'] for d in v['defects']} == {'publication_check_response', 'supplementary_windows[0]'}
    assert all('when' not in d and 'temperature_c' not in d for d in v['defects'])


@pytest.mark.parametrize('raw', [b'METAR KBBB 12000Z 20/10', b'not a weather report',
                               b'METAR KAAA ERROR 20/10'])
def test_wrong_station_or_generic_parse_failure_cannot_be_reclassified_as_a_clock_defect(raw):
    with pytest.raises(ValueError):
        review.review_malformed_clocks(retained(raw, reports(('130100', 21))))


def test_valid_clocks_and_speci_only_defects_cannot_certify_or_dispute_point_truth():
    e = retained(reports(('120100', 20)), reports(('130100', 21)))
    e = repack(e, lambda p: p.update(target_response=p['target_response']+'\nSPECI KAAA 12200Z 29/10'))
    verdict = review.review_malformed_clocks(e)
    assert verdict['verdict'] == 'no_unreadable_routine_time_found'
    assert verdict['certifies_complete_day'] is False
    with pytest.raises(ValueError):
        review.dispute(e, verdict)


@pytest.mark.parametrize('change', [lambda p:p.update(station_id='KBBB'),
    lambda p:p.update(source_url=p['source_url'].replace('ids=KAAA','ids=KBBB')),
    lambda p:p.update(publication_check_url=p['publication_check_url'].replace('ids=KAAA','ids=KBBB'))])
def test_dated_source_identity_is_required_before_a_clock_review(change):
    e = retained(b'METAR KAAA 12000Z 19/10', reports(('130100', 21)))
    with pytest.raises(ValueError):
        review.review_malformed_clocks(repack(e, change))


def test_fingerprint_mismatch_refuses_review_and_prior_omission_protocol_stays_identical():
    e = retained(b'METAR KAAA 12000Z 19/10', reports(('130100', 21)))
    e['payload_sha256'] = '0'*64
    with pytest.raises(ValueError, match='fingerprint'):
        review.review_malformed_clocks(e)
    original = retained(reports(('120100', 20)), reports(('122200', 24), ('130100', 21)))
    v = review.review(original); a = review.dispute(original, v)
    assert v['version'] == a['parser_version'] == 'awc-evidence-review-v1'
    digest = hashlib.sha256(json.dumps(v, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert a['evidence_id'] == hashlib.sha256(('awc-evidence-review-v1|original|'+digest).encode()).hexdigest()
