#!/usr/bin/env python3
"""da_floor:v1's looks, exactly as docs/P11_DA_FLOOR_PREREG.md fixes them.

Reads one look's aggregates (one line per target date and checkpoint, made by
the look's query.sql, read-only against production) and applies the
pre-registered rule: the pooled and per-checkpoint log-loss gain (served minus
da_floor) with tools/fec_same_day.cluster_boot (dates resampled, seed 11, 1,000
resamples, 90% for the rule and 95% beside it), top-1 with Wilson 95%
intervals, Brier secondary, and the market and S10 rd1 beside them,
descriptive. The bootstrap needs only each date's sum and count, so the
aggregates reproduce it exactly.

    python tools/p11_da_floor_look.py data/eval/p11/da_floor_first_look_2026-10-25
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fec_same_day as F  # noqa: E402

CHECKPOINTS = ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
MIN_DATES = 20
COLUMNS = ("date", "checkpoint", "n", "s_ll", "k_served", "k_da_floor", "s_brier",
           "n_market", "s_ll_served_market", "s_ll_da_floor_market", "k_market",
           "n_rd1", "s_ll_served_rd1", "s_ll_da_floor_rd1", "k_rd1")


def read(path):
    rows = []
    for line in open(path).read().strip().split("\n"):
        v = line.split("|")
        r = dict(zip(COLUMNS, v))
        for k in COLUMNS[2:]:
            r[k] = float(r[k]) if k.startswith("s_") else int(r[k])
        rows.append(r)
    return rows


def pairs(rows, n_key, s_key):
    """cluster_boot's (date, value) pairs: each line's n rows carry its mean,
    so every date's sum and count are the rows' own."""
    out = []
    for r in rows:
        if r[n_key]:
            out += [(r["date"], r[s_key] / r[n_key])] * r[n_key]
    return out


def block(rows):
    n = sum(r["n"] for r in rows)
    ks, kd = sum(r["k_served"] for r in rows), sum(r["k_da_floor"] for r in rows)
    nm, km = sum(r["n_market"] for r in rows), sum(r["k_market"] for r in rows)
    nr, kr = sum(r["n_rd1"] for r in rows), sum(r["k_rd1"] for r in rows)
    return {
        "rows": n, "dates": len({r["date"] for r in rows}),
        "log_loss_gain_served_minus_da_floor": F.cluster_boot(pairs(rows, "n", "s_ll")),
        "brier_gain_served_minus_da_floor": F.cluster_boot(pairs(rows, "n", "s_brier")),
        "top1_served": {"k": ks, "rate": round(ks / n, 4), "wilson95": F.wilson(ks, n)},
        "top1_da_floor": {"k": kd, "rate": round(kd / n, 4), "wilson95": F.wilson(kd, n)},
        "market_descriptive": {
            "rows_book_whole": nm, "top1_market": round(km / nm, 4) if nm else None,
            "log_loss_served_minus_market": F.cluster_boot(pairs(rows, "n_market", "s_ll_served_market")),
            "log_loss_da_floor_minus_market": F.cluster_boot(pairs(rows, "n_market", "s_ll_da_floor_market"))},
        "rd1_descriptive": {
            "rows_with_rd1": nr, "top1_rd1": round(kr / nr, 4) if nr else None,
            "log_loss_served_minus_rd1": F.cluster_boot(pairs(rows, "n_rd1", "s_ll_served_rd1")),
            "log_loss_da_floor_minus_rd1": F.cluster_boot(pairs(rows, "n_rd1", "s_ll_da_floor_rd1"))},
    }


def verdict(pooled, per_checkpoint):
    """The acceptance rule. Rule 3 reads 'not lower than the served top-1
    beyond the Wilson 95% interval' as: da_floor's top-1 rate is at or above
    the lower end of the served top-1's Wilson 95% interval."""
    gain = pooled["log_loss_gain_served_minus_da_floor"]["ci90"]
    rules = {
        "1_pooled_ci90_above_0_over_20_dates": gain[0] > 0 and pooled["dates"] >= MIN_DATES,
        "2_no_checkpoint_ci90_wholly_below_0": not any(
            per_checkpoint[c]["log_loss_gain_served_minus_da_floor"]["ci90"][1] < 0 for c in per_checkpoint),
        "3_da_floor_top1_not_below_served_wilson95":
            pooled["top1_da_floor"]["rate"] >= pooled["top1_served"]["wilson95"][0],
        "pooled_ci90_wholly_below_0": gain[1] < 0,
    }
    if all(rules[k] for k in list(rules)[:3]):
        rules["verdict"] = "accepted"
    elif rules["pooled_ci90_wholly_below_0"]:
        rules["verdict"] = "rejected"
    else:
        rules["verdict"] = "insufficient evidence"
    return rules


def look(rows):
    per = {c: block([r for r in rows if r["checkpoint"] == c]) for c in CHECKPOINTS
           if any(r["checkpoint"] == c for r in rows)}
    pooled = block(rows)
    return {"pooled": pooled, "per_checkpoint": per, "rule": verdict(pooled, per)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("look_dir")
    args = ap.parse_args(argv)
    path = os.path.join(args.look_dir, "aggregates.txt")
    raw = open(path, "rb").read()
    out = {"contract": "docs/P11_DA_FLOOR_PREREG.md", "scored_under": F.CONTRACT,
           "aggregates": {"file": "aggregates.txt", "md5": hashlib.md5(raw).hexdigest(), "bytes": len(raw)},
           "bootstrap": {"seed": F.SEED, "resamples": F.BOOT}, **look(read(path))}
    with open(os.path.join(args.look_dir, "result.json"), "w") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out["rule"], indent=1))
    return out


if __name__ == "__main__":
    main()
