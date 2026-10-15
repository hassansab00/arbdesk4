"""Guarded nightly remaining-day refit; automatic changes are shadow-only.

Fixed recipe, a 14-date holdout unused by either model, one calendar-day gap,
recorded engineered first-call inputs, proper-score/regression/drift gates and rollback.
Research dependencies are imported only after the cheap planning gate passes.
"""
import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import common, cycle_registry as registry

HOLDOUT_DATES = 14
MIN_CITIES_PER_DATE = 20
MIN_TRAIN_ROWS = 50000
MIN_COVERAGE = 0.80
MAX_BUCKET_CHANGE = 0.10
SEED, ROUNDS = 20261011, 250
CHECKPOINTS = ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
JOB = "P7.9_candidate_cycle"


def plan(today, incumbent, paused=False):
    last = today - dt.timedelta(days=2)  # local-day completion and settlement margin
    first = last - dt.timedelta(days=HOLDOUT_DATES - 1)
    fit_last = first - dt.timedelta(days=2)  # leave a whole calendar day between fit and evaluation
    why = ("paused by rollback" if paused else
           "incumbent trained inside this holdout" if incumbent["fit_last"] >= first.isoformat() else
           "no new training date before the independent holdout" if fit_last.isoformat() <= incumbent["fit_last"] else None)
    return {"night": today.isoformat(), "fit_last": fit_last.isoformat(), "evaluation_first": first.isoformat(),
            "evaluation_last": last.isoformat(), "incumbent_fit_last": incumbent["fit_last"],
            "decision": "wait" if why else "ready", "reason": why, "changes_serving": False,
            "policy": {"holdout_dates": HOLDOUT_DATES, "minimum_cities_per_date": MIN_CITIES_PER_DATE,
                       "minimum_training_rows": MIN_TRAIN_ROWS, "minimum_coverage": MIN_COVERAGE,
                       "maximum_validation_bucket_change": MAX_BUCKET_CHANGE, "seed": SEED, "rounds": ROUNDS}}


def assess(rows, expected, first, last):
    dates = [(dt.date.fromisoformat(first) + dt.timedelta(days=i)).isoformat() for i in range(HOLDOUT_DATES)]
    if dates[-1] != last:
        raise ValueError("evaluation is not the fixed fourteen-date window")
    seen = set()
    for r in rows:
        key = r["city"], r["date"], r["checkpoint"]
        if key in seen or r["date"] not in dates or r["checkpoint"] not in CHECKPOINTS:
            raise ValueError("duplicate or out-of-window evaluation row")
        seen.add(key)
        if any(not math.isfinite(r[k]) or r[k] < 0 for k in ("candidate_nll", "incumbent_nll", "market_nll", "jump")):
            raise ValueError("invalid candidate score")
    def aggregate(rs):
        n = len(rs)
        return {"n": n, **{k + "_hits": sum(r[k + "_hit"] for r in rs) for k in ("candidate", "incumbent", "market")},
                **{k + "_nll": sum(r[k + "_nll"] for r in rs) / n if n else None for k in ("candidate", "incumbent", "market")}}
    groups = {cp: aggregate([r for r in rows if r["checkpoint"] == cp]) for cp in CHECKPOINTS}
    sparse = [cp + ":" + d for cp in CHECKPOINTS for d in dates
              if len({r["city"] for r in rows if r["checkpoint"] == cp and r["date"] == d}) < MIN_CITIES_PER_DATE]
    regressed = [cp for cp, g in groups.items() if g["n"] and
                 (g["candidate_hits"] < g["incumbent_hits"] or g["candidate_nll"] > g["incumbent_nll"])]
    by_date = {d: [r for r in rows if r["date"] == d] for d in dates}
    interval = None
    if rows and all(by_date.values()):
        sums = {d: (sum(r["incumbent_nll"] - r["candidate_nll"] for r in rs), len(rs)) for d, rs in by_date.items()}
        rng = random.Random(SEED)
        draws = []
        for _ in range(2000):
            sample = [sums[rng.choice(dates)] for _ in dates]
            draws.append(sum(s for s, _ in sample) / sum(n for _, n in sample))
        draws.sort()
        interval = [draws[99], draws[1899]]
    coverage = len(rows) / expected if expected else 0
    jump = max((r["jump"] for r in rows), default=0)
    reasons = []
    if sparse: reasons.append("insufficient dates/cities per checkpoint")
    if coverage < MIN_COVERAGE: reasons.append("insufficient common-call coverage")
    if regressed: reasons.append("checkpoint regression")
    if jump > MAX_BUCKET_CHANGE: reasons.append("prediction change exceeds its bound")
    if interval is None or interval[0] <= 0: reasons.append("no positive date-clustered log-loss gain")
    return {"decision": "reject" if reasons else "accept shadow", "reasons": reasons, "all": aggregate(rows),
            "checkpoints": groups, "regressed_checkpoints": regressed, "sparse_checkpoint_dates": sparse,
            "coverage": coverage, "expected_first_calls": expected, "maximum_bucket_change": jump,
            "nll_gain_ci90_by_date": interval}


def incumbent():
    selected = registry.selected()
    if selected:
        return selected
    path = os.path.join(common.ROOT, "data/models/wxrd/wxrd-v2_remaining.json")
    with open(path) as fh:
        return path, json.load(fh)


def fit_and_evaluate(p, model_path, meta, work):
    import numpy as np
    import pandas as pd
    from wxpredict import build_table, remaining, walkforward as wf, wxrd_live, arrival_dataset, live_reader
    built = build_table.build(last=p["evaluation_last"], out_dir=work, write_meta=False, report_lag_s=480)
    table = os.path.join(work, "wxpredict_table.csv.gz")
    df = pd.read_csv(table, low_memory=False)
    train = df[(df.date <= p["fit_last"]) & df.label_unit.notna() & remaining.eligible(df)].reset_index(drop=True)
    del df
    if len(train) < MIN_TRAIN_ROWS or train.date.max() >= p["evaluation_first"] or meta["fit_last"] >= p["evaluation_first"]:
        raise ValueError("insufficient training data or training/evaluation overlap")
    x = remaining.features(train, wf.features(train))
    model = remaining.fit(x, remaining.target(train), SEED, ROUNDS)
    training_rows = len(train)
    del train, x
    through = dt.datetime.fromisoformat(p["night"]).replace(tzinfo=common.UTC).timestamp()
    rows, coverage = arrival_dataset.build(p["evaluation_first"], p["evaluation_last"], table, through)
    if not rows:
        return {**p, "decision": "reject", "reasons": ["no complete recorded first-call evaluation rows"], "coverage": coverage}, None, None
    # The live predictor uses this exact write/read conversion before calling
    # LightGBM. It fixes numeric dtypes and preserves the same rounding.
    evaluation_path = os.path.join(work, "actual_arrival_evaluation.csv.gz")
    live_reader.write_rows(evaluation_path, rows)
    test = pd.read_csv(evaluation_path, low_memory=False)
    lo, hi, q, mask, win = wf.ladders(test)
    tx = remaining.features(test, wf.features(test))
    def predict(m):
        return remaining.bucket_probs(m.predict(tx[m.feature_name()]), test.unit.values, test.rmax_unit.values, lo, hi, mask)
    candidate = predict(model)
    base = wxrd_live.load_model(meta, model_path)
    base = base if isinstance(base, list) else [base]
    old = np.mean([predict(m) for m in base], axis=0)
    scores = []
    cn, bn, mn = wf.nll(candidate, win), wf.nll(old, win), wf.nll(q, win)
    for i, r in enumerate(rows):
        scores.append({"city": r["city_key"], "date": r["date"], "checkpoint": r["checkpoint"],
                       "candidate_hit": bool(candidate[i].argmax() == win[i]), "incumbent_hit": bool(old[i].argmax() == win[i]),
                       "market_hit": bool(q[i].argmax() == win[i]), "candidate_nll": float(cn[i]),
                       "incumbent_nll": float(bn[i]), "market_nll": float(mn[i]), "jump": float(np.max(np.abs(candidate[i] - old[i])))})
    result = {**p, **assess(scores, coverage["first_calls"], p["evaluation_first"], p["evaluation_last"]),
              "input_coverage": coverage, "training_rows": training_rows,
              "table_content_sha256": built["sha256"], "table_sources": built["sources"],
              "incumbent_model_sha256": meta["model_sha256"], "recipe": "remaining-v2; fixed parameters; one seed; recorded first-call validation"}
    result["evaluation_gzip_sha256"] = hashlib.sha256(open(evaluation_path, "rb").read()).hexdigest()
    result["code_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=common.ROOT, text=True).strip()
    raw = model.model_to_string().encode()
    sha = hashlib.sha256(raw).hexdigest()
    result["candidate_model_sha256"] = sha
    result["table_gzip_sha256"] = hashlib.sha256(open(table, "rb").read()).hexdigest()
    model_id = "wxrd-auto-" + p["night"].replace("-", "") + "-" + sha[:12]
    fitted = {"version": model_id, "model_sha256": sha, "model_file": model_id + ".txt.gz",
              "fit_last": p["fit_last"], "report_lag_s": 480, "features": model.feature_name(),
              "rows": training_rows, "recipe": result["recipe"], "seed": SEED, "rounds": ROUNDS,
              "parameters": remaining.PARAMS, "evaluation_report": "data/eval/wxpredict/cycles/" + p["night"] + ".json"}
    weight = os.path.join(work, fitted["model_file"])
    with open(weight, "wb") as fh:
        fh.write(gzip.compress(raw, compresslevel=9, mtime=0))
    return result, fitted, weight


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--rollback", metavar="ACCEPTED_MODEL_ID")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    if args.rollback:
        registry.rollback(args.rollback)
        print("shadow rolled back; automatic refits paused")
        return 0
    if args.resume:
        state = registry.registry(); state["paused"] = False
        registry.write(os.path.join(common.ROOT, registry.REGISTRY), state)
        print("automatic shadow refits resumed")
        return 0
    model_path, meta = incumbent()
    state = registry.registry()
    p = plan(dt.datetime.now(common.UTC).date(), meta, state["paused"])
    recorded = next((r for r in state["history"] if r.get("action") == "cycle" and r["night"] == p["night"]), None)
    ready = p["decision"] == "ready" and recorded is None
    if ready:
        from wxpredict import arrival_dataset
        through = dt.datetime.fromisoformat(p["night"]).replace(tzinfo=common.UTC).timestamp()
        availability = arrival_dataset.preflight(p["evaluation_first"], p["evaluation_last"], through, MIN_CITIES_PER_DATE)
        p["recorded_input_availability"] = availability
        if not availability["ready"]:
            p.update(decision="wait", reason="insufficient on-time recorded first-call inputs")
            ready = False
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write("fit=" + str(ready).lower() + "\n")
    if recorded:
        print(json.dumps(registry.read(os.path.join(common.ROOT, recorded["report"]))))
        return 0
    if args.plan and ready:
        print(json.dumps(p)); return 0
    fitted = None
    if ready:
        from wxpredict import model_artifacts
        work = os.path.join(common.ROOT, ".cache/wxpredict-cycle", p["night"])
        os.makedirs(work, exist_ok=True)
        p, fitted, weight = fit_and_evaluate(p, model_path, meta, work)
        if p["decision"] == "accept shadow":
            fitted["asset_url"] = model_artifacts.publish(weight, fitted["version"], fitted["model_sha256"])
        else:
            fitted = None
    registry.record(p, fitted)
    if os.environ.get("SUPABASE_URL"):
        common.ops().log_run(JOB, "ok", p.get("training_rows", 0),
                             {"decision": p["decision"], "reason": p.get("reason"), "changes_serving": False})
    print(json.dumps(p, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
