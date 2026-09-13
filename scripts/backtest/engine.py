"""
AD4 backtest engine (Task 12). Walk-forward, point-in-time.

Non-negotiable per the spec:
  - walk-forward only: no parameter chosen with knowledge of the test period
  - point-in-time reconstruction: at simulated time T, only data known at T
    is visible - including the model cycle current *then*, not the latest
  - execution against the historical book - depth, spread, full cost model
  - immutable saved runs (handled by runner.py: every run gets a fresh
    backtest_runs row, never overwritten)

`simulate_city_day` is a pure function: every "what was known then" input
(forecast rows, skill rows, regime result, book rows) is passed in already
point-in-time-filtered by the caller (`run_backtest`, which does the
Supabase I/O). This is what makes the actual decision logic independently
testable on fixtures - the same reason strategies take a Context instead
of touching the network.

HONEST LIMITATION, stated in the spec and preserved here rather than
hidden: book depth history begins 22 Aug 2026. Before that date,
`book_rows_by_band` will be empty for every band, executable prices fall
back to whatever `quoted_price` is available (or None), and most bands
end up untradeable - correctly, not by a special case, just because the
data isn't there. Forecast accuracy can be backtested against ~2.5 years;
strategy profitability cannot, until AD4's own market data accumulates.

Also a deliberate, stated simplification: one evaluation instant per
city-day (`evaluation_lead_days` before resolution), holding straight to
settlement. This backtest does not model multiple intraday decision
points or early exits - book snapshot history is too thin to make that
meaningful yet (see the limitation above). S1's exit-trigger logic and
S5's day_decided path are consequently untested by this harness until
intraday book/live-weather history exists to replay.
"""
import datetime as dt

import cost_model
import edge_engine
import paper_engine
import probability_engine as pe
from settlement import find_winning_band
from strategy_rules import run_strategies
from strategies.base import BandView, Context
from strategies.conflicts import resolve_conflicts

MAX_SLIPPAGE_DEFAULT = 0.05


def evaluation_instant(resolution_date, evaluation_lead_days=1, hour_utc=12):
    d = resolution_date - dt.timedelta(days=evaluation_lead_days)
    return dt.datetime.combine(d, dt.time(hour_utc, 0), tzinfo=dt.timezone.utc)


def pick_latest_row_by(rows, ts_field, as_of):
    """The single row with the latest `ts_field` at or before `as_of` -
    the point-in-time selection primitive used throughout this module."""
    eligible = [r for r in rows if r.get(ts_field) and
                dt.datetime.fromisoformat(r[ts_field].replace("Z", "+00:00")) <= as_of]
    if not eligible:
        return None
    return max(eligible, key=lambda r: r[ts_field])


def pick_shortest_lead(rows):
    """Among point-in-time-eligible rows, the one AD4 would actually have
    used: shortest lead_days (freshest forecast for that resolution date)."""
    if not rows:
        return None
    return min(rows, key=lambda r: r["lead_days"])


def simulate_city_day(city_key, resolution_date, unit, bands, forecast_rows_as_of,
                       skill_row, reg, book_rows_by_band, actual_settled_value,
                       strategy_configs, open_positions, as_of, max_slippage=MAX_SLIPPAGE_DEFAULT,
                       portfolio=None):
    """
    Pure: no network calls. Returns dict(trades=[...], signals=[...],
    conflicts=[...]). `forecast_rows_as_of` and `book_rows_by_band` must
    already be filtered to what was knowable at `as_of` by the caller.
    """
    forecast_row = pick_shortest_lead(forecast_rows_as_of)
    if forecast_row is None or forecast_row.get("forecast_max_c") is None or skill_row is None \
            or not skill_row.get("mae_c"):
        return dict(trades=[], signals=[], conflicts=[])  # TODO: unmeasured that day - no fabricated distribution

    bias_c = skill_row.get("bias_c") or 0.0
    mae_c = skill_row["mae_c"]
    centre_c = forecast_row["forecast_max_c"] - bias_c
    sigma_c = mae_c * pe.MAE_TO_SIGMA * reg.sigma_multiplier

    probs = dict(pe.compute_band_probabilities(centre_c, sigma_c, unit, bands))
    winning_band_id = find_winning_band(bands, actual_settled_value, unit)

    views = []
    for b in bands:
        book = book_rows_by_band.get(b["band_id"], {})
        best_bid, best_ask = book.get("best_bid"), book.get("best_ask")
        model_prob_yes = probs.get(b["band_id"])

        yes_levels = edge_engine.levels_for_side(book, "YES") if book else []
        no_levels = edge_engine.levels_for_side(book, "NO") if book else []
        yes_quoted = edge_engine.quoted_price_for_side(best_bid, best_ask, "YES")
        no_quoted = edge_engine.quoted_price_for_side(best_bid, best_ask, "NO")
        yes_edge = edge_engine.compute_edge(model_prob_yes, yes_levels, yes_quoted, max_slippage)
        no_edge = edge_engine.compute_edge(1 - model_prob_yes if model_prob_yes is not None else None,
                                            no_levels, no_quoted, max_slippage)

        yes_tradeable, yes_block = edge_engine.classify_tradeability(
            "YES", yes_edge["executable_price"], 0.0, "LIVE" if book else "NO_BOOK")
        no_tradeable, no_block = edge_engine.classify_tradeability(
            "NO", no_edge["executable_price"], 0.0, "LIVE" if book else "NO_BOOK")

        views.append(BandView(
            band_id=b["band_id"], city_key=city_key, resolution_date=resolution_date.isoformat(),
            band_lo=b.get("band_lo"), band_hi=b.get("band_hi"), open_low=bool(b.get("open_low")),
            open_high=bool(b.get("open_high")), band_label=b.get("band_label") or "", unit=unit,
            model_prob_yes=model_prob_yes, yes_price=yes_edge["executable_price"],
            no_price=no_edge["executable_price"], yes_edge_net_pp=yes_edge["edge_net_pp"],
            no_edge_net_pp=no_edge["edge_net_pp"], yes_tradeable=yes_tradeable, yes_block_reason=yes_block,
            no_tradeable=no_tradeable, no_block_reason=no_block, confidence=reg.confidence,
            regime_label=reg.label, market_state="LIVE" if book else "NO_BOOK",
            fillable_usd_5c_yes=yes_edge["fillable_usd_5c"], fillable_usd_5c_no=no_edge["fillable_usd_5c"],
            lead_days=forecast_row["lead_days"], s5_allowed=reg.s5_allowed,
        ))

    ctx = Context(bands=views, settings={}, now=as_of)
    fired, blocked, conflict_rows = run_strategies(ctx, strategy_configs, open_positions, portfolio)

    trades = []
    for sig in fired:
        if sig.action != "ENTER" or not sig.suggested_shares:
            continue
        # Multi-leg baskets (S2, S6) carry every covered band in
        # payload["band_ids"] - filling and settling only the anchor leg
        # would misrepresent a guaranteed-payoff basket as a single-band
        # bet. Single-leg strategies are just a one-leg "basket".
        leg_band_ids = sig.payload.get("band_ids") or [sig.band_id]
        legs = [{
            "band_id": bid, "side": sig.side,
            "levels": edge_engine.levels_for_side(book_rows_by_band.get(bid, {}), sig.side),
            "requested_shares": sig.suggested_shares,
        } for bid in leg_band_ids]
        basket = paper_engine.fill_basket(legs, max_slippage)
        if not any(leg["fill"]["shares"] > 0 for leg in basket["legs"]):
            continue  # no historical book depth to fill against that day - correctly no trade.
            # NOTE: legs_filled (fully_filled count) can be 0 while shares
            # were still partially filled - that's a real partial fill,
            # not "nothing happened", so it must not gate recording a trade.

        gross_total, fee_total, shares_total, cost_total = 0.0, 0.0, 0.0, 0.0
        anchor_settled = None
        for leg in basket["legs"]:
            fill = leg["fill"]
            if fill["shares"] <= 0:
                continue
            won_leg = (leg["band_id"] == winning_band_id) if sig.side == "YES" else (leg["band_id"] != winning_band_id)
            # The anchor is the band model_prob was quoted for, so it is the
            # only leg whose settlement can be compared against that number.
            if leg["band_id"] == sig.band_id:
                anchor_settled = won_leg
            exit_price = 1.0 if won_leg else 0.0
            fee = cost_model.taker_fee(fill["shares"], fill["avg_price"])
            gross_total += fill["shares"] * (exit_price - fill["avg_price"])
            fee_total += fee
            shares_total += fill["shares"]
            cost_total += fill["usd_spent"]
        net_total = gross_total - fee_total - cost_model.DEFAULT_GAS_USD
        avg_price = (cost_total / shares_total) if shares_total else None

        trades.append({
            "strategy_id": sig.strategy_id, "city_key": city_key, "band_id": sig.band_id,
            "side": sig.side, "shares": shares_total, "avg_fill_price": avg_price,
            "gross_pnl": gross_total, "net_pnl": net_total, "fee_paid": fee_total,
            "gas_paid": cost_model.DEFAULT_GAS_USD,
            "slippage_paid": basket["legs"][0]["fill"].get("slippage") if basket["legs"] else None,
            # Three different questions, which `won` used to answer with one
            # number. `won` was net_total > 0 - money - while model_prob is a
            # probability that a CONTRACT settles yes. Scoring one against the
            # other made every calibration figure a blend of forecast accuracy
            # and execution cost, so a desk with a perfect model but a wide
            # spread read as overconfident.
            #
            #   settled_winner         did the contract we were quoted on
            #                          settle our way (None if the day never
            #                          settled, or the anchor leg never filled)
            #   profitable_after_costs did the trade make money once fees,
            #                          gas and slippage were paid
            #   closed_reason          how the position ended
            #
            # `won` is kept, equal to profitable_after_costs, because
            # backtest_trades rows already carry it and the UI reads it.
            "model_prob": sig.prob_at_fire,
            "settled_winner": anchor_settled,
            "profitable_after_costs": net_total > 0,
            "won": net_total > 0,
            # This harness holds every position to settlement - it has no
            # intraday exit to model, because book history starts late August
            # 2026. Recording that explicitly means "held to settlement" is a
            # stated fact about the run rather than an assumption a reader has
            # to make, and the column is already there when exits arrive.
            "closed_reason": "settled",
            "resolution_date": resolution_date.isoformat(), "regime_label": reg.label,
            "legs_requested": basket["legs_requested"], "legs_filled": basket["legs_filled"],
        })

    return dict(trades=trades, signals=[s.__dict__ for s in fired], conflicts=conflict_rows)
