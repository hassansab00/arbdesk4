"""Offline review must not load the live Tick through a shadowed common module."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from book_mark import market_price


@pytest.mark.parametrize("quote, expected", [
    ({"bid": .2, "ask": .5, "last": .9}, .35),
    ({"ask": .001, "last": .999}, .001),
    ({"bid": .8, "last": .1}, .8),
    ({"last": .4}, .4),
    ({"ask": .4}, None),
    ({}, None),
])
def test_shared_mark_preserves_tick_and_offline_rule(quote, expected):
    from tick import market_price as tick_mark
    assert tick_mark is market_price
    assert market_price(quote) == expected


def test_candidate_entrypoint_import_order_can_grade_without_live_tick():
    root = Path(__file__).resolve().parents[1]
    # A script's own directory precedes PYTHONPATH, exactly as candidate_cycle.py
    # in the nightly workflow. Its common.py must remain the research module.
    code = """
import runpy, sys
sys.path.insert(0, 'tools/wxpredict')
runpy.run_path('tools/wxpredict/candidate_cycle.py', run_name='import_probe')
import common
assert common.__file__.endswith('tools/wxpredict/common.py')
from wxpredict import candidate_forward
assert candidate_forward.collect([], [], [], {}, 'probe',
    '2026-01-01T00:00:00Z', '2026-01-03', '2026-01-04', 0) == ([], 0)
from wxpredict.decision_inputs import unix
call = dict(checkpoint_id='original', city_key='london', target_date='2026-01-03',
            checkpoint='noon', station='EGLC', decided_at='2026-01-03T12:00:00Z',
            frozen_at='2026-01-03T12:01:00Z', probs={'a': .6, 'b': .4},
            market={'a': {'bid': .2, 'ask': .4}, 'b': {'bid': .6, 'ask': .8}})
shadow = dict(call, variant='wxrd_auto', variant_version='probe',
              frozen_at='2026-01-03T12:02:00Z', probs={'a': .8, 'b': .2})
outcome = dict(checkpoint_id='original', winner_band_id='a',
               settled_at='2026-01-04T01:00:00Z', banked_at='2026-01-04T02:00:00Z')
rows, expected = candidate_forward.collect([call], [outcome], [shadow],
    {'london': 'Europe/London'}, 'probe', '2026-01-01T00:00:00Z',
    '2026-01-03', '2026-01-04', unix('2026-01-05T00:00:00Z'))
assert expected == len(rows) == 1
assert rows[0]['candidate_hit'] and rows[0]['incumbent_hit'] and not rows[0]['market_hit']
assert abs(rows[0]['market_brier'] - .98) < 1e-12
from ensemble_early import saved_market
assert saved_market({'a': {'bid': .2, 'ask': .4},
                     'b': {'bid': .6, 'ask': .8}}, ['a', 'b'])[0] == {'a': .3, 'b': .7}
assert 'tick' not in sys.modules
"""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([
        str(root / 'scripts'), os.environ.get('PYTHONPATH', '')]))
    result = subprocess.run([sys.executable, '-c', code], cwd=root, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
