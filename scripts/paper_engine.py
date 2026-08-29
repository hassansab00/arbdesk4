"""
AD4 paper engine (Task 9).

This is where paper engines usually cheat. This one:
  - fills at EXECUTABLE price walking the ladder for the requested size
    (never mid, never top-of-book)
  - models PARTIAL fills when depth runs out
  - uses price AT SIGNAL TIME (the caller passes the book snapshot that
    was current when the signal fired, not whatever is live "now" -
    otherwise hindsight leaks in)
  - models multi-leg LEGGING RISK - a basket (S2, S6) may fill one leg and
    miss another; nothing here assumes all-or-nothing
  - applies the full cost model (Task 3)
  - respects max_slippage_cents

Trade lifecycle: signal fired -> logged to signals (ALWAYS, approved or
not) -> if auto_approve off, awaits approval -> if approved, paper_trades
row created -> marked open -> exit rules evaluated while open -> closed by
exit signal or settled at resolution (Task 11) -> gross/net P&L written ->
ledger entries appended at every step.
"""
import datetime as dt
from dataclasses import dataclass, field

import cost_model
from strategies.conflicts import city_day_exposure

PAPER_ENGINE_COST_VERSION = "polymarket_weather_2026_03"   # matches cost_params.label


# --------------------------------------------------------------------------
# Pure functions - fully unit-testable, no network.
# --------------------------------------------------------------------------

def fill_single_leg(levels, requested_shares, max_slippage):
    """One order against one book. Never assumes full fill."""
    result = cost_model.walk_ladder(levels, shares_target=requested_shares, max_slippage=max_slippage)
    result = dict(result)
    result["requested_shares"] = requested_shares
    result["fill_quality"] = (result["shares"] / requested_shares) if requested_shares else 0.0
    return result


def fill_basket(legs, max_slippage):
    """
    legs: list of {"band_id", "side", "levels", "requested_shares"}.
    Each leg is filled independently, sequentially, exactly as separate
    order submissions would be on a real exchange - this is what makes
    legging risk real instead of assumed away. A basket strategy (S2, S6)
    can end up holding an incomplete, no-longer-riskless position.
    """
    leg_results = []
    for leg in legs:
        fill = fill_single_leg(leg["levels"], leg["requested_shares"], max_slippage)
        leg_results.append({**leg, "fill": fill})

    legs_requested = len(legs)
    legs_filled = sum(1 for lr in leg_results if lr["fill"]["fully_filled"])
    avg_fill_quality = (sum(lr["fill"]["fill_quality"] for lr in leg_results) / legs_requested) if legs_requested else 0.0

    return dict(legs=leg_results, legs_requested=legs_requested, legs_filled=legs_filled,
                fill_quality=avg_fill_quality, complete=(legs_filled == legs_requested))


def kelly_fraction(model_prob, price):
    """
    Simplified Kelly criterion fraction of bankroll for a binary bet at
    price `price` with estimated win probability `model_prob`. Used only
    as the "what the maths supports" reference point for the risk warning
    below - PROVISIONAL, not a sizing rule anyone is forced to follow.
    """
    if price is None or model_prob is None or not (0 < price < 1):
        return 0.0
    b = (1.0 - price) / price
    q = 1.0 - model_prob
    f = (b * model_prob - q) / b if b else 0.0
    return max(0.0, min(f, 1.0))


def check_risk_limits(requested_usd, bankroll, model_prob, price, risk_limits):
    """
    Risk limits are Hassan's to set AND guarded by the system's own
    calculation. If his setting allows more than what the maths supports
    given bankroll/edge/probability, warn before the trade - his number
    stands, but never silently.

    Returns (warnings: list[str]). Never returns a "blocked" outcome -
    this function only ever warns, per the spec.
    """
    warnings = []
    if not bankroll:
        return warnings
    requested_pct = 100.0 * requested_usd / bankroll

    max_per_band_pct = (risk_limits or {}).get("max_per_band_pct")
    if max_per_band_pct is not None and requested_pct > max_per_band_pct:
        warnings.append(f"requested {requested_pct:.1f}% of bankroll exceeds "
                         f"configured max_per_band_pct={max_per_band_pct}%")

    kelly_pct = 100.0 * kelly_fraction(model_prob, price)
    if kelly_pct and requested_pct > kelly_pct:
        warnings.append(f"requested {requested_pct:.1f}% of bankroll exceeds the "
                         f"Kelly-implied {kelly_pct:.1f}% given current edge/probability")

    return warnings


def compute_fill_costs(shares, avg_price, is_taker, spread, gas_usd=cost_model.DEFAULT_GAS_USD):
    fee = cost_model.taker_fee(shares, avg_price) if is_taker else cost_model.maker_fee(shares, avg_price)
    spread_cost = shares * spread if (is_taker and spread) else 0.0
    return dict(fee=fee, spread_cost=spread_cost, gas=gas_usd, total=fee + spread_cost + gas_usd)


@dataclass
class Portfolio:
    bankroll: float
    compounding: bool = False
    open_positions: list = field(default_factory=list)
    realized_pnl: float = 0.0

    def apply_realized_pnl(self, net_pnl):
        self.realized_pnl += net_pnl
        if self.compounding:
            self.bankroll += net_pnl

    def open_exposure_usd(self):
        return sum(p.get("shares", 0.0) * p.get("avg_fill_price", 0.0) for p in self.open_positions)


def build_paper_trade_row(signal, fill, portfolio, versions, approved_by_user=True,
                           legs_requested=1, legs_filled=1):
    """Assembles a paper_trades row per the spec's trade-lifecycle field list."""
    return {
        "strategy_id": signal.strategy_id, "band_id": signal.band_id, "side": signal.side,
        "action": signal.action, "shares": fill["shares"], "avg_fill_price": fill["avg_price"],
        "quoted_price": fill["quoted_price"], "slippage_paid": fill.get("slippage"),
        "fee_paid": fill.get("fee"), "gas_paid": fill.get("gas"),
        "partial_fill": not fill["fully_filled"], "requested_shares": fill["requested_shares"],
        "legs_requested": legs_requested, "legs_filled": legs_filled,
        "fill_quality": fill["fill_quality"], "max_slippage_setting": fill.get("max_slippage_setting"),
        "cost_version": versions.get("cost_version", PAPER_ENGINE_COST_VERSION),
        "forecast_version": versions.get("forecast_version"),
        "calibration_version": versions.get("calibration_version"),
        "regime_label": signal.regime_label, "approved_by_user": approved_by_user,
        "opened_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def ledger_entry(stage, trade_row, extra=None):
    """One append-only chain link: signal -> decision -> order -> fill ->
    mark -> exit|settlement -> realised net P&L. Forecast edge and
    microstructure edge are attributed separately - never merged."""
    row = {
        "stage": stage, "strategy_id": trade_row.get("strategy_id"),
        "band_id": trade_row.get("band_id"), "regime_label": trade_row.get("regime_label"),
        "forecast_version": trade_row.get("forecast_version"),
        "calibration_version": trade_row.get("calibration_version"),
        "cost_version": trade_row.get("cost_version"),
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if extra:
        row.update(extra)
    return row


# --------------------------------------------------------------------------
# I/O - wires the above to Supabase. Kept thin; every decision above is a
# pure, independently-tested function.
# --------------------------------------------------------------------------

def process_signal(signal, levels_by_side, portfolio, settings, versions, max_slippage,
                    is_taker=True, spread=0.0, legs=None):
    """
    Runs one fired signal through the full lifecycle up to (but not
    including) the Supabase writes, which the caller (main(), or the
    backtest harness reusing this same function) performs. Always returns
    a `signals` row to log, regardless of what happens next - every fired
    signal is logged, approved or not.
    """
    signal_row = {
        "strategy_id": signal.strategy_id, "band_id": signal.band_id, "side": signal.side,
        "action": signal.action, "reason": signal.reason, "price_at_fire": signal.price_at_fire,
        "prob_at_fire": signal.prob_at_fire, "edge_at_fire": signal.edge_at_fire,
        "suggested_shares": signal.suggested_shares, "confidence": signal.confidence,
        "regime_label": signal.regime_label, "severity": signal.severity,
        "dedupe_key": signal.dedupe_key, "payload": signal.payload,
        "fired_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    auto_approve = bool((settings.get("auto_approve") or {}).get("value", False))
    if not auto_approve:
        return dict(signal_row=signal_row, status="pending_approval", trade_row=None, ledger_rows=[])

    return approve_and_fill(signal, levels_by_side, portfolio, settings, versions,
                             max_slippage, is_taker, spread, legs, signal_row)


def approve_and_fill(signal, levels_by_side, portfolio, settings, versions, max_slippage,
                      is_taker=True, spread=0.0, legs=None, signal_row=None):
    risk_limits = settings.get("risk_limits") or {}
    requested_shares = signal.suggested_shares or 0.0
    requested_usd = requested_shares * (signal.price_at_fire or 0.0)
    warnings = check_risk_limits(requested_usd, portfolio.bankroll, signal.prob_at_fire,
                                  signal.price_at_fire, risk_limits)

    if legs:
        basket = fill_basket(legs, max_slippage)
        total_shares = sum(lr["fill"]["shares"] for lr in basket["legs"])
        total_cost = sum(lr["fill"]["usd_spent"] for lr in basket["legs"])
        avg_price = (total_cost / total_shares) if total_shares else None
        fill = dict(shares=total_shares, usd_spent=total_cost, avg_price=avg_price,
                    quoted_price=basket["legs"][0]["fill"]["quoted_price"] if basket["legs"] else None,
                    slippage=None, fully_filled=basket["complete"], requested_shares=requested_shares,
                    fill_quality=basket["fill_quality"], max_slippage_setting=max_slippage)
        legs_requested, legs_filled = basket["legs_requested"], basket["legs_filled"]
    else:
        levels = levels_by_side.get(signal.side, [])
        fill = fill_single_leg(levels, requested_shares, max_slippage)
        fill["max_slippage_setting"] = max_slippage
        legs_requested = legs_filled = 1

    costs = compute_fill_costs(fill["shares"], fill["avg_price"] or 0.0, is_taker, spread)
    fill["fee"] = costs["fee"]
    fill["gas"] = costs["gas"]

    trade_row = build_paper_trade_row(signal, fill, portfolio, versions,
                                       approved_by_user=True, legs_requested=legs_requested,
                                       legs_filled=legs_filled)

    ledger_rows = [
        ledger_entry("signal", trade_row, {"detail": {"reason": signal.reason}}),
        ledger_entry("decision", trade_row, {"detail": {"warnings": warnings}}),
        ledger_entry("order", trade_row, {"detail": {"requested_shares": requested_shares}}),
        ledger_entry("fill", trade_row, {"detail": {"shares": fill["shares"], "avg_price": fill["avg_price"]}}),
    ]

    if fill["shares"] > 0:
        portfolio.open_positions.append({
            "strategy_id": signal.strategy_id, "band_id": signal.band_id, "side": signal.side,
            "shares": fill["shares"], "avg_fill_price": fill["avg_price"],
            "city_key": signal.payload.get("city_key"), "resolution_date": signal.payload.get("resolution_date"),
        })

    return dict(signal_row=signal_row, status="filled" if fill["shares"] > 0 else "unfilled",
                trade_row=trade_row, ledger_rows=ledger_rows, warnings=warnings, fill=fill)


def close_position(position, exit_price, reason, versions):
    shares = position["shares"]
    costs = cost_model.round_trip_cost(shares, position["avg_fill_price"], exit_price)
    pnl = cost_model.net_pnl(shares, position["avg_fill_price"], exit_price, costs)
    trade_row = {**position, "closed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                 "exit_price": exit_price, "gross_pnl": pnl["gross_pnl"], "net_pnl": pnl["net_pnl"]}
    ledger_rows = [ledger_entry("exit", trade_row, {"detail": {"reason": reason, "pnl": pnl}})]
    return dict(trade_row=trade_row, ledger_rows=ledger_rows, pnl=pnl)


def main():
    print("paper_engine.py exposes process_signal()/close_position() as a library. "
          "See scripts/signals.py for the runner that assembles ctx from Supabase, "
          "evaluates strategies, and calls into this module for every fired signal.")


if __name__ == "__main__":
    main()
