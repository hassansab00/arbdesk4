import datetime as dt
import math

from backtest import metrics
from backtest.engine import evaluation_instant, pick_latest_row_by, pick_shortest_lead, simulate_city_day
from strategies.base import StrategyConfig
import paper_engine
import regime as regime_module


def test_evaluation_instant_is_before_resolution_date():
    d = dt.date(2026, 8, 30)
    as_of = evaluation_instant(d, evaluation_lead_days=1)
    assert as_of.date() == dt.date(2026, 8, 29)


def test_pick_latest_row_by_respects_as_of():
    rows = [
        {"run_at": "2026-08-27T00:00:00Z", "v": 1},
        {"run_at": "2026-08-28T00:00:00Z", "v": 2},
        {"run_at": "2026-08-29T00:00:00Z", "v": 3},   # after as_of - must not be picked
    ]
    as_of = dt.datetime(2026, 8, 28, 12, tzinfo=dt.timezone.utc)
    picked = pick_latest_row_by(rows, "run_at", as_of)
    assert picked["v"] == 2   # not 3 - no lookahead


def test_pick_latest_row_by_empty_when_nothing_eligible():
    rows = [{"run_at": "2026-09-01T00:00:00Z", "v": 1}]
    as_of = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    assert pick_latest_row_by(rows, "run_at", as_of) is None


def test_pick_shortest_lead():
    rows = [{"lead_days": 3, "v": "far"}, {"lead_days": 1, "v": "near"}]
    assert pick_shortest_lead(rows)["v"] == "near"


def closed_bands():
    return [
        {"band_id": "b23", "band_lo": 23, "band_hi": 24, "open_low": False, "open_high": False, "band_label": "23-24C"},
        {"band_id": "b24", "band_lo": 24, "band_hi": 25, "open_low": False, "open_high": False, "band_label": "24-25C"},
        {"band_id": "b25", "band_lo": 25, "band_hi": 26, "open_low": False, "open_high": False, "band_label": "25-26C"},
    ]


def s2_config():
    return StrategyConfig(strategy_id="s2_combination_arb", name="s2", side="BOTH", universe=["ALL"],
                           regime_filter=[], conflict_class="arb", capital_cap_pct=10.0,
                           max_concurrent=10, enabled=True)


def test_simulate_city_day_no_forecast_returns_empty():
    reg = regime_module.RegimeResult("paris", "2026-08-30", "SHARP", 0.9, 1.0, 1.0, True, 5.0, [], {})
    result = simulate_city_day("paris", dt.date(2026, 8, 30), "C", closed_bands(), [], None, reg,
                                {}, 24.3, [s2_config()], [], as_of=dt.datetime(2026, 8, 29, tzinfo=dt.timezone.utc))
    assert result == dict(trades=[], signals=[], conflicts=[])


def test_simulate_city_day_arb_produces_a_settled_trade():
    reg = regime_module.RegimeResult("paris", "2026-08-30", "SHARP", 0.9, 1.0, 1.0, True, 5.0, [], {})
    forecast_rows = [{"lead_days": 1, "forecast_max_c": 24.3, "model": "open_meteo_best_match",
                       "run_at": "2026-08-29T06:00:00Z", "for_date": "2026-08-30"}]
    skill_row = {"mae_c": 1.0, "bias_c": 0.0, "n_days": 300}
    # Cheap, deep book on every band -> S2's all-YES basket should be a
    # guaranteed riskless arb (3 x 0.20 = 0.60 << 1.0).
    book = {"best_bid": 0.15, "best_ask": 0.20,
            "ask_levels": [{"price": 0.20, "size": 5000}], "bid_levels": [{"price": 0.15, "size": 5000}]}
    book_by_band = {"b23": book, "b24": book, "b25": book}

    portfolio = paper_engine.Portfolio(bankroll=10000.0)
    result = simulate_city_day("paris", dt.date(2026, 8, 30), "C", closed_bands(), forecast_rows, skill_row,
                                reg, book_by_band, actual_settled_value=24.3, strategy_configs=[s2_config()],
                                open_positions=[], as_of=dt.datetime(2026, 8, 29, 12, tzinfo=dt.timezone.utc),
                                portfolio=portfolio)

    assert len(result["trades"]) >= 1
    trade = result["trades"][0]
    assert trade["strategy_id"] == "s2_combination_arb"
    assert trade["legs_filled"] == 3   # all three legs of the covering basket filled
    assert trade["net_pnl"] > 0        # a genuine riskless arb nets positive after fees
    assert trade["won"] is True        # "won" for a basket means net-profitable, not one band's outcome


def test_simulate_city_day_no_book_data_produces_no_trades():
    # Before 22 Aug 2026 there's no book depth history at all - this must
    # produce zero trades, not a fabricated fill.
    reg = regime_module.RegimeResult("paris", "2026-01-15", "SHARP", 0.9, 1.0, 1.0, True, 5.0, [], {})
    forecast_rows = [{"lead_days": 1, "forecast_max_c": 24.3, "model": "open_meteo_best_match",
                       "run_at": "2026-01-14T06:00:00Z", "for_date": "2026-01-15"}]
    skill_row = {"mae_c": 1.0, "bias_c": 0.0, "n_days": 300}
    result = simulate_city_day("paris", dt.date(2026, 1, 15), "C", closed_bands(), forecast_rows, skill_row,
                                reg, {}, actual_settled_value=24.3, strategy_configs=[s2_config()],
                                open_positions=[], as_of=dt.datetime(2026, 1, 14, 12, tzinfo=dt.timezone.utc))
    assert result["trades"] == []


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

def sample_trades():
    return [
        {"strategy_id": "s2", "city_key": "paris", "band_id": "b1", "net_pnl": 10.0, "gross_pnl": 12.0,
         "fee_paid": 2.0, "gas_paid": 0.01, "slippage_paid": 0.0, "shares": 100, "model_prob": 0.6,
         "won": True, "resolution_date": "2026-08-29"},
        {"strategy_id": "s2", "city_key": "tokyo", "band_id": "b2", "net_pnl": -5.0, "gross_pnl": -4.0,
         "fee_paid": 1.0, "gas_paid": 0.01, "slippage_paid": 0.01, "shares": 50, "model_prob": 0.4,
         "won": False, "resolution_date": "2026-08-30"},
    ]


def test_headline_empty_says_so_rather_than_a_percentage():
    result = metrics.headline([])
    assert result["n_trades"] == 0
    assert "note" in result


def test_headline_computes_win_rate_and_totals():
    result = metrics.headline(sample_trades())
    assert result["n_trades"] == 2
    assert result["win_rate"] == 0.5
    assert result["total_net_pnl"] == 5.0


def test_by_strategy_flags_insufficient_sample():
    result = metrics.by_strategy(sample_trades())
    assert result["s2"]["insufficient_sample"] is True   # 2 trades, well under the min


def test_costs_shows_gross_and_net():
    result = metrics.costs(sample_trades())
    assert result["total_gross_pnl"] == 8.0
    assert result["total_net_pnl"] == 5.0
    assert result["total_fees"] == 3.0


def test_signal_frequency_counts_enter_and_exit():
    signals = [{"strategy_id": "s1", "action": "ENTER"}, {"strategy_id": "s1", "action": "ENTER"},
               {"strategy_id": "s1", "action": "EXIT"}]
    result = metrics.signal_frequency(signals)
    assert result["s1"] == {"fired": 3, "enter": 2, "exit": 1}


def test_equity_curve_tracks_drawdown():
    trades = [
        {"net_pnl": 100.0, "resolution_date": "2026-08-01"},
        {"net_pnl": -150.0, "resolution_date": "2026-08-02"},
    ]
    curve = metrics.equity_curve(trades, starting_budget=1000.0)
    assert curve[0]["equity"] == 1100.0
    assert curve[1]["equity"] == 950.0
    assert curve[1]["drawdown"] == 950.0 - 1100.0


def test_calibration_flags_overconfidence():
    trades = [{"model_prob": 0.9, "won": False}] * 5 + [{"model_prob": 0.9, "won": True}] * 5
    result = metrics.calibration(trades, n_bins=10)
    row = result[0]
    assert math.isclose(row["avg_predicted_prob"], 0.9)
    assert row["actual_win_rate"] == 0.5
    assert row["overconfidence"] > 0.3
