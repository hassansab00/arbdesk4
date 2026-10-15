"""Daily, paired, out-of-sample audit of the richer predictor against serving.

First calls only, the same decision instant and settled winner. Averages
never hide a morning regression. This reports a promotion gate, changes no
setting/model, and never evaluates a date the candidate was trained on.
"""
import argparse
import collections
import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import common  # noqa: E402

CHECKPOINTS = ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
MIN_FORWARD_DATES = 14  # phase 5.3's out-of-sample window, not 14 correlated calls
JOB = "P7.8_wxrd_forward_audit"
HISTORY_DAYS = 35


def instant(value):
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def score(checkpoints, outcomes, variants, fit_last, version):
    first = {}
    for p in sorted(checkpoints, key=lambda p: (instant(p["decided_at"]), str(p["checkpoint_id"]))):
        if p["checkpoint"] in CHECKPOINTS and str(p["target_date"]) > fit_last:
            first.setdefault((p["city_key"], str(p["target_date"]), p["checkpoint"]), p)
    winners = {str(o["checkpoint_id"]): str(o["winner_band_id"]) for o in outcomes if o.get("winner_band_id")}
    paired, excluded = [], collections.Counter()
    seen = set()
    for v in variants:
        key = (v["city_key"], str(v["target_date"]), v["checkpoint"])
        if v.get("variant_version") != version:
            excluded["other_version"] += 1
            continue
        if key[1] <= fit_last:
            excluded["in_sample"] += 1
            continue
        if key in seen:
            raise ValueError("duplicate candidate checkpoint")
        seen.add(key)
        p = first.get(key)
        if p is None or instant(v["decided_at"]) != instant(p["decided_at"]):
            excluded["not_the_first_decision"] += 1
            continue
        win = winners.get(str(p["checkpoint_id"]))
        if win is None:
            excluded["not_settled"] += 1
            continue
        ladders = []
        for r in (p, v):
            probs = json.loads(r["probs"]) if isinstance(r["probs"], str) else r["probs"]
            probs = {str(k): float(n) for k, n in probs.items()}
            if not probs or any(not math.isfinite(n) or not 0 <= n <= 1 for n in probs.values()) or abs(sum(probs.values()) - 1) > 1e-4:
                raise ValueError("invalid probability ladder")
            ladders.append(probs)
        if set(ladders[0]) != set(ladders[1]):
            raise ValueError("candidate and serving ladders name different buckets")
        # Use the stored top picks, whose tie rules are part of each model.
        paired.append({"date": key[1], "city": key[0], "checkpoint": key[2],
                       "candidate_hit": str(v["top_band_id"]) == win,
                       "serving_hit": str(p["top_band_id"]) == win,
                       "candidate_nll": -math.log(max(1e-6, ladders[1].get(win, 0))),
                       "serving_nll": -math.log(max(1e-6, ladders[0].get(win, 0)))})

    def aggregate(rows):
        n = len(rows)
        dates = sorted({r["date"] for r in rows})
        return {"n": n, "dates": dates,
                "candidate_hits": sum(r["candidate_hit"] for r in rows),
                "serving_hits": sum(r["serving_hit"] for r in rows),
                "candidate_nll": sum(r["candidate_nll"] for r in rows) / n if n else None,
                "serving_nll": sum(r["serving_nll"] for r in rows) / n if n else None}
    groups = {cp: aggregate([r for r in paired if r["checkpoint"] == cp]) for cp in CHECKPOINTS}
    before = aggregate([r for r in paired if r["checkpoint"] != "postpeak_1h"])
    regressed = [cp for cp, g in groups.items() if cp != "postpeak_1h" and g["n"] and
                 (g["candidate_hits"] < g["serving_hits"] or g["candidate_nll"] > g["serving_nll"])]
    # Ready for review is deliberately not permission to trade or an automatic
    # promotion. Per-date intervals and input-parity evidence must accompany it.
    immature = [cp for cp, g in groups.items() if cp != "postpeak_1h" and len(g["dates"]) < MIN_FORWARD_DATES]
    gate = "insufficient forward dates" if immature else "checkpoint regression" if regressed else "ready for review"
    return {"version": version, "fit_last": fit_last, "minimum_forward_dates": MIN_FORWARD_DATES,
            "all": aggregate(paired), "before_peak": before, "checkpoints": groups,
            "regressed_checkpoints": regressed, "immature_checkpoints": immature,
            "excluded": dict(excluded), "settled_first_calls": sum(str(p["checkpoint_id"]) in winners for p in first.values()),
            "gate": gate, "changes_serving": False}


def run():
    with open(os.path.join(common.ROOT, "data/models/wxrd/wxrd-v3_remaining.json")) as fh:
        meta = json.load(fh)
    version = "wxrd3:" + meta["version"].rsplit("-", 1)[-1] + ":" + meta["model_sha256"][:12]
    today = dt.datetime.now(common.UTC).date()
    first = max(dt.date.fromisoformat(meta["fit_last"]) + dt.timedelta(days=1),
                today - dt.timedelta(days=HISTORY_DAYS))
    filters = [("target_date", "gte." + first.isoformat()),
               ("target_date", "lt." + today.isoformat())]
    ops = common.ops()
    checkpoints = ops.rest_all("prediction_checkpoints", [("select", "checkpoint_id,city_key,target_date,checkpoint,decided_at,top_band_id,probs"), *filters], order="checkpoint_id.asc")
    outcomes = ops.rest_all("fact_checkpoint_outcome", [("select", "checkpoint_id,winner_band_id"), *filters], order="checkpoint_id.asc")
    variants = ops.rest_all("variant_shadow_checkpoints", [("select", "city_key,target_date,checkpoint,decided_at,variant_version,top_band_id,probs"), ("variant", "eq.wxrd3"), *filters], order="shadow_id.asc")
    result = score(checkpoints, outcomes, variants, meta["fit_last"], version)
    result["made"] = dt.datetime.now(common.UTC).isoformat()
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    result = run()
    path = args.out or os.path.join(common.ROOT, "data/eval/wxpredict/forward", dt.datetime.now(common.UTC).strftime("%Y-%m-%d.json"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(result, fh, indent=1)
        fh.write("\n")
    common.ops().log_run(JOB, "ok", result["all"]["n"], {"gate": result["gate"], "regressed_checkpoints": result["regressed_checkpoints"]})
    print(json.dumps({"gate": result["gate"], "before_peak": result["before_peak"], "regressed": result["regressed_checkpoints"]}))


if __name__ == "__main__":
    main()
