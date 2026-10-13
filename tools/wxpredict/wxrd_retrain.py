"""The weekly refit of wxrd-v3: every settled day reaches the model (13 Oct).

Hassan, 12 Oct: "each day needs to teach the model". The remaining-day model
(remaining.py, five seeds, ladders averaged: wxrd-v3) is refitted each week on
every eligible row up to the last settled day, and replaces the model serving
in shadow only through this gate.

WHY THE NEWEST FIT, AND WHAT THE GATE CHECKS
--------------------------------------------
data/eval/wxpredict/retrain_backtest_2026-10-13.json replayed it on four weeks.
A candidate held back from the gate week served no better than a frozen fit
(hit -0.19 to +0.77 points), while the fit on every day before the served week
beat it (+0.38 to +1.06 points, log loss -0.0087 to -0.0019). The newest data
is what helps, so the newest fit is the candidate, and the gate checks the
procedure on days no model it compares has seen (Rule 11):

  the week served  the serving model (incumbent, fitted through F) and its
                   predecessor (the same procedure through F - 7) are scored
                   on the venue decisions 09-16h local of F + 1 .. THROUGH,
                   which neither was fitted on. The incumbent must not lose
                   log loss to its predecessor: the last refit helped, or at
                   least did no harm.
  minimum sample   at least MIN_DECISIONS decisions on MIN_DATES dates there.
  change bound     the candidate's ladders may move at most TV_CAP (mean total
                   variation) from the incumbent's on those decisions. It is a
                   bound on change, not a score: the candidate was fitted on
                   them.

Rule 11 in full: the prior is the incumbent, which serves until a candidate
passes; the bounds are TV_CAP and the gate; the minimum sample is
MIN_DECISIONS / MIN_DATES; the maximum change per update is TV_CAP, one update
a week; the version (wxrd3:v3w<THROUGH>:<sha256 12>) is on every row the
model writes (wxrd_live.variant_version).

A held candidate is not installed; the incumbent keeps serving and the record
says why. Every run writes its record, promoted or not.

    python tools/wxpredict/wxrd_retrain.py --table <v2 table> --through 2026-10-18 --out <dir>
"""
import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INCUMBENT = os.path.join(ROOT, "data", "models", "wxrd", "wxrd-v3_remaining.json")
SEEDS = 5
HOURS = (9, 16)             # the decisions before the peak, as stability_eval and retrain_backtest
PREDECESSOR_DAYS = 7
# Weeks of the backtest held 2,627-2,632 decisions on 7 dates, and 1,888 on
# the 6 dates of 6-11 Oct (retrain_backtest_2026-10-13.json).
MIN_DECISIONS = 1500
MIN_DATES = 5
# Twice the largest weekly change measured for five-seed ladders, 0.0517
# (retrain_backtest_2026-10-13.json, gates); a single seed moved 0.093-0.096.
TV_CAP = 0.10


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------
def day(s, k=0):
    return (dt.date.fromisoformat(str(s)) + dt.timedelta(days=k)).isoformat()


def version_name(through):
    """'wxrd-v3w20261018': wxrd_live.variant_version makes it 'wxrd3:v3w20261018:<sha>'."""
    return "wxrd-v3w" + str(through).replace("-", "")


def served_window(incumbent_fit_last, through):
    """The days the incumbent served and was never fitted on."""
    return day(incumbent_fit_last, 1), str(through)


def tv(p, q):
    """Total variation distance per decision between two ladders."""
    import numpy as np
    return 0.5 * np.abs(np.asarray(p) - np.asarray(q)).sum(1)


def decide(gate):
    """(promote, reasons held). gate: n, dates, nll_incumbent,
    nll_predecessor, tv_mean (candidate against incumbent)."""
    held = []
    if gate["n"] < MIN_DECISIONS:
        held.append(f"{gate['n']} decisions in the week served, under {MIN_DECISIONS}")
    if gate["dates"] < MIN_DATES:
        held.append(f"{gate['dates']} dates in the week served, under {MIN_DATES}")
    if gate["n"] and gate["nll_incumbent"] > gate["nll_predecessor"]:
        held.append(f"the serving model's log loss {gate['nll_incumbent']:.4f} was above its predecessor's "
                    f"{gate['nll_predecessor']:.4f} on the week it served")
    if gate["n"] and gate["tv_mean"] > TV_CAP:
        held.append(f"the candidate moves the ladders {gate['tv_mean']:.4f} from the serving model's, over {TV_CAP}")
    return not held, held


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
def load(meta_path):
    import wxrd_live
    with open(meta_path) as fh:
        meta = json.load(fh)
    models = wxrd_live.load_model(meta, meta_path)
    return meta, models if isinstance(models, list) else [models]


def ladders(models, x, sub, lo, hi, mask):
    import numpy as np
    import remaining
    return np.mean([remaining.bucket_probs(m.predict(x), sub.unit.values, sub.rmax_unit.values, lo, hi, mask)
                    for m in models], axis=0)


def run(table, through, out, incumbent=INCUMBENT, predecessor=None):
    import numpy as np
    import remaining
    import v2_eval as ev
    import walkforward as wf
    import wxrd_fit
    t0 = time.time()
    inc_meta, inc = load(incumbent)
    a, b = served_window(inc_meta["fit_last"], through)
    if b < a:
        raise SystemExit(f"nothing served since {inc_meta['version']}'s last fitted day {inc_meta['fit_last']}")
    with tempfile.TemporaryDirectory() as tmp:
        if predecessor is None:
            pred_meta = wxrd_fit.fit(table, tmp, inc_meta["version"] + "-predecessor", SEEDS,
                                     day(inc_meta["fit_last"], -PREDECESSOR_DAYS))
            predecessor = os.path.join(tmp, f"{pred_meta['version']}_remaining.json")
        pred_meta, pred = load(predecessor)
    cand_meta = wxrd_fit.fit(table, out, version_name(through), SEEDS, through)
    _, cand = load(os.path.join(out, f"{cand_meta['version']}_remaining.json"))

    df, sha = ev.load(table)
    x = remaining.features(df, wf.features(df))
    te = ((df.date >= a) & (df.date <= b) & (df.source == "venue") & (df.mkt_complete == 1)
          & (df.local_hour >= HOURS[0]) & (df.local_hour <= HOURS[1])).values
    sub = df[te]
    gate = {"n": int(te.sum()), "dates": int(sub.date.nunique())}
    if gate["n"]:
        lo, hi, q, mask, win = wf.ladders(sub)
        p = {name: ladders(models, x[te], sub, lo, hi, mask)
             for name, models in (("incumbent", inc), ("predecessor", pred), ("candidate", cand))}
        for name, ladder in list(p.items()) + [("market", q)]:
            gate[f"hit_{name}"] = round(float((ladder.argmax(1) == win).mean()), 4)
            gate[f"nll_{name}"] = round(float(wf.nll(ladder, win).mean()), 4)
        gate["d_nll_ci90"] = [round(v, 4) for v in wf.boot_ci(wf.nll(p["incumbent"], win) - wf.nll(p["predecessor"], win),
                                                             sub.date.values)]
        d = tv(p["candidate"], p["incumbent"])
        gate["tv_mean"] = round(float(d.mean()), 4)
        gate["tv_p90"] = round(float(np.quantile(d, 0.9)), 4)
        gate["top_changed"] = round(float((p["candidate"].argmax(1) != p["incumbent"].argmax(1)).mean()), 4)
    promote, held = decide({"nll_incumbent": 0, "nll_predecessor": 0, "tv_mean": 0, **gate})
    record = {"made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "through": str(through),
              "week_served": [a, b], "hours_local": list(HOURS),
              "table": {"file": os.path.relpath(table, ROOT) if table.startswith(ROOT) else table, "gzip_sha256": sha},
              "incumbent": {"version": inc_meta["version"], "model_sha256": inc_meta["model_sha256"],
                            "fit_last": inc_meta["fit_last"]},
              "predecessor": {"version": pred_meta["version"], "model_sha256": pred_meta["model_sha256"],
                              "fit_last": pred_meta["fit_last"]},
              "candidate": {"version": cand_meta["version"], "model_sha256": cand_meta["model_sha256"],
                            "fit_last": cand_meta["fit_last"], "rows": cand_meta["rows"]},
              "rules": {"min_decisions": MIN_DECISIONS, "min_dates": MIN_DATES, "tv_cap": TV_CAP},
              "gate": gate, "promoted": promote, "held": held, "seconds": round(time.time() - t0)}
    with open(os.path.join(out, f"gate_{through}.json"), "w") as fh:
        fh.write(json.dumps(record, indent=1) + "\n")
    print(json.dumps(record, indent=1))
    return record


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True, help="the v2 table built through --through (build_table.py --v2)")
    ap.add_argument("--through", required=True, help="the last settled day the candidate is fitted on")
    ap.add_argument("--out", required=True, help="where the candidate and the gate record go")
    ap.add_argument("--incumbent", default=INCUMBENT)
    ap.add_argument("--predecessor", default=None,
                    help="the incumbent's predecessor's json; fitted here (F - 7) when not given")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    return 0 if run(args.table, args.through, args.out, args.incumbent, args.predecessor)["promoted"] else 3


if __name__ == "__main__":
    sys.exit(main())
