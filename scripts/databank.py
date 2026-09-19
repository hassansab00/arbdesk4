#!/usr/bin/env python3
"""
Freeze the desk's own history: what it predicted, what the market said, and
what independently verified outcome evidence says happened.

WHY THIS EXISTS. Everything AD4 knows is either live or derived, and both are
destroyed by their own next run. weather_forecasts keeps every run but never
marks the one that was acted on. edges is recomputed in place, so the edge a
signal fired on is gone by the next cycle. band_probabilities is append-only
but nothing ever joined it to the outcome, which means this desk has never
once measured whether a band it priced at 30% settled 30% of the time.

A trading desk's only durable asset is its own record of predictions against
outcomes. Nobody else has it, every improvement is measured against it, and
until now AD4 was throwing it away every four hours.

WHAT IT WRITES. Three immutable tables (sql/ad4_18_databank.sql), once per
settled city-day, never updated:

  fact_forecast_outcome  each model at each lead, against a verified station max
  fact_band_outcome      model probability and market price per band, against
                         whether that band settled yes
  fact_signal_outcome    every signal, and whether acting on it paid

Run daily, after settlement. Idempotent at the database, not just in this
script: every write is an upsert that ignores duplicates on the table's primary
key, so re-running is always safe and a half-written day repairs itself.

  python scripts/databank.py [--days 7] [--force]
"""
import argparse
import datetime as dt
import sys
from collections import defaultdict

from common import (rest, rest_all, upsert, log_run, get_cities,
                    city_local_date, timezone_of)

# Kept for observation-quality diagnostics. It is no longer sufficient to
# declare a day settled: only v_verified_weather_outcomes may do that.
MIN_OBS_FOR_A_DAY = 12


def _observed_max(days_back):
    """(city_key, date) -> {max_c, n_obs}. From v_city_daily_max where it
    exists - it groups by the CITY's local day, which is the day a daily
    maximum actually belongs to - falling back to a UTC grouping if the view
    is absent so this still runs on a database without ad4_17."""
    since = (dt.date.today() - dt.timedelta(days=days_back + 2)).isoformat()
    out = {}
    try:
        rows = rest("v_city_daily_max", [
            ("select", "city_key,obs_date,max_c,n_obs"),
            ("obs_date", f"gte.{since}"),
            ("limit", "20000"),
        ])
        for r in rows:
            out[(r["city_key"], str(r["obs_date"]))] = {
                "max_c": r["max_c"], "n_obs": r.get("n_obs") or 0, "source": "v_city_daily_max",
            }
        return out
    except Exception as e:
        print(f"  note: v_city_daily_max unavailable ({e}); grouping observations by UTC day",
              file=sys.stderr)

    rows = rest("weather_observations", [
        ("select", "city_key,valid_at,temp_c"),
        ("valid_at", f"gte.{since}T00:00:00Z"),
        ("limit", "100000"),
    ])
    # THE CITY'S DAY, not UTC. This bucketed by `valid_at[:10]` and labelled
    # the result "utc_day" - honest about what it was doing and wrong about
    # what it should have been doing, because every forecast it is frozen
    # against is keyed by the city's LOCAL date. See common.city_local_date.
    tz = timezone_of(get_cities(require_coords=False))
    agg = defaultdict(lambda: {"max_c": None, "n_obs": 0, "source": "local_day"})
    for r in rows:
        if r.get("temp_c") is None:
            continue
        key = (r["city_key"], city_local_date(r["valid_at"], tz.get(r["city_key"])))
        a = agg[key]
        a["n_obs"] += 1
        if a["max_c"] is None or r["temp_c"] > a["max_c"]:
            a["max_c"] = r["temp_c"]
    return dict(agg)


def _already_banked(table, since):
    """The (city, date, model, lead) rows already frozen - the table's ACTUAL
    primary key, not a prefix of it.

    This used to return (city, date) pairs, which is a coarser key than the one
    the database enforces: a day banked for one model at one lead marked the
    whole day done, so a later model or a later lead could never be added. It
    also silently hid the reverse failure - a day whose rows were only
    partially written stayed partially written for good.

    This is now an optimisation only. Correctness comes from the primary key:
    every writer below goes through upsert(), so a row already present is
    ignored by Postgres rather than raising 409 and killing the run.
    """
    try:
        rows = rest_all(table, [("select", "city_key,for_date,model,lead_days"),
                                ("for_date", f"gte.{since}")],
                        order="city_key.asc,for_date.asc,model.asc,lead_days.asc",
                        page_size=1000)
        return {(r["city_key"], str(r["for_date"]), r.get("model"), r.get("lead_days"))
                for r in rows}
    except Exception:
        return set()


def _verified_weather(days_back):
    """Return only versioned, authoritative station outcomes.

    A running maximum with many readings is useful operational evidence, but
    it is not final resolution evidence. The former collector treated twelve
    readings as final and could freeze a partial maximum permanently.
    """
    since = (dt.date.today() - dt.timedelta(days=days_back)).isoformat()
    try:
        rows = rest_all("v_verified_weather_outcomes", [
            ("select", "city_key,for_date,observed_max_c,source_authority,station_id,captured_at"),
            ("for_date", f"gte.{since}"),
        ], order="city_key.asc,for_date.asc", page_size=1000)
    except Exception as e:
        print(f"  verified weather outcomes unavailable ({e}); no forecast outcomes will be frozen",
              file=sys.stderr)
        return {}
    return {
        (r["city_key"], str(r["for_date"])): {
            "max_c": r["observed_max_c"],
            "n_obs": None,
            "source": f"{r.get('source_authority') or 'authority'}:{r.get('station_id') or 'station'}",
            "evidence_at": r.get("captured_at"),
        }
        for r in rows
    }


def bank_forecasts(observed, days_back, force):
    """One row per forecast whose final station outcome is verified."""
    since = (dt.date.today() - dt.timedelta(days=days_back)).isoformat()
    until = dt.date.today().isoformat()          # today is not settled yet
    done = set() if force else _already_banked("fact_forecast_outcome", since)

    # rest_all, NOT rest. PostgREST caps a response at db-max-rows (1,000
    # here) and ignores a larger ?limit=, silently. This window holds 7,766
    # forecast rows, so the old read saw 13% of them in no particular order,
    # found no verified outcome for most of what it did see, and banked
    # nothing - while reporting "ok, banked 0". Five runs on 2026-09-14 alone
    # said exactly that while 09-12 and 09-13 sat there fully joinable.
    rows = rest_all("weather_forecasts", [
        ("select", "forecast_id,city_key,model,run_at,for_date,lead_days,forecast_max_c"),
        ("for_date", f"gte.{since}"),
        ("for_date", f"lt.{until}"),
    ], order="forecast_id.asc", page_size=1000)

    # Keep the LATEST run per (city, date, model, lead): that is the forecast
    # standing at the time, which is what was acted on.
    best = {}
    for r in rows:
        if r.get("forecast_max_c") is None:
            continue
        k = (r["city_key"], str(r["for_date"]), r.get("model") or "unknown", r.get("lead_days"))
        cur = best.get(k)
        if not cur or str(r.get("run_at") or "") > str(cur.get("run_at") or ""):
            best[k] = r

    out = []
    for (city, date, model, lead), r in best.items():
        if (city, date, model, lead if lead is not None else -1) in done:
            continue
        obs = observed.get((city, date))
        if not obs or obs["max_c"] is None:
            continue
        out.append({
            "city_key": city, "for_date": date, "model": model,
            "lead_days": lead if lead is not None else -1,
            "run_at": r.get("run_at"),
            "forecast_max_c": r["forecast_max_c"],
            "observed_max_c": obs["max_c"],
            "obs_source": obs["source"], "n_obs": obs["n_obs"],
        })
    return out


def bank_bands(observed, days_back, force):
    """One row per band after its complete ladder is venue-confirmed.

    `observed` contains verified weather evidence when available. It is useful
    context but never decides YES/NO; the matching Gamma+CLOB winner does.
    """
    since = (dt.date.today() - dt.timedelta(days=days_back)).isoformat()
    until = dt.date.today().isoformat()
    done = set()
    if not force:
        try:
            rows = rest_all("fact_band_outcome", [("select", "band_id")],
                            order="band_id.asc", page_size=1000)
            done = {r["band_id"] for r in rows}
        except Exception:
            pass

    markets = rest_all("markets", [("select", "market_id,city_key,resolution_date"),
                                    ("resolution_date", f"gte.{since}"),
                                    ("resolution_date", f"lt.{until}")],
                       order="market_id.asc", page_size=1000)
    if not markets:
        return []
    by_market = {m["market_id"]: m for m in markets}

    # CHUNKED. This used to put every market id of the window into ONE
    # market_id=in.(...) filter - about 1,000 UUIDs, a 37 KB URL - and PostgREST
    # answered 400. The except below then reported "no band outcomes will be
    # frozen" on every run since 8 September, which is why fact_band_outcome
    # stopped growing. The bands read further down already chunked by 100.
    market_resolution, band_resolution = {}, {}
    try:
        all_ids = list(by_market)
        for i in range(0, len(all_ids), 100):
            market_filter = f"in.({','.join(str(x) for x in all_ids[i:i + 100])})"
            for r in rest_all("v_venue_market_resolution", [
                    ("select", "market_id,resolution_state"),
                    ("market_id", market_filter)],
                    order="market_id.asc", page_size=1000):
                market_resolution[r["market_id"]] = r["resolution_state"]
            for r in rest_all("v_venue_band_resolution", [
                    ("select", "band_id,market_id,settled_yes,resolution_state,confirmed_at"),
                    ("market_id", market_filter)],
                    order="band_id.asc", page_size=1000):
                band_resolution[r["band_id"]] = r
    except Exception as e:
        print(f"  verified venue outcomes unavailable ({e}); no band outcomes will be frozen",
              file=sys.stderr)
        return []

    bands = []
    ids = list(by_market)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        bands += rest_all("bands", [
            ("select", "band_id,market_id,band_lo,band_hi,open_low,open_high"),
            ("market_id", f"in.({','.join(str(x) for x in chunk)})"), ], order="band_id.asc", page_size=1000)
    bands = [b for b in bands
             if b["band_id"] not in done
             and market_resolution.get(b["market_id"]) == "confirmed"
             and band_resolution.get(b["band_id"], {}).get("resolution_state") == "confirmed"]
    if not bands:
        return []

    # The probability standing when the day settled, and the last edge seen.
    probs, edges = {}, {}
    band_ids = [b["band_id"] for b in bands]
    for i in range(0, len(band_ids), 100):
        chunk = band_ids[i:i + 100]
        inlist = f"in.({','.join(chunk)})"
        for r in rest_all("band_probabilities", [
                ("select", "band_id,calibrated_prob,raw_prob,sigma_c,confidence,regime_label,"
                           "forecast_max_c,computed_at"),
                ("band_id", inlist)],
                order="computed_at.desc,prob_id.desc", page_size=1000):
            probs.setdefault(r["band_id"], r)
        # v_latest_edge, NOT v_opportunities: the opportunities view only holds
        # markets whose resolution date is today or later, so by the time a
        # band settles it has left the view and every frozen fact carried a
        # null market price - the "did being right pay" views could never fill.
        try:
            for r in rest_all("v_latest_edge", [
                    ("select", "band_id,side,market_price,edge_net_pp,fillable_usd_5c"),
                    ("band_id", inlist), ("side", "eq.YES")],
                    order="band_id.asc", page_size=1000):
                edges.setdefault(r["band_id"], r)
        except Exception as e:
            print(f"  note: v_latest_edge unavailable ({e})", file=sys.stderr)

    out = []
    for b in bands:
        m = by_market[b["market_id"]]
        city, date = m["city_key"], str(m["resolution_date"])
        obs = observed.get((city, date))
        mx = obs.get("max_c") if obs else None
        lo, hi = b.get("band_lo"), b.get("band_hi")
        settled = bool(band_resolution[b["band_id"]]["settled_yes"])
        p = probs.get(b["band_id"], {})
        e = edges.get(b["band_id"], {})
        out.append({
            "band_id": b["band_id"], "city_key": city, "for_date": date,
            "band_lo": lo, "band_hi": hi,
            "open_low": b.get("open_low"), "open_high": b.get("open_high"),
            # The RAW model probability, never the calibrated one: calibration.py
            # fits its map on this column, and fitting a map on already-mapped
            # numbers converges on nothing.
            "model_prob": p.get("raw_prob") if p.get("raw_prob") is not None else p.get("calibrated_prob"),
            "sigma_c": p.get("sigma_c"), "confidence": p.get("confidence"),
            "regime_label": p.get("regime_label"), "forecast_max_c": p.get("forecast_max_c"),
            "market_price": e.get("market_price"), "edge_net_pp": e.get("edge_net_pp"),
            "volume_usd": e.get("volume_usd"), "depth_5c": e.get("fillable_usd_5c"),
            "priced_at": p.get("computed_at"),
            "observed_max_c": mx, "settled_yes": bool(settled),
        })
    return out


def signal_correct(action, side, settled_yes):
    """Was the CALL right? Not the same question as whether the band won.

    A NO signal on a band that lost is correct, and its settled_yes is false.
    Scoring the two with one column reads every NO signal backwards.

    An EXIT is a different claim again - "get out of this now" - which the
    band's result does not answer: a position exited at a profit before a band
    that went on to win was still a good exit or a bad one depending on the
    price, not on the outcome. It is left unscored rather than scored wrongly.
    """
    if settled_yes is None or action != "ENTER":
        return None
    if side == "YES":
        return bool(settled_yes)
    if side == "NO":
        return not bool(settled_yes)
    return None


def bank_signals(days_back, force):
    """Every signal whose BAND has settled, with the call scored against it.

    IT USED TO KEY OFF A CLOSED TRADE, and that is the wrong hinge. A signal is
    a CALL - "this bucket wins" or "this bucket loses" - and whether the desk
    happened to get filled is a separate fact about execution. Keying off the
    fill meant:

      a correct call that was never filled looked identical to no signal, so
      nothing could measure whether a strategy was right, only whether it made
      money, which is a much noisier question;

      settled_yes was never written at all - 1,963 rows, none with it - so
      "which forecast version made a correct call" had no answer;

      and it banked the wrong rows. Measured 2026-09-19: 2,217 signals sat on
      a band the venue had settled, 1,963 rows existed, and only 353 were in
      both sets. 1,864 settled signals had never been banked.

    Now the hinge is the band's outcome from fact_band_outcome, which is
    venue-confirmed evidence - Gamma and CLOB agreeing on the winner, not a
    partially observed running maximum, which is a number still moving. The
    trade, when there is one, still supplies the fill, the slippage and the
    P&L.
    """
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_back)).isoformat()
    done = set()
    if not force:
        try:
            done = {r["signal_id"] for r in
                    rest_all("fact_signal_outcome", [("select", "signal_id")],
                             order="signal_id.asc", page_size=1000)}
        except Exception:
            pass

    sigs = rest_all("signals", [("select", "*"), ("fired_at", f"gte.{since}")],
                    order="signal_id.asc", page_size=1000)
    sigs = [s for s in sigs if s.get("signal_id") not in done]
    if not sigs:
        return []

    # THE BAND'S OUTCOME IS THE HINGE. Venue-confirmed, one row per band.
    outcome = {}
    for r in rest_all("fact_band_outcome",
                      [("select", "band_id,settled_yes,captured_at,for_date")],
                      order="band_id.asc", page_size=1000):
        outcome[str(r["band_id"])] = r

    trades = rest_all("paper_trades", [("select", "*")], order="trade_id.asc", page_size=1000)
    by_band = defaultdict(list)
    for t in trades:
        if t.get("band_id"):
            by_band[str(t["band_id"])].append(t)

    out = []
    for s in sigs:
        band = str(s.get("band_id")) if s.get("band_id") else None
        o = outcome.get(band or "")
        if o is None or o.get("settled_yes") is None:
            continue                       # nothing to score the call against yet

        # The trade this signal produced, when there is one: same band and
        # strategy, opened at or after it fired. Nearest in time wins. Absent
        # is now a normal outcome rather than a reason to skip the row.
        cand = [t for t in by_band.get(band or "", [])
                if t.get("strategy_id") == s.get("strategy_id")
                and str(t.get("opened_at") or "") >= str(s.get("fired_at") or "")]
        cand.sort(key=lambda t: str(t.get("opened_at") or ""))
        t = cand[0] if cand else None
        closed = bool(t and t.get("closed_at") and t.get("net_pnl") is not None)

        fill = (t or {}).get("avg_fill_price")
        fired = s.get("price_at_fire")
        settled_yes = bool(o["settled_yes"])
        snap = ((s.get("payload") or {}).get("decision_snapshot") or {}).get(band or "") or {}

        out.append({
            "signal_id": s["signal_id"], "strategy_id": s.get("strategy_id"),
            "band_id": s.get("band_id"), "city_key": s.get("city_key"),
            "for_date": o.get("for_date"),
            "side": s.get("side"), "action": s.get("action"), "reason": s.get("reason"),
            "fired_at": s.get("fired_at"), "severity": s.get("severity"),
            "price_at_fire": fired, "prob_at_fire": s.get("prob_at_fire"),
            "edge_at_fire": s.get("edge_at_fire"), "status": s.get("status"),
            "filled": bool(t), "fill_price": fill, "shares": (t or {}).get("shares"),
            "gross_pnl": (t or {}).get("gross_pnl") if closed else None,
            "net_pnl": (t or {}).get("net_pnl") if closed else None,
            # Where a correct call turns into a loss: the gap between the price
            # that justified the signal and the price actually paid.
            "slippage_c": (round((fill - fired) * 100, 4)
                           if fill is not None and fired is not None else None),
            # THE BAND'S RESULT, and separately THE DESK'S CALL. A NO signal on
            # a band that lost is a correct call whose settled_yes is false;
            # one column could not say both.
            "settled_yes": settled_yes,
            "signal_correct": signal_correct(s.get("action"), s.get("side"), settled_yes),
            "settled_at": o.get("captured_at"),
            "outcome_source": "fact_band_outcome",
            # What produced the call, so "which forecast version was right" has
            # an answer. Absent on signals fired before the snapshot existed.
            "forecast_version": snap.get("forecast_version"),
            "calibration_version": snap.get("calibration_version"),
            "cost_version": snap.get("cost_version"),
        })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7, help="how far back to look for settled days")
    ap.add_argument("--force", action="store_true",
                    help="ignore the local skip-list and offer every settled day to the "
                         "database. Rows already frozen stay as they are; rows MISSING from "
                         "a partially-banked day get written. Use it to repair, not to rewrite.")
    args = ap.parse_args()

    observed = _observed_max(args.days)
    verified = _verified_weather(args.days)
    print(f"observed maxima available for {len(observed)} city-day(s); "
          f"{len(verified)} have final authority evidence")

    fc = bank_forecasts(verified, args.days, args.force)
    bd = bank_bands(verified, args.days, args.force)
    sg = bank_signals(args.days, args.force)

    # upsert, not insert. These tables are immutable and primary-keyed, so a
    # row already banked must be a no-op - not a 409 that aborts the run and
    # loses every row after it in the batch. That is what happened on
    # 2026-09-05: one warsaw forecast outcome was already present and the whole
    # job died with 678 city-days waiting behind it.
    #
    # resolution=ignore-duplicates keeps the immutability guarantee - a frozen
    # row is never rewritten - while making the job safe to re-run at will.
    n_fc = upsert("fact_forecast_outcome", fc, "city_key,for_date,model,lead_days") if fc else 0
    n_bd = upsert("fact_band_outcome", bd, "band_id") if bd else 0
    n_sg = upsert("fact_signal_outcome", sg, "signal_id") if sg else 0

    summary = (f"banked {n_fc} forecast outcome(s), {n_bd} band outcome(s), "
               f"{n_sg} signal outcome(s)")
    print(summary)
    if n_bd:
        hits = sum(1 for b in bd if b["settled_yes"])
        print(f"  of the bands banked, {hits} settled yes ({hits / len(bd):.1%})")
    log_run("databank", "ok", n_fc + n_bd + n_sg,
            {"forecasts": n_fc, "bands": n_bd, "signals": n_sg, "summary": summary})


if __name__ == "__main__":
    main()
