"""How steady is the remaining-day model, and what steadies it? (13 Oct)

Hassan, 12 Oct: the prediction must be right before the peak, and each day must
teach the model. Before building a model that refits on every new week, three
questions on the v2 table, the same venue decisions as v2_eval (09-16h local,
every bucket priced, folds of v2_eval.FOLDS, each fold fitted only on the
days before it):

  refit      does a model fitted on the days before each week beat the one
             fitted before the first week (frozen at 14 Sep)?
  churn      how far does a weekly refit move the ladders? total variation
             (half the summed absolute difference) between the fit before
             week k and the fit before week k-1, on week k's decisions, and
             how often the top bucket changes
  seeds      how much of that is the model's own randomness? the same data
             fitted with SEEDS (bagging and feature sampling draw by seed):
             seed against seed, and the mean of the SEEDS ladders against
             one seed's

Hit (the most likely bucket won) and log loss, with 90% intervals resampling
whole dates. Research only: nothing here prices or trades.

    python tools/wxpredict/stability_eval.py --out-dir data/eval/wxpredict --stamp 2026-10-13
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import remaining  # noqa: E402
import v2_eval as ev  # noqa: E402
import walkforward as wf  # noqa: E402

ROOT = ev.ROOT
SEEDS = [wf.SEED + k for k in range(5)]
HOURS = (9, 16)


def tv(p, q):
    """Total variation distance per decision between two ladders."""
    return 0.5 * np.abs(p - q).sum(1)


def spread(d):
    return {"mean": round(float(d.mean()), 4), "p90": round(float(np.quantile(d, 0.9)), 4),
            "max": round(float(d.max()), 4)}


def pooled(rows, a, b, la, lb):
    """b against a over every fold: hit, log loss, b - a with 90% intervals by date."""
    date = np.concatenate([r["date"] for r in rows])
    ha, hb = (np.concatenate([r[f"hit_{k}"] for r in rows]) for k in (a, b))
    na, nb = (np.concatenate([r[f"nll_{k}"] for r in rows]) for k in (a, b))
    return {"n": int(len(date)), f"hit_{la}": round(float(ha.mean()), 4), f"hit_{lb}": round(float(hb.mean()), 4),
            f"nll_{la}": round(float(na.mean()), 4), f"nll_{lb}": round(float(nb.mean()), 4),
            "d_hit_ci90": [round(v, 4) for v in wf.boot_ci(hb - ha, date)],
            "d_nll_ci90": [round(v, 4) for v in wf.boot_ci(nb - na, date)]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "data", "eval", "wxpredict"))
    ap.add_argument("--stamp", required=True)
    args = ap.parse_args(argv)
    t0 = time.time()
    df, sha = ev.load(ev.TABLE_V2)
    x = remaining.features(df, wf.features(df))
    j = remaining.target(df)
    fits = {}                                   # (fold start, seed) -> model
    rows, folds = [], {}
    first = ev.FOLDS[0][0]
    for k, (a, b) in enumerate(ev.FOLDS):
        tr = (df.date < a).values
        for s in SEEDS:
            fits[(a, s)] = remaining.fit(x[tr], j[tr], s)
        te = ((df.date >= a) & (df.date <= b) & (df.source == "venue") & (df.mkt_complete == 1)
              & (df.local_hour >= HOURS[0]) & (df.local_hour <= HOURS[1])).values
        sub = df[te]
        lo, hi, q, mask, win = wf.ladders(sub)

        def ladder(start, seed):
            return remaining.bucket_probs(fits[(start, seed)].predict(x[te]), sub.unit.values,
                                          sub.rmax_unit.values, lo, hi, mask)
        one = ladder(a, SEEDS[0])
        frozen = ladder(first, SEEDS[0])
        each = [one] + [ladder(a, s) for s in SEEDS[1:]]
        avg = np.mean(each, axis=0)
        r = {"date": sub.date.values}
        for name, p in (("one", one), ("frozen", frozen), ("avg", avg), ("market", q)):
            r[f"hit_{name}"] = (p.argmax(1) == win).astype(float)
            r[f"nll_{name}"] = wf.nll(p, win)
        rows.append(r)
        f = {"n": int(te.sum()), **{f"hit_{n}": round(float(r[f'hit_{n}'].mean()), 4)
                                     for n in ("frozen", "one", "avg", "market")},
             **{f"nll_{n}": round(float(r[f'nll_{n}'].mean()), 4) for n in ("frozen", "one", "avg", "market")},
             "seed_vs_seed": {"tv": spread(np.concatenate([tv(p, one) for p in each[1:]])),
                              "top_changed": round(float(np.mean([(p.argmax(1) != one.argmax(1)).mean()
                                                                  for p in each[1:]])), 4)},
             "tv_model_vs_market": round(float(tv(one, q).mean()), 4)}
        if k > 0:
            last = ladder(ev.FOLDS[k - 1][0], SEEDS[0])
            f["refit_vs_last_week"] = {"tv": spread(tv(one, last)),
                                       "top_changed": round(float((one.argmax(1) != last.argmax(1)).mean()), 4)}
        folds[a] = f
        print(a, json.dumps(f), f"{time.time() - t0:.0f} s", flush=True)
    out = {"made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "window": [ev.FOLDS[0][0], ev.FOLDS[-1][1]], "folds_used": ev.FOLDS, "hours_local": list(HOURS),
           "table": {"file": os.path.relpath(ev.TABLE_V2, ROOT), "gzip_sha256": sha}, "seeds": SEEDS,
           "model": "remaining.py, fitted per fold on every eligible row dated before it",
           "refit_vs_frozen": pooled(rows, "frozen", "one", "frozen", "refit"),
           "seed_average_vs_one_seed": pooled(rows, "one", "avg", "one_seed", "seed_average"),
           "per_fold": folds}
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, f"stability_{args.stamp}.json"), "w") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("refit_vs_frozen", "seed_average_vs_one_seed")}, indent=1))


if __name__ == "__main__":
    main()
