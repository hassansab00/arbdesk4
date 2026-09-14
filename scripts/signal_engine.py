"""
The live signal runner - the step that was missing.

WHAT WAS BROKEN
---------------
`paper_plans.py` proposes a plan for every ENTER signal fired in the last
fifteen minutes. Nothing wrote signals. The runner that did was removed with
the signals FEATURE (the Overview panel, the Analytics attribution), and only
the pure half survived - strategy_rules.run_strategies, kept because
backtest/engine.py imports it to replay strategies over history.

So the assisted and automatic desks polled a table nobody filled, found
nothing, and proposed nothing. Forever. Every desk looked paused even when it
was not, and the only way to place an order was to type a ticket by hand.
`signals` holds 1,894 rows, none newer than 2026-09-13: orphans of the
removal, not output.

This restores the runner and nothing else. The panel and the attribution
counts stay gone - they were the feature that was deliberately removed. What
comes back is the WIRE: strategies run against the live board, and what they
decide is written where the paper desk already knows to look for it.

WHY IT BUILDS ITS CONTEXT FROM v_opportunities
----------------------------------------------
The backtest builds a BandView by recomputing edges from a historical book,
because that is the only way to price a day that has already happened. Live,
the edge engine has already done exactly that work and written it - so
re-deriving it here would be a second implementation of the desk's pricing,
free to drift from the one the board displays. The whole point of the
architecture note in docs/architecture_deviations.md is that there is ONE
edge computation. This reads its output.

v_opportunities carries one row per band per SIDE, so the two are pivoted
back into a single BandView. Anything it does not carry - the live-weather
fields S5 and S10 need - is left None, and the strategies that require them
simply do not fire rather than firing against a null. That is the correct
failure: a strategy that cannot see the running maximum has no business
claiming the day is decided.
"""
import datetime as dt
import os
import sys
import uuid
from collections import defaultdict

from common import rest, rest_all, insert, log_run
from strategies import StrategyConfig
from strategies.base import BandView, Context
from strategy_rules import run_strategies

# A signal is a decision about a moment. paper_plans.py reads the last fifteen
# minutes, so anything older has already been acted on or has expired; this is
# the same window, stated once here so the two cannot drift apart.
SIGNAL_TTL_MINUTES = 30


def _enabled_strategies():
    """Only what the operator has switched on. All ten ship disabled."""
    rows = rest("strategies", [("select", "*"), ("enabled", "eq.true")])
    out = []
    for r in rows:
        extra = r.get("params") or r.get("extra") or {}
        out.append(StrategyConfig(
            strategy_id=r["strategy_id"],
            name=r.get("name") or r["strategy_id"],
            side=r.get("side") or "BOTH",
            universe=r.get("universe") or ["ALL"],
            regime_filter=r.get("regime_filter") or [],
            conflict_class=r.get("conflict_class") or "default",
            capital_cap_pct=float(r.get("capital_cap_pct") or 5.0),
            max_concurrent=int(r.get("max_concurrent") or 10),
            enabled=True,
            extra=extra if isinstance(extra, dict) else {},
        ))
    return out


def _band_views():
    """One BandView per band, both sides folded in.

    v_opportunities is one row per band per side. A band with only one side
    priced is still a band - the missing side's price, edge and tradeable flag
    stay None/False, which is what they mean.
    """
    rows = rest_all("v_opportunities", [("select", "*")],
                    order="band_id.asc,side.asc", page_size=1000)
    by_band = defaultdict(dict)
    for r in rows:
        by_band[r["band_id"]][r.get("side")] = r

    # Live weather, for the running-max strategies. Absent is not zero.
    live = {}
    try:
        for r in rest_all("live_weather", [("select", "*")],
                          order="city_key.asc", page_size=1000):
            live[r["city_key"]] = r
    except Exception as e:
        print(f"  note: live_weather unavailable ({e}); running-max strategies will not fire",
              file=sys.stderr)

    views = []
    for band_id, sides in by_band.items():
        yes, no = sides.get("YES"), sides.get("NO")
        base = yes or no
        if base is None:
            continue
        lw = live.get(base.get("city_key")) or {}
        views.append(BandView(
            band_id=band_id,
            city_key=base["city_key"],
            resolution_date=str(base["resolution_date"]),
            band_lo=base.get("band_lo"), band_hi=base.get("band_hi"),
            open_low=bool(base.get("open_low")), open_high=bool(base.get("open_high")),
            band_label=base.get("band_label") or "", unit=base.get("unit") or "C",
            model_prob_yes=(yes or {}).get("model_prob"),
            yes_price=(yes or {}).get("market_price"),
            no_price=(no or {}).get("market_price"),
            yes_edge_net_pp=(yes or {}).get("edge_net_pp"),
            no_edge_net_pp=(no or {}).get("edge_net_pp"),
            yes_tradeable=bool((yes or {}).get("tradeable")),
            yes_block_reason=(yes or {}).get("block_reason"),
            no_tradeable=bool((no or {}).get("tradeable")),
            no_block_reason=(no or {}).get("block_reason"),
            confidence=float(base.get("confidence") or 0.0),
            regime_label=base.get("regime_label") or "NORMAL",
            market_state=base.get("market_state") or "UNKNOWN",
            fillable_usd_5c_yes=float((yes or {}).get("fillable_usd_5c") or 0.0),
            fillable_usd_5c_no=float((no or {}).get("fillable_usd_5c") or 0.0),
            token_yes=base.get("token_yes"), token_no=base.get("token_no"),
            spread=base.get("spread"),
            running_max_c=lw.get("running_max_c"),
            peak_window_state=lw.get("peak_window_state"),
            day_decided=bool(lw.get("day_decided")),
        ))
    return views


def _context(views):
    capacity, correlation = {}, {}
    try:
        for r in rest("derived_capacity", [("select", "*"),
                                           ("order", "computed_at.desc"), ("limit", "500")]):
            capacity.setdefault(r["city_key"], r)
    except Exception as e:
        print(f"  note: derived_capacity unavailable ({e})", file=sys.stderr)
    try:
        for r in rest("derived_city_correlation", [("select", "city_a,city_b,err_corr"),
                                                   ("order", "computed_at.desc"), ("limit", "2000")]):
            correlation.setdefault(frozenset((r["city_a"], r["city_b"])), r.get("err_corr"))
    except Exception as e:
        print(f"  note: derived_city_correlation unavailable ({e})", file=sys.stderr)
    return Context(bands=views, settings={}, now=dt.datetime.now(dt.timezone.utc),
                   capacity=capacity, correlation=correlation)


def _open_positions():
    try:
        return rest_all("paper_positions", [("select", "*"), ("shares", "gt.0")],
                        order="position_id.asc", page_size=1000)
    except Exception:
        return []          # no desk has traded yet; every strategy sees a flat book


def _row(sig, city_of, now):
    return {
        "signal_id": str(uuid.uuid4()),
        "strategy_id": sig.strategy_id, "band_id": sig.band_id,
        "city_key": city_of.get(sig.band_id),
        "side": sig.side, "action": sig.action, "reason": sig.reason,
        "price_at_fire": sig.price_at_fire, "prob_at_fire": sig.prob_at_fire,
        "edge_at_fire": sig.edge_at_fire, "suggested_shares": sig.suggested_shares,
        "confidence": sig.confidence, "regime_label": sig.regime_label,
        "severity": sig.severity, "dedupe_key": sig.dedupe_key,
        "payload": sig.payload, "fired_at": now.isoformat(),
        "status": "pending_approval",
        "ttl": f"{SIGNAL_TTL_MINUTES} minutes",
    }


def main():
    now = dt.datetime.now(dt.timezone.utc)
    configs = _enabled_strategies()
    counts = {"strategies_enabled": len(configs), "bands": 0,
              "fired": 0, "blocked": 0, "conflicts": 0, "written": 0}

    if not configs:
        # Not an error. Ten strategies ship disabled on purpose, and a desk
        # with none switched on should say so rather than look broken.
        print("no strategies enabled - nothing to run. "
              "Enable one: update strategies set enabled = true where strategy_id = '...';")
        log_run("signal_engine", "ok", 0, counts)
        return 0

    views = _band_views()
    counts["bands"] = len(views)
    if not views:
        print("no priced bands - run the intraday pipeline first")
        log_run("signal_engine", "ok", 0, counts)
        return 0

    ctx = _context(views)
    fired, blocked, conflict_rows = run_strategies(ctx, configs, _open_positions())
    counts["fired"], counts["blocked"] = len(fired), len(blocked)
    counts["conflicts"] = len(conflict_rows)

    city_of = {v.band_id: v.city_key for v in views}
    rows = [_row(s, city_of, now) for s in fired]
    if rows:
        insert("signals", rows)
        counts["written"] = len(rows)
    if conflict_rows:
        try:
            insert("strategy_conflicts", conflict_rows)
        except Exception as e:
            print(f"  note: strategy_conflicts not written ({e})", file=sys.stderr)

    for s in fired[:20]:
        print(f"  {s.strategy_id} {s.action} {s.side} {city_of.get(s.band_id)} "
              f"{s.suggested_shares:.0f}sh @ {s.price_at_fire} - {s.reason}")
    print(f"signals: {counts['written']} written, {counts['blocked']} blocked by conflict rules, "
          f"over {counts['bands']} bands")
    log_run("signal_engine", "ok", counts["written"], counts)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
