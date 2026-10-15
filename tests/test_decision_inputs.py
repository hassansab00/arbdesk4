"""A later report/history vintage cannot enter a promotion evaluation."""
import copy
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
    duplicate = {**call, 'checkpoint_id': 13, 'decided_at': '2026-10-16T10:36:00Z'}
    assert arrivals.first_calls([duplicate, call, call], '2026-10-16', '2026-10-16') == [call]
    p = di.pack(call, event, row, 'a'*40, 0)['payload']
    monkeypatch.setattr(arrivals, 'recorded', lambda *a: ([call], {'12': p}))
    plan = arrivals.preflight('2026-10-16', '2026-10-16', 0, minimum_cities=1)
    assert not plan['ready'] and plan['recorded_first_calls'] == 1
    assert '2026-10-16:morning' not in plan['sparse_checkpoint_dates']
    assert '2026-10-16:noon' in plan['sparse_checkpoint_dates']
