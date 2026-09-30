"""The scope's market-weight gate, before and after audit repair 4 (30 Sep),
on simulated ladders where it is known whether the model knows anything.

Before, market_anchor.fit took the w with the lowest log loss over every
settled day and bootstrapped its gain over those same days (in-sample; rebuilt
here as in_sample_passed). After, the w each day's earlier days chose is scored
on that day (market_anchor._forward). Every world, seed and size is fixed here,
before any result was read.

The world, 40 rows a day, 11 buckets:
  truth   a random bell-shaped ladder; the winner is drawn from it.
  'edge'  the model is the truth, the market the truth with noise;
  'null'  the market is the truth, the model the market with noise.

    python tools/market_anchor_gate_sim.py
"""
import datetime as dt
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import market_anchor as ma          # noqa: E402

SCOPE = "engine:morning"
PER_DAY = 40
SCENARIOS = [                        # (name, phases, trials, latest days scored for the chosen w)
    ("no information, 60 days", [(60, "null")], 100, None),
    ("edge 20 days, then none for 10 (the audit's case)", [(20, "edge"), (10, "null")], 60, 10),
    ("edge 40 days, then none for 12", [(40, "edge"), (12, "null")], 60, 12),
    ("stable edge, 45 days", [(45, "edge")], 30, None),
    ("stable edge, 39 days (one short of 2 x MIN_DAYS)", [(39, "edge")], 10, None),
]


def ladder(rng, k=11):
    c, s = rng.uniform(2, 8), rng.uniform(0.8, 2.0)
    p = [math.exp(-((i - c) / s) ** 2 / 2) + 0.005 for i in range(k)]
    t = sum(p)
    return [x / t for x in p]


def noisy(p, rng, s):
    m = [x * math.exp(rng.gauss(0, s)) for x in p]
    t = sum(m)
    return [x / t for x in m]


def world(seed, phases):
    rng, rows, days = random.Random(seed), [], []
    d0, d = dt.date(2026, 8, 1), 0
    for n, kind in phases:
        for _ in range(n):
            day = (d0 + dt.timedelta(days=d)).isoformat()
            for _ in range(PER_DAY):
                p = ladder(rng)
                b = [f"b{i}" for i in range(len(p))]
                market, model = (noisy(p, rng, 0.4), p) if kind == "edge" else (p, noisy(p, rng, 0.3))
                u, acc, win = rng.random(), 0.0, b[-1]
                for bi, x in zip(b, p):
                    acc += x
                    if u < acc:
                        win = bi
                        break
                rows.append((day, SCOPE, dict(zip(b, model)), dict(zip(b, market)), win))
            days.append(day)
            d += 1
    return rows, (d0 + dt.timedelta(days=d)).isoformat(), days


def in_sample_passed(rows, best_w):
    """The gate before 30 Sep: best_w's gain bootstrapped over the days that chose it."""
    if best_w <= 0:
        return False
    i, per = ma._ix(best_w), {}
    for r in rows:
        g = ma._ll_grid(*r[2:5])
        s, n = per.get(r[0], (0.0, 0))
        per[r[0]] = (s + g[0] - g[i], n + 1)
    _mean, lower = ma._interval(per)
    return lower > 0


def gain_on(rows, days, w):
    rs = [r for r in rows if r[0] in days]
    return sum(ma._log_loss(r[2], r[3], r[4], 0) - ma._log_loss(r[2], r[3], r[4], w) for r in rs) / len(rs)


def main():
    print("| world | trials | old gate passed | new gate passed | chosen w lost on the latest days (old / new) |")
    print("|---|---|---|---|---|")
    for name, phases, trials, last_k in SCENARIOS:
        old = new = lost_old = lost_new = 0
        for seed in range(trials):
            rows, as_of, days = world(seed, phases)
            ev = ma.fit(rows, as_of)["evidence"][SCOPE]
            po, pn = in_sample_passed(rows, ev["best_w"]), ev["target"] > 0
            old, new = old + po, new + pn
            if last_k:
                lost = gain_on(rows, set(days[-last_k:]), ev["best_w"]) < 0
                lost_old, lost_new = lost_old + (po and lost), lost_new + (pn and lost)
        print(f"| {name} | {trials} | {old} | {new} | "
              + (f"{lost_old} / {lost_new} (last {last_k})" if last_k else "-") + " |", flush=True)


if __name__ == "__main__":
    main()
