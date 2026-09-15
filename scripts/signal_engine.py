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
import sys
import uuid
from collections import defaultdict
from dataclasses import asdict

from common import rest, rest_all, insert, log_run
from paper_engine import Portfolio
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


def _optional(path, params, order, note):
    """A read whose absence must not stop the run - but must be named."""
    try:
        return rest_all(path, params, order=order, page_size=1000)
    except Exception as e:
        print(f"  note: {path} unavailable ({e}); {note}", file=sys.stderr)
        return []


def _first(v, *keys):
    for k in keys:
        if v.get(k) is not None:
            return v[k]
    return None


def _band_views():
    """One BandView per band, both sides folded in, with the TIMING, SKILL and
    FORECAST fields the strategies read.

    v_opportunities is one row per band per side. A band with only one side
    priced is still a band - the missing side's price, edge and tradeable flag
    stay None/False, which is what they mean.

    WHAT WAS MISSING. The first restoration of this runner carried only the
    pricing columns and three live-weather fields. S3 reads mae_bands, S5 reads
    s5_allowed and day_decided, S7 reads minutes_to_peak and the slopes, S8/S9
    anchor on implied_max_c or forecast_max_c, S1's time mode reads lead_days.
    None of those was ever set, so five of the nine strategies could not fire
    against any board. They are joined here from the views that already compute
    them (sql/ad4_26, ad4_33, v_latest_prob, derived_forecast_skill) rather
    than recomputed, for the same reason the pricing is read from
    v_opportunities and not re-derived: one implementation, read everywhere.
    """
    rows = rest_all("v_opportunities", [("select", "*")],
                    order="band_id.asc,side.asc", page_size=1000)
    by_band = defaultdict(dict)
    for r in rows:
        by_band[r["band_id"]][r.get("side")] = r
    if not by_band:
        return []

    # Live weather, for the running-max strategies. Absent is not zero.
    live = {r["city_key"]: r for r in
            _optional("live_weather", [("select", "*")], "city_key.asc",
                      "running-max strategies will not fire")}
    # Where the day is heading: peak timing, trend slopes, implied maximum.
    timing = {r["city_key"]: r for r in
              _optional("v_trade_timing", [("select", "*")], "city_key.asc",
                        "S7 and the timing gates will not fire")}
    approach = {r["city_key"]: r for r in
                _optional("v_city_peak_approach", [("select", "city_key,slope_6_c_per_h")],
                          "city_key.asc", "six-reading slope unavailable")}
    # The forecast each band was priced from, and whether pricing is eligible.
    probs = {}
    band_ids = sorted(by_band)
    for i in range(0, len(band_ids), 100):
        chunk = ",".join(band_ids[i:i + 100])
        for r in _optional("v_latest_prob", [
                ("select", "band_id,forecast_max_c,lead_days,input_forecast_run,forecast_version,"
                           "skill_lead_days,pricing_eligible,pricing_block_reason"),
                ("band_id", f"in.({chunk})")], "band_id.asc", "forecast identity unavailable"):
            probs[r["band_id"]] = r
    # Market identity, for the plan builder's contract checks.
    band_meta = {}
    for i in range(0, len(band_ids), 100):
        chunk = ",".join(band_ids[i:i + 100])
        for r in _optional("v_canonical_bands", [
                ("select", "band_id,market_id,condition_id,band_index"),
                ("band_id", f"in.({chunk})")], "band_id.asc", "market identity unavailable"):
            band_meta[r["band_id"]] = r
    # Measured skill, verified scope only, newest per (city, lead).
    skill = {}
    for r in _optional("derived_forecast_skill", [
            ("select", "city_key,lead_days,mae_bands,n_days,computed_at"),
            ("evidence_scope", "eq.verified_outcomes_v1"),
            ("computed_at", f"gte.{(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)).isoformat()}")],
            "computed_at.desc,city_key.asc,lead_days.asc", "S3 will not fire"):
        skill.setdefault((r["city_key"], int(r["lead_days"])), r)

    views = []
    for band_id, sides in by_band.items():
        yes, no = sides.get("YES"), sides.get("NO")
        base = yes or no
        if base is None:
            continue
        city = base["city_key"]
        lw = live.get(city) or {}
        tm = timing.get(city) or {}
        ap = approach.get(city) or {}
        pr = probs.get(band_id) or {}
        meta = band_meta.get(band_id) or {}
        lead = pr.get("lead_days")
        sk = skill.get((city, int(lead))) if lead is not None else None
        if sk is None and lead is not None:
            # The nearest longer horizon, as the probability engine does.
            for cand in range(int(lead) + 1, 8):
                sk = skill.get((city, cand))
                if sk:
                    break
        window_state = _first(tm, "window_state") or lw.get("peak_window_state")
        day_decided = bool(_first(tm, "day_decided") if tm.get("day_decided") is not None
                           else lw.get("day_decided"))
        forecast = {
            "for_date": str(base["resolution_date"]),
            "run_at": pr.get("input_forecast_run"),
            "forecast_max_c": pr.get("forecast_max_c"),
            "lead_days": lead,
            "forecast_version": pr.get("forecast_version"),
        }
        market = {"market_id": meta.get("market_id"), "city_key": city,
                  "resolution_date": str(base["resolution_date"]), "unit": base.get("unit")}
        band = {"band_id": band_id, "market_id": meta.get("market_id"),
                "condition_id": meta.get("condition_id"), "band_index": meta.get("band_index"),
                "band_label": base.get("band_label") or "", "band_lo": base.get("band_lo"),
                "band_hi": base.get("band_hi"), "open_low": bool(base.get("open_low")),
                "open_high": bool(base.get("open_high")),
                "token_yes": base.get("token_yes"), "token_no": base.get("token_no")}
        views.append(BandView(
            band_id=band_id,
            city_key=city,
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
            lead_days=int(lead) if lead is not None else None,
            spread=base.get("spread"),
            mae_bands=(sk or {}).get("mae_bands"),
            running_max_c=_first(tm, "running_max_c") if tm.get("running_max_c") is not None
                          else lw.get("running_max_c"),
            minutes_to_peak=_first(tm, "minutes_to_peak") if tm.get("minutes_to_peak") is not None
                            else lw.get("minutes_to_peak"),
            peak_window_state=window_state,
            day_decided=day_decided,
            window_width_h=tm.get("window_width_h"),
            # S5 may lock a running maximum only once the day is decided AND the
            # reading behind it is a station's, not a model's interpolation.
            s5_allowed=bool(day_decided and (lw.get("obs_source") or lw.get("source_kind") == "station")),
            slope_3_c_per_h=tm.get("slope_3_c_per_h"),
            slope_6_c_per_h=ap.get("slope_6_c_per_h"),
            trend_direction=tm.get("direction"),
            rolling_over=bool(tm.get("rolling_over")),
            latest_temp_c=tm.get("latest_temp_c"),
            reading_age_min=tm.get("reading_age_min"),
            typical_climb_left_c=tm.get("typical_climb_left_c"),
            implied_max_c=tm.get("implied_max_c"),
            implied_max_low_c=tm.get("implied_max_low_c"),
            implied_max_high_c=tm.get("implied_max_high_c"),
            pct_already_peaked=tm.get("pct_already_peaked"),
            forecast_max_c=pr.get("forecast_max_c"),
            decision_evidence={"market": market, "band": band, "yes_edge": yes, "no_edge": no,
                               "forecast": forecast, "weather": lw, "approach": tm},
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
        newest = rest("derived_city_correlation", [("select", "computed_at"),
                                                   ("order", "computed_at.desc"), ("limit", "1")])
        if newest:
            for r in rest_all("derived_city_correlation", [
                    ("select", "city_a,city_b,err_corr"),
                    ("computed_at", f"eq.{newest[0]['computed_at']}")],
                    order="city_a.asc,city_b.asc", page_size=1000):
                correlation.setdefault(frozenset((r["city_a"], r["city_b"])), r.get("err_corr"))
    except Exception as e:
        print(f"  note: derived_city_correlation unavailable ({e})", file=sys.stderr)
    return Context(bands=views, settings={}, now=dt.datetime.now(dt.timezone.utc),
                   capacity=capacity, correlation=correlation)


def _open_positions():
    try:
        return rest_all("paper_positions", [("select", "*"), ("shares", "gt.0")],
                        order="account_id.asc,band_id.asc,side.asc", page_size=1000)
    except Exception:
        return []          # no desk has traded yet; every strategy sees a flat book


def _portfolio():
    """The paper cash the strategies may size against.

    run_strategies sizes every ENTER signal from portfolio.bankroll, and the
    first restoration passed None - so every strategy but S6 suggested 0
    shares. The bankroll is the free cash of the desks that can act on a
    proposal (assisted or automatic); the plan builder re-checks each account's
    own limits, so this is a sizing hint, never an authorisation.
    """
    bankroll = 0.0
    try:
        rows = rest("paper_accounts", [("select", "cash,reserved_cash,mode,archived_at"),
                                       ("mode", "in.(assisted,automatic)"),
                                       ("archived_at", "is.null"), ("limit", "100")])
        bankroll = sum(float(r.get("cash") or 0) - float(r.get("reserved_cash") or 0) for r in rows)
    except Exception as e:
        print(f"  note: paper_accounts unavailable ({e}); sizing from settings.bankroll", file=sys.stderr)
    if bankroll <= 0:
        try:
            rows = rest("settings", [("select", "value"), ("key", "eq.bankroll"), ("limit", "1")])
            bankroll = float(((rows or [{}])[0].get("value") or {}).get("amount") or 0)
        except Exception:
            bankroll = 0.0
    return Portfolio(bankroll=max(bankroll, 0.0))


def _recently_fired(now):
    """dedupe_keys that fired inside the TTL - the same condition must not
    re-fire every cycle and pile identical proposals on the desk."""
    since = (now - dt.timedelta(minutes=SIGNAL_TTL_MINUTES)).isoformat()
    try:
        return {r["dedupe_key"] for r in rest_all(
            "signals", [("select", "dedupe_key"), ("fired_at", f"gte.{since}")],
            order="signal_id.asc", page_size=1000) if r.get("dedupe_key")}
    except Exception as e:
        print(f"  note: could not read recent signals ({e}); dedupe skipped", file=sys.stderr)
        return set()


def _row(sig, city_of, now):
    # No signal_id: the column is bigserial and the database assigns it. The
    # first restoration wrote a UUID string here, which Postgres rejects
    # (22P02) - so the first real signal could never have landed.
    return {
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


def _enrich(sig, decision_bands, cycle_id, now):
    """What paper_plans.py reads: when the decision was made, and the exact
    view of every band it was made on."""
    ids = (sig.payload or {}).get("band_ids") or [sig.band_id]
    inputs = {str(bid): asdict(decision_bands[str(bid)])
              for bid in ids if str(bid) in decision_bands}
    sig.payload = {**(sig.payload or {}), "cycle_id": cycle_id,
                   "decision_at": now.isoformat(), "decision_inputs": inputs}
    return sig


def main():
    now = dt.datetime.now(dt.timezone.utc)
    configs = _enabled_strategies()
    counts = {"strategies_enabled": len(configs), "bands": 0,
              "fired": 0, "blocked": 0, "deduped": 0, "conflicts": 0, "written": 0}

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
    fired, blocked, conflict_rows = run_strategies(ctx, configs, _open_positions(), _portfolio())
    counts["fired"], counts["blocked"] = len(fired), len(blocked)
    counts["conflicts"] = len(conflict_rows)

    recent = _recently_fired(now)
    cycle_id = str(uuid.uuid4())
    decision_bands = {str(v.band_id): v for v in views}
    city_of = {v.band_id: v.city_key for v in views}
    rows = []
    for s in fired:
        if s.dedupe_key in recent:
            counts["deduped"] += 1
            continue
        rows.append(_row(_enrich(s, decision_bands, cycle_id, now), city_of, now))
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
    print(f"signals: {counts['written']} written, {counts['deduped']} deduped, "
          f"{counts['blocked']} blocked by conflict rules, over {counts['bands']} bands")
    log_run("signal_engine", "ok", counts["written"], counts)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
