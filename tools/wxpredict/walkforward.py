"""WXPredict phases 3-4, first cut: does a model trained on the training table
price the venue's ladders better than the market, on days it never saw?

For each test month M (May-Oct 2026), walking forward:

  weather model  LightGBM on every row dated before M (station and venue
                 days): the day's maximum in C from what was known at the
                 decision (observations, forecasts, past error, the running
                 maximum), then a spread model fitted on the 60 days before M
                 from a model that had not seen them. Each bucket's
                 probability is the mass of that distribution over the
                 bucket, in the market's unit, above the maximum already
                 reported on the day.
  fusion         p_i proportional to q_i^a * w_i^b (q the market's normalised
                 prices, w the weather model's), a and b fitted per decision
                 hour group on the 60 days before M.

Scored on M's venue decisions with every bucket priced (the table's
mkt_complete), against the venue's winner: mean negative log-likelihood per
decision, and the difference to the market with a 90% interval from
resampling whole dates. Also the market's own prices tempered (q^a alone),
which separates "the market is miscalibrated" from "the weather adds
information". Nothing here prices or trades. Research only: it needs numpy,
pandas, scipy and lightgbm, which requirements.txt does not carry, and the
table built by build_table.py (data/training/wxpredict/table/, not committed).

    python tools/wxpredict/walkforward.py [--out data/eval/wxpredict/walkforward_<date>.json]
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import ndtr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remaining  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TABLE = os.path.join(ROOT, "data", "training", "wxpredict", "table", "wxpredict_table.csv.gz")
FLOOR = 0.0005                      # features.PRICE_FLOOR: the market's floor before normalising
TEST_MONTHS = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
CALIB_DAYS = 60
SEED = 20261011

ID = ["event_id", "source", "city_key", "station", "date", "unit", "n_bands", "bands_lo", "bands_hi",
      "decision_utc", "decision_local"]
LABELS = ["winner", "winner_lo", "winner_hi", "station_max_unit", "station_max_c", "station_in_winner",
          "station_reports_day", "label_unit"]
TEXT = ["fc_daily_source", "mkt_prices", "mkt_ages_min"]


def groups(day_offset, hour):
    """Decision hour groups the fusion weights are fitted in."""
    g = np.where(day_offset < 0, 0, 1 + np.searchsorted([9, 13, 17], hour, side="right"))
    return g.astype(int)


GROUP_NAMES = {0: "D-1 evening", 1: "D 00-08h", 2: "D 09-12h", 3: "D 13-16h", 4: "D 17-23h"}


def parse_edges(s):
    return [None if x == "" else float(x) for x in s.split(";")]


def ladders(df):
    """Padded (rows, K) arrays: bucket lower/upper edges in the market's unit
    (open ends -inf/+inf), the market's normalised prices, a validity mask and
    the winner's index."""
    k = int(df.n_bands.max())
    n = len(df)
    lo = np.full((n, k), np.nan)
    hi = np.full((n, k), np.nan)
    q = np.zeros((n, k))
    mask = np.zeros((n, k), bool)
    for r, (bl, bh, pr) in enumerate(zip(df.bands_lo.values, df.bands_hi.values, df.mkt_prices.values)):
        a, b = parse_edges(bl), parse_edges(bh)
        p = [max(FLOOR, float(x)) for x in pr.split(";")]
        m = len(a)
        lo[r, :m] = [-np.inf if x is None else x for x in a]
        hi[r, :m] = [np.inf if x is None else x for x in b]
        s = sum(p)
        q[r, :m] = [x / s for x in p]
        mask[r, :m] = True
    return lo, hi, q, mask, df.winner.values.astype(int)


def target_c(df):
    lab = df.label_unit.astype(float)
    return np.where(df.unit.values == "F", (lab - 32) * 5 / 9, lab)


def weather_probs(mu_c, sd_c, unit, lo, hi, mask, rmax_unit, day_offset):
    """Each bucket's mass of N(mu, sd) in the market's unit over [lo - 0.5, hi - 0.5),
    the integer outcome's continuity correction, above the maximum already
    reported on the day; floored and normalised like the market."""
    f = unit == "F"
    mu = np.where(f, mu_c * 9 / 5 + 32, mu_c)[:, None]
    sd = np.where(f, sd_c * 9 / 5, sd_c)[:, None]
    a = lo - 0.5
    b = hi - 0.5
    floor = np.where((day_offset == 0) & np.isfinite(rmax_unit), rmax_unit - 0.5, -np.inf)[:, None]
    a = np.maximum(a, floor)
    mass = np.clip(ndtr((b - mu) / sd) - ndtr((a - mu) / sd), 0, None)
    mass = np.where(mask & (b > a), mass, 0.0)
    w = np.where(mask, np.maximum(mass, FLOOR), 0.0)
    return w / w.sum(1, keepdims=True)


def pool(logs, weights, mask):
    """softmax over valid buckets of sum_j weights_j * log p_j."""
    z = sum(wt * lg for wt, lg in zip(weights, logs))
    z = np.where(mask, z, -np.inf)
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def nll(p, winner):
    return -np.log(np.clip(p[np.arange(len(p)), winner], 1e-12, None))


def fit_pool(logs, mask, winner, x0):
    def f(wts):
        return nll(pool(logs, wts, mask), winner).mean()
    res = minimize(f, x0, method="Nelder-Mead", options={"xatol": 1e-3, "fatol": 1e-6, "maxiter": 400})
    return [float(x) for x in res.x]


def features(df):
    drop = set(ID) | set(LABELS) | set(TEXT) | {"y_c", "d"} | {c for c in df.columns if c.startswith("mkt_")}
    cols = [c for c in df.columns if c not in drop]
    x = df[cols].copy()
    x["city"] = df.city_key.astype("category")
    x["is_f"] = (df.unit == "F").astype(int)
    return x


# deterministic and force_col_wise: the same table and seed give the same
# numbers on a re-run (LightGBM's multithreaded histograms otherwise may not).
LGB = dict(objective="regression", learning_rate=0.05, num_leaves=63, min_data_in_leaf=200,
           feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1,
           seed=SEED, num_threads=4, deterministic=True, force_col_wise=True)


def fit_mu(x, y, rounds=600):
    return lgb.train(LGB, lgb.Dataset(x, y), num_boost_round=rounds)


def fit_sd(x, abs_resid, rounds=300):
    params = dict(LGB, objective="regression")
    return lgb.train(params, lgb.Dataset(x, abs_resid), num_boost_round=rounds)


def boot_ci(diff, dates, n=2000, seed=SEED):
    """90% interval of the mean difference, resampling whole dates."""
    rng = np.random.default_rng(seed)
    d = pd.DataFrame({"d": diff, "date": dates})
    per = d.groupby("date")["d"].agg(["sum", "count"])
    s, c = per["sum"].values, per["count"].values
    idx = rng.integers(0, len(s), size=(n, len(s)))
    means = s[idx].sum(1) / c[idx].sum(1)
    return [float(np.quantile(means, 0.05)), float(np.quantile(means, 0.95))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--months", default=",".join(TEST_MONTHS))
    ap.add_argument("--remaining", action="store_true",
                    help="phase 3A: on the day from 09h, the remaining-day model's buckets (tools/wxpredict/remaining.py)")
    ap.add_argument("--dump", default=None,
                    help="also write each scored decision's ladders (csv.gz) for tools/wxpredict/ask_backtest.py")
    args = ap.parse_args(argv)
    t0 = time.time()
    sha = hashlib.sha256(open(TABLE, "rb").read()).hexdigest()
    df = pd.read_csv(TABLE, low_memory=False)
    df = df[df.label_unit.notna()].reset_index(drop=True).copy()
    df["y_c"] = target_c(df)
    df["d"] = pd.to_datetime(df.date)
    x_all = features(df)
    leaks = [c for c in x_all.columns if c in LABELS or c in ("y_c", "d") or c.startswith("mkt_")]
    assert not leaks, leaks
    print("features:", ",".join(x_all.columns), flush=True)
    print(f"loaded {len(df):,} rows, {x_all.shape[1]} features, {time.time() - t0:.0f} s", flush=True)
    if args.remaining:
        el = remaining.eligible(df)
        x_inc = remaining.features(df, x_all)
        j_inc = remaining.target(df)

    scored = []
    dumps = []
    fitted = {}
    calib = {}
    for month in args.months.split(","):
        m0 = pd.Timestamp(month + "-01")
        m1 = m0 + pd.offsets.MonthBegin(1)
        c0 = m0 - pd.Timedelta(days=CALIB_DAYS)
        # THE EVE OF EACH CUTOFF IS LEFT OUT (Codex on #372). A decision on
        # the evening before a day (D-1) is made while that evening's own day
        # is still running, so a model or weights fitted on that day's outcome
        # would know what the decision could not: the 1st's D-1 decisions are
        # made on the last day of the month before, the calibration window's
        # first on the day before it.
        eve = pd.Timedelta(days=1)
        tr_a = df.d < c0 - eve                # model A: before the calibration window
        cal = (df.d >= c0) & (df.d < m0 - eve)
        tr_b = df.d < m0 - eve                # model B: everything before the month
        test = (df.d >= m0) & (df.d < m1) & (df.source == "venue") & (df.mkt_complete == 1)
        if not test.any():
            continue
        mu_a = fit_mu(x_all[tr_a], df.y_c[tr_a])
        r_cal = df.y_c[cal].values - mu_a.predict(x_all[cal])
        # The spread on the calibration window comes from a spread model that
        # did not see those dates (two folds by date), so the fusion weights
        # are fitted on honest weather probabilities; the test month uses the
        # spread model fitted on the whole window.
        cal_idx = np.flatnonzero(cal.values)
        cal_dates = df.d.values[cal_idx]
        fold = (pd.Series(cal_dates).rank(method="dense").values.astype(int) % 2) == 0
        sd_cal = np.empty(len(cal_idx))
        for f in (True, False):
            m = fit_sd(x_all.iloc[cal_idx[fold != f]], np.abs(r_cal[fold != f]))
            sd_cal[fold == f] = m.predict(x_all.iloc[cal_idx[fold == f]])
        sd_by_row = pd.Series(sd_cal, index=cal_idx)
        sd_m = fit_sd(x_all[cal], np.abs(r_cal))
        mu_b = fit_mu(x_all[tr_b], df.y_c[tr_b])
        if args.remaining:
            ta, tb = tr_a.values & el, tr_b.values & el
            inc_a = remaining.fit(x_inc[ta], j_inc[ta], SEED)
            inc_b = remaining.fit(x_inc[tb], j_inc[tb], SEED)

        def with_remaining(w, rows, model, sub, lo, hi, mask):
            # on the day from 09h, the remaining-day model's buckets replace the forecast model's
            if not args.remaining:
                return w
            e = el[rows]
            if e.any():
                pr = model.predict(x_inc[rows][e])
                w = w.copy()
                w[e] = remaining.bucket_probs(pr, sub.unit.values[e], sub.rmax_unit.values[e], lo[e], hi[e], mask[e])
            return w

        # fusion weights on the calibration window's priced venue decisions,
        # from model A (which had not seen them)
        cv = cal & (df.source == "venue") & (df.mkt_complete == 1)
        sub = df[cv]
        lo, hi, q, mask, win = ladders(sub)
        mu = mu_a.predict(x_all[cv])
        sd = np.maximum(0.3, sd_by_row.loc[np.flatnonzero(cv.values)].values * 1.2533)
        w = weather_probs(mu, sd, sub.unit.values, lo, hi, mask, sub.rmax_unit.values, sub.day_offset.values)
        w = with_remaining(w, cv.values, inc_a if args.remaining else None, sub, lo, hi, mask)
        g = groups(sub.day_offset.values, sub.local_hour.values)
        lq, lw = np.log(np.where(mask, q, 1)), np.log(np.where(mask, w, 1))
        calib[month] = {"decisions": int(len(sub)), "nll_market": round(float(nll(q, win).mean()), 4),
                        "nll_weather": round(float(nll(w, win).mean()), 4),
                        "weather_mae_c": round(float(np.abs(mu - sub.y_c.values).mean()), 3)}
        wts = {}
        for gi in sorted(set(g)):
            s = g == gi
            wts[gi] = {"market_only": fit_pool([lq[s]], mask[s], win[s], [1.0]),
                       "fusion": fit_pool([lq[s], lw[s]], mask[s], win[s], [1.0, 0.3])}
        fitted[month] = {GROUP_NAMES[k]: v for k, v in wts.items()}

        sub = df[test]
        lo, hi, q, mask, win = ladders(sub)
        mu = mu_b.predict(x_all[test])
        sd = np.maximum(0.3, sd_m.predict(x_all[test]) * 1.2533)
        w = weather_probs(mu, sd, sub.unit.values, lo, hi, mask, sub.rmax_unit.values, sub.day_offset.values)
        w = with_remaining(w, test.values, inc_b if args.remaining else None, sub, lo, hi, mask)
        g = groups(sub.day_offset.values, sub.local_hour.values)
        lq, lw = np.log(np.where(mask, q, 1)), np.log(np.where(mask, w, 1))
        p_mt = np.zeros_like(q)
        p_fu = np.zeros_like(q)
        for gi in sorted(set(g)):
            s = g == gi
            gw = wts.get(gi) or {"market_only": [1.0], "fusion": [1.0, 0.0]}
            p_mt[s] = pool([lq[s]], gw["market_only"], mask[s])
            p_fu[s] = pool([lq[s], lw[s]], gw["fusion"], mask[s])
        if args.dump:
            def fmt(a, m):
                return [";".join(f"{v:.6f}" for v in row[mk]) for row, mk in zip(a, m)]
            dumps.append(pd.DataFrame({
                "event_id": sub.event_id.values, "city_key": sub.city_key.values, "date": sub.date.values,
                "unit": sub.unit.values, "decision_utc": sub.decision_utc.values,
                "day_offset": sub.day_offset.values, "local_hour": sub.local_hour.values,
                "bands_lo": sub.bands_lo.values, "bands_hi": sub.bands_hi.values, "winner": win,
                "q": fmt(q, mask), "p_tempered": fmt(p_mt, mask), "p_fusion": fmt(p_fu, mask),
                "p_weather": fmt(w, mask)}))
        scored.append(pd.DataFrame({
            "month": month, "date": sub.date.values, "city": sub.city_key.values, "group": g,
            "nll_market": nll(q, win), "nll_market_tempered": nll(p_mt, win),
            "nll_weather": nll(w, win), "nll_fusion": nll(p_fu, win),
            "top_market": q.argmax(1) == win, "top_fusion": p_fu.argmax(1) == win,
            "mae_c": np.abs(mu - sub.y_c.values)}))
        print(f"{month}: {test.sum():,} decisions, train {tr_b.sum():,} rows, {time.time() - t0:.0f} s", flush=True)

    s = pd.concat(scored, ignore_index=True)

    def summary(part):
        out = {"decisions": int(len(part)), "dates": int(part.date.nunique()),
               "events": None,
               "nll": {k: round(float(part[f"nll_{k}"].mean()), 4)
                       for k in ("market", "market_tempered", "weather", "fusion")},
               "top_bucket_hit": {"market": round(float(part.top_market.mean()), 4),
                                  "fusion": round(float(part.top_fusion.mean()), 4)},
               "weather_mae_c": round(float(part.mae_c.mean()), 3)}
        for k in ("fusion", "market_tempered", "weather"):
            diff = (part[f"nll_{k}"] - part.nll_market).values
            out[f"{k}_minus_market"] = {"mean": round(float(diff.mean()), 4),
                                        "ci90": [round(v, 4) for v in boot_ci(diff, part.date.values)]}
        return out

    result = {
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "table_gzip_sha256": sha, "rows": int(len(df)),
        "method": __doc__.strip().splitlines()[0],
        "test_months": args.months.split(","), "calib_days": CALIB_DAYS, "seed": SEED,
        "cutoff_eve_left_out": True,
        "remaining_day_model": bool(args.remaining),
        "all": summary(s),
        "by_group": {GROUP_NAMES[k]: summary(part) for k, part in s.groupby("group")},
        "by_month": {k: summary(part) for k, part in s.groupby("month")},
        "fitted_pool_weights": fitted,
        "calibration_window": calib,
        "seconds": round(time.time() - t0),
    }
    if args.dump:
        pd.concat(dumps, ignore_index=True).to_csv(args.dump, index=False, compression="gzip")
    text = json.dumps(result, indent=1)
    print(text)
    if args.out:
        if os.path.dirname(args.out):
            os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
    return result


if __name__ == "__main__":
    main()
