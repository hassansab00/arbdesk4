"""WXPredict phase 3C: start at the market, learn only where it is wrong.

market_aware.py learned the remaining day's whole distribution from scratch
with the market's prices among its features; on unseen months it was far
worse than the market (11 Oct: +0.117 nats per decision, July to October),
because it had to relearn everything the price already says. This model
starts every bucket at the market's own price and learns corrections only:
a binary LightGBM per (decision, bucket), "is this bucket the winner?",
whose starting score is the logit of the market's normalised price, so with
no trees it is the market. The trees see the decision (the weather model's
features, the running maximum, the hour, the market's shape and recent
moves) and the bucket (its place against the market's favourite, the running
maximum's bucket and each forecast, in the market's steps), and move a
bucket's price only where that held in the past. The buckets of a decision
are then normalised to sum to one.

How far it moves is fixed out of sample: for each test month the model is
fitted on the decisions dated before the eve of the month, first leaving
out the last VALID_DAYS of them to choose the number of trees by the
decision NLL there (early stopping), then refitted on all of them with that
number. No tree, no move. Scored on the month's venue decisions with every
bucket priced, against the market's normalised prices and the market
tempered per hour group (walkforward.py's control), with a 90% interval from
resampling whole dates. --dump writes the per-bucket probabilities in the
form ask_backtest.py reads (the model's in p_model). Research only.

    python tools/wxpredict/market_residual.py [--out data/eval/wxpredict/market_residual_<date>.json] [--dump D.csv.gz]
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import walkforward as wf  # noqa: E402

TEST_MONTHS = ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
VALID_DAYS = 14
MAX_ROUNDS = 600
EARLY_STOP = 40
CLIP = 1e-4
SEED = 20261011
PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=31, min_data_in_leaf=1000,
              feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0, verbose=-1,
              seed=SEED, num_threads=4, deterministic=True, force_col_wise=True)
FORECASTS = ("fc_bm_tmax_c", "fc_models_mean_c", "fch_day_max_c", "fch_rest_max_c", "rmax_c", "t_now_c")
MARKET = ("mkt_overround", "mkt_mean", "mkt_sd", "mkt_top_p", "mkt_entropy", "mkt_p_rmax_bucket", "mkt_p_alive",
          "mkt_mean_d1h", "mkt_mean_d3h", "mkt_mean_d6h", "mkt_top_p_d3h", "mkt_newest_age_min",
          "mkt_moves_1h", "mkt_moves_6h")


def to_c(unit, v):
    return np.where(np.asarray(unit)[:, None] == "F", (v - 32) * 5 / 9, v)


def long_form(df, lo, hi, q, mask):
    """One row per (decision, valid bucket): the decision's features and the bucket's."""
    n, k = q.shape
    x = wf.features(df)
    x = pd.concat([x, pd.DataFrame({c: pd.to_numeric(df[c], errors="coerce").values for c in MARKET},
                                   index=x.index)], axis=1)
    x["group"] = wf.groups(df.day_offset.values, df.local_hour.values)
    x["n_bands"] = df.n_bands.values
    per_step = np.where(df.unit.values == "F", 10 / 9, 1.0)[:, None]          # C per market step
    fav = q.argmax(1)
    i0 = np.where((df.day_offset.values == 0) & df.rmax_bucket.notna().values,
                  df.rmax_bucket.fillna(-1).values, np.nan)
    cols = np.arange(k)[None, :].repeat(n, 0)
    open_lo, open_hi = ~np.isfinite(lo), ~np.isfinite(hi)
    # the bucket's values in the market's unit: [lo, hi - 1] for integers; an open end takes its one edge
    top = np.where(open_hi, lo, hi - 1)
    bot = np.where(open_lo, hi - 1, lo)
    centre = to_c(df.unit.values, (top + bot) / 2)
    qc = np.where(mask, q, 0)
    b = {"k_rel_fav": cols - fav[:, None], "k_rel_rmax": cols - i0[:, None],
         "k_from_top": df.n_bands.values[:, None] - 1 - cols,
         "q": q, "logit_q": np.log(np.clip(q, CLIP, 1 - CLIP) / (1 - np.clip(q, CLIP, 1 - CLIP))),
         "q_below": np.cumsum(qc, 1) - qc, "q_rank": (-qc).argsort(1).argsort(1),
         "open_lo": open_lo.astype(int), "open_hi": open_hi.astype(int)}
    for f in FORECASTS:
        v = df[f].values.astype(float)[:, None]
        b[f"centre_minus_{f}"] = (centre - v) / per_step
        b[f"lo_minus_{f}"] = np.where(open_lo, np.nan, (to_c(df.unit.values, lo) - v) / per_step)
        b[f"hi_minus_{f}"] = np.where(open_hi, np.nan, (to_c(df.unit.values, hi - 1) - v) / per_step)
    r, c = np.nonzero(mask)
    xl = pd.concat([x.iloc[r].reset_index(drop=True),
                    pd.DataFrame({name: np.asarray(a, dtype=float)[r, c] for name, a in b.items()})], axis=1)
    return xl, r, c


def renorm(p, r, n):
    s = np.bincount(r, weights=p, minlength=n)
    return p / s[r]


def decision_nll(p, r, y, n):
    """Mean NLL per decision of the normalised bucket probabilities."""
    pn = renorm(p, r, n)
    return float(-np.log(np.clip(pn[y == 1], 1e-12, None)).mean())


def sigmoid(z):
    return 1 / (1 + np.exp(-z))


def fit(xl, y, init, r, dates, valid_from):
    """Trees chosen on the last VALID_DAYS (early stopping on the decision
    NLL), then refitted on every training row with that number."""
    tr, va = dates < valid_from, dates >= valid_from
    rv = np.unique(r[va], return_inverse=True)[1]
    nv = int(rv.max()) + 1

    def feval(preds, data):
        return "decision_nll", decision_nll(preds, rv, y[va], nv), False

    d_tr = lgb.Dataset(xl[tr], y[tr], init_score=init[tr], free_raw_data=False)
    d_va = lgb.Dataset(xl[va], y[va], init_score=init[va], reference=d_tr, free_raw_data=False)
    m = lgb.train(dict(PARAMS, metric="None"), d_tr, num_boost_round=MAX_ROUNDS, valid_sets=[d_va], feval=feval,
                  callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)])
    rounds = int(m.best_iteration or 0)
    base = decision_nll(sigmoid(init[va]), rv, y[va], nv)
    best = m.best_score["valid_0"]["decision_nll"] if rounds else base
    if rounds == 0 or best >= base:
        return None, 0, base, best
    full = lgb.train(PARAMS, lgb.Dataset(xl, y, init_score=init), num_boost_round=rounds)
    return full, rounds, base, best


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--dump", default=None)
    ap.add_argument("--months", default=",".join(TEST_MONTHS))
    args = ap.parse_args(argv)
    t0 = time.time()
    sha = hashlib.sha256(open(wf.TABLE, "rb").read()).hexdigest()
    df = pd.read_csv(wf.TABLE, low_memory=False)
    df = df[df.label_unit.notna() & (df.source == "venue") & (df.mkt_complete == 1)].reset_index(drop=True).copy()
    df["d"] = pd.to_datetime(df.date)
    lo, hi, q, mask, win = wf.ladders(df)
    n = len(df)
    xl, r, c = long_form(df, lo, hi, q, mask)
    leaks = [col for col in xl.columns if col in wf.LABELS or col in ("y_c", "d")]
    assert not leaks, leaks
    y = (c == win[r]).astype(int)
    init = np.log(np.clip(q[r, c], CLIP, 1 - CLIP) / (1 - np.clip(q[r, c], CLIP, 1 - CLIP)))
    dates = df.d.values[r]
    groups = wf.groups(df.day_offset.values, df.local_hour.values)
    print(f"{n:,} decisions, {len(xl):,} buckets, {xl.shape[1]} features, {time.time() - t0:.0f} s", flush=True)

    scored, dumps, fits = [], [], {}
    for month in args.months.split(","):
        m0 = pd.Timestamp(month + "-01")
        m1 = m0 + pd.offsets.MonthBegin(1)
        cut = m0 - pd.Timedelta(days=1)                        # the eve of the month is left out
        tr_dec = (df.d < cut).values
        te_dec = ((df.d >= m0) & (df.d < m1)).values
        if not te_dec.any() or not tr_dec.any():
            continue
        tr, te = tr_dec[r], te_dec[r]
        model, rounds, base, best = fit(xl[tr], y[tr], init[tr], r[tr], dates[tr],
                                        np.datetime64(cut - pd.Timedelta(days=VALID_DAYS)))
        fits[month] = {"rounds": rounds, "valid_nll_market": round(base, 4), "valid_nll_model": round(best, 4),
                       "train_decisions": int(tr_dec.sum())}
        raw = init[te] + (model.predict(xl[te], raw_score=True) if model is not None else 0.0)
        idx = np.flatnonzero(te_dec)
        pos = np.full(n, -1)
        pos[idx] = np.arange(len(idx))
        p = np.zeros((len(idx), q.shape[1]))
        p[pos[r[te]], c[te]] = sigmoid(raw)
        p = p / p.sum(1, keepdims=True)
        qq, mm, ww = q[idx], mask[idx], win[idx]
        # the market tempered per hour group on the training decisions: its own calibration
        lq_tr, lq_te = np.log(np.where(mask[tr_dec], q[tr_dec], 1)), np.log(np.where(mm, qq, 1))
        qt = qq.copy()
        g_tr, g_te = groups[tr_dec], groups[idx]
        for gi in sorted(set(g_te)):
            s_tr, s_te = g_tr == gi, g_te == gi
            a = wf.fit_pool([lq_tr[s_tr]], mask[tr_dec][s_tr], win[tr_dec][s_tr], [1.0]) if s_tr.any() else [1.0]
            qt[s_te] = wf.pool([lq_te[s_te]], a, mm[s_te])
        sub = df.iloc[idx]
        scored.append(pd.DataFrame({
            "month": month, "date": sub.date.values, "group": g_te,
            "nll_market": wf.nll(qq, ww), "nll_market_tempered": wf.nll(qt, ww), "nll_model": wf.nll(p, ww),
            "top_market": qq.argmax(1) == ww, "top_model": p.argmax(1) == ww}))
        if args.dump:
            def fmt(a):
                return [";".join(f"{v:.6f}" for v in row[mk]) for row, mk in zip(a, mm)]
            dumps.append(pd.DataFrame({
                "event_id": sub.event_id.values, "city_key": sub.city_key.values, "date": sub.date.values,
                "unit": sub.unit.values, "decision_utc": sub.decision_utc.values,
                "day_offset": sub.day_offset.values, "local_hour": sub.local_hour.values,
                "bands_lo": sub.bands_lo.values, "bands_hi": sub.bands_hi.values, "winner": ww,
                "q": fmt(qq), "p_tempered": fmt(qt), "p_model": fmt(p)}))
        print(f"{month}: {len(idx):,} decisions, {rounds} trees (valid {base:.4f} -> {best:.4f}), "
              f"{time.time() - t0:.0f} s", flush=True)

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
              "decisions": n, "method": __doc__.strip().splitlines()[0], "test_months": args.months.split(","),
              "seed": SEED, "fits": fits, "all": summary(s),
              "by_group": {wf.GROUP_NAMES[k]: summary(p) for k, p in s.groupby("group")},
              "by_month": {k: summary(p) for k, p in s.groupby("month")},
              "seconds": round(time.time() - t0)}
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
