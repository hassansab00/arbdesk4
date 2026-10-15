"""The fixed early-hour specialist experiment in PREDICTIVE_REPAIR_2026-10-15.

Train through 11 Oct only; evaluate 12–14 Oct against frozen wxrd-v3 on the
same complete venue ladders. No search, no serving change. ML imports are
local so the normal runtime and CI do not require research dependencies.
"""
import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FIT_LAST = "2026-10-11"
FIRST, LAST = "2026-10-12", "2026-10-14"
SEED = 20261011
ROUNDS = 250
EARLY = (9, 11)


def training_date(date):
    return date <= FIT_LAST


def validate_windows(train_dates, test_dates, baseline_fit_last):
    if not train_dates or not test_dates:
        raise ValueError("empty training or evaluation window")
    if max(train_dates) > FIT_LAST or min(test_dates) < FIRST or max(test_dates) > LAST:
        raise ValueError("dates outside the fixed experiment")
    if max(train_dates) >= min(test_dates) or baseline_fit_last >= min(test_dates):
        raise ValueError("a model trained on this evaluation")


def main(argv=None):
    import numpy as np
    import pandas as pd
    from wxpredict import remaining, walkforward as wf, wxrd_live
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--test-table", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    started = time.monotonic()
    df = pd.read_csv(args.table, low_memory=False)
    keep = (df.date <= FIT_LAST) & remaining.eligible(df) & df.local_hour.between(*EARLY)
    train = df[keep].reset_index(drop=True)
    del df
    if train.empty or not all(training_date(d) for d in train.date):
        raise ValueError("invalid or leaking training window")
    x = remaining.features(train, wf.features(train))
    y = remaining.target(train)
    model = remaining.fit(x, y, SEED, ROUNDS)
    print("early specialist fitted", flush=True)
    test = pd.read_csv(args.test_table, low_memory=False)
    test = test[(test.source == "venue") & (test.mkt_complete == 1) & (test.date >= FIRST) &
                (test.date <= LAST) & remaining.eligible(test) & test.local_hour.between(*EARLY)].reset_index(drop=True)
    if test.empty:
        raise ValueError("no held-out decisions")
    if test.date.min() <= train.date.max():
        raise ValueError("training and evaluation overlap")
    with open(os.path.join(wf.ROOT, "data/models/wxrd/wxrd-v3_remaining.json")) as fh:
        meta = json.load(fh)
    if meta["fit_last"] >= test.date.min():
        raise ValueError("the baseline trained on this evaluation")
    validate_windows(train.date.tolist(), test.date.tolist(), meta["fit_last"])
    base = wxrd_live.load_model(meta, os.path.join(wf.ROOT, "data/models/wxrd/wxrd-v3_remaining.json"))
    lo, hi, q, mask, win = wf.ladders(test)
    tx = remaining.features(test, wf.features(test))
    def probs(booster):
        return remaining.bucket_probs(booster.predict(tx[booster.feature_name()]), test.unit.values,
                                      test.rmax_unit.values, lo, hi, mask)
    specialist = probs(model)
    baseline = np.mean([probs(m) for m in base], axis=0)
    sh, bh = specialist.argmax(1) == win, baseline.argmax(1) == win
    sn, bn = wf.nll(specialist, win), wf.nll(baseline, win)
    def aggregate(indices):
        return {"n": int(indices.sum()), "specialist_hits": int(sh[indices].sum()),
                "baseline_hits": int(bh[indices].sum()), "market_hits": int((q.argmax(1)[indices] == win[indices]).sum()),
                "specialist_nll": float(sn[indices].mean()), "baseline_nll": float(bn[indices].mean())}
    by_date = {d: aggregate((test.date == d).values) for d in sorted(test.date.unique())}
    total = aggregate(np.ones(len(test), bool))
    passes = total["specialist_hits"] >= total["baseline_hits"] and total["specialist_nll"] < total["baseline_nll"] and all(
        v["specialist_hits"] >= v["baseline_hits"] and v["specialist_nll"] <= v["baseline_nll"] for v in by_date.values())
    digest = lambda path: hashlib.sha256(open(path, "rb").read()).hexdigest()
    out = {"experiment": "early-09-11-specialist", "fit_last": FIT_LAST, "evaluation": [FIRST, LAST],
           "training_rows": len(train), "training_first": train.date.min(), "training_last": train.date.max(),
           "training_class_counts": {str(k): int(v) for k, v in zip(*np.unique(y, return_counts=True))},
           "seed": SEED, "rounds": ROUNDS, "model_sha256": hashlib.sha256(model.model_to_string().encode()).hexdigest(),
           "baseline_sha256": meta["model_sha256"], "table_gzip_sha256": digest(args.table),
           "test_table_gzip_sha256": digest(args.test_table), "all": total, "by_date": by_date,
           "nll_gain_ci90_by_date": [float(v) for v in wf.boot_ci(bn-sn, test.date.values)],
           "hit_gain_ci90_by_date": [float(v) for v in wf.boot_ci(sh.astype(float)-bh.astype(float), test.date.values)],
           "verdict": "passes exploratory check; requires forward validation" if passes else "reject specialist",
           "changes_serving": False, "seconds": round(time.monotonic()-started, 2)}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
