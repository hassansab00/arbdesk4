"""WXPredict phase 3C, learned: where is the market wrong on the day?

The log pool (walkforward.py) can only weigh the market against the weather.
This model is handed both and learns when the market's ladder is wrong: a
multiclass LightGBM over the remaining-day steps (remaining.py), whose
features are the weather and remaining-day features plus the market's own
probability of each step above the running maximum's bucket, at the decision.

Rows: venue decisions on the day from 09h, with a running maximum and every
bucket priced (mkt_complete). Each test month is scored on its own rows by a
model fitted on every such row dated before the eve of the month (as in
walkforward.py), against the market's normalised prices on the same rows.
Research only.

    python tools/wxpredict/market_aware.py [--out data/eval/wxpredict/market_aware_<date>.json]
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from scipy.optimize import minimize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remaining  # noqa: E402
import walkforward as wf  # noqa: E402

TEST_MONTHS = ["2026-07", "2026-08", "2026-09", "2026-10"]
STEPS_SEEN = 6                # the market's probability of steps 0..5 above the running maximum's bucket
SEED = 20261011


def market_steps(q, mask, i0):
    """The market's probability of the running maximum's bucket and the
    STEPS_SEEN - 1 above it, of everything above those, and below."""
    n, k = q.shape
    idx = i0[:, None] + np.arange(STEPS_SEEN)[None, :]
    ok = (idx < k)
    qs = np.where(ok, q[np.arange(n)[:, None], np.minimum(idx, k - 1)], 0.0)
    cols = np.arange(k)[None, :]
    above = np.where(mask & (cols >= (i0 + STEPS_SEEN)[:, None]), q, 0).sum(1)
    below = np.where(mask & (cols < i0[:, None]), q, 0).sum(1)
    out = {f"mq_{s}": qs[:, s] for s in range(STEPS_SEEN)}
    out["mq_above"] = above
    out["mq_below"] = below
    out["mq_mean_step"] = (qs * np.arange(STEPS_SEEN)[None, :]).sum(1) + above * STEPS_SEEN
    return pd.DataFrame(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--months", default=",".join(TEST_MONTHS))
    args = ap.parse_args(argv)
    t0 = time.time()
    sha = hashlib.sha256(open(wf.TABLE, "rb").read()).hexdigest()
    df = pd.read_csv(wf.TABLE, low_memory=False)
    df = df[df.label_unit.notna() & (df.source == "venue") & (df.mkt_complete == 1)].reset_index(drop=True).copy()
    df = df[remaining.eligible(df)].reset_index(drop=True).copy()
    df["y_c"] = wf.target_c(df)
    df["d"] = pd.to_datetime(df.date)
    lo, hi, q, mask, win = wf.ladders(df)
    i0 = df.rmax_bucket.values.astype(int)
    x = remaining.features(df, wf.features(df))
    x = pd.concat([x.reset_index(drop=True), market_steps(q, mask, i0)], axis=1)
    leaks = [c for c in x.columns if c in wf.LABELS or c in ("y_c", "d")]
    assert not leaks, leaks
    j = remaining.target(df)
    groups = wf.groups(df.day_offset.values, df.local_hour.values)
    print(f"{len(df):,} rows, {x.shape[1]} features, {time.time() - t0:.0f} s", flush=True)

    scored = []
    for month in args.months.split(","):
        m0 = pd.Timestamp(month + "-01")
        m1 = m0 + pd.offsets.MonthBegin(1)
        train = (df.d < m0 - pd.Timedelta(days=1)).values
        test = ((df.d >= m0) & (df.d < m1)).values
        if not test.any():
            continue
        model = remaining.fit(x[train], j[train], SEED)
        p = remaining.bucket_probs(model.predict(x[test]), df.unit.values[test], df.rmax_unit.values[test],
                                   lo[test], hi[test], mask[test])
        # the market tempered on the same training rows, per hour group: its own calibration
        qt = q[test].copy()
        lq_tr, lq_te = np.log(np.where(mask[train], q[train], 1)), np.log(np.where(mask[test], q[test], 1))
        for gi in sorted(set(groups[test])):
            s_tr, s_te = groups[train] == gi, groups[test] == gi
            a = wf.fit_pool([lq_tr[s_tr]], mask[train][s_tr], win[train][s_tr], [1.0]) if s_tr.any() else [1.0]
            qt[s_te] = wf.pool([lq_te[s_te]], a, mask[test][s_te])
        scored.append(pd.DataFrame({
            "month": month, "date": df.date.values[test], "group": groups[test],
            "nll_market": wf.nll(q[test], win[test]), "nll_market_tempered": wf.nll(qt, win[test]),
            "nll_model": wf.nll(p, win[test]),
            "top_market": q[test].argmax(1) == win[test], "top_model": p.argmax(1) == win[test]}))
        print(f"{month}: {test.sum():,} decisions, trained on {train.sum():,}, {time.time() - t0:.0f} s", flush=True)

    s = pd.concat(scored, ignore_index=True)

    def summary(part):
        out = {"decisions": int(len(part)), "dates": int(part.date.nunique()),
               "nll": {k: round(float(part[f"nll_{k}"].mean()), 4) for k in ("market", "market_tempered", "model")},
               "top_bucket_hit": {"market": round(float(part.top_market.mean()), 4),
                                  "model": round(float(part.top_model.mean()), 4)}}
        for k in ("model", "market_tempered"):
            diff = (part[f"nll_{k}"] - part.nll_market).values
            out[f"{k}_minus_market"] = {"mean": round(float(diff.mean()), 4),
                                        "ci90": [round(v, 4) for v in wf.boot_ci(diff, part.date.values)]}
        return out

    result = {"made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "table_gzip_sha256": sha,
              "rows": int(len(df)), "method": __doc__.strip().splitlines()[0], "test_months": args.months.split(","),
              "seed": SEED, "all": summary(s),
              "by_group": {wf.GROUP_NAMES[k]: summary(p) for k, p in s.groupby("group")},
              "by_month": {k: summary(p) for k, p in s.groupby("month")},
              "seconds": round(time.time() - t0)}
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
