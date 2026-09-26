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

JOB = "P5.8_strategy_learn"

NOT_FITTED = {
    "p_fill_touch_1h": "needs maker orders and fills; the maker path is P5.12",
    "adverse_spread_fraction": "needs maker orders and fills; the maker path is P5.12",
    "timing_transition": "needs the engine's hourly series per cell (P5.6 part 2, P5.12)",
    "lambda": "needs 40 settled decisions with predicted growth (P5.12)",
    "h": "needs observed churn cost from settled decisions (P5.12)",
    "cluster_correlation": "P5.9 part 2",
}


def previous_belief(rest):
    """The last belief table written, whatever the flag says: the step limit is
    taken from what the loop learned last, not from what the engine used."""
    rows = rest("strategy_params", [("select", "value,version"), ("param", "eq.belief"),
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


def main(argv=None, today=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", help="learn from target dates before this day (default: today, UTC)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from common import rest, rest_all, upsert, log_run

    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else (
        today or dt.datetime.now(dt.timezone.utc).date())
    checkpoints = rest_all("prediction_checkpoints",
                           {"select": "checkpoint_id,city_key,target_date,checkpoint,probs",
                            "target_date": f"lt.{as_of}"}, order="checkpoint_id.asc")
    outcomes = rest_all("fact_checkpoint_outcome",
                        {"select": "checkpoint_id,winner_band_id,ladder_has_winner",
                         "target_date": f"lt.{as_of}"}, order="checkpoint_id.asc")
    previous = previous_belief(rest)
    row, table = belief_row(checkpoints, outcomes, as_of, previous)

    written = 0
    if row and not args.dry_run:
        # ignore-duplicates: a version is written once, and a re-run of the
        # same night finds its row already there.
        written = upsert("strategy_params", [row], "param,scope,version")
    detail = {
        "as_of": str(as_of), "checkpoints": len(checkpoints), "outcomes": len(outcomes),
        "belief": {"version": table["version"], "n": table["n"], "held_back": table["held_back"],
                   "previous": (previous or {}).get("version"),
                   "written": bool(row) and not args.dry_run,
                   "unchanged": row is None},
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
