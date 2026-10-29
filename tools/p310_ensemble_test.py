#!/usr/bin/env python3
"""Does the ensemble record add to the price? Plan v2 P2.10 part 1 / P3.10, R23.

Research: nothing here prices or trades.

WHAT THE PLAN FIXED
-------------------
P2.10 part 1: "The test is the P3.10 one: does it add to the price,
walk-forward, once 30 days have accrued." R23 (docs/WXPREDICT_BUILD.md):
"waits for 30 days of record ... Run as planned." P3.10's decision rule: a
second stage (recal or pooled) goes to the engine, in shadow, only if its
walk-forward gain over the market has a 90% interval above zero at a
checkpoint; the model's weight in it only if pooled beats recal the same way.
P3.10 Q3 (tools/market_vs_model.py) is that test: at the cutoffs d0_00 and
d0_08 on the venue's own record, market vs recal (market^a) vs pooled
(market^a x model^b) vs anchor, fitted walk-forward by calendar month from the
first month with MIN_TRAIN (1,000) city-days behind it, gains with a 90%
date-bootstrap interval (BOOT 4,000, seed 11). This tool reuses those
functions unchanged (market_ladders, pool, fit_pool, fit_anchor, boot, logloss,
walk_forward).

WHAT THE PLAN LEFT OPEN, FIXED HERE ON 29 OCT BEFORE ANY SCORE WAS COMPUTED
--------------------------------------------------------------------------
1. The model. The record keeps each model's members' daily maxima summarised
   (n, mean, population SD, percentiles), not the members. The model is all
   82 members of both ensembles as one: the mixture's exact mean and SD from
   the two summaries (n-weighted), priced on the venue's ladder by the
   engine's own compute_band_probabilities(mean, SD), floored at MODEL_FLOOR
   and renormalised, as Q3's model is. Window 00_23: the venue settles on the
   whole local day. Raw grid-point output, not corrected to the station; the
   pooled exponent b rescales its log probabilities (for a normal ladder, b
   widens or narrows it), so its width need not be right, its centre must.
2. What was known at the cutoff. For each (city, day, model), the row from
   the newest night whose fetched_at is at or before the cutoff; a city-day
   needs both models. Nothing fetched after the cutoff is used.
3. The folds. 30 days cannot fill Q3's monthly fold: the months before
   October hold the record's first two days only. So the result is reported
   twice: (a) Q3's walk_forward exactly as written (it scores whatever months
   have MIN_TRAIN behind them, possibly none); (b) the same fits, by day: each
   date scored with fits on every date at least two days before it (the day
   before may not be settled at the cutoff), from the first date with
   MIN_TRAIN city-days behind it. (b) changes only the fold's length.
   Neither reads a score of the other's.
4. The verdict, per P3.10's rule, on (b) when (a) scores nothing: "adds to
   the price" only if pooled minus recal has a 90% interval above 0 at a
   checkpoint; a second stage "goes to shadow" only if its gain over the
   market does. Either is a proposal to Hassan, never a switch (Rule 6/11).

THE LOOKS (added 29 Oct after the first run, which scored no fold of either
kind and so showed no score; nothing above changed)
--------------------------------------------------------------------------
The first run had 898 city-days at d0_00 and 921 at d0_08, settled to 22 Oct,
fewer than MIN_TRAIN behind any fold. The tool is re-run unchanged, the
venue's record refreshed first: the first look when (b) has at least 20
scored dates at both cutoffs; the second and last when (a)'s November fold
holds every November date the record has settled, the month's verdict.

    python tools/p310_ensemble_test.py --out data/eval/p310/ensemble_test_2026-10-29
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import market_vs_model as M  # noqa: E402
from probability_engine import compute_band_probabilities  # noqa: E402  (scripts/, on M's path)

ENS = "data/training/ensembles/ensemble_daily.csv.gz"
MODELS = ("ecmwf_ifs025", "gfs025")
WINDOW = "00_23"
GAP_DAYS = 2


def unix(iso):
    return int(dt.datetime.fromisoformat(iso).timestamp())


def ensemble_rows(path=ENS):
    """{(city, local_date, model): [(fetched_unix, n, mean, sd), ...]} for the window."""
    out = defaultdict(list)
    for r in M.read_csv(path):
        if r["window"] == WINDOW and r["model"] in MODELS:
            out[(r["city_key"], r["local_date"], r["model"])].append(
                (unix(r["fetched_at"]), int(r["n_members"]), float(r["mean_c"]), float(r["sd_c"])))
    return out


def mixture(parts):
    """Exact mean and population SD of the members of several summarised sets."""
    n = sum(p[0] for p in parts)
    mu = sum(p[0] * p[1] for p in parts) / n
    second = sum(p[0] * (p[2] ** 2 + p[1] ** 2) for p in parts) / n
    return mu, math.sqrt(max(second - mu * mu, 0.0))


def at_cutoff(ens, city, day, cut):
    """(mean, sd) of both models' newest rows fetched at or before cut, or None."""
    parts = []
    for m in MODELS:
        got = [x for x in ens.get((city, day, m), []) if x[0] <= cut]
        if not got:
            return None
        _t, n, mean, sd = max(got)
        parts.append((n, mean, sd))
    return mixture(parts)


def ladder(mu, sigma, e):
    probs = compute_band_probabilities(mu, sigma, e["unit"], e["bands"])
    raw = [max(M.MODEL_FLOOR, p) for _b, p in probs]
    s = sum(raw)
    return [p / s for p in raw]


def build(events, ladders, ens):
    """{cutoff: [row]} and {cutoff: {reason: n}}."""
    first = min(k[1] for k in ens)
    rows, left = defaultdict(list), defaultdict(lambda: defaultdict(int))
    for eid, e in sorted(events.items(), key=lambda kv: (kv[1]["date"], kv[1]["city"])):
        if e["date"] < first:
            continue
        for k in M.CUTOFFS:
            if (eid, k) not in ladders:
                left[k]["no whole market ladder"] += 1
                continue
            got = at_cutoff(ens, e["city"], e["date"], e["cut"][k])
            if got is None:
                left[k]["no ensemble of both models by the cutoff"] += 1
                continue
            mkt, over = ladders[(eid, k)]
            rows[k].append({"eid": eid, "city": e["city"], "date": e["date"], "unit": e["unit"],
                            "month": e["date"][:7], "w": e["winner"], "mkt": mkt,
                            "mdl": ladder(got[0], got[1], e), "over": over, "mu": got[0], "sigma": got[1]})
    return rows, left


def by_day(rs):
    """(b): each date scored with fits on every date GAP_DAYS or more before it."""
    dates = sorted({r["date"] for r in rs})
    scored, fits = [], []
    for d in dates:
        edge = (dt.date.fromisoformat(d) - dt.timedelta(days=GAP_DAYS - 1)).isoformat()
        train = [(r["mkt"], r["mdl"], r["w"]) for r in rs if r["date"] < edge]
        if len(train) < M.MIN_TRAIN:
            continue
        a_r, _ = M.fit_pool(train, with_model=False)
        a_p, b_p = M.fit_pool(train, with_model=True)
        w_a = M.fit_anchor(train)
        fits.append({"date": d, "train": len(train), "a_recal": round(a_r, 4), "a_pooled": round(a_p, 4),
                     "b_pooled": round(b_p, 4), "w_anchor": w_a})
        for r in rs:
            if r["date"] != d:
                continue
            scored.append(dict(r, ll_m=M.logloss(r["mkt"], r["w"]),
                               ll_r=M.logloss(M.pool(r["mkt"], r["mdl"], a_r, 0.0), r["w"]),
                               ll_p=M.logloss(M.pool(r["mkt"], r["mdl"], a_p, b_p), r["w"]),
                               ll_a=M.logloss([m + w_a * (x - m) for m, x in zip(r["mkt"], r["mdl"])], r["w"])))
    return scored, fits


def iv(t):
    return {"mean": round(t[0], 4), "ci90": [round(t[1], 4), round(t[2], 4)]}


def summary(scored):
    if not scored:
        return {"rows": 0}
    g = lambda f: iv(M.boot([(r["date"], f(r)) for r in scored]))  # noqa: E731
    top = lambda key: sum(M.top(r[key]) == r["w"] for r in scored)  # noqa: E731
    return {
        "rows": len(scored), "dates": len({r["date"] for r in scored}),
        "first_date": min(r["date"] for r in scored), "last_date": max(r["date"] for r in scored),
        "cities": len({r["city"] for r in scored}),
        "log_loss": {k: round(sum(r[k] for r in scored) / len(scored), 4) for k in ("ll_m", "ll_r", "ll_p", "ll_a")},
        "gain_recal_over_market": g(lambda r: r["ll_m"] - r["ll_r"]),
        "gain_pooled_over_market": g(lambda r: r["ll_m"] - r["ll_p"]),
        "gain_anchor_over_market": g(lambda r: r["ll_m"] - r["ll_a"]),
        "gain_pooled_over_recal": g(lambda r: r["ll_r"] - r["ll_p"]),
        "top1": {"market": top("mkt"), "ensemble": top("mdl")},
    }


def verdict(per_cutoff):
    ok = {k: s for k, s in per_cutoff.items() if s.get("rows")}
    adds = any(s["gain_pooled_over_recal"]["ci90"][0] > 0 for s in ok.values())
    stage = any(s[g]["ci90"][0] > 0 for s in ok.values()
                for g in ("gain_recal_over_market", "gain_pooled_over_market"))
    return {"scored_cutoffs": sorted(ok), "ensemble_adds_to_the_price": adds,
            "a_second_stage_beats_the_market": stage,
            "verdict": "no fold scored" if not ok else
            "adds to the price (proposal to Hassan)" if adds else "does not add to the price (not shown)"}


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    tz = json.load(open(M.RI + "tz.json"))
    cities, cities_path = M.load_cities()
    events, left_events = M.load_events(tz, cities)
    ladders = M.market_ladders(events)
    ens = ensemble_rows()
    rows, left = build(events, ladders, ens)
    out = {"contract": "tools/p310_ensemble_test.py docstring (plan v2 P2.10 part 1, P3.10 Q3; R23)",
           "sources": {p: sha256(p) for p in (ENS, M.MH + "events.csv.gz", M.MH + "bands.csv.gz",
                                              M.MH + "prices.csv.gz", M.RI + "tz.json", cities_path)},
           "constants": {"MIN_TRAIN": M.MIN_TRAIN, "BOOT": M.BOOT, "SEED": M.SEED, "MAX_AGE_H": M.MAX_AGE_H,
                         "A_BOUNDS": M.A_BOUNDS, "B_BOUNDS": M.B_BOUNDS, "WINDOW": WINDOW, "GAP_DAYS": GAP_DAYS},
           "events_left_out": left_events, "monthly_as_written": {}, "by_day": {}}
    for k in M.CUTOFFS:
        rs = rows[k]
        out["monthly_as_written"][k] = {"rows": len(rs), "left_out": dict(left[k]),
                                        "months": {m: sum(r["month"] == m for r in rs)
                                                   for m in sorted({r["month"] for r in rs})}}
        scored, fits = M.walk_forward(rs)
        out["monthly_as_written"][k].update(scored=summary(scored),
                                            fits=[dict(zip(("month", "train", "a_recal", "a_pooled", "b_pooled",
                                                            "w_anchor"), f)) for f in fits])
        scored, fits = by_day(rs)
        out["by_day"][k] = {"scored": summary(scored), "fits": fits}
    out["rule_monthly_as_written"] = verdict({k: v["scored"] for k, v in out["monthly_as_written"].items()})
    out["rule_by_day"] = verdict({k: v["scored"] for k, v in out["by_day"].items()})
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "result.json"), "w") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("rule_monthly_as_written", "rule_by_day")}, indent=1))
    return out


if __name__ == "__main__":
    main()
