#!/usr/bin/env python3
"""The nightly learning loop (plan v2 P5.8), part 1: the belief maps.

Runs in the daily chain after databank, which banks the checkpoint outcomes
(bank_checkpoint_outcomes) it learns from. It learns only from target dates
strictly before the day it runs, and writes each fit to strategy_params as one
immutable row per version: the value, the sample n, and the prior and bounds
it was held to (Rule 11).

WHAT IT FITS NOW
  belief   the P5.3 reliability map: per 0.05 bin of the engine's probability,
           how often buckets priced there won, pooled and per checkpoint
           class, from prediction_checkpoints ladders and the venue's
           confirmed winners (fact_checkpoint_outcome). belief.fit holds its
           prior (K0 = 30 on p_model), bounds, minimum sample (N_MIN per bin)
           and 25% step from the previous version; this loop passes the
           previous version in so the step is taken from what was written.

  city_clusters   (P5.9 part 2, weekly) the Ledoit-Wolf shrunk correlation of
           day-ahead forecast errors between cities, and the clusters it
           implies (city_clusters.py). Read from the honest record in the
           repository and the whole-day labels; refitted when the last version
           is REFIT_DAYS old, each pair moving at most MAX_STEP from it.

  market_weight   (P5.3 amended, nightly) how far each view source may move
           the belief off the market, per checkpoint class: the engine's
           ladders (prediction_checkpoints) and S10's remaining-day ladders
           (s10_shadow_checkpoints), each against the book the tick recorded
           beside the engine's call, scored on the venue's winner
           (fact_checkpoint_outcome). market_anchor.fit holds a scope at 0
           until it has MIN_DAYS settled days and a gain whose bootstrap lower
           bound is above zero, and moves it at most MAX_STEP a night.

WHAT IT DOES NOT FIT YET, and why - named in every run's log so nobody reads
an absent parameter as a learned one:
  p_fill_touch_1h, adverse_spread_fraction   need maker orders and their
      fills; the maker path is part of the one engine (P5.12)
  timing transition model                    needs the engine's hourly
      (p_post, ask, depth) series per cell (P5.6 part 2, P5.12)
  lambda, h                                  need settled decisions with the
      growth the solver predicted (P5.12)
  cluster correlation                        P5.9 part 2

PRIORS FROZEN. What is written here is used only when
settings.strategy_learning.enabled is true (scripts/learned.py). The plan
turns it on after the replay (P7.3) shows the learned values beat the priors
on out-of-sample dates; until then the loop records every night and the
engine runs on the priors.

    python scripts/strategy_learn.py [--as-of YYYY-MM-DD] [--dry-run]
"""
import argparse
import datetime as dt
import json
import sys

import belief
import city_clusters
import market_anchor

JOB = "P5.8_strategy_learn"

NOT_FITTED = {
    "p_fill_touch_1h": "needs maker orders and fills; the maker path is P5.12",
    "adverse_spread_fraction": "needs maker orders and fills; the maker path is P5.12",
    "timing_transition": "needs the engine's hourly series per cell (P5.6 part 2, P5.12)",
    "lambda": "needs 40 settled decisions with predicted growth (P5.12)",
    "h": "needs observed churn cost from settled decisions (P5.12)",
}


def previous_belief(rest, param="belief"):
    """The last table written for a param, whatever the flag says: the step
    limit is taken from what the loop learned last, not from what the engine used."""
    rows = rest("strategy_params", [("select", "value,version"), ("param", f"eq.{param}"),
                                    ("scope", "eq.all"),
                                    ("order", "fitted_at.desc"), ("limit", "1")])
    if not rows:
        return None
    table = rows[0].get("value") or {}
    table.setdefault("version", rows[0].get("version"))
    return table


def belief_row(checkpoints, outcomes, as_of, previous=None):
    """(row, table) for strategy_params, or (None, table) when nothing is new:
    the same settled buckets as the previous version would only restamp it."""
    table = belief.fit(checkpoints, outcomes, as_of, previous=previous)
    if previous and previous.get("n") == table["n"] and previous.get("bins") == table["bins"]:
        return None, table
    row = {
        "param": "belief", "scope": "all", "version": table["version"], "value": table,
        "n": table["n"], "as_of": str(as_of),
        "prior": {"centre": "p_model", "k0": belief.K0, "n_min": belief.N_MIN,
                  "max_step": belief.MAX_STEP},
        "bounds": {"p": [belief.P_LO, belief.P_HI]},
    }
    return row, table


def cluster_inputs(rest_all):
    """(honest record rows, whole-day labels, active cities) for the cluster fit."""
    import honest_record
    import station_mos
    from common import get_cities
    return (honest_record.read_rows(honest_record.BEST_MATCH_FILE), station_mos.load_labels(rest_all),
            [c["city_key"] for c in get_cities(require_coords=False)])


def cluster_row(record_rows, labels, cities, as_of, previous=None):
    """(row, table) for strategy_params; (None, table) when no city has
    city_clusters.MIN_DATES of evidence, which would only restamp the prior."""
    table = city_clusters.fit(record_rows, labels, as_of, previous=previous, all_cities=cities)
    if not table["measured_cities"]:
        return None, table
    row = {
        "param": "city_clusters", "scope": "all", "version": table["version"], "value": table,
        "n": table["n_dates"], "as_of": str(as_of),
        "prior": {"rho": city_clusters.RHO_PRIOR, "min_dates": city_clusters.MIN_DATES,
                  "max_step": city_clusters.MAX_STEP, "cluster_rho": city_clusters.CLUSTER_RHO,
                  "window_days": city_clusters.WINDOW_DAYS, "bias_window": city_clusters.BIAS_WINDOW},
        "bounds": {"rho": list(city_clusters.RHO_BOUNDS)},
    }
    return row, table


def market_rows(checkpoints, outcomes, s10_rows):
    """[(date, scope, model probs, market probs, winner)] for market_anchor.fit.

    The market is the book the tick stored on the engine's checkpoint
    (prediction_checkpoints.market), read the way the engine reads it at a
    decision (market_anchor.market_probs). S10's call at the same city, date
    and checkpoint is scored against that same book and winner. A checkpoint
    with an unquoted bucket, or a ladder without the winner, is left out.
    """
    won = {o["checkpoint_id"]: o["winner_band_id"] for o in outcomes
           if o.get("ladder_has_winner") and o.get("winner_band_id")}
    rows, by_key = [], {}
    for c in checkpoints:
        winner = won.get(c["checkpoint_id"])
        probs = c.get("probs") or {}
        if winner is None or not probs:
            continue
        market = market_anchor.market_probs(c.get("market"), list(probs))
        if market is None or winner not in market:
            continue
        by_key[(c["city_key"], str(c["target_date"]), c["checkpoint"])] = (market, winner)
        rows.append((str(c["target_date"]), market_anchor.scope("engine", c["checkpoint"]),
                     {b: float(p) for b, p in probs.items()}, market, winner))
    for s in s10_rows:
        hit = by_key.get((s["city_key"], str(s["target_date"]), s["checkpoint"]))
        probs = s.get("probs") or {}
        if hit is None or set(probs) != set(hit[0]):
            continue
        rows.append((str(s["target_date"]), market_anchor.scope("s10", s["checkpoint"]),
                     {b: float(p) for b, p in probs.items()}, hit[0], hit[1]))
    return rows


def market_weight_row(rows, as_of, previous=None):
    """(row, table) for strategy_params, or (None, table) when there is no
    evidence, or it is what the previous version already saw: either would
    only restamp the prior or that version."""
    table = market_anchor.fit(rows, as_of, previous=previous)
    if not table["evidence"]:
        return None, table
    if previous and previous.get("weights") == table["weights"] and previous.get("evidence") == table["evidence"]:
        return None, table
    row = {
        "param": "market_weight", "scope": "all", "version": table["version"], "value": table,
        "n": sum(1 for r in rows if str(r[0]) < str(as_of)),
        "as_of": str(as_of),
        "prior": {"w": market_anchor.W_PRIOR, "min_days": market_anchor.MIN_DAYS,
                  "max_step": market_anchor.MAX_STEP, "boot_n": market_anchor.BOOT_N,
                  "boot_lower": market_anchor.BOOT_LOWER},
        "bounds": {"w": list(market_anchor.W_BOUNDS)},
    }
    return row, table


def main(argv=None, today=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", help="learn from target dates before this day (default: today, UTC)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from common import rest, rest_all, upsert, log_run

    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else (
        today or dt.datetime.now(dt.timezone.utc).date())
    checkpoints = rest_all("prediction_checkpoints",
                           {"select": "checkpoint_id,city_key,target_date,checkpoint,probs,market",
                            "target_date": f"lt.{as_of}"}, order="checkpoint_id.asc")
    outcomes = rest_all("fact_checkpoint_outcome",
                        {"select": "checkpoint_id,winner_band_id,ladder_has_winner",
                         "target_date": f"lt.{as_of}"}, order="checkpoint_id.asc")
    previous = previous_belief(rest)
    row, table = belief_row(checkpoints, outcomes, as_of, previous)

    # Nightly: how far each view may move the belief off the market (P5.3 amended).
    s10_rows = rest_all("s10_shadow_checkpoints",
                        {"select": "city_key,target_date,checkpoint,probs", "target_date": f"lt.{as_of}"},
                        order="checkpoint_id.asc")
    prev_w = previous_belief(rest, "market_weight")
    m_rows = market_rows(checkpoints, outcomes, s10_rows)
    w_row, w_table = market_weight_row(m_rows, as_of, prev_w)
    w_detail = {"version": w_table["version"], "previous": (prev_w or {}).get("version"),
                "rows": len(m_rows), "s10_rows": len(s10_rows), "weights": w_table["weights"],
                "evidence": w_table["evidence"], "written": bool(w_row) and not args.dry_run,
                "unchanged": w_row is None}

    # Weekly: the cluster correlations (P5.9 part 2).
    prev_c = previous_belief(rest, "city_clusters")
    c_row, c_detail = None, {"due": city_clusters.due(prev_c, as_of), "previous": (prev_c or {}).get("version")}
    if c_detail["due"]:
        c_row, c_table = cluster_row(*cluster_inputs(rest_all), as_of, previous=prev_c)
        c_detail.update({k: c_table[k] for k in ("version", "n_dates", "first_date", "last_date",
                                                  "shrinkage", "rbar", "pairs_moved")},
                        measured_cities=len(c_table["measured_cities"]),
                        clusters=len(set(c_table["cluster"].values())),
                        max_rho=max(c_table["rho"].values(), default=None),
                        written=bool(c_row) and not args.dry_run)

    written = 0
    if not args.dry_run:
        # ignore-duplicates: a version is written once, and a re-run of the
        # same night finds its row already there.
        new_rows = [r for r in (row, w_row, c_row) if r]
        if new_rows:
            written = upsert("strategy_params", new_rows, "param,scope,version")
    detail = {
        "as_of": str(as_of), "checkpoints": len(checkpoints), "outcomes": len(outcomes),
        "belief": {"version": table["version"], "n": table["n"], "held_back": table["held_back"],
                   "previous": (previous or {}).get("version"),
                   "written": bool(row) and not args.dry_run,
                   "unchanged": row is None},
        "market_weight": w_detail,
        "city_clusters": c_detail,
        "not_fitted": NOT_FITTED,
        "dry_run": args.dry_run,
    }
    print(json.dumps(detail, indent=2))
    if not args.dry_run:
        log_run(JOB, "ok", written, detail)
    return detail


if __name__ == "__main__":
    main()
    sys.exit(0)
