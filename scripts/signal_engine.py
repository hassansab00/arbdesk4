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

import allocator
import risk_budget
import strategy_gate
from collections import defaultdict
from dataclasses import asdict

from common import rest, rest_all, insert, log_run, model_version_id
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
    # HOW OFTEN THIS CITY'S THERMOMETER NAMES THE BAND THE VENUE SETTLED ON.
    #
    # Read from the column rather than from v_settlement_agreement, for the
    # same reason v_trade_plan does: the view needs band_contains() out of
    # ad4_34 and installs after it, while both engines need the number.
    # refresh_observation_trust() moves it across, nightly.
    #
    # An absent city is absent from the dict, so BandView.observation_trust is
    # None, which every gate reads as "unmeasured, let it through".
    trust = {r["city_key"]: r.get("observation_trust") for r in
             _optional("cities", [("select", "city_key,observation_trust")],
                       "city_key.asc", "observation-trust gates will not bind")}
    # The forecast each band was priced from, and whether pricing is eligible.
    probs = {}
    band_ids = sorted(by_band)
    for i in range(0, len(band_ids), 100):
        chunk = ",".join(band_ids[i:i + 100])
        for r in _optional("v_latest_prob", [
                # Widened for the decision snapshot. Every one of these is
                # rewritten by the next pricing run, so a trade that does not
                # copy them keeps no record of what it was priced on.
                ("select", "band_id,prob_id,computed_at,forecast_max_c,lead_days,"
                           "input_forecast_run,forecast_version,calibration_version,"
                           "raw_prob,calibrated_prob,sigma_c,confidence,"
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

    # Once per run. It changes when someone edits the fee model, not between
    # bands, and every snapshot below has to carry the same one or two trades
    # from the same cycle would claim different cost assumptions.
    cost_version = _cost_version()

    views = []
    for band_id, sides in by_band.items():
        yes, no = sides.get("YES"), sides.get("NO")
        base = yes or no
        if base is None:
            continue
        city = base["city_key"]
        # TODAY'S THERMOMETER BELONGS TO TODAY'S MARKET, and to no other.
        #
        # live_weather and v_trade_timing each hold one row per city
        # describing the day currently in progress there: the running maximum
        # so far, how far it is from the peak, whether the day is effectively
        # decided. Keyed by city alone, as they were, a band resolving
        # TOMORROW was handed all of it.
        #
        # s5 is the clearest casualty - its entire premise is "the day is over
        # and the maximum is locked in this band" - and it fired on a
        # 2026-09-17 market because 2026-09-16's maximum landed there. s7's
        # pre-peak window is the same mistake with a different sign: a
        # countdown to today's peak, applied to a day that has not begun.
        #
        # v_trade_timing now carries local_date. When it does not match this
        # market's resolution date the timing is simply absent, every gate
        # below falls back to its "no evidence" branch, and the row is priced
        # from its forecast alone - which is all that can honestly be said
        # about a day nobody has measured yet.
        def for_this_day(row):
            """The row, unless it positively states it is about another day.

            A MISSING local_date rides along; a DIFFERENT one is dropped. Only
            a stated mismatch is evidence, and inventing one from an absence
            would silently disable the running-max strategies on the degraded
            path where these sources have no date at all. In production all 54
            cities carry it, so the mismatch is always detectable where it
            matters.
            """
            stated = row.get("local_date")
            if stated and str(stated) != str(base["resolution_date"]):
                return {}
            return row

        tm = for_this_day(timing.get(city) or {})
        lw = for_this_day(live.get(city) or {})
        # v_city_peak_approach has no date of its own: it is the six-reading
        # slope through the day the timing row describes, so it rides with it.
        ap = (approach.get(city) or {}) if tm else {}
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
            # Per city, from v_trade_timing, which reads the column
            # refresh_observation_trust() writes. None where fewer than ten
            # ladders have settled, and None means "no opinion" downstream.
            observation_trust=trust.get(city),
            # The fallback is live_weather's own maximum, which may carry model
            # output unless the row is a station's (plan v2 P2.7).
            running_max_c=_first(tm, "running_max_c") if tm.get("running_max_c") is not None
                          else (lw.get("running_max_c") if lw.get("source_kind") == "station"
                                else None),
            minutes_to_peak=_first(tm, "minutes_to_peak") if tm.get("minutes_to_peak") is not None
                            else lw.get("minutes_to_peak"),
            peak_window_state=window_state,
            day_decided=day_decided,
            window_width_h=tm.get("window_width_h"),
            # S5 may lock a running maximum only once the day is decided, the
            # reading behind it is a station's rather than a model's
            # interpolation, AND that maximum rests on a SERIES of today's
            # readings.
            #
            # The third gate is the one that was missing. A city whose
            # observation feed runs a day behind still has a live thermometer,
            # and one reading says the day reached AT LEAST that - a floor, not
            # a maximum. s5's premise is "the day is over and the maximum is
            # locked in this band", which a floor cannot support: the real
            # maximum may be several degrees higher and in another band
            # entirely. live_weather.running_max_basis is written beside the
            # number by refresh_live_weather_timing(); see
            # sql/ad4_71_observation_health.sql.
            s5_allowed=bool(day_decided
                            and (lw.get("obs_source") or lw.get("source_kind") == "station")
                            and lw.get("running_max_basis") == "series"),
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
            decision_snapshot=_snapshot(pr, (yes or {}).get("market_price"),
                                        (no or {}).get("market_price"), cost_version),
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


def _ledgers():
    """{strategy_id: its active shadow ledger} (plan v2 P5.1).

    Every registered strategy has one, opened by the registration itself, and
    it is the only account that strategy trades on. The bankroll a strategy
    sizes against is that ledger's free cash - not the summed cash of every
    desk that can act, which is what sized every strategy before, and which
    made each one's size depend on how many other desks existed.

    A read that fails is not a reason to size from anything else: no ledgers
    means every strategy sizes to zero, the signals are still written as the
    day's record, and paper_plans refuses a zero size (P5.0 item 3).
    """
    try:
        rows = rest("paper_accounts",
                    [("select", "account_id,strategy_id,cash,reserved_cash,bankroll_usd"),
                     ("kind", "eq.shadow"), ("status", "eq.active"), ("limit", "1000")])
    except Exception as e:
        print(f"  note: shadow ledgers unavailable ({e}); every strategy sizes to zero",
              file=sys.stderr)
        return {}
    return {r["strategy_id"]: r for r in rows or [] if r.get("strategy_id")}


def _portfolios(ledgers):
    """{strategy_id: Portfolio} - each strategy's own ledger, at its free cash."""
    return {sid: Portfolio(bankroll=max(float(r.get("cash") or 0)
                                        - float(r.get("reserved_cash") or 0), 0.0))
            for sid, r in ledgers.items()}


def _risk_states(ledgers):
    """{strategy_id: (equity, high_water, spent_today_usd)}, each from its own ledger.

    One ledger's drawdown scales that strategy and no other. A missing row, or
    a failed read, leaves (None, None, 0.0), which leaves
    risk_budget.drawdown_scale at 1.0 - a missing view is not a reason to stop.
    """
    blank = {sid: (None, None, 0.0) for sid in ledgers}
    if not ledgers:
        return blank
    ids = ",".join(str(r["account_id"]) for r in ledgers.values())
    try:
        rows = rest("v_desk_risk_state",
                    [("select", "account_id,equity,high_water,spent_today_usd"),
                     ("account_id", f"in.({ids})"), ("limit", "1000")])
    except Exception as e:
        print(f"  note: v_desk_risk_state unavailable ({e}); "
              f"no drawdown scaling this run", file=sys.stderr)
        return blank
    by_account = {str(r["account_id"]): r for r in rows or []}
    out = {}
    for sid, led in ledgers.items():
        r = by_account.get(str(led["account_id"]))
        out[sid] = ((float(r["equity"]) if r.get("equity") is not None else None,
                     float(r["high_water"]) if r.get("high_water") is not None else None,
                     float(r.get("spent_today_usd") or 0)) if r else (None, None, 0.0))
    return out


def _correlations(cities):
    """{(city_a, city_b): err_corr} for the cities being sized today.

    Only the newest row per pair: derived_city_correlation is keyed on
    (city_a, city_b, computed_at), so every recompute leaves the old row
    behind and reading them all would average this month's correlation with
    one measured in another season.

    Pairs with no row are NOT added as zero. risk_budget.correlation_of falls
    back to DEFAULT_CORRELATION for anything absent, because "unmeasured" and
    "independent" are different statements.
    """
    if len(cities) < 2:
        return {}
    keys = sorted(cities)
    out, seen_at = {}, {}
    try:
        rows = rest_all("derived_city_correlation",
                        [("select", "city_a,city_b,err_corr,n_days,computed_at"),
                         ("city_a", f"in.({','.join(keys)})"),
                         ("city_b", f"in.({','.join(keys)})")],
                        order="city_a.asc,city_b.asc,computed_at.asc", page_size=1000)
    except Exception as e:
        print(f"  note: derived_city_correlation unavailable ({e}); "
              f"every pair assumed correlated at "
              f"{risk_budget.DEFAULT_CORRELATION}", file=sys.stderr)
        return {}
    for r in rows:
        if r.get("err_corr") is None:
            continue
        key = (r["city_a"], r["city_b"])
        at = str(r.get("computed_at") or "")
        if key in seen_at and at < seen_at[key]:
            continue
        seen_at[key] = at
        out[key] = float(r["err_corr"])
    return out


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


def _cost_version():
    """The cost assumptions in force right now, as a uuid MODEL_VERSIONS knows.

    THE COMMENT THIS REPLACES WAS THE BUG. It said "a uuid into cost_params",
    and that is exactly what it returned - but paper_trades.cost_version is a
    foreign key into MODEL_VERSIONS, beside forecast_version and
    calibration_version. Two tables, two id spaces, and cost_params ids are
    not in the other one. So every fill since the lineage columns started
    being stamped died in complete_paper_order with

        23503  Key (cost_version)=(68bdc8a9-...) is not present in table
               "model_versions"

    and no paper trade was recorded for seventeen hours while the rest of the
    pipeline reported success. It is the same mistake the first forecast_version
    made - putting the wrong kind of identifier in a uuid column - which is
    why the fix is the same helper that fixed that one.

    model_version_id registers (kind='cost', label) on first use and returns
    an id the foreign key accepts. The cost_params row id goes into the
    version's config, so the trail from a trade back to the exact fee row is
    still there; it just runs through the table the column actually references.
    """
    try:
        rows = rest("cost_params", {"select": "version_id,label,created_at",
                                    "active": "is.true",
                                    "order": "created_at.desc", "limit": "1"})
        if not rows:
            return None
        return model_version_id("cost", rows[0]["label"],
                                config={"cost_params_version": rows[0]["version_id"]})
    except Exception as e:
        print(f"  (cost_params unavailable: {e} - trades will carry no cost version)",
              file=sys.stderr)
        return None


def _snapshot(pr, yes_price, no_price, cost_version):
    """What was believed, at the moment the decision could be made.

    The plan's list, and nothing reconstructed: raw probability, calibrated
    probability, the price actually reachable, the model centre, sigma,
    confidence, and all four versions. prob_id names the exact
    band_probabilities row, so the snapshot can be checked against it for as
    long as that row survives - and remains readable after it does not.
    """
    return {
        "prob_id": pr.get("prob_id"),
        "priced_at": pr.get("computed_at"),
        "raw_prob": pr.get("raw_prob"),
        "calibrated_prob": pr.get("calibrated_prob"),
        # The price an order could actually have reached, per side. A snapshot
        # holding one number could not say which side it belonged to, and the
        # two are not complements once the spread is real.
        "executable_price": {"YES": yes_price, "NO": no_price},
        "model_centre_c": pr.get("forecast_max_c"),
        "sigma_c": pr.get("sigma_c"),
        "confidence": pr.get("confidence"),
        "lead_days": pr.get("lead_days"),
        "forecast_version": pr.get("forecast_version"),
        "calibration_version": pr.get("calibration_version"),
        "cost_version": cost_version,
        "input_forecast_run": pr.get("input_forecast_run"),
    }


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
    # The snapshot also sits at a SHALLOW path, keyed by band. decision_inputs
    # is the whole BandView and its shape changes every time a strategy needs
    # a new field; the trigger that stamps a trade's lineage reads this one,
    # which is a fixed contract of exactly the things a post-mortem needs.
    snapshot = {str(bid): (decision_bands[str(bid)].decision_snapshot or {})
                for bid in ids if str(bid) in decision_bands}
    sig.payload = {**(sig.payload or {}), "cycle_id": cycle_id,
                   "decision_at": now.isoformat(), "decision_inputs": inputs,
                   "decision_snapshot": snapshot}
    return sig


def _earned_weights(days=45):
    """What each strategy's own settled record says it should be given.

    Reads one return per settled signal from v_signal_mark and hands them to
    strategy_gate, which shrinks the mean toward a zero-centred prior and
    turns the result into a share of bankroll. A strategy that has never
    settled anything, or whose record is under water, earns nothing and its
    proposals are sized to zero - which is what should have happened to s1
    for the three weeks it spent losing 17.8c on the dollar before anybody
    measured it.

    A FAILURE HERE MUST NOT SIZE EVERYTHING TO ZERO. If the view is missing
    or the read fails, every strategy gets weight 1.0 and sizing falls back
    to the per-signal Kelly in base.size(). The gate is there to hold money
    back from strategies that have earned nothing, not to become a single
    point of failure that stops the desk.
    """
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).date().isoformat()
    try:
        rows = rest_all("v_signal_mark", [
            ("select", "strategy_id,price_at_fire,mark_net_per_share"),
            ("fired_at", f"gte.{since}"),
        ], order="signal_id.asc", page_size=1000)
    except Exception as e:
        print(f"  note: no signal marks ({e}); every strategy keeps full weight",
              file=sys.stderr)
        return {}

    by_strategy = {}
    for r in rows:
        stake = r.get("price_at_fire")
        net = r.get("mark_net_per_share")
        if not stake or net is None:
            continue
        by_strategy.setdefault(r["strategy_id"], []).append(float(net) / float(stake))
    return {sid: v for sid, v in strategy_gate.weights(by_strategy).items()}


def _allocate_ladders(fired, views, portfolio, earned,
                      risk=(None, None, 0.0), corr=None):
    """Re-size the day's entries across each ladder, then across the book.

    base.size() already sizes each signal on its own edge, but the bands of
    one market-day are MUTUALLY EXCLUSIVE - exactly one wins - so they are a
    single allocation problem, not N of them. allocator.allocate() solves it
    (see scripts/allocator.py), and the part that matters on this venue is
    the term per-band sizing has nowhere to put: the venue quotes a median
    5.7 of ~11 bands, so the chance that NONE of the quoted bands wins is
    large and real.

    AND THEN THERE IS THE SECOND LADDER. Solving each one correctly at a
    quarter of full Kelly does not make the desk a quarter-Kelly desk; it
    makes it forty-eight of them. scripts/risk_budget.py adds the three limits
    that only exist above a single ladder, and they enter here in two places
    because they are two different kinds of thing:

      THE DRAWDOWN SCALE GOES INTO THE KELLY FRACTION, before the ladders are
      solved. It is a preference about how much of an edge to take, so it
      belongs where the edge is turned into money - and changing it can change
      WHICH bands are worth holding, not just how much of them.

      THE CAPS ARE APPLIED AFTER, to the solved ladders. A per-city-day cap, a
      correlation-adjusted gross cap and what is left of today's entry budget
      are limits, not preferences: they cut what the solve produced rather
      than changing what it was solving.

    ENTRIES ONLY, AND YES ONLY. An exit is not a stake, and it keeps the size
    the strategy asked for. A NO leg is a bet on the complement of one band
    rather than on one outcome of the ladder, so it is not one of the
    mutually exclusive alternatives this formula is about - those keep their
    per-signal size too. Widening it to NO means modelling the ladder as
    2N outcomes with constraints between them, which is a different problem
    and not one to guess at.

    Returns how many signals were re-sized, for the run's own log.
    """
    by_band = {str(v.band_id): v for v in views}
    ladders = {}
    for sig in fired:
        if sig.action != "ENTER" or sig.side != "YES":
            continue
        v = by_band.get(str(sig.band_id))
        if v is None or sig.prob_at_fire is None or not sig.price_at_fire:
            continue
        ladders.setdefault((v.city_key, str(v.resolution_date)), []).append((sig, v))

    bankroll = getattr(portfolio, "bankroll", 0.0) or 0.0
    if bankroll <= 0:
        return 0

    equity, high_water, spent_today = risk
    base_lambda = allocator.DEFAULT_KELLY_FRACTION
    scale, why = risk_budget.drawdown_scale(equity, high_water, base_lambda)
    print(f"  risk: {why}")
    if scale <= 0:
        # The floor is a stop, not a shrink. Every entry is zeroed and the
        # signals still fire and are still written, so the day is on the
        # record as one the desk declined to fund rather than one it never saw.
        zeroed = 0
        for members in ladders.values():
            for sig, _v in members:
                sig.suggested_shares = 0.0
                zeroed += 1
        return zeroed

    # ---- 1. Solve each ladder at the allowed fraction. --------------------
    solved = {}          # (city, date) -> {band_id: (sig, v, weight, usd)}
    all_claims = {}      # (city, date) -> {band_id: [sig, ...]}
    for key, members in ladders.items():
        # One signal per band: if two strategies both want the same band, the
        # ladder is still one allocation and the band is still one position.
        # The other claims must be zeroed, not left alone - a rejected claim
        # that keeps its per-signal size is the band staked twice.
        best, claims = {}, {}
        for sig, v in members:
            bid = str(sig.band_id)
            w = earned[sig.strategy_id].weight if sig.strategy_id in earned else 1.0
            claims.setdefault(bid, []).append(sig)
            prev = best.get(bid)
            if prev is None or w > prev[2]:
                best[bid] = (sig, v, w)
        all_claims[key] = claims

        legs = [allocator.Leg(band_id=bid, prob=float(sig.prob_at_fire),
                              price=float(sig.price_at_fire),
                              depth_usd=getattr(v, "fillable_usd_5c_yes", None),
                              label=f"{v.city_key} {v.band_label}")
                for bid, (sig, v, _) in best.items()]
        stakes, _detail = allocator.allocate(
            legs, bankroll, kelly_fraction=base_lambda * scale)
        staked = {s.band_id: s for s in stakes}
        # UNWEIGHTED. The earned weight is applied at the very end, AFTER the
        # caps - see the ordering note in step 3.
        solved[key] = {bid: (sig, v, w,
                             staked[bid].usd if bid in staked else 0.0)
                       for bid, (sig, v, w) in best.items()}

    # ---- 2. The book-wide limits. -----------------------------------------
    # N_eff is counted by CITY, not by city-day: two ladders for the same city
    # on different dates share a station and a season, so they are one bet for
    # the purpose of asking how many independent ones the book holds.
    proposed_city_day = {k: sum(u for *_r, u in v.values()) for k, v in solved.items()}
    proposed_city = defaultdict(float)
    for (city, _date), usd in proposed_city_day.items():
        proposed_city[city] += usd

    limits = risk_budget.budget(
        bankroll=bankroll, equity=equity, high_water=high_water,
        proposed_by_city=dict(proposed_city), corr=corr or {},
        lambda_base=base_lambda, spent_today_usd=spent_today)
    for r in limits.reasons[1:]:          # [0] is the drawdown line, printed above
        print(f"  risk: {r}")

    final, detail = risk_budget.apply_budget(proposed_city_day, limits)
    if detail["per_city_cut"] or detail["gross_scale"] < 1.0 or detail["dropped_min"]:
        print(f"  risk: ${detail['before_usd']:,.0f} proposed -> "
              f"${detail['after_usd']:,.0f} allowed "
              f"({detail['per_city_cut']} city-day(s) at the cap, "
              f"gross x{detail['gross_scale']:.2f}, "
              f"{detail['dropped_min']} under the venue minimum)")

    # ---- 3. Carry the cut back to the signals, then apply earned trust. ---
    #
    # THE CAP COMES BEFORE THE WEIGHT AND THE ORDER IS NOT INTERCHANGEABLE.
    # Weighting first and capping second lets the cap swallow the weight: a
    # half-trusted strategy proposing $3,030 and a fully trusted one proposing
    # $6,061 both land on a $3,000 cap and are funded identically, which makes
    # the earned weight mean nothing exactly where the money is largest.
    #
    # Capping the UNWEIGHTED ladder and then applying the weight keeps both
    # statements intact: the cap is the most any one weather event may carry,
    # and the weight is how much of that a strategy has earned. Every weight is
    # at most 1, so the weighted total can only be smaller - the cap still
    # binds as a ceiling.
    resized = 0
    for key, members in solved.items():
        want = proposed_city_day.get(key, 0.0)
        got = final.get(key, 0.0)
        ratio = (got / want) if want > 0 else 0.0
        for bid, (sig, _v, weight, usd) in members.items():
            for other in all_claims[key].get(bid, []):
                if other is not sig:
                    other.suggested_shares = 0.0     # the band is one position
                    resized += 1
            sig.suggested_shares = (usd * ratio * weight) / float(sig.price_at_fire)
            resized += 1
    return resized


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
    ledgers = _ledgers()
    portfolios = _portfolios(ledgers)
    counts["ledgers"] = len(ledgers)
    fired, blocked, conflict_rows = run_strategies(ctx, configs, _open_positions(),
                                                   portfolios=portfolios)
    counts["fired"], counts["blocked"] = len(fired), len(blocked)
    counts["conflicts"] = len(conflict_rows)

    # EACH LADDER IS SOLVED INSIDE ONE STRATEGY'S OWN BOOK (plan v2 P5.1).
    # The allocator, the risk budget and the drawdown scale all run once per
    # shadow ledger, on that ledger's cash and that ledger's record, so one
    # strategy's claim on a band never zeroes another's.
    #
    # THE EARNED WEIGHT IS NOT APPLIED HERE. It shrinks a strategy's stake by
    # its settled record, which is a decision about how to split one pot of
    # capital - the portfolio account's question (P5.10), not a shadow
    # ledger's. On a shadow ledger it only starves a weak strategy of the
    # evidence that would show whether it is weak; P5.2 suspends a strategy
    # whose record says it loses. The weights are still computed and logged.
    earned = _earned_weights()
    risks = _risk_states(ledgers)
    corr = _correlations({v.city_key for v in views if v.city_key})
    by_strategy = defaultdict(list)
    for sig in fired:
        by_strategy[sig.strategy_id].append(sig)
    counts["resized"] = 0
    for sid, own in sorted(by_strategy.items()):
        counts["resized"] += _allocate_ladders(
            own, views, portfolios.get(sid) or Portfolio(bankroll=0.0), {},
            risks.get(sid, (None, None, 0.0)), corr)
    counts["strategies_earning"] = sum(1 for v in earned.values() if v.weight > 0)
    for sid, v in sorted(earned.items()):
        print(f"  weight {v.weight:.2f}  {sid}: {v.reason} (logged; not applied to a shadow ledger)")

    fired, held = hold_entries_without_cost_version(fired, views)
    counts["held_no_cost_version"] = len(held)
    if held:
        print(f"  HELD {len(held)} ENTER signal(s): no cost version could be resolved this run "
              f"(cost_params unreadable or empty). Exits still go out.", file=sys.stderr)

    stamp_sized_on(fired, portfolios)
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
    log_run("signal_engine", "attention" if held else "ok", counts["written"], counts)
    return 0


def stamp_sized_on(fired, portfolios):
    """Every signal records the bankroll it was sized on (plan v2 P5.10).

    The size is Kelly on the strategy's shadow ledger. The portfolio account
    trades the same signal with a different pot - the strategy's share of it -
    and scales the size by allocated capital / sized_on_usd, so it needs this
    number and must not assume it.
    """
    for s in fired:
        pf = portfolios.get(s.strategy_id)
        s.payload = {**(s.payload or {}), "sized_on_usd": float(getattr(pf, "bankroll", 0.0) or 0.0)}


def hold_entries_without_cost_version(fired, views):
    """(kept, held): ENTER signals are held when the run has no cost version.

    cost_version is never null on a trade (plan v2 P5.0 item 7). Live on 24
    Sep, 66 of 70 paper_trades carried none - all of them before the 19 Sep
    lineage fix - and 0 of 4,069 ENTER signals in the 14 days before. The
    one path still open is _cost_version() returning None when cost_params
    cannot be read, and every entry of that run would then be priced by an
    unrecorded cost model. An entry can wait for the next run; an EXIT is
    risk coming off and is never held for a label.
    """
    have_cost = any((v.decision_snapshot or {}).get("cost_version") for v in views)
    if have_cost:
        return list(fired), []
    kept = [s for s in fired if s.action != "ENTER"]
    held = [s for s in fired if s.action == "ENTER"]
    return kept, held


if __name__ == "__main__":
    sys.exit(main() or 0)
