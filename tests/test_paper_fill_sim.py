"""Order manager and maker fill simulator (plan v2 P5.7), on recorded trade prints."""
import json
import pathlib

import pytest

import paper_fill_sim as fs

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures" / "trade_prints_23sep.json").read_text())
PRINTS = FIX["prints"]
TOKEN = PRINTS[0]["token_id"]


def _bid(price, shares, queue, placed="2026-09-23T12:30:00+00:00", expires="2026-09-23T13:30:00+00:00"):
    return {"token_id": TOKEN, "price": price, "shares": shares, "queue_ahead": queue,
            "placed_at": placed, "expires_at": expires}


# ---- fills on the recorded prints ------------------------------------------------

def test_a_bid_fills_only_after_the_queue_ahead_is_used_up():
    # 12:39:31 printed 52.1 at 0.6558, the only print at or below 0.66 inside the hour.
    r = fs.simulate_bid(_bid(0.66, 20, queue=30), PRINTS)
    assert r["status"] == "filled" and r["filled"] == pytest.approx(20)
    assert [f["price"] for f in r["fills"]] == [0.66]                 # filled at the bid, not the print
    assert str(r["fills"][0]["at"]) == "2026-09-23 12:39:31+00:00"


def test_a_partial_fill_when_the_volume_past_the_queue_is_short():
    r = fs.simulate_bid(_bid(0.66, 40, queue=30), PRINTS)
    assert r["status"] == "partial" and r["filled"] == pytest.approx(52.1 - 30)


def test_no_fill_without_prints_at_or_below_the_bid():
    # Nothing printed at or below 0.60 between 12:30 and 13:30 (0.6 printed at 12:29:40, before placement).
    r = fs.simulate_bid(_bid(0.60, 10, queue=0), PRINTS)
    assert r["status"] == "unfilled" and r["fills"] == []


def test_a_print_before_placement_or_after_expiry_does_not_fill():
    r = fs.simulate_bid(_bid(0.60, 10, queue=0, placed="2026-09-23T12:29:40+00:00"), PRINTS)
    assert r["status"] == "unfilled"                                  # the 12:29:40 print is AT placement, not after
    r = fs.simulate_bid(_bid(0.60, 10, queue=0, expires="2026-09-23T14:00:00+00:00"), PRINTS)
    assert r["filled"] == pytest.approx(9.45)                         # 13:49:53 at 0.44, inside the longer life


def test_a_deep_queue_leaves_the_bid_unfilled():
    assert fs.simulate_bid(_bid(0.66, 20, queue=60), PRINTS)["status"] == "unfilled"


def test_other_tokens_and_unattributed_prints_are_not_used():
    extra = [{"token_id": "other", "traded_at": "2026-09-23T12:45:00+00:00", "price": "0.10", "size": "999"},
             {"token_id": None, "traded_at": "2026-09-23T12:45:00+00:00", "price": "0.10", "size": "999"}]
    r = fs.simulate_bid(_bid(0.60, 10, queue=0), PRINTS + extra)
    assert r["status"] == "unfilled" and r["unattributed_prints"] == 1


def test_queue_ahead_is_the_depth_at_the_bid_or_better():
    bids = [{"price": "0.66", "size": "10"}, {"price": "0.67", "size": "5"}, {"price": "0.65", "size": "100"}]
    assert fs.queue_ahead(bids, 0.66) == 15


# ---- adverse selection -----------------------------------------------------------

def test_the_mid_fifteen_minutes_after_a_fill_is_recorded():
    fills = fs.simulate_bid(_bid(0.66, 20, queue=30), PRINTS)["fills"]
    books = [{"observed_at": "2026-09-23T12:50:00+00:00", "best_bid": 0.60, "best_ask": 0.64},   # 10:29 after: too early
             {"observed_at": "2026-09-23T12:56:00+00:00", "best_bid": 0.74, "best_ask": 0.80}]
    (m,) = fs.adverse_marks(fills, books)
    assert m["mid_after"] == pytest.approx(0.77) and m["adverse"] == pytest.approx(0.66 - 0.77)


def test_no_snapshot_in_the_window_means_no_mark():
    fills = fs.simulate_bid(_bid(0.66, 20, queue=30), PRINTS)["fills"]
    (m,) = fs.adverse_marks(fills, [{"observed_at": "2026-09-23T15:00:00+00:00", "best_bid": 0.5, "best_ask": 0.6}])
    assert m["mid_after"] is None and m["adverse"] is None


# ---- order type and legs ---------------------------------------------------------

@pytest.mark.parametrize("g_taker,g_maker,hours,spread,want", [
    (0.010, 0.012, 5.0, 0.05, "LIMIT"),
    (0.010, 0.010, 5.0, 0.05, "LIMIT"),        # a tie goes to the maker
    (0.012, 0.010, 5.0, 0.05, "IOC"),
    (0.010, 0.050, 2.0, 0.05, "IOC"),          # under 3 h to close: resting is not considered
    (0.010, 0.050, 5.0, 0.03, "IOC"),          # spread under 4c: resting is not considered
    (-0.01, None, 5.0, 0.05, None),
])
def test_the_order_type(g_taker, g_maker, hours, spread, want):
    assert fs.choose_order_type(g_taker, g_maker, hours, spread)[0] == want


def test_linked_legs_go_least_liquid_first():
    legs = [{"band_id": "a", "depth_usd": 500}, {"band_id": "b", "depth_usd": 40}, {"band_id": "c", "depth_usd": None}]
    assert [l["band_id"] for l in fs.leg_order(legs)] == ["c", "b", "a"]
