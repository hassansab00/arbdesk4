"""
AD4 backtest metrics (Task 12). Pure aggregation over a list of simulated
trades - independently testable, no network. `engine.py` produces the
trade list this operates on; `runner.py` writes the results to
`backtest_results` by scope: headline, strategy, city, calibration, costs,
distribution, signal_frequency, equity_curve.

Every trade dict is expected to carry at least: strategy_id, city_key,
band_id, side, shares, avg_fill_price, exit_price, gross_pnl, net_pnl,
fee_paid, gas_paid, model_prob, won (bool), resolution_date.
"""
import math
from collections import defaultdict


def headline(trades):
    if not trades:
        return {"n_trades": 0, "total_net_pnl": 0.0, "total_gross_pnl": 0.0,
                "win_rate": None, "avg_net_pnl_per_trade": 0.0,
                "note": "no trades - too few to mean anything, not shown as a result"}
    n = len(trades)
    total_net = sum(t["net_pnl"] for t in trades)
    total_gross = sum(t["gross_pnl"] for t in trades)
    wins = sum(1 for t in trades if t.get("won"))
    return {
        "n_trades": n, "total_net_pnl": total_net, "total_gross_pnl": total_gross,
        "win_rate": wins / n, "avg_net_pnl_per_trade": total_net / n,
    }


MIN_TRADES_FOR_RATE = 20   # "too few trades for a result to mean anything" - the spec's own phrase


def _group(trades, key_fn):
    out = defaultdict(list)
    for t in trades:
        out[key_fn(t)].append(t)
    return out


def by_strategy(trades):
    """Per-strategy attribution. Never merged with microstructure edge -
    each strategy's numbers stand alone."""
    out = {}
    for strategy_id, group in _group(trades, lambda t: t["strategy_id"]).items():
        h = headline(group)
        h["insufficient_sample"] = h["n_trades"] < MIN_TRADES_FOR_RATE
        out[strategy_id] = h
    return out


def by_city(trades):
    out = {}
    for city_key, group in _group(trades, lambda t: t["city_key"]).items():
        h = headline(group)
        h["insufficient_sample"] = h["n_trades"] < MIN_TRADES_FOR_RATE
        out[city_key] = h
    return out


def costs(trades):
    """Gross vs net, the one place gross is shown, as the contrast."""
    total_fee = sum(t.get("fee_paid") or 0.0 for t in trades)
    total_gas = sum(t.get("gas_paid") or 0.0 for t in trades)
    total_slippage_usd = sum((t.get("slippage_paid") or 0.0) * t.get("shares", 0.0) for t in trades)
    total_gross = sum(t["gross_pnl"] for t in trades)
    total_net = sum(t["net_pnl"] for t in trades)
    return {
        "total_gross_pnl": total_gross, "total_net_pnl": total_net,
        "total_fees": total_fee, "total_gas": total_gas, "total_slippage_usd": total_slippage_usd,
        "cost_drag_pct": (100.0 * (total_gross - total_net) / abs(total_gross)) if total_gross else None,
    }


def distribution(trades, bucket_width=10.0):
    """Histogram of net_pnl per trade, bucketed to `bucket_width`-wide bins."""
    buckets = defaultdict(int)
    for t in trades:
        b = math.floor(t["net_pnl"] / bucket_width) * bucket_width
        buckets[b] += 1
    return dict(sorted(buckets.items()))


def signal_frequency(signals):
    """Per-strategy signal counts - fired, and the fraction that were ENTER vs EXIT."""
    out = defaultdict(lambda: {"fired": 0, "enter": 0, "exit": 0})
    for s in signals:
        row = out[s["strategy_id"]]
        row["fired"] += 1
        row["enter" if s.get("action") == "ENTER" else "exit"] += 1
    return dict(out)


def equity_curve(trades, starting_budget):
    """Cumulative net P&L over time, plus running drawdown from the peak."""
    ordered = sorted(trades, key=lambda t: t.get("resolution_date") or "")
    equity = starting_budget
    peak = starting_budget
    curve = []
    for t in ordered:
        equity += t["net_pnl"]
        peak = max(peak, equity)
        drawdown = equity - peak
        curve.append({"resolution_date": t.get("resolution_date"), "equity": equity,
                      "drawdown": drawdown, "drawdown_pct": (drawdown / peak) if peak else 0.0})
    return curve


def calibration(trades, n_bins=10):
    """
    Reliability diagram: bucket trades by model_prob into n_bins, compare
    each bucket's average predicted probability to its actual win rate.
    Needs `model_prob` and `won` on every trade - both already available
    from paper_trades/settlement, so this needs no separate data source.
    """
    bins = defaultdict(list)
    for t in trades:
        if t.get("model_prob") is None or t.get("won") is None:
            continue
        idx = min(int(t["model_prob"] * n_bins), n_bins - 1)
        bins[idx].append(t)
    out = []
    for idx in sorted(bins):
        group = bins[idx]
        n = len(group)
        avg_pred = sum(t["model_prob"] for t in group) / n
        actual_rate = sum(1 for t in group if t["won"]) / n
        out.append({"bin": idx, "n": n, "avg_predicted_prob": avg_pred, "actual_win_rate": actual_rate,
                    "overconfidence": avg_pred - actual_rate, "insufficient_sample": n < MIN_TRADES_FOR_RATE})
    return out
