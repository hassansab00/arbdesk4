#!/usr/bin/env python3
"""Is there a rule that turns the served predictions into profit at the prices
that were on the book? (15 Nov 2026.) Research: nothing here prices or trades.

Hassan, 15 Nov: "devise the best most profitable strategy based on the
predictions. The buy ins can be YES or NO and can be multiple cities based on
accuracy and performance."

WHAT IS REPLAYED. Every first call (city, date, checkpoint) the mirror holds
with a graded outcome and the book recorded on it (prediction_checkpoints.
market: each bucket's best ask and best bid at the call). A candidate trade is
one bucket and one side, bought at the touch and held to settlement:

  YES  pays 1 if the bucket wins; price = the best ask
  NO   pays 1 if it loses;        price = 1 - the best bid (the venue's NO book
       mirrors the YES book; a bucket with no bid has no NO to buy)
  cost = price + the taker fee 0.05 x price x (1 - price) (scripts/cost_model.py)

One dollar is staked on each trade, so the return is win / cost - 1. No size,
no depth and no slippage beyond the touch are modelled: a real order of any
size does no better than this. The model's probability is the served ladder's;
at the morning call it is the corrected ladder (sd_corr:v1's row), which is
what that call prices from once settings.station_same_day_pricing is on. Noon
and later are scored from 17 Oct, when wxrd3 began serving them.

THE RULES, fixed before any holdout date was read. Development is every date
to 31 Oct; the holdout is 1-14 Nov and is read once, for the one rule that
development leaves standing.

  edge      buy any side whose model probability beats its cost by a
            threshold (0.03, 0.06, 0.10, 0.15), by checkpoint, side and price
  market    buy every bucket in a price band, whatever the model says
  pick      buy YES on the model's top pick
  cities    the pick rule on the cities that have paid so far (walk-forward:
            a city trades on a date only on its outcomes settled before that
            date's first call), beside the ten best cities in hindsight
  postpeak  one hour after the peak, YES on the top pick when the model gives
            it 0.90 or more and it is still offered below 0.97
  ladder    every bucket's YES, or every bucket's NO, for less than the payout

Intervals are 90%, resampling dates (seed 11, 1,000).

    python tools/strategy_study.py --out data/eval/strategy_study_2026-11-15
"""
import argparse
import collections
import csv
import datetime as dt
import glob
import gzip
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
csv.field_size_limit(sys.maxsize)
CHECKPOINTS = ("d1_eve", "morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
SAME_DAY_MODEL = ("noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
WXRD3_FROM = "2026-10-17"
DEV_LAST, HOLD_FIRST = "2026-10-31", "2026-11-01"
FEE = 0.05
TRUE = ("t", "true", "True", "1")
SEED, BOOT = 11, 1000
PRICE_BANDS = ((0.03, 0.10), (0.10, 0.25), (0.25, 0.45), (0.45, 0.65), (0.65, 0.85), (0.85, 0.93),
               (0.93, 0.97), (0.97, 0.995))


def read(table):
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "mirror", table, "*.csv.gz"))):
        with gzip.open(path, "rt", newline="") as fh:
            yield from csv.DictReader(fh)


def cost(price):
    return price + FEE * price * (1 - price)


def candidates():
    """Every (call, bucket, side) the book offered, with the model's probability
    of that side, and the calls themselves (for the ladder check)."""
    first = {}
    for r in read("prediction_checkpoints"):
        k = (r["city_key"], r["target_date"], r["checkpoint"])
        if r["checkpoint"] in CHECKPOINTS and (k not in first or (r["decided_at"], r["checkpoint_id"])
                                               < (first[k]["decided_at"], first[k]["checkpoint_id"])):
            first[k] = {c: r.get(c, "") for c in ("checkpoint_id", "decided_at", "probs", "market")}
    outcome = {r["checkpoint_id"]: r for r in read("fact_checkpoint_outcome")}
    corrected = {(r["city_key"], r["target_date"]): json.loads(r["probs"])
                 for r in read("variant_shadow_checkpoints")
                 if r["variant_version"] == "sd_corr:v1" and r["checkpoint"] == "morning" and r["probs"]}
    trades, calls = [], []
    for (city, date, cp), r in first.items():
        f = outcome.get(r["checkpoint_id"])
        if (not f or not f["settled_at"] or not f["winner_band_id"] or f["ladder_has_winner"] not in TRUE
                or not r["market"] or (cp in SAME_DAY_MODEL and date < WXRD3_FROM)):
            continue
        probs, book = json.loads(r["probs"]), json.loads(r["market"])
        if cp == "morning":
            probs = corrected.get((city, date))
            if probs is None or set(probs) != set(json.loads(r["probs"])):
                continue
        top = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        quotes = []
        for band, p in probs.items():
            q = book.get(band) or {}
            ask, bid, won = q.get("ask"), q.get("bid"), int(band == f["winner_band_id"])
            quotes.append((ask, bid))
            base = {"city": city, "date": date, "cp": cp, "top": int(band == top),
                    "decided_at": r["decided_at"], "settled_at": f["settled_at"]}
            if ask is not None and 0 < ask < 1:
                trades.append(dict(base, side="YES", price=ask, cost=cost(ask), p=p, win=won))
            if bid is not None and 0 < bid < 1:
                trades.append(dict(base, side="NO", price=1 - bid, cost=cost(1 - bid), p=1 - p, win=1 - won))
        calls.append(quotes)
    return trades, calls


def result(ts):
    """Return per dollar staked, one dollar a trade, with a 90% interval by date."""
    if not ts:
        return {"n": 0}
    by = collections.defaultdict(list)
    for t in ts:
        by[t["date"]].append(t["win"] / t["cost"] - 1.0)
    ds = sorted(by)
    rng = np.random.default_rng(SEED)
    st = [np.mean([z for i in rng.integers(0, len(ds), len(ds)) for z in by[ds[i]]]) for _ in range(BOOT)]
    return {"n": len(ts), "dates": len(ds), "return_per_dollar": round(float(np.mean([z for d in ds for z in by[d]])), 4),
            "ci90": [round(float(np.percentile(st, 5)), 4), round(float(np.percentile(st, 95)), 4)],
            "win_rate": round(float(np.mean([t["win"] for t in ts])), 4),
            "mean_cost": round(float(np.mean([t["cost"] for t in ts])), 4)}


def edge_grid(dev):
    cells = []
    for cp in CHECKPOINTS:
        for side in ("YES", "NO"):
            for th in (0.03, 0.06, 0.10, 0.15):
                for name, lo, hi in (("all", 0.03, 0.97), ("under 0.2", 0.03, 0.2), ("0.2-0.6", 0.2, 0.6), ("over 0.6", 0.6, 0.97)):
                    r = result([t for t in dev if t["cp"] == cp and t["side"] == side
                                and t["p"] - t["cost"] >= th and lo <= t["price"] < hi])
                    if r["n"] >= 80:
                        cells.append(dict(r, checkpoint=cp, side=side, edge=th, price=name))
    cells.sort(key=lambda c: -c["ci90"][0])
    return {"cells_with_80_trades": len(cells), "cells_with_interval_above_0": sum(c["ci90"][0] > 0 for c in cells),
            "cells_with_interval_below_0": sum(c["ci90"][1] < 0 for c in cells),
            "best_cell": cells[0], "worst_cell": cells[-1],
            "edge_0.06_all_prices": {f"{cp}:{side}": result([t for t in dev if t["cp"] == cp and t["side"] == side
                                                             and t["p"] - t["cost"] >= 0.06 and 0.03 <= t["price"] < 0.97])
                                     for cp in CHECKPOINTS for side in ("YES", "NO")}}


def when(stamp):
    return dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


def by_city(ts):
    """The rule on every city; on the cities that paid so far (a city trades
    on a date only with 8 earlier trades whose mean return is above 0); on the
    ten best so far; and on the ten best in hindsight, which is not a rule.

    "So far" is what had SETTLED before the date's first call (Codex on #512):
    the evening-before call for a day is made before the day before it has
    settled, so an earlier target date alone is not an earlier outcome."""
    ts = sorted(ts, key=lambda t: t["date"])
    hist, paid, ten = collections.defaultdict(list), [], []
    for d in sorted({t["date"] for t in ts}):
        today = [t for t in ts if t["date"] == d]
        first_call = min(when(t["decided_at"]) for t in today)
        known = {c: [z for at, z in v if at <= first_call] for c, v in hist.items()}
        seen = [c for c, v in known.items() if len(v) >= 8]
        good = {c for c in seen if np.mean(known[c]) > 0}
        best = set(sorted(seen, key=lambda c: -np.mean(known[c]))[:10])
        paid += [t for t in today if t["city"] in good]
        ten += [t for t in today if t["city"] in best]
        for t in today:
            hist[t["city"]].append((when(t["settled_at"]), t["win"] / t["cost"] - 1))
    every = {c: [z for _at, z in v] for c, v in hist.items()}
    hindsight = set(sorted(every, key=lambda c: -np.mean(every[c]))[:10])
    return {"all_cities": result(ts), "cities_that_paid_so_far": result(paid), "ten_best_so_far": result(ten),
            "ten_best_in_hindsight_not_a_rule": result([t for t in ts if t["city"] in hindsight])}


def study():
    trades, calls = candidates()
    dev = [t for t in trades if t["date"] <= DEV_LAST]
    pre_peak = ("noon", "prepeak_2h", "prepeak_1h")

    def pick(t, cps):
        return t["cp"] in cps and t["side"] == "YES" and t["top"] and 0.2 <= t["price"] < 0.9

    def postpeak(t):
        return t["cp"] == "postpeak_1h" and t["side"] == "YES" and t["top"] and t["p"] >= 0.9 and t["price"] < 0.97
    yes = [sum(cost(a) for a, _b in q) for q in calls if all(a is not None for a, _b in q)]
    no = [sum(cost(1 - b) for _a, b in q) - (len(q) - 1) for q in calls if all(b is not None for _a, b in q)]
    return {
        "candidates": len(trades), "calls": len(calls),
        "first_date": min(t["date"] for t in trades), "last_date": max(t["date"] for t in trades),
        "development_to": DEV_LAST, "holdout_from": HOLD_FIRST,
        "edge_development": edge_grid(dev),
        "market_alone_all_dates": {side: {f"{lo:.2f}-{hi:.3f}": result([t for t in trades if t["side"] == side and lo <= t["price"] < hi])
                                          for lo, hi in PRICE_BANDS} for side in ("YES", "NO")},
        "pick_all_dates": {cp: result([t for t in trades if pick(t, (cp,))]) for cp in CHECKPOINTS},
        "cities_all_dates": {"pick_noon_to_1h_before_peak": by_city([t for t in trades if pick(t, pre_peak)]),
                             "pick_evening_before": by_city([t for t in trades if pick(t, ("d1_eve",))]),
                             "pick_morning": by_city([t for t in trades if pick(t, ("morning",))])},
        "postpeak": {"development": result([t for t in dev if postpeak(t)]),
                     "holdout": result([t for t in trades if postpeak(t) and t["date"] >= HOLD_FIRST]),
                     "both": result([t for t in trades if postpeak(t)])},
        "ladder": {"calls_with_every_ask": len(yes), "lowest_cost_of_every_yes": round(min(yes), 4),
                   "calls_where_every_yes_costs_under_1": sum(v < 1 for v in yes),
                   "calls_with_every_bid": len(no), "lowest_cost_of_every_no_over_its_payout": round(min(no), 4),
                   "calls_where_every_no_costs_under_its_payout": sum(v < 0 for v in no)},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    out = study()
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "result.json"), "w") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
