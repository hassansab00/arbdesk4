"""The fec-v1 same-day harness (tools/fec_same_day.py): the decision instant on
each city's clock, full-ladder scoring, a market ladder only when complete and
fresh, and a bootstrap that resamples dates, not rows."""
import datetime as dt
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import fec_same_day as F  # noqa: E402


def test_the_decision_is_the_ticks_minute_on_the_citys_clock():
    t, tick = F.decision_time(dt.date(2026, 7, 15), 13, "America/New_York")
    assert abs(t - (13 + 36 / 60)) < 1e-9 and tick.isoformat() == "2026-07-15T17:36:00+00:00"
    t, _ = F.decision_time(dt.date(2026, 7, 15), 13, "Asia/Kolkata")     # a half-hour zone
    assert abs(t - (13 + 6 / 60)) < 1e-9
    t, _ = F.decision_time(dt.date(2026, 1, 15), 13, "Europe/London")    # winter, UTC+0
    assert abs(t - (13 + 36 / 60)) < 1e-9


def test_scoring_is_over_the_whole_ladder():
    probs = {"a": 0.2, "b": 0.5, "c": 0.3}
    ll, hit, brier = F.score(probs, "c", ["a", "b", "c"])
    assert abs(ll + math.log(0.3)) < 1e-12 and not hit
    assert abs(brier - (0.04 + 0.25 + 0.49)) < 1e-12
    ll, hit, _ = F.score({"a": 0.0, "b": 1.0}, "a", ["a", "b"])
    assert ll == -math.log(F.FLOOR), "floored, never infinite"
    assert F.score({"a": 0.5, "b": 0.5}, "a", ["a", "b"])[1], "a tie goes to the lower bucket"


def test_the_market_is_compared_only_complete_fresh_and_after_the_decision():
    bands = [{"band_id": "x"}, {"band_id": "y"}]
    t = 1_000_000
    series = {"x": [(t - 60, 0.9), (t + 600, 0.6)], "y": [(t + 1200, 0.4)]}
    m = F.market_ladder(series, bands, t)
    assert m == {"x": 0.6, "y": 0.4}, "the first price at or after the decision, not the stale one before"
    assert F.market_ladder({"x": [(t + 600, 0.6)]}, bands, t) is None, "a bucket with no price"
    assert F.market_ladder({"x": [(t + 600, 0.6)], "y": [(t + 4000, 0.4)]}, bands, t) is None, "older than an hour"
    assert F.market_ladder({"x": [(t, 0.9)], "y": [(t, 0.9)]}, bands, t) is None, "a sum of 1.8 is not a ladder"


def test_the_bootstrap_moves_a_date_with_all_its_rows():
    # one date carries every positive value: a row bootstrap would narrow this,
    # a date bootstrap must straddle it
    pairs = [("d1", 1.0)] * 50 + [(f"d{i}", -0.02) for i in range(2, 22)]
    b = F.cluster_boot(pairs, n=400)
    assert b["dates"] == 21 and b["n"] == 70
    assert b["ci90"][0] < 0 < b["ci90"][1]


def test_a_wilson_interval():
    lo, hi = F.wilson(50, 100)
    assert lo < 0.5 < hi and abs((lo + hi) / 2 - 0.5) < 1e-9
    assert F.wilson(0, 0) is None
