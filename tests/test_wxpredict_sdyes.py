"""The same-day YES pilot's entry rule (tools/wxpredict/sdyes.py,
docs/SAME_DAY_YES_PREREG.md): every level bought is at least THETA below the
probability after the fee, the ticket and the slippage cap stop the walk, the
minimum order is kept, and one entry per city-day is the first decision's
best edge."""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "wxpredict"))

import sdyes  # noqa: E402


def fee(p):
    return 0.05 * p * (1 - p)


LEVELS = [{"price": 0.30, "size": 10.0}, {"price": 0.32, "size": 20.0}, {"price": 0.40, "size": 100.0}]


def test_a_level_is_bought_only_while_the_fee_leaves_theta():
    # p 0.45, theta 0.10: 0.30 + fee 0.0105 and 0.32 + fee 0.0109 pass; 0.40 + fee 0.012 does not
    shares, usd, f = sdyes.walk_yes(LEVELS, p=0.45, theta=0.10, ticket_usd=1000, min_shares=5)
    assert shares == pytest.approx(30) and usd == pytest.approx(3.0 + 6.4)
    assert f == pytest.approx(10 * fee(0.30) + 20 * fee(0.32))
    assert 0.45 - (usd + f) / shares >= 0.10
    # the fee alone can fail a level the bare price passes: 0.45 - 0.34 = 0.11 but 0.34 + fee 0.0112 leaves 0.0988
    assert sdyes.walk_yes([{"price": 0.34, "size": 50.0}], p=0.45, theta=0.10) is None


def test_the_touch_must_qualify():
    assert sdyes.walk_yes(LEVELS, p=0.35, theta=0.05) is None
    assert sdyes.walk_yes([], p=0.9, theta=0.05) is None


def test_the_ticket_and_the_slippage_cap_stop_the_walk():
    shares, usd, _ = sdyes.walk_yes(LEVELS, p=0.95, theta=0.05, ticket_usd=10.0)
    assert usd == pytest.approx(10.0) and shares == pytest.approx(10 + 20 + 0.6 / 0.40), \
        "the third level takes the last 60 cents"
    wide = [{"price": 0.10, "size": 10.0}, {"price": 0.40, "size": 100.0}]
    shares, usd, _ = sdyes.walk_yes(wide, p=0.95, theta=0.05, ticket_usd=100.0, min_shares=5)
    assert shares == pytest.approx(10) and usd == pytest.approx(1.0), "0.40 is 30 cents past the touch"


def test_below_the_minimum_order_nothing_is_bought():
    thin = [{"price": 0.30, "size": 4.0}]
    assert sdyes.walk_yes(thin, p=0.6, theta=0.05) is None
    assert sdyes.walk_yes(thin, p=0.6, theta=0.05, min_shares=1)[0] == pytest.approx(4)


def test_the_touch_stress_costs_more_but_buys_the_same():
    a = sdyes.walk_yes(LEVELS, p=0.45, theta=0.10, ticket_usd=1000)
    b = sdyes.walk_yes(LEVELS, p=0.45, theta=0.10, ticket_usd=1000, touch_penalty=0.01)
    assert b[0] == pytest.approx(a[0]) and b[1] == pytest.approx(a[1] + 10 * 0.01)


def test_one_entry_per_city_day_the_first_decisions_best_edge():
    q = [{"t": 200, "bucket": 3, "edge": 0.30}, {"t": 100, "bucket": 5, "edge": 0.06},
         {"t": 100, "bucket": 4, "edge": 0.12}, {"t": 100, "bucket": 2, "edge": 0.12}]
    assert sdyes.choose(q) == {"t": 100, "bucket": 2, "edge": 0.12}, "earliest, then largest edge, then lowest bucket"
    assert sdyes.choose([]) is None


def test_held_to_settlement():
    e = {"shares": 30.0, "usd": 9.4, "fee": 0.3}
    assert sdyes.settle(e, True) == pytest.approx(20.3)
    assert sdyes.settle(e, False) == pytest.approx(-9.7)


def test_the_candidate_is_the_frozen_one():
    """docs/SAME_DAY_YES_PREREG.md: a change to any of these is sdyes-v2 on later dates, never an edit."""
    assert sdyes.VERSION == "sdyes-v1"
    assert sdyes.CITIES == ("jeddah", "istanbul") and sdyes.WINDOW_LOCAL_H == (10, 17)
    assert (sdyes.MODEL, sdyes.THETA, sdyes.TICKET_USD, sdyes.MIN_SHARES, sdyes.MAX_SLIP, sdyes.FEE_RATE) == \
        ("p_weather", 0.10, 10.0, 5.0, 0.25, 0.05)
    assert (sdyes.LATENCY_S, sdyes.BOOK_WITHIN_S, sdyes.FIRST_FORWARD_DATE) == (60, 300, "2026-10-13")
    assert (sdyes.FIRST_LOOK_DATES, sdyes.SECOND_LOOK_DATES) == (20, 40)


def test_the_frozen_model_is_the_committed_file():
    import gzip
    import hashlib
    import json
    raw = gzip.decompress((ROOT / sdyes.MODEL_FILE).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == sdyes.MODEL_SHA256
    meta = json.loads((ROOT / "data" / "models" / "sdyes" / "sdyes-v1_remaining.json").read_text())
    assert meta["model_sha256"] == sdyes.MODEL_SHA256 and meta["fit_last"] < sdyes.FIRST_FORWARD_DATE


def test_the_freeze_names_the_values():
    doc = (ROOT / "docs" / "SAME_DAY_YES_PREREG.md").read_text()
    for v in (sdyes.VERSION, sdyes.MODEL_SHA256, "THETA 0.10", sdyes.FIRST_FORWARD_DATE):
        assert v in doc, v
