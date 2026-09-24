"""The hard floor gate (plan v2 P5.0 item 5).

Never buy YES on a bucket the day has already passed. The probability engine
prices such a band at the floor, but the P3.1 measurement layer can leave it a
small non-floor probability, and edge_engine had no rule of its own: only the
generic prob_at_floor check stood in the way. These hold the gate to
probability_engine's own impossible_band_ids, on the city's local today only,
and to a floor that is never a model value.
"""
import pathlib

import edge_engine as ee
import probability_engine as pe

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _ladder(market="m1", lows=range(20, 27)):
    bands = [{"band_id": f"{market}-lo", "market_id": market, "band_lo": None, "band_hi": 20,
              "open_low": True, "open_high": False}]
    bands += [{"band_id": f"{market}-{lo}", "market_id": market, "band_lo": lo, "band_hi": lo + 1,
               "open_low": False, "open_high": False} for lo in lows]
    bands.append({"band_id": f"{market}-hi", "market_id": market, "band_lo": 27, "band_hi": None,
                  "open_low": False, "open_high": True})
    return bands


def test_buckets_two_or_more_under_the_reading_are_impossible_today():
    markets = [{"market_id": "m1", "city_key": "london", "resolution_date": "2026-09-24"}]
    got = ee.floor_impossible_ids(markets, _ladder(), {"london": ("2026-09-24", 24.2)}, {"london": "C"})
    # read 24 C: the 24 bucket holds it, 23 is one below (a station can still
    # disagree by one), 22 and everything under it cannot happen
    assert got == {"m1-lo", "m1-20", "m1-21", "m1-22"}


def test_tomorrow_is_never_judged_on_today_s_floor():
    markets = [{"market_id": "m1", "city_key": "london", "resolution_date": "2026-09-25"}]
    assert ee.floor_impossible_ids(markets, _ladder(), {"london": ("2026-09-24", 24.2)},
                                   {"london": "C"}) == set()


def test_no_floor_means_nothing_is_impossible():
    markets = [{"market_id": "m1", "city_key": "london", "resolution_date": "2026-09-24"}]
    assert ee.floor_impossible_ids(markets, _ladder(), {}, {"london": "C"}) == set()


def test_a_model_reading_is_never_a_floor():
    """The floor comes from measured_floor, which refuses a model value above
    the station series (P2.7) - so it can never make a band impossible."""
    row = {"running_max_c": 30.0, "observed_max_today_c": 24.2, "live_source_kind": "model"}
    assert pe.measured_floor(row) == 24.2
    assert pe.measured_floor(dict(row, observed_max_today_c=None)) is None


def test_the_gate_blocks_yes_only_and_is_the_reason_shown():
    src = (ROOT / "scripts" / "edge_engine.py").read_text()
    loop = src[src.index("for side in (\"YES\", \"NO\"):"):src.index("out_rows.append(")]
    gate = loop[loop.index('if side == "YES" and (band_id in impossible'):]
    assert 'block_reason = "floor_impossible"' in gate
    assert loop.index("if book_is_stale:") < loop.index('if side == "YES" and (band_id in impossible'), (
        "applied last, so the permanent reason is the one shown")
    assert "pe._observed_floors()" in src, "the engine's own measured floors, not a second reading"


def test_without_floors_it_fails_closed_on_today_only():
    src = (ROOT / "scripts" / "edge_engine.py").read_text()
    assert 'else "floor_unknown"' in src
    assert "city_local_date(" in src[src.index("today_markets"):src.index("today_markets") + 400]
