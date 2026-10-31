"""A later report/history vintage cannot enter a promotion evaluation."""
import copy
import csv
import gzip
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from wxpredict import decision_inputs as di, arrival_dataset as arrivals, features


def case():
    call = dict(checkpoint_id=12, city_key='synthetic', target_date='2026-10-16', checkpoint='morning',
                decided_at='2026-10-16T09:36:00Z', top_band_id='low',
                market={'low': {'bid': .2, 'ask': .4}, 'high': {'bid': None, 'ask': .6}})
    event = dict(city='synthetic', date='2026-10-16', station='SYN', unit='C',
                 event_id='exact-contract', band_ids=['low', 'high'], bands=[(None, 20.0), (20.0, None)])
    row = {k: None for k in features.COLUMNS}
    row.update(event_id='db-contract', city_key='synthetic', date=call['target_date'], station='SYN', unit='C',
               n_bands=2, bands_lo=';20', bands_hi='20;', decision_utc=int(di.unix(call['decided_at'])),
               local_hour=9, rmax_unit=19, winner=1, label_unit=22, t_now_c=19)
    return call, event, row


def contract_sources(monkeypatch, events):
    """Exact synthetic contracts, without reading changing production files."""
    from wxpredict import build_table, common, live_reader
    monkeypatch.setattr(common, 'cities', lambda: {})
    monkeypatch.setattr(common, 'nightly_reports', lambda: {})
    monkeypatch.setattr(build_table, 'report_stations', lambda _: {'SYN'})
    monkeypatch.setattr(build_table, 'load_events', lambda *a: (events, {}, set()))
    venue = [dict(event_id=e['event_id'], slug=e['event_id'], city_key=e['city'], date=e['date']) for e in events]
    markets = [dict(market_id=e['event_id'], event_slug=e['event_id'], city_key=e['city'],
                    resolution_date=e['date']) for e in events]
    monkeypatch.setattr(common, 'market_rows', lambda kind: iter(venue if kind == 'events' else []))
    monkeypatch.setattr(live_reader, 'read_definitions', lambda: (markets, []))
    live = copy.deepcopy(events)
    monkeypatch.setattr(live_reader, 'live_events', lambda *a: live)
    return live


def test_only_the_real_on_time_label_free_hashed_vector_is_eligible():
    call, event, row = case()
    packed = di.pack(call, event, row, 'a' * 40, di.unix(call['decided_at']) - 100)
    p = packed['payload']
    assert all(p['row'][k] is None for k in di.LABELS)
    assert row['winner'] == 1
    assert di.eligible(p, '2026-10-16T09:37:00Z')
    assert not di.eligible(p, '2026-10-16T09:35:00Z')
    assert not di.eligible(p, '2026-10-16T10:07:00Z')
    late = copy.deepcopy(p); late['history_committed_at'] = di.unix(call['decided_at']) + 1
    assert not di.eligible(late, '2026-10-16T09:37:00Z')
    bad = copy.deepcopy(p); bad['row']['t_now_c'] = 25
    with pytest.raises(ValueError, match='hash mismatch'):
        di.eligible(bad, '2026-10-16T09:37:00Z')
    bad = copy.deepcopy(p); bad['row']['winner'] = 0
    with pytest.raises(ValueError, match='outcome'):
        di.eligible(bad, '2026-10-16T09:37:00Z')


def test_evaluation_uses_first_call_book_and_exact_identity_and_only_adds_labels():
    call, event, row = case()
    p = di.pack(call, event, row, 'a' * 40, 0)['payload']
    out = arrivals.attach(call, p, event, {'winner': '1', 'label_unit': '22'})
    assert out['winner'] == '1' and out['t_now_c'] == row['t_now_c'] and out['event_id'] == 'exact-contract'
    assert [float(v) for v in out['mkt_prices'].split(';')] == pytest.approx([.3, .3])
    bad = {**event, 'band_ids': ['high', 'low']}
    with pytest.raises(ValueError, match='contract'):
        arrivals.attach(call, p, bad, {})
    bad = copy.deepcopy(p); bad['row']['bands_lo'] = ';21'
    with pytest.raises(ValueError, match='geometry'):
        arrivals.attach(call, bad, event, {})
    no_book = {**call, 'market': {}}
    assert arrivals.attach(no_book, p, event, {}) is None


def test_retries_and_engine_versions_cannot_replace_the_first_call(monkeypatch):
    call, event, row = case()
    contract_sources(monkeypatch, [event])
    duplicate = {**call, 'checkpoint_id': 13, 'decided_at': '2026-10-16T10:36:00Z'}
    assert arrivals.first_calls([duplicate, call, call], '2026-10-16', '2026-10-16') == [call]
    p = di.pack(call, event, row, 'a'*40, 0)['payload']
    monkeypatch.setattr(arrivals, 'recorded', lambda *a: ([call], {'12': p}))
    plan = arrivals.preflight('2026-10-16', '2026-10-16', 0, minimum_cities=1)
    assert not plan['ready'] and plan['recorded_first_calls'] == 1
    assert '2026-10-16:morning' not in plan['sparse_checkpoint_dates']
    assert '2026-10-16:noon' in plan['sparse_checkpoint_dates']


def test_scoped_preflight_counts_all_requested_first_calls_and_does_not_require_morning(monkeypatch):
    call,event,row=case()
    contract_sources(monkeypatch, [event])
    noon=dict(call,checkpoint='noon',checkpoint_id=13)
    payload=di.pack(noon,event,row,'a'*40,0)['payload']
    def recorded(*args):
        assert args[-1]==('noon',)
        return [noon],{'13':payload}
    monkeypatch.setattr(arrivals,'recorded',recorded)
    result=arrivals.preflight('2026-10-16','2026-10-16',0,minimum_cities=1,checkpoints=('noon',))
    assert result['ready'] and result['first_calls']==1 and result['checkpoints']==['noon']
    assert arrivals.first_calls([call,noon],'2026-10-16','2026-10-16',('noon',))==[noon]


@pytest.mark.parametrize('field,value', [('station', 'WRNG'), ('unit', 'F'),
                                         ('bands', [(None, 21.), (21., None)])])
def test_one_incompatible_contract_cannot_abort_other_cities_or_shrink_the_denominator(monkeypatch, tmp_path, field, value):
    call, event, row = case()
    good_event = dict(event, event_id='other-contract', city='other', band_ids=['other-low', 'other-high'])
    good_call = dict(call, checkpoint_id=13, city_key='other', top_band_id='other-low',
                     market={'other-low': {'bid': .2, 'ask': .4}, 'other-high': {'ask': .6}})
    good_row = dict(row, city_key='other', event_id='other-contract')
    payloads = {'12': di.pack(call, event, row, 'a'*40, 0)['payload'],
                '13': di.pack(good_call, good_event, good_row, 'a'*40, 0)['payload']}
    original = copy.deepcopy(payloads)
    live = contract_sources(monkeypatch, [event, good_event])
    live[0][field] = value
    if field == 'station':
        live[0]['contract_station'] = value
    rows, coverage = arrivals.paired([call, good_call], payloads, '2026-10-16', '2026-10-16')
    assert [r['city_key'] for r in rows] == ['other']
    assert coverage['first_calls'] == 2 and coverage['paired_rows'] == 1
    assert coverage['excluded'] == {'live_settled_contract_mismatch': 1}
    assert coverage['contract_mismatches'] == [dict(city='synthetic', date=event['date'],
        live_event_id=event['event_id'], venue_event_id=event['event_id'], fields=[field],
        live={field: value}, venue={field: event[field]}, excluded_first_calls=1)]
    assert payloads == original
    monkeypatch.setattr(arrivals, 'recorded', lambda *a: ([call, good_call], payloads))
    plan = arrivals.preflight('2026-10-16', '2026-10-16', 0, minimum_cities=2, checkpoints=('morning',))
    assert not plan['ready'] and plan['first_calls'] == 2 and plan['recorded_first_calls'] == 2
    assert plan['eligible_first_calls'] == 1
    assert plan['input_coverage']['excluded'] == coverage['excluded']
    from wxpredict import parity
    table = tmp_path / 'labels.csv.gz'
    with gzip.open(table, 'wt') as fh:
        writer = csv.DictWriter(fh, fieldnames=['source', 'date', 'event_id', *parity.LABELS])
        writer.writeheader()
        for e in (event, good_event):
            writer.writerow(dict(source='venue', date=e['date'], event_id=e['event_id'], winner='1', label_unit='22'))
    labeled, result = arrivals.build('2026-10-16', '2026-10-16', str(table), 0)
    assert result == coverage and len(labeled) == 1
    assert labeled[0]['winner'] == '1' and labeled[0]['event_id'] == good_event['event_id']
    assert payloads == original


def test_incompatible_frozen_vector_is_excluded_but_malformed_quotes_still_fail(monkeypatch):
    call, event, row = case()
    contract_sources(monkeypatch, [event])
    # The live definition now agrees with settlement, but the intact vector
    # still names the former station. Never repair that frozen vector.
    payload = di.pack(call, event, dict(row, station='OLD'), 'a'*40, 0)['payload']
    rows, coverage = arrivals.paired([call], {'12': payload}, '2026-10-16', '2026-10-16')
    assert not rows and coverage['first_calls'] == 1
    assert coverage['excluded'] == {'recorded_settled_contract_mismatch': 1}
    assert coverage['recorded_contract_mismatches'][0]['recorded_station'] == 'OLD'
    assert coverage['recorded_contract_mismatches'][0]['settled_station'] == 'SYN'
    payload = di.pack(call, event, row, 'a'*40, 0)['payload']
    malformed = copy.deepcopy(call)
    malformed['market']['low']['ask'] = 2
    with pytest.raises(ValueError, match='invalid first-call market quote'):
        arrivals.paired([malformed], {'12': payload}, '2026-10-16', '2026-10-16')


def test_city_default_cannot_override_the_original_vector_and_exact_settled_station(monkeypatch):
    call, event, row = case()
    live = contract_sources(monkeypatch, [event])
    live[0]['station'] = 'OLD'
    payload = di.pack(call, event, row, 'a'*40, 0)['payload']
    rows, coverage = arrivals.paired([call], {'12': payload}, '2026-10-16', '2026-10-16')
    assert len(rows) == coverage['paired_rows'] == coverage['first_calls'] == 1
    assert rows[0]['station'] == 'SYN' and not coverage['excluded']
