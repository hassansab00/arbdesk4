"""Settlement and profit are different questions.

The backtest recorded one field, `won = net_pnl > 0`, and calibration scored
model_prob against it. model_prob is the probability that a BUCKET CONTAINS
THE DAY'S HIGH - a statement about weather. Scoring it against money charges
the forecast for the spread, the fee and the gas, so a perfectly calibrated
model trading into a wide book reads as overconfident. ad4_45 then feeds that
reading back as a sigma multiplier and widens a distribution that was already
correct.

These tests pin the separation, and pin the fallback that keeps old rows -
written before the split - still rendering.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from backtest import metrics


def _t(**kw):
    base = {"strategy_id": "S1", "city_key": "nyc", "band_id": "b1", "side": "YES",
            "shares": 100.0, "avg_fill_price": 0.5, "gross_pnl": 0.0, "net_pnl": 0.0,
            "fee_paid": 0.0, "gas_paid": 0.0, "model_prob": 0.5,
            "resolution_date": "2026-09-01"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# The case that motivated the split
# ---------------------------------------------------------------------------
def test_a_correct_forecast_that_lost_money_is_not_miscalibrated():
    """Every contract settled our way; every trade lost money to costs.

    Calibration must read 1.0 actual against 0.9 predicted - slightly
    UNDER-confident - not 0.0, which is what scoring against profit gave.
    """
    trades = [_t(model_prob=0.9, settled_winner=True,
                 profitable_after_costs=False, net_pnl=-3.0) for _ in range(40)]

    cal = metrics.calibration(trades)
    assert len(cal) == 1
    row = cal[0]
    assert row["actual_win_rate"] == 1.0, (
        "the contracts settled our way - calibration must say so"
    )
    assert row["overconfidence"] < 0, (
        "predicting 0.9 on something that happened every time is "
        "under-confidence, not over-confidence"
    )


def test_the_old_behaviour_would_have_called_it_maximally_overconfident():
    """Guards the regression directly: profit-scored calibration is wrong."""
    trades = [_t(model_prob=0.9, settled_winner=True,
                 profitable_after_costs=False, net_pnl=-3.0) for _ in range(40)]

    profit_scored = sum(1 for t in trades if t["profitable_after_costs"]) / len(trades)
    settlement_scored = metrics.calibration(trades)[0]["actual_win_rate"]

    assert profit_scored == 0.0
    assert settlement_scored == 1.0
    assert settlement_scored != profit_scored, (
        "if these ever agree the test has stopped testing anything"
    )


def test_a_wrong_forecast_that_made_money_is_still_miscalibrated():
    """The mirror image: profit must not launder a bad forecast."""
    trades = [_t(model_prob=0.8, settled_winner=False,
                 profitable_after_costs=True, net_pnl=5.0) for _ in range(30)]

    row = metrics.calibration(trades)[0]
    assert row["actual_win_rate"] == 0.0
    assert row["overconfidence"] == row["avg_predicted_prob"] > 0


# ---------------------------------------------------------------------------
# Headline reports both, and they can disagree
# ---------------------------------------------------------------------------
def test_headline_reports_settlement_and_profit_separately():
    trades = ([_t(settled_winner=True, profitable_after_costs=False, net_pnl=-1.0)] * 3 +
              [_t(settled_winner=False, profitable_after_costs=True, net_pnl=2.0)])

    h = metrics.headline(trades)
    assert h["settled_winner_rate"] == 0.75
    assert h["profitable_rate"] == 0.25
    assert h["n_settled"] == 4
    assert h["win_rate"] == h["profitable_rate"], (
        "win_rate keeps its old meaning - money - so nothing reading it moves"
    )


def test_settled_rate_is_none_when_nothing_settled():
    trades = [_t(settled_winner=None, profitable_after_costs=True, net_pnl=1.0)]
    h = metrics.headline(trades)
    assert h["settled_winner_rate"] is None
    assert h["n_settled"] == 0
    assert h["profitable_rate"] == 1.0


def test_unsettled_trades_are_excluded_from_calibration_not_counted_as_losses():
    """A day that never settled is missing evidence, not a failed prediction."""
    trades = ([_t(model_prob=0.7, settled_winner=True, profitable_after_costs=True)] * 10 +
              [_t(model_prob=0.7, settled_winner=None, profitable_after_costs=False)] * 10)

    row = metrics.calibration(trades)[0]
    assert row["n"] == 10, "the ten unsettled trades must not be scored"
    assert row["actual_win_rate"] == 1.0


# ---------------------------------------------------------------------------
# Old rows keep working
# ---------------------------------------------------------------------------
def test_rows_written_before_the_split_still_score():
    """backtest_trades rows predating this change carry only `won`."""
    trades = [_t(model_prob=0.6, won=True) for _ in range(25)]

    h = metrics.headline(trades)
    assert h["profitable_rate"] == 1.0

    row = metrics.calibration(trades)[0]
    assert row["n"] == 25, "legacy rows must not silently vanish from calibration"
    assert row["actual_win_rate"] == 1.0


def test_new_fields_win_over_the_legacy_alias_when_both_present():
    trades = [_t(model_prob=0.5, won=False, settled_winner=True,
                 profitable_after_costs=False) for _ in range(20)]
    assert metrics.calibration(trades)[0]["actual_win_rate"] == 1.0
    assert metrics.headline(trades)["profitable_rate"] == 0.0


# ---------------------------------------------------------------------------
# by_strategy / by_city inherit the split
# ---------------------------------------------------------------------------
def test_per_strategy_numbers_carry_both_rates():
    trades = ([_t(strategy_id="S1", settled_winner=True, profitable_after_costs=True)] * 5 +
              [_t(strategy_id="S7", settled_winner=True, profitable_after_costs=False)] * 5)

    out = metrics.by_strategy(trades)
    assert out["S1"]["profitable_rate"] == 1.0
    assert out["S7"]["profitable_rate"] == 0.0
    assert out["S7"]["settled_winner_rate"] == 1.0, (
        "S7 was right about the weather and lost on costs - that distinction "
        "is the whole point of the split"
    )


def test_engine_records_all_three_outcome_fields():
    """The writer must emit what the readers now expect."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).parent.parent / "scripts" / "backtest" / "engine.py"
    tree = ast.parse(src.read_text())

    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)

    for field in ("settled_winner", "profitable_after_costs", "closed_reason"):
        assert field in keys, f"engine.py never writes {field}"
