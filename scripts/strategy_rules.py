"""
Strategy evaluation rules - the pure half of what used to be signals.py.

WHAT WAS REMOVED, AND WHY THIS FILE SURVIVED IT
-----------------------------------------------
The signals FEATURE is gone from the platform: the panel on the Overview,
the strategy-attribution counts on Analytics, and the Intraday Pipeline step
that ran this file every cycle and INSERTed into `signals`, `paper_trades`,
`ledger` and `strategy_conflicts`. Nothing writes a signal any more.

What is left here is the part that was never the feature: the detectors and
`run_strategies`, which decide what a strategy WOULD do given a context.
scripts/backtest/engine.py imports run_strategies to replay strategies over
history, so deleting this file outright would have taken the Backtest page
down with it - the strategy engine and the live signal runner happened to
share a module, and only the runner was the thing being removed.

Pure: no network calls, no database, no side effects. Everything below is a
function of its arguments.
"""
import datetime as dt
import uuid
from dataclasses import asdict
from collections import defaultdict

from common import rest, rest_all, insert, get_cities
import edge_engine
import paper_engine
import regime
from strategies import REGISTRY, StrategyConfig
from strategies.base import BandView, Context
from strategies.conflicts import resolve_conflicts

DEFAULT_TTL_MINUTES = 30
STALE_JOB_HOURS = 6
PEAK_WINDOW_SOON_MINUTES = 30
THIN_BOOK_MIN_USD = 200.0
SELLOFF_CENTS = 0.10


# --------------------------------------------------------------------------
# Pure detectors - independently unit-testable.
# --------------------------------------------------------------------------

def detect_model_shift(prev_centre_c, curr_centre_c, band_width_c):
    if prev_centre_c is None or curr_centre_c is None or not band_width_c:
        return False, 0.0
    bands_moved = abs(curr_centre_c - prev_centre_c) / band_width_c
    return bands_moved >= 1.0, bands_moved


def detect_regime_change(prev_label, curr_label):
    return prev_label is not None and curr_label is not None and prev_label != curr_label


def detect_thin_book(fillable_usd, min_required=THIN_BOOK_MIN_USD):
    return fillable_usd is not None and fillable_usd < min_required


def detect_peak_window_open(minutes_to_peak, threshold=PEAK_WINDOW_SOON_MINUTES):
    return minutes_to_peak is not None and 0 <= minutes_to_peak <= threshold


def detect_selloff(price_now, price_before, cents_threshold=SELLOFF_CENTS):
    if price_now is None or price_before is None:
        return False, 0.0
    move = abs(price_now - price_before)
    return move >= cents_threshold, move


def system_health_signals(ingest_log_rows, now, stale_hours=STALE_JOB_HOURS):
    """ingest_log_rows: latest row per job, [{"job","status","logged_at"}]."""
    out = []
    for row in ingest_log_rows:
        job = row.get("job")
        if row.get("status") == "error":
            out.append(_system_signal(f"job_failed:{job}", "medium", {"job": job}))
            continue
        logged_at = row.get("logged_at")
        if logged_at:
            ts = dt.datetime.fromisoformat(logged_at.replace("Z", "+00:00"))
            age_h = (now - ts).total_seconds() / 3600.0
            if age_h > stale_hours:
                out.append(_system_signal(f"job_stale:{job}:{age_h:.1f}h", "medium", {"job": job, "age_h": age_h}))
    return out


def _system_signal(reason, severity, payload):
    from strategies.base import Signal, dedupe_key
    return Signal(strategy_id="system", band_id=None, side=None, action="ALERT", reason=reason,
                  price_at_fire=None, prob_at_fire=None, edge_at_fire=None, suggested_shares=0.0,
                  confidence=None, regime_label=None, severity=severity,
                  dedupe_key=dedupe_key("system", "none", "none", "ALERT", reason), payload=payload)


def anomaly_signals(anomaly_rows):
    from strategies.base import Signal, dedupe_key
    out = []
    for a in anomaly_rows:
        out.append(Signal(
            strategy_id="system", band_id=a.get("band_id"), side=a.get("side"), action="ALERT",
            reason="implausible_edge_anomaly", price_at_fire=None, prob_at_fire=None,
            edge_at_fire=a.get("value"), suggested_shares=0.0, confidence=None, regime_label=None,
            severity="critical",
            dedupe_key=dedupe_key("system", a.get("band_id") or "none", a.get("side") or "none",
                                   "ALERT", f"anomaly:{a.get('detected_at')}"),
            payload={"anomaly_id": a.get("id"), "detail": a.get("detail")},
        ))
    return out


# --------------------------------------------------------------------------
# Strategy orchestration
# --------------------------------------------------------------------------

def run_strategies(ctx, strategy_configs, open_positions, portfolio=None):
    """
    Runs every enabled strategy's entry+exit rules, sizes every ENTER
    signal via that strategy's own size(signal, portfolio) - entry_signals
    itself has no portfolio context, so every strategy deliberately leaves
    suggested_shares at 0.0/N-from-formula for this step to finalise -
    then applies the Task 8 conflict rules before returning.
    """
    all_entries, all_exits = [], []
    for cfg in strategy_configs:
        cls = REGISTRY.get(cfg.strategy_id)
        if cls is None or not cfg.enabled:
            continue
        strat = cls(cfg)
        for sig in strat.entry_signals(ctx):
            sig.suggested_shares = strat.size(sig, portfolio)
            all_entries.append(sig)
        all_exits.extend(strat.exit_signals(ctx, open_positions))

    allowed_entries, blocked_entries, conflict_rows = resolve_conflicts(all_entries, open_positions)
    return allowed_entries + all_exits, blocked_entries, conflict_rows
