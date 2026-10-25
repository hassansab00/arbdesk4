"""Would a weekly, gated refit of wxrd-v3 have served better? (13 Oct)

Hassan, 12 Oct: each day must teach the model. stability_eval.py measured that
a fit on the days before each week beats the fit frozen before the first week
(+0.40 points hit, log loss -0.0067). This replays the procedure the weekly
retrain would run, on the same venue decisions (09-16h local, every bucket
priced, the weeks of v2_eval.FOLDS), with the five-seed average of wxrd-v3:

  each Monday, the gate week is the seven days just ended
  the candidate is fitted on every day before the gate week
  the incumbent is the model serving now (fitted a week or more earlier)
  both are scored on the gate week, which neither has seen (Rule 11);
  the candidate replaces the incumbent when its log loss there is not worse
  the model that comes out serves the following week

and reports:

  gates   per gate week: candidate against incumbent (hit, log loss, the
          market's), and how far the candidate's ladders are from the
          incumbent's (total variation, how often the top bucket changes):
          the measured weekly change the retrain's change bound is set from
  served  per served week and pooled: the gated model, the model frozen
          before the first gate, and the freshest fit (every day before the
          served week, which no gate could have checked), with 90% intervals
          resampling whole dates

Research only: nothing here prices or trades.

    python tools/wxpredict/retrain_backtest.py --out-dir data/eval/wxpredict --stamp 2026-10-13
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
import stability_eval as st  # noqa: E402
import v2_eval as ev  # noqa: E402
import walkforward as wf  # noqa: E402

ROOT = ev.ROOT
SEEDS = st.SEEDS
HOURS = st.HOURS


def day(s, k=0):
    return (dt.date.fromisoformat(s) + dt.timedelta(days=k)).isoformat()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "data", "eval", "wxpredict"))
    ap.add_argument("--stamp", required=True)
    args = ap.parse_args(argv)
    t0 = time.time()
    df, sha = ev.load(ev.TABLE_V2)
    x = remaining.features(df, wf.features(df))
    j = remaining.target(df)
    weeks = ev.FOLDS
    # fitted on every day before `start`: the week before the first gate, and
    # every week's start
    starts = [day(weeks[0][0], -7)] + [a for a, _ in weeks]
    fits = {}

    def fitted(start):
        if start not in fits:
            tr = (df.date < start).values
            fits[start] = [remaining.fit(x[tr], j[tr], s) for s in SEEDS]
            print("fitted before", start, f"{time.time() - t0:.0f} s", flush=True)
        return fits[start]

    def week(a, b):
        te = ((df.date >= a) & (df.date <= b) & (df.source == "venue") & (df.mkt_complete == 1)
              & (df.local_hour >= HOURS[0]) & (df.local_hour <= HOURS[1])).values
        sub = df[te]
        lo, hi, q, mask, win = wf.ladders(sub)

        def ladder(start):
            return np.mean([remaining.bucket_probs(m.predict(x[te]), sub.unit.values, sub.rmax_unit.values,
                                                   lo, hi, mask) for m in fitted(start)], axis=0)
        return sub, q, win, ladder

    def stats(p, win):
        return (p.argmax(1) == win).astype(float), wf.nll(p, win)

    gates, served, incumbent = [], [], starts[0]
    first = starts[0]
    for k, (a, b) in enumerate(weeks):
        sub, q, win, ladder = week(a, b)
        # the week served by the model the last gate chose
        if k > 0:
            fresh = a
            r = {"date": sub.date.values}
            for name, start in (("gated", incumbent), ("frozen", first), ("fresh", fresh)):
                r[f"hit_{name}"], r[f"nll_{name}"] = stats(ladder(start), win)
            r["hit_market"], r["nll_market"] = stats(q, win)
            r["serving"] = incumbent
            served.append(r)
        if k == len(weeks) - 1:
            break
        # the gate: this week is the gate week; the candidate saw every day before it
        cand = a
        pc, pi = ladder(cand), ladder(incumbent)
        hc, nc = stats(pc, win)
        hi_, ni = stats(pi, win)
        hm, nm = stats(q, win)
        g = {"gate_week": [a, b], "n": int(len(sub)), "dates": int(sub.date.nunique()),
             "candidate_fitted_before": cand, "incumbent_fitted_before": incumbent,
             "hit_candidate": round(float(hc.mean()), 4), "hit_incumbent": round(float(hi_.mean()), 4),
             "hit_market": round(float(hm.mean()), 4),
             "nll_candidate": round(float(nc.mean()), 4), "nll_incumbent": round(float(ni.mean()), 4),
             "nll_market": round(float(nm.mean()), 4),
             "d_nll_ci90": [round(v, 4) for v in wf.boot_ci(nc - ni, sub.date.values)],
             "tv_candidate_vs_incumbent": st.spread(st.tv(pc, pi)),
             "top_changed": round(float((pc.argmax(1) != pi.argmax(1)).mean()), 4)}
        g["promoted"] = bool(nc.mean() <= ni.mean())
        if g["promoted"]:
            incumbent = cand
        gates.append(g)
        print(json.dumps(g), f"{time.time() - t0:.0f} s", flush=True)

    def pooled(a, b):
        out = st.pooled(served, a, b, a, b)
        return out

    per_week = []
    for (a, b), r in zip(weeks[1:], served):
        per_week.append({"week": [a, b], "n": int(len(r["date"])), "serving_fitted_before": r["serving"],
                         **{f"hit_{n}": round(float(r[f'hit_{n}'].mean()), 4)
                            for n in ("gated", "frozen", "fresh", "market")},
                         **{f"nll_{n}": round(float(r[f'nll_{n}'].mean()), 4)
                            for n in ("gated", "frozen", "fresh", "market")}})
    out = {"made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "window": [weeks[0][0], weeks[-1][1]], "weeks": weeks, "hours_local": list(HOURS), "seeds": SEEDS,
           "table": {"file": os.path.relpath(ev.TABLE_V2, ROOT), "gzip_sha256": sha},
           "model": "remaining.py, five seeds, ladders averaged (wxrd-v3), fitted on every eligible row dated before",
           "rule": "promote when the candidate's log loss on the gate week is not above the incumbent's",
           "gates": gates, "served": per_week,
           "gated_vs_frozen": pooled("frozen", "gated"),
           "fresh_vs_gated": pooled("gated", "fresh")}
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, f"retrain_backtest_{args.stamp}.json"), "w") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("gated_vs_frozen", "fresh_vs_gated")}, indent=1))


if __name__ == "__main__":
    main()
