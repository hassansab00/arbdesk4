#!/usr/bin/env python3
"""YES on the model's pick in its best cities, NO on its pick in its worst
(15 Nov 2026). Research: nothing here prices or trades.

Hassan, 15 Nov: "bet YES on the top performing 3 cities, and NO on the least
performing. Analyze this strategy, and recommend to me the 6 cities to trade
on with potential pnl calc."

READ AS: at one checkpoint a day, buy YES on the served top bucket in the three
cities where that pick has been right most often, and buy NO on the served top
bucket in the three cities where it has been right least often. Trades, prices,
the fee and the return are tools/strategy_study.py's, unchanged: the touch
recorded with the call, the taker fee, one dollar a trade, held to settlement.
No price filter: every top pick with an ask (YES) or a bid (NO).

  all        both sides on every city: what choosing cities has to beat
  walk       the strategy as it could have been traded: on each date the
             cities are ranked on their picks that had SETTLED before that
             date's first call (MIN_TRADES or more), by hit rate, and beside
             it by the YES return
  persist    does a city's record carry forward? The rank correlation of each
             city's hit rate, and of its YES return, between the earlier and
             the later half of the dates
  hindsight  the three best and worst chosen on the whole record and scored
             on that same record: not a rule, shown for what it flatters
  now        the six cities the rule names from the whole record, with each
             city's own numbers

Intervals are 90%, resampling dates (strategy_study.result).

    python tools/city_sides_study.py --out data/eval/city_sides_2026-11-15
"""
import argparse
import collections
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategy_study as S  # noqa: E402

K = 3
MIN_TRADES = 8
METRICS = ("hit", "yes_return")


def ret(t):
    return t["win"] / t["cost"] - 1.0


def picks(trades, cp):
    """One record per served top pick at this checkpoint, in call order: whether
    the bucket won, when the call was made and when it settled, and the YES and
    the NO the book offered on it (either may be missing)."""
    out = {}
    for t in trades:
        if t["top"] and t["cp"] == cp:
            p = out.setdefault((t["city"], t["date"]), {
                "city": t["city"], "date": t["date"], "decided": S.when(t["decided_at"]),
                "settled": S.when(t["settled_at"]), "hit": t["win"] if t["side"] == "YES" else 1 - t["win"]})
            p[t["side"].lower()] = t
    return sorted(out.values(), key=lambda p: (p["date"], p["decided"], p["city"]))


def rank(hist, metric):
    """Cities with MIN_TRADES known picks, best first: by hit rate, or by the
    return of the YES on the pick (cities with that many YES trades)."""
    i = METRICS.index(metric)
    score = {c: [h[i] for h in v if h[i] is not None] for c, v in hist.items()}
    seen = [c for c, v in score.items() if len(v) >= MIN_TRADES]
    return sorted(seen, key=lambda c: (-float(np.mean(score[c])), c))


def record(p):
    return (p["hit"], ret(p["yes"]) if "yes" in p else None)


def walk(ps, metric):
    """The strategy on each date with the cities its earlier record names.

    The record on a date is every earlier pick that had SETTLED before the
    date's first call (Codex on #512: the evening-before call for a day is
    made before the day before it has settled). The NO is bought wherever the
    book had a bid on the pick, whether or not it also had an ask."""
    past, legs = [], {"yes": [], "no": []}
    for d in sorted({p["date"] for p in ps}):
        today = [p for p in ps if p["date"] == d]
        first_call = min(p["decided"] for p in today)
        hist = collections.defaultdict(list)
        for p in past:
            if p["settled"] <= first_call:
                hist[p["city"]].append(record(p))
        order = rank(hist, metric)
        if len(order) >= 2 * K:
            best, worst = set(order[:K]), set(order[-K:])
            legs["yes"] += [p["yes"] for p in today if p["city"] in best and "yes" in p]
            legs["no"] += [p["no"] for p in today if p["city"] in worst and "no" in p]
        past += today
    return {"yes_on_the_3_best": S.result(legs["yes"]), "no_on_the_3_worst": S.result(legs["no"]),
            "both_legs": S.result(legs["yes"] + legs["no"])}


def spearman(a, b):
    def ranks(v):
        v = np.asarray(v, dtype=float)
        order = v.argsort()
        r = np.empty(len(v))
        r[order] = np.arange(len(v), dtype=float)
        for x in np.unique(v):                     # ties share their mean rank
            r[v == x] = r[v == x].mean()
        return r
    return float(np.corrcoef(ranks(a), ranks(b))[0, 1])


def persist(ps):
    ds = sorted({p["date"] for p in ps})
    mid = ds[len(ds) // 2]
    a, b = collections.defaultdict(list), collections.defaultdict(list)
    for p in ps:
        (a if p["date"] < mid else b)[p["city"]].append(record(p))

    def corr(i):
        x = {c: [h[i] for h in v if h[i] is not None] for c, v in a.items()}
        y = {c: [h[i] for h in v if h[i] is not None] for c, v in b.items()}
        cs = sorted(c for c in x if len(x[c]) >= MIN_TRADES and len(y.get(c, [])) >= MIN_TRADES)
        return len(cs), round(spearman([np.mean(x[c]) for c in cs], [np.mean(y[c]) for c in cs]), 3)
    (n, hit), (_n, yes) = corr(0), corr(1)
    return {"cities": n, "later_half_from": mid, "hit_rate_rank_correlation": hit, "yes_return_rank_correlation": yes}


def now(ps):
    """The six cities the hit-rate rule names from the whole record."""
    hist = collections.defaultdict(list)
    for p in ps:
        hist[p["city"]].append(record(p))
    order = rank(hist, "hit")

    def city(c):
        mine = [p for p in ps if p["city"] == c]
        ys = [p["yes"] for p in mine if "yes" in p]
        return {"city": c, "picks": len(mine), "hit_rate": round(float(np.mean([p["hit"] for p in mine])), 4),
                "mean_yes_ask": round(float(np.mean([t["price"] for t in ys])), 4) if ys else None,
                "yes": S.result(ys), "no": S.result([p["no"] for p in mine if "no" in p])}
    best, worst = order[:K], order[-K:]
    hind_yes = [p["yes"] for p in ps if p["city"] in set(best) and "yes" in p]
    hind_no = [p["no"] for p in ps if p["city"] in set(worst) and "no" in p]
    return {"cities_ranked": len(order), "yes_on": [city(c) for c in best], "no_on": [city(c) for c in worst],
            "hindsight_not_a_rule": {"yes_on_the_3_best": S.result(hind_yes), "no_on_the_3_worst": S.result(hind_no),
                                     "both_legs": S.result(hind_yes + hind_no)}}


def study():
    trades, _calls = S.candidates()
    out = {"k": K, "min_trades": MIN_TRADES, "first_date": min(t["date"] for t in trades),
           "last_date": max(t["date"] for t in trades), "checkpoints": {}}
    for cp in S.CHECKPOINTS:
        ps = picks(trades, cp)
        out["checkpoints"][cp] = {
            "picks": len(ps), "with_a_yes": sum("yes" in p for p in ps), "with_a_no": sum("no" in p for p in ps),
            "all": {"yes_on_every_city": S.result([p["yes"] for p in ps if "yes" in p]),
                    "no_on_every_city": S.result([p["no"] for p in ps if "no" in p])},
            "walk": {m: walk(ps, m) for m in METRICS}, "persist": persist(ps), "now": now(ps)}
    cells = [(cp, m, leg, v) for cp, c in out["checkpoints"].items() for m, w in c["walk"].items()
             for leg, v in w.items() if leg != "both_legs"]
    out["walk_cells"] = {"cells": len(cells), "interval_above_0": [f"{cp}:{m}:{leg}" for cp, m, leg, v in cells if v["ci90"][0] > 0],
                         "interval_below_0": sum(v["ci90"][1] < 0 for *_x, v in cells),
                         "no_leg_cells_with_a_positive_return": sum(v["return_per_dollar"] > 0 for *_x, leg, v in cells
                                                                    if leg == "no_on_the_3_worst")}
    return out


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
