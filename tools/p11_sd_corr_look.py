#!/usr/bin/env python3
"""sd_corr:v1's looks, exactly as docs/SD_CORR_PREREG.md fixes them.

Reads the nightly mirror in the repository (data/mirror: variant_shadow_checkpoints,
prediction_checkpoints, fact_checkpoint_outcome, s10_shadow_checkpoints), so a look
is rebuilt from committed files alone; every file read is named with its sha256.

Rows: each sd_corr:v1 row at the five same-day checkpoints, matched to the served
first call of the same city, date and checkpoint (the first prediction_checkpoints
row by decided_at, checkpoint_id: v_checkpoint_calls' first_call), graded by
fact_checkpoint_outcome, the winner on the ladder. Left out by reason: no live
corrected row, no width, a calibrated served ladder, no served first call, no graded
outcome yet, the winner off the ladder.

Scores as fec-v1: log loss -ln(max(p_winner, 1e-6)); top-1 the largest p, ties by
bucket id (the smallest band_id, as scripts/variant_shadow.py picks its top bucket);
Brier summed over the ladder's buckets. Gains paired: served minus sd_corr, and
da_floor minus sd_corr where both have a row. Intervals: fec_same_day.cluster_boot
(seed 11, 1,000), 90% for the rule, 95% beside it. The market (fact_checkpoint_outcome's
market columns, on rows whose book was whole) and S10 rd1 are descriptive.

    python tools/p11_sd_corr_look.py --out data/eval/p11/sd_corr_first_look_2026-10-26
"""
import argparse
import collections
import csv
import glob
import gzip
import hashlib
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import fec_same_day as F  # noqa: E402
import p11_da_floor_look as D  # noqa: E402

csv.field_size_limit(sys.maxsize)
CHECKPOINTS = D.CHECKPOINTS
MIN_DATES = 20
CANDIDATE = "sd_corr:v1"
RD1 = "rd1:2026-09-25:f5372ebb05"
TABLES = ("variant_shadow_checkpoints", "prediction_checkpoints", "fact_checkpoint_outcome",
          "s10_shadow_checkpoints")
TRUE = ("t", "true", "True", "1")


def files(table, root=ROOT, only=None):
    """The mirror's files of a table, or only those named (paths relative to root):
    a look names the files it read, so it can be rebuilt after later nights append."""
    paths = sorted(glob.glob(os.path.join(root, "data", "mirror", table, "*.csv.gz")))
    return [p for p in paths if only is None or os.path.relpath(p, root) in only]


def read(table, root=ROOT, only=None):
    for path in files(table, root, only):
        with gzip.open(path, "rt", newline="") as fh:
            yield from csv.DictReader(fh)


def score(probs, winner):
    """(log loss, top-1 hit, Brier) of a {band_id: p} ladder, fec-v1, ties by bucket id."""
    top = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    brier = sum((p - (1.0 if b == winner else 0.0)) ** 2 for b, p in probs.items())
    return -math.log(max(probs.get(winner, 0.0), F.FLOOR)), int(top == winner), brier


def ladder(text):
    return {k: float(v) for k, v in json.loads(text).items()} if text else None


def rows(root=ROOT, only=None):
    """(scored rows, exclusions by reason, last target date read)."""
    shadow = {}
    for r in read("variant_shadow_checkpoints", root, only):
        if r["checkpoint"] in CHECKPOINTS:
            shadow[r["shadow_id"]] = r
    first = {}
    for r in read("prediction_checkpoints", root, only):
        k = (r["city_key"], r["target_date"], r["checkpoint"])
        if r["checkpoint"] in CHECKPOINTS and (k not in first or (r["decided_at"], r["checkpoint_id"])
                                               < (first[k]["decided_at"], first[k]["checkpoint_id"])):
            first[k] = r
    outcome = {r["checkpoint_id"]: r for r in read("fact_checkpoint_outcome", root, only)}
    rd1 = {}
    for r in read("s10_shadow_checkpoints", root, only):
        k = (r["city_key"], r["target_date"], r["checkpoint"])
        if r["model_version"] == RD1 and (k not in rd1 or r["decided_at"] < rd1[k]["decided_at"]):
            rd1[k] = r
    floor = {(r["city_key"], r["target_date"], r["checkpoint"]): r for r in shadow.values()
             if r["variant_version"] == "da_floor:v1"}
    out, left = [], collections.Counter()
    for r in shadow.values():
        if r["variant_version"] != CANDIDATE:
            continue
        k = (r["city_key"], r["target_date"], r["checkpoint"])
        served = first.get(k)
        f = outcome.get(served["checkpoint_id"]) if served else None
        if not r.get("corrected_centre_c"):
            left["no live corrected row"] += 1
        elif not r.get("corrected_width_c"):
            left["live row has no width"] += 1
        elif r.get("served_calibrated") in TRUE:
            left["served ladder calibrated"] += 1
        elif served is None:
            left["no served first call"] += 1
        elif f is None or not f.get("winner_band_id") or not f.get("settled_at"):
            left["no graded outcome yet"] += 1
        elif f.get("ladder_has_winner") not in TRUE:
            left["winner not on the ladder"] += 1
        else:
            win = f["winner_band_id"]
            c, s = ladder(r["probs"]), ladder(served["probs"])
            if set(c) != set(s):
                left["ladders differ in buckets"] += 1
                continue
            row = {"date": r["target_date"], "checkpoint": r["checkpoint"], "city": r["city_key"],
                   "engine_version": served["engine_version"], "model_path": served.get("model_path") or ""}
            row["ll_c"], row["top_c"], row["br_c"] = score(c, win)
            row["ll_s"], row["top_s"], row["br_s"] = score(s, win)
            d = floor.get(k)
            dl = ladder(d["probs"]) if d else None
            row["ll_d"] = score(dl, win)[0] if dl and win in dl else None
            whole = f.get("market_complete") in TRUE and f.get("log_loss_market")
            row["ll_m"] = float(f["log_loss_market"]) if whole else None
            row["top_m"] = int(f.get("market_hit") in TRUE) if whole else None
            q = rd1.get(k)
            row["ll_r"] = score(ladder(q["probs"]), win)[0] if q and q.get("probs") else None
            row["top_r"] = int(q["top_band_id"] == win) if q and q.get("probs") else None
            out.append(row)
    return out, left, max(r["target_date"] for r in shadow.values() if r["variant_version"] == CANDIDATE)


def pairs(rs, key):
    return [(r["date"], v) for r in rs if (v := key(r)) is not None]


def boot(p):
    return F.cluster_boot(p) if p else None


def block(rs):
    n = len(rs)
    ks, kc = sum(r["top_s"] for r in rs), sum(r["top_c"] for r in rs)
    m = [r for r in rs if r["ll_m"] is not None]
    q = [r for r in rs if r["ll_r"] is not None]
    return {
        "rows": n, "dates": len({r["date"] for r in rs}), "cities": len({r["city"] for r in rs}),
        "log_loss_gain_served_minus_sd_corr": F.cluster_boot(pairs(rs, lambda r: r["ll_s"] - r["ll_c"])),
        "brier_gain_served_minus_sd_corr": F.cluster_boot(pairs(rs, lambda r: r["br_s"] - r["br_c"])),
        "top1_served": {"k": ks, "rate": round(ks / n, 4), "wilson95": F.wilson(ks, n)},
        "top1_sd_corr": {"k": kc, "rate": round(kc / n, 4), "wilson95": F.wilson(kc, n)},
        "da_floor_minus_sd_corr": boot(pairs(rs, lambda r: None if r["ll_d"] is None else r["ll_d"] - r["ll_c"])),
        "market_descriptive": {
            "rows_book_whole": len(m), "top1_market": round(sum(r["top_m"] for r in m) / len(m), 4) if m else None,
            "log_loss_served_minus_market": boot(pairs(m, lambda r: r["ll_s"] - r["ll_m"])),
            "log_loss_sd_corr_minus_market": boot(pairs(m, lambda r: r["ll_c"] - r["ll_m"]))},
        "rd1_descriptive": {
            "rows_with_rd1": len(q), "top1_rd1": round(sum(r["top_r"] for r in q) / len(q), 4) if q else None,
            "log_loss_served_minus_rd1": boot(pairs(q, lambda r: r["ll_s"] - r["ll_r"])),
            "log_loss_sd_corr_minus_rd1": boot(pairs(q, lambda r: r["ll_c"] - r["ll_r"]))},
    }


def verdict(pooled, per_checkpoint):
    """The acceptance rule of docs/SD_CORR_PREREG.md (rule 3 is per checkpoint)."""
    gain = pooled["log_loss_gain_served_minus_sd_corr"]["ci90"]
    rules = {
        "1_pooled_ci90_above_0_over_20_dates": gain[0] > 0 and pooled["dates"] >= MIN_DATES,
        "2_no_checkpoint_ci90_wholly_below_0": not any(
            b["log_loss_gain_served_minus_sd_corr"]["ci90"][1] < 0 for b in per_checkpoint.values()),
        "3_no_checkpoint_sd_corr_top1_below_served_wilson95": not any(
            b["top1_sd_corr"]["rate"] < b["top1_served"]["wilson95"][0] for b in per_checkpoint.values()),
        "pooled_ci90_wholly_below_0": gain[1] < 0,
    }
    if all(rules[k] for k in list(rules)[:3]):
        rules["verdict"] = "accepted"
    elif rules["pooled_ci90_wholly_below_0"]:
        rules["verdict"] = "rejected"
    else:
        rules["verdict"] = "insufficient evidence"
    return rules


def look(rs):
    per = {c: block([r for r in rs if r["checkpoint"] == c]) for c in CHECKPOINTS
           if any(r["checkpoint"] == c for r in rs)}
    pooled = block(rs)
    return {"pooled": pooled, "per_checkpoint": per, "rule": verdict(pooled, per)}


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    rs, left, newest = rows()
    paths = {"engine_versions": len({r["engine_version"] for r in rs}),
             "model_path": dict(collections.Counter(r["model_path"] or "-" for r in rs))}
    out = {"contract": "docs/SD_CORR_PREREG.md", "scored_under": F.CONTRACT,
           "bootstrap": {"seed": F.SEED, "resamples": F.BOOT},
           "sources": {os.path.relpath(p, ROOT): sha256(p) for t in TABLES for p in files(t)},
           "candidate_rows_newest_target_date": newest, "left_out": dict(left), "served": paths,
           **look(rs)}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "result.json"), "w") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("left_out", "served", "rule")}, indent=1))
    return out


if __name__ == "__main__":
    main()
