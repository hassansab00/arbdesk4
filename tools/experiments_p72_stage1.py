"""P7.2 stage 1 (first cut, 26 Sep): the remaining-day model as a DISTRIBUTION, scored on
the whole-degree bucket (log loss, hit) walk-forward, against a floor-atom
Gaussian around the floor+forecast centre (what the engine's same-day path
roughly prices from).

Same inputs and arguments as tools/experiments_p72_remaining_day.py:

    python tools/experiments_p72_stage1.py <the same five files>

RESULT (26 Sep; whole-degree bucket in the city's unit, walk-forward by month,
~15,700 city-days per hour; log loss of the winning bucket, 400 samples, floor 1e-3):
  hour  log loss model / base   gain [95% by date]      most-likely-bucket hit model / base
  09    1.999 / 2.016           +0.017 [-0.001, 0.032]  25.2% / 24.7%
  11    1.713 / 1.898           +0.184 [0.171, 0.197]   32.3% / 26.5%
  13    1.262 / 1.608           +0.346 [0.328, 0.364]   49.1% / 41.6%
  15    0.641 / 1.357           +0.716 [0.690, 0.746]   77.6% / 61.8%
At 09h this two-part form is no better than the base and its hit is below the
point ridge's 31.3%: the early-day rise needs a better distribution.

  model: P(set) logistic (rise < 0.25 C) + rise | not set ~ lognormal with
         mean from a ridge on log(rise) and a scale from a ridge on |resid|
         (heteroscedastic), all per decision hour, pooled + city intercept.
  base:  Y ~ N(max(R, fc_day), sigma_H) with the mass below R moved to R
         (the floor atom); sigma_H = the training MAE of that centre * 1.25.
"""
import sys, math, json, random
import numpy as np
sys.argv = [sys.argv[0]] + sys.argv[1:6]
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import experiments_p72_remaining_day as E
from collections import defaultdict
import datetime as dt

rng = np.random.default_rng(3)
SAMPLES = 400


def design(train):
    X = np.array([r["x"] for r in train]); mu, sd = X.mean(0), X.std(0) + 1e-9
    return lambda rows: np.hstack([np.ones((len(rows), 1)), (np.array([r["x"] for r in rows]) - mu) / sd])


def logistic(X, y, lam=1.0, iters=200):
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ b))
        g = X.T @ (p - y) + lam * np.r_[0, b[1:]]
        H = (X * (p * (1 - p))[:, None]).T @ X + lam * np.diag(np.r_[0, np.ones(len(b) - 1)])
        b -= np.linalg.solve(H, g)
    return b


def ridge(X, y, lam=10.0):
    P = lam * np.eye(X.shape[1]); P[0, 0] = 0
    return np.linalg.solve(X.T @ X + P, X.T @ y)


def whole(v, unit): return np.round(v * 9 / 5 + 32) if unit == "F" else np.round(v)


def bucket_probs_from_samples(samples, unit):
    k = whole(samples, unit)
    vals, counts = np.unique(k, return_counts=True)
    return dict(zip(vals.tolist(), (counts / len(samples)).tolist()))


def run(H):
    data = E.rows_for(H)
    months = sorted({(r["date"].year, r["date"].month) for r in data})
    res = []
    for yy, mm in months:
        start = dt.date(yy, mm, 1)
        if start < E.FIRST_TEST:
            continue
        train = [r for r in data if r["date"] < start]
        test = [r for r in data if (r["date"].year, r["date"].month) == (yy, mm)]
        if len(train) < 2000 or not test:
            continue
        D = design(train)
        Xt = D(train)
        rise = np.array([r["y"] - r["R"] for r in train])
        is_set = (rise < 0.25).astype(float)
        bset = logistic(Xt, is_set)
        pos = rise >= 0.25
        blog = ridge(Xt[pos], np.log(rise[pos]))
        resid = np.abs(np.log(rise[pos]) - Xt[pos] @ blog)
        bsc = ridge(Xt[pos], resid)
        base_c = np.array([max(r["R"], r["fc_day"]) for r in train])
        sigma = max(0.5, float(np.mean(np.abs(base_c - np.array([r["y"] for r in train])))) * 1.25)
        Xs = D(test)
        p_set = 1 / (1 + np.exp(-Xs @ bset))
        m = Xs @ blog
        s = np.clip(Xs @ bsc * math.sqrt(math.pi / 2), 0.15, 2.0)
        for i, r in enumerate(test):
            unit = E.UNIT.get(r["city"], "C")
            n_set = rng.binomial(SAMPLES, p_set[i])
            draws = np.r_[r["R"] + rng.uniform(0, 0.25, n_set),
                          r["R"] + np.exp(m[i] + s[i] * rng.standard_normal(SAMPLES - n_set))]
            c = max(r["R"], r["fc_day"])
            g = c + sigma * rng.standard_normal(SAMPLES)
            g = np.maximum(g, r["R"])
            truth = float(whole(r["y"], unit))
            pm = bucket_probs_from_samples(draws, unit)
            pb = bucket_probs_from_samples(g, unit)
            res.append((r["date"], math.log(max(pm.get(truth, 0), 1e-3)), math.log(max(pb.get(truth, 0), 1e-3)),
                        max(pm, key=pm.get) == truth, max(pb, key=pb.get) == truth))
    ll_m = -np.mean([x[1] for x in res]); ll_b = -np.mean([x[2] for x in res])
    by = defaultdict(list)
    for x in res: by[x[0]].append(x)
    days = list(by); r2 = random.Random(1)
    gains = []
    for _ in range(300):
        smp = [r2.choice(days) for _ in days]
        gains.append(np.mean([x[1] - x[2] for d in smp for x in by[d]]))
    print(json.dumps({"hour": H, "n": len(res), "logloss_model": round(ll_m, 3), "logloss_base": round(ll_b, 3),
                      "logloss_gain": round(ll_b - ll_m, 3),
                      "gain_ci95": [round(float(np.percentile(gains, 2.5)), 3), round(float(np.percentile(gains, 97.5)), 3)],
                      "hit_model": round(float(np.mean([x[3] for x in res])), 4),
                      "hit_base": round(float(np.mean([x[4] for x in res])), 4)}), flush=True)


for H in E.HOURS:
    run(H)
