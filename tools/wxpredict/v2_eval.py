"""WXPredict v2 (12 Oct): what closes the gap to the market before the peak?

Hassan, 12 Oct: the prediction must be right BEFORE the peak, and learn from
the days before. On the same venue decisions the market hit the winning
bucket 51-89% of the time from 09 to 16h local, the remaining-day model 41-81%.
Two causes were tested, each on the same decisions, each fold fitted only on
the days before it (walk-forward, weekly folds of FOLDS):

  report_lag  v1 rows count a station report 20 min after its reading; the
              v2 table 8 min (Aviation Weather's p90 receipt latency, 12 Oct).
              The remaining-day model fitted on each; scored against the
              market on the same decisions.
  same_day    the newest forecast the platform had fetched by the decision
              (open_meteo_forecast, archived with its fetch time, Sep 2026 on:
              data/archive/forecasts and the mirror's weather_forecasts):
              (a) as features beside the others, the model refitted; (b) the
              model's forecast inputs moved by the revision (newest daily
              maximum minus the lead-1 best_match one), not refitted.

Scored on the venue decisions from 09h with every bucket priced (the table's
mkt_complete): hit (the model's most likely bucket won), log loss, the
market's; differences with 90% intervals resampling whole dates. Research
only; nothing here prices or trades.

    python tools/wxpredict/v2_eval.py --out-dir data/eval/wxpredict --stamp 2026-10-12
"""
import argparse
import csv
import datetime as dt
import glob
import gzip
import hashlib
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remaining  # noqa: E402
import walkforward as wf  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TABLE_V1 = wf.TABLE
TABLE_V2 = os.path.join(ROOT, "data", "training", "wxpredict", "table_v2", "wxpredict_table.csv.gz")
FOLDS = [("2026-09-15", "2026-09-21"), ("2026-09-22", "2026-09-28"), ("2026-09-29", "2026-10-05"),
         ("2026-10-06", "2026-10-11")]
HOURS = [(9, 10), (11, 12), (13, 14), (15, 16), (17, 23), (9, 16)]
FORECAST_GLOBS = ("data/archive/forecasts/*.csv.gz", "data/mirror/weather_forecasts/*.csv.gz")
REVISED = ("fc_bm_tmax_c", "fch_rest_max_c", "fch_day_max_c", "fc_models_mean_c")


def unix(s):
    return dt.datetime.fromisoformat(str(s).replace(" ", "T").replace("Z", "+00:00")).timestamp()


def load_latest(root=ROOT):
    """{(city, for_date): (fetch times, daily maxima)} of open_meteo_forecast at
    leads 0 and 1, each fetch once, oldest first."""
    by, seen = {}, set()
    for pattern in FORECAST_GLOBS:
        for path in sorted(glob.glob(os.path.join(root, pattern))):
            with gzip.open(path, "rt", newline="") as fh:
                for r in csv.DictReader(fh):
                    if r.get("model") != "open_meteo_forecast" or str(r.get("lead_days")) not in ("0", "1") \
                            or not r.get("forecast_max_c"):
                        continue
                    t = unix(r["observed_at"])
                    k = (r["city_key"], r["for_date"], t)
                    if k in seen:
                        continue
                    seen.add(k)
                    by.setdefault((r["city_key"], r["for_date"]), []).append((t, float(r["forecast_max_c"])))
    out = {}
    for k, v in by.items():
        v.sort()
        out[k] = (np.array([a for a, _ in v]), np.array([b for _, b in v]))
    return out


def asof(df, latest):
    """(the newest fetched daily maximum at or before each row's decision, its age in hours)."""
    val = np.full(len(df), np.nan)
    age = np.full(len(df), np.nan)
    for i, (c, d, t) in enumerate(zip(df.city_key.values, df.date.values, df.decision_utc.values)):
        e = latest.get((c, d))
        if e is None:
            continue
        j = np.searchsorted(e[0], t, side="right") - 1
        if j >= 0:
            val[i], age[i] = e[1][j], (t - e[0][j]) / 3600
    return val, age


def load(path):
    with open(path, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    df = pd.read_csv(path, low_memory=False)
    df = df[df.label_unit.notna()].reset_index(drop=True)
    return df[remaining.eligible(df)].reset_index(drop=True), sha


def score(df, x, fits=None, variants=None):
    """Per fold: fit on the days before it (or reuse `fits`), score its venue
    decisions; variants {name: X} are scored with the same fitted model.
    Returns (rows, fits)."""
    j = remaining.target(df)
    variants = variants or {"": x}
    fits = dict(fits or {})
    out = []
    for a, b in FOLDS:
        tr = (df.date < a).values
        te = ((df.date >= a) & (df.date <= b) & (df.source == "venue") & (df.mkt_complete == 1)).values
        sub = df[te]
        lo, hi, q, mask, win = wf.ladders(sub)
        if a not in fits:
            fits[a] = remaining.fit(x[tr], j[tr], wf.SEED)
        for name, X in variants.items():
            w = remaining.bucket_probs(fits[a].predict(X[te]), sub.unit.values, sub.rmax_unit.values, lo, hi, mask)
            out.append(pd.DataFrame({"variant": name, "event_id": sub.event_id.values, "t": sub.decision_utc.values,
                                     "date": sub.date.values, "hour": sub.local_hour.values, "unit": sub.unit.values,
                                     "obs_age_min": sub.obs_age_min.values, "hit": w.argmax(1) == win,
                                     "nll": wf.nll(w, win), "mkt_hit": q.argmax(1) == win, "mkt_nll": wf.nll(q, win)}))
    return pd.concat(out, ignore_index=True), fits


def compare(a, b, la, lb):
    """Per hour group: a's and b's hit and log loss on the same decisions, the market's, b - a with 90% intervals."""
    m = a.merge(b, on=["event_id", "t"], suffixes=("_a", "_b"))
    res = {"matched_decisions": int(len(m))}
    for h0, h1 in HOURS:
        s = ((m.hour_a >= h0) & (m.hour_a <= h1)).values
        g = {"n": int(s.sum()),
             f"hit_{la}": round(float(m.hit_a[s].mean()), 4), f"hit_{lb}": round(float(m.hit_b[s].mean()), 4),
             "hit_market": round(float(m.mkt_hit_a[s].mean()), 4),
             f"nll_{la}": round(float(m.nll_a[s].mean()), 4), f"nll_{lb}": round(float(m.nll_b[s].mean()), 4),
             "nll_market": round(float(m.mkt_nll_a[s].mean()), 4),
             "d_hit_ci90": [round(v, 4) for v in wf.boot_ci(m.hit_b[s].astype(float).values
                                                             - m.hit_a[s].astype(float).values, m.date_a[s].values)],
             "d_nll_ci90": [round(v, 4) for v in wf.boot_ci(m.nll_b[s].values - m.nll_a[s].values,
                                                             m.date_a[s].values)],
             f"obs_age_min_median_{la}": float(m.obs_age_min_a[s].median()),
             f"obs_age_min_median_{lb}": float(m.obs_age_min_b[s].median())}
        for u in ("C", "F"):
            su = s & (m.unit_a == u).values
            g[u] = {"n": int(su.sum()), f"hit_{la}": round(float(m.hit_a[su].mean()), 4),
                    f"hit_{lb}": round(float(m.hit_b[su].mean()), 4),
                    "hit_market": round(float(m.mkt_hit_a[su].mean()), 4)}
        res[f"{h0}-{h1}h"] = g
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "data", "eval", "wxpredict"))
    ap.add_argument("--stamp", required=True)
    args = ap.parse_args(argv)
    t0 = time.time()
    made = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    window = [FOLDS[0][0], FOLDS[-1][1]]
    v1, sha1 = load(TABLE_V1)
    x1 = remaining.features(v1, wf.features(v1))
    r1, _ = score(v1, x1)
    print(f"v1 scored, {time.time() - t0:.0f} s", flush=True)
    v2, sha2 = load(TABLE_V2)
    x2 = remaining.features(v2, wf.features(v2))
    r2, fits2 = score(v2, x2)
    print(f"v2 scored, {time.time() - t0:.0f} s", flush=True)
    lag = {"made": made, "window": window, "folds": FOLDS,
           "tables": {"v1": {"file": os.path.relpath(TABLE_V1, ROOT), "gzip_sha256": sha1, "report_lag_s": 1200},
                      "v2": {"file": os.path.relpath(TABLE_V2, ROOT), "gzip_sha256": sha2, "report_lag_s": 480}},
           "model": "remaining.py, refitted per fold on every eligible row dated before it",
           "result": compare(r1, r2, "v1", "v2")}

    latest = load_latest()
    val, age = asof(v2, latest)
    per_step = np.where(v2.unit.values == "F", 10 / 9, 1.0)
    xf = x2.copy()
    xf["fcl_c"], xf["fcl_age_h"] = val, age
    xf["fcl_rev_c"] = val - v2.fc_bm_tmax_c.values
    xf["gap_fcl"] = (val - v2.rmax_c.values) / per_step
    rf, _ = score(v2, xf)
    print(f"same-day features scored, {time.time() - t0:.0f} s", flush=True)
    rev = val - v2.fc_bm_tmax_c.values
    has = ~np.isnan(rev)
    moved = v2.copy()
    for c in REVISED:
        moved[c] = np.where(has, v2[c].values + np.nan_to_num(rev), v2[c].values)
    xr = remaining.features(moved, wf.features(moved))
    rr, _ = score(v2, x2, fits=fits2, variants={"": xr})
    have = v2[(v2.date >= window[0]) & (v2.date <= window[1]) & (v2.source == "venue")]
    same_day = {"made": made, "window": window, "folds": FOLDS,
                "table": {"file": os.path.relpath(TABLE_V2, ROOT), "gzip_sha256": sha2},
                "forecast_sources": list(FORECAST_GLOBS),
                "decisions_with_a_fetched_forecast": int((~np.isnan(asof(have, latest)[0])).sum()),
                "decisions": int(len(have)),
                "as_features": compare(r2, rf, "v2", "v2_same_day"),
                "inputs_revised": compare(r2, rr, "v2", "v2_revised")}
    os.makedirs(args.out_dir, exist_ok=True)
    for name, body in ((f"report_lag_{args.stamp}.json", lag), (f"same_day_forecast_{args.stamp}.json", same_day)):
        with open(os.path.join(args.out_dir, name), "w") as fh:
            fh.write(json.dumps(body, indent=1) + "\n")
    print(json.dumps({"report_lag_9_16h": lag["result"]["9-16h"],
                      "same_day_as_features_9_16h": same_day["as_features"]["9-16h"],
                      "same_day_revised_9_16h": same_day["inputs_revised"]["9-16h"]}, indent=1))


if __name__ == "__main__":
    main()
