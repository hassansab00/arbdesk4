import math

import paper_engine as pe
from strategies.base import Signal


def make_signal(band_id="b1", side="YES", price=0.30, prob=0.45, shares=100, payload=None):
    return Signal(strategy_id="s1_buy_low_sell_signal", band_id=band_id, side=side, action="ENTER",
                  reason="test", price_at_fire=price, prob_at_fire=prob, edge_at_fire=prob - price,
                  suggested_shares=shares, confidence=0.8, regime_label="SHARP", severity="high",
                  dedupe_key="k1", payload=payload or {})


def test_partial_fill_when_book_is_thin():
    levels = [{"price": 0.30, "size": 50}]
    fill = pe.fill_single_leg(levels, requested_shares=200, max_slippage=0.05)
    assert fill["fully_filled"] is False
    assert fill["shares"] == 50
    assert math.isclose(fill["fill_quality"], 0.25)


def test_full_fill_reports_quality_one():
    levels = [{"price": 0.30, "size": 500}]
    fill = pe.fill_single_leg(levels, requested_shares=100, max_slippage=0.05)
    assert fill["fully_filled"] is True
    assert math.isclose(fill["fill_quality"], 1.0)


def test_slippage_is_reported_not_hidden():
    levels = [{"price": 0.30, "size": 10}, {"price": 0.40, "size": 100}]
    fill = pe.fill_single_leg(levels, requested_shares=50, max_slippage=0.20)
    assert fill["avg_price"] > 0.30  # walked past the touch
    assert fill["slippage"] > 0


def test_legging_risk_basket_can_partially_complete():
    legs = [
        {"band_id": "b1", "side": "YES", "levels": [{"price": 0.20, "size": 1000}], "requested_shares": 100},
        {"band_id": "b2", "side": "YES", "levels": [{"price": 0.25, "size": 5}], "requested_shares": 100},
        {"band_id": "b3", "side": "YES", "levels": [], "requested_shares": 100},
    ]
    basket = pe.fill_basket(legs, max_slippage=0.05)
    assert basket["legs_requested"] == 3
    assert basket["legs_filled"] == 1          # only b1 fully filled
    assert basket["complete"] is False          # a real basket strategy can end up incomplete
    assert 0 < basket["fill_quality"] < 1


def test_kelly_fraction_zero_when_no_edge():
    # price already equals fair value -> no edge -> Kelly fraction is 0
    assert math.isclose(pe.kelly_fraction(model_prob=0.30, price=0.30), 0.0, abs_tol=1e-9)


def test_kelly_fraction_positive_with_real_edge():
    f = pe.kelly_fraction(model_prob=0.50, price=0.30)
    assert f > 0


def test_risk_limits_warn_but_never_block():
    warnings = pe.check_risk_limits(requested_usd=5000, bankroll=10000, model_prob=0.35,
                                     price=0.30, risk_limits={"max_per_band_pct": 5})
    assert len(warnings) >= 1
    assert isinstance(warnings, list)  # never a bool/blocked signal - always just warnings


def test_no_warnings_within_limits():
    warnings = pe.check_risk_limits(requested_usd=100, bankroll=10000, model_prob=0.31,
                                     price=0.30, risk_limits={"max_per_band_pct": 5})
    assert warnings == []


def test_full_lifecycle_not_auto_approved_still_logs_signal():
    signal = make_signal()
    portfolio = pe.Portfolio(bankroll=10000)
    levels_by_side = {"YES": [{"price": 0.30, "size": 1000}]}
    result = pe.process_signal(signal, levels_by_side, portfolio, settings={"auto_approve": {"value": False}},
                                versions={}, max_slippage=0.05)
    assert result["status"] == "pending_approval"
    assert result["signal_row"] is not None      # every fired signal is logged, approved or not
    assert result["trade_row"] is None


def test_full_lifecycle_auto_approved_creates_trade_and_ledger():
    signal = make_signal()
    portfolio = pe.Portfolio(bankroll=10000)
    levels_by_side = {"YES": [{"price": 0.30, "size": 1000}]}
    result = pe.process_signal(signal, levels_by_side, portfolio, settings={"auto_approve": {"value": True}},
                                versions={"forecast_version": "fv1", "calibration_version": "cv1"},
                                max_slippage=0.05)
    assert result["status"] == "filled"
    assert result["trade_row"]["shares"] > 0
    assert result["trade_row"]["partial_fill"] is False
    assert len(result["ledger_rows"]) == 4
    assert len(portfolio.open_positions) == 1


def test_close_position_computes_net_pnl_below_gross():
    position = {"strategy_id": "s1_buy_low_sell_signal", "band_id": "b1", "side": "YES",
                "shares": 100, "avg_fill_price": 0.30}
    result = pe.close_position(position, exit_price=0.60, reason="target_converged", versions={})
    assert result["pnl"]["gross_pnl"] == 30.0
    assert result["pnl"]["net_pnl"] < result["pnl"]["gross_pnl"]


def test_compounding_grows_bankroll_only_when_enabled():
    p_off = pe.Portfolio(bankroll=1000, compounding=False)
    p_off.apply_realized_pnl(100)
    assert p_off.bankroll == 1000

    p_on = pe.Portfolio(bankroll=1000, compounding=True)
    p_on.apply_realized_pnl(100)
    assert p_on.bankroll == 1100


def test_open_exposure_sums_positions():
    portfolio = pe.Portfolio(bankroll=1000, open_positions=[
        {"shares": 100, "avg_fill_price": 0.3}, {"shares": 50, "avg_fill_price": 0.5},
    ])
    assert math.isclose(portfolio.open_exposure_usd(), 100 * 0.3 + 50 * 0.5)
