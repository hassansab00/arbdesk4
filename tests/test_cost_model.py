import math

import cost_model as cm


def test_taker_fee_peaks_at_half():
    assert math.isclose(cm.taker_fee(100, 0.50), 1.25)


def test_taker_fee_low_near_extremes():
    assert cm.taker_fee(100, 0.01) < 0.06
    assert cm.taker_fee(100, 0.99) < 0.06


def test_settlement_is_free():
    assert cm.taker_fee(1000, 1.0) == 0
    assert cm.taker_fee(1000, 0.0) == 0


def test_maker_fee_is_zero():
    assert cm.maker_fee(500, 0.37) == 0.0


def test_maker_rebate():
    assert math.isclose(cm.maker_rebate(1.25, 0.25), 0.3125)


def test_walk_ladder_thin_book_partial_fill():
    levels = [{"price": 0.10, "size": 50}, {"price": 0.12, "size": 30}]
    result = cm.walk_ladder(levels, shares_target=200)
    assert result["fully_filled"] is False
    assert result["shares"] == 80
    assert result["levels_consumed"] == 2


def test_walk_ladder_full_fill_within_budget():
    levels = [{"price": 0.10, "size": 100}, {"price": 0.11, "size": 100}]
    result = cm.walk_ladder(levels, usd_budget=15.0)
    assert result["fully_filled"] is True
    assert result["shares"] > 100
    assert result["avg_price"] > 0.10


def test_walk_ladder_respects_max_slippage_and_stops():
    levels = [
        {"price": 0.10, "size": 10},
        {"price": 0.20, "size": 10},   # 10c above touch - beyond slippage cap
        {"price": 0.21, "size": 1000},
    ]
    result = cm.walk_ladder(levels, shares_target=1000, max_slippage=0.02)
    assert result["fully_filled"] is False
    assert result["shares"] == 10
    assert result["levels_consumed"] == 1


def test_walk_ladder_empty_book():
    result = cm.walk_ladder([], shares_target=100)
    assert result["fully_filled"] is False
    assert result["shares"] == 0.0
    assert result["quoted_price"] is None


def test_round_trip_cost_wide_spread_exceeds_gross_edge():
    shares = 100
    entry_price = exit_price = 0.50
    gross_edge = shares * 0.10  # a "10pp edge" on 100 shares
    costs = cm.round_trip_cost(shares, entry_price, exit_price, spread=0.15)
    assert costs["total_cost"] > gross_edge


def test_round_trip_cost_maker_both_legs_has_no_fee_or_spread():
    costs = cm.round_trip_cost(100, 0.5, 0.5, entry_is_taker=False,
                                exit_is_taker=False, spread=0.15, gas_usd=0.01)
    assert costs["entry_fee"] == 0.0
    assert costs["exit_fee"] == 0.0
    assert costs["entry_spread_cost"] == 0.0
    assert costs["exit_spread_cost"] == 0.0
    assert costs["total_cost"] == 0.01


def test_net_pnl_from_round_trip_costs():
    costs = cm.round_trip_cost(100, 0.30, 0.30, spread=0.0, gas_usd=0.0)
    result = cm.net_pnl(100, 0.30, 0.55, costs)
    assert math.isclose(result["gross_pnl"], 25.0)
    assert result["net_pnl"] < result["gross_pnl"]


def test_net_pnl_accepts_plain_number_costs():
    result = cm.net_pnl(100, 0.30, 0.55, costs=2.5)
    assert math.isclose(result["gross_pnl"], 25.0)
    assert math.isclose(result["net_pnl"], 22.5)
