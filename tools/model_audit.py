#!/usr/bin/env python3
"""The served prediction model, audited on settled outcomes (15 Nov 2026).

Reads only the nightly mirror in the repository (data/mirror), so every number
is rebuilt from committed files. Nothing here prices or trades.

What is scored: the FIRST call of each city, target date and checkpoint
(prediction_checkpoints by decided_at, checkpoint_id: v_checkpoint_calls'
first_call), graded by fact_checkpoint_outcome, the winner on the ladder. The
market's figures are on the rows whose book was whole (market_complete).

  by_checkpoint   top pick right, log loss, the market beside it, by period
  per_city        the same per city from --since, with the centre's bias and
                  mean absolute error where the call has a centre (the station
                  maximum is fact_forecast_outcome's observed_max_c)
  reliability     the top pick's stated probability against how often it won
  same_day_path   sd_corr:v1's forward rows (tools/p11_sd_corr_look.py) by the
                  path that served the call, and the morning gain on the dates
                  of its first look and on the dates after it

    python tools/model_audit.py --out data/eval/model_audit_2026-11-15
"""
import argparse
import collections
import csv
import glob
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import fec_same_day as F  # noqa: E402
import p11_sd_corr_look as L  # noqa: E402

csv.field_size_limit(sys.maxsize)
CHECKPOINTS = ("d1_eve", "morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
PERIODS = (("2026-09-24", "2026-10-16"), ("2026-10-17", "2026-11-14"))
SINCE = "2026-10-17"          # wxrd3 serves noon and later from this date (#409)
FIRST_LOOK_LAST = "2026-10-25"
BINS = (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)
TRUE = L.TRUE
KEEP = ("checkpoint_id", "city_key", "target_date", "checkpoint", "decided_at", "model_path", "centre_c", "top_prob")


def read(table, root=ROOT):
    for path in sorted(glob.glob(os.path.join(root, "data", "mirror", table, "*.csv.gz"))):
        with gzip.open(path, "rt", newline="") as fh:
            yield from csv.DictReader(fh)


def scored(root=ROOT):
    """The first calls with a graded outcome and the winner on the ladder."""
    first = {}
    for r in read("prediction_checkpoints", root):
        k = (r["city_key"], r["target_date"], r["checkpoint"])
        if r["checkpoint"] in CHECKPOINTS and (k not in first or (r["decided_at"], r["checkpoint_id"])
                                               < (first[k]["decided_at"], first[k]["checkpoint_id"])):
            first[k] = {c: r.get(c, "") for c in KEEP}
    outcome = {r["checkpoint_id"]: r for r in read("fact_checkpoint_outcome", root)}
    out = []
    for r in first.values():
        f = outcome.get(r["checkpoint_id"])
        if not f or not f["settled_at"] or not f["winner_band_id"] or f["ladder_has_winner"] not in TRUE:
            continue
        whole = f["market_complete"] in TRUE and f["log_loss_market"] not in ("", None)
        out.append(dict(r, hit=int(f["hit"] in TRUE), ll=float(f["log_loss_model"]),
                        hit_m=int(f["market_hit"] in TRUE) if whole else None,
                        ll_m=float(f["log_loss_market"]) if whole else None))
    return out


def observed(root=ROOT):
    return {(r["city_key"], r["for_date"]): float(r["observed_max_c"])
            for r in read("fact_forecast_outcome", root) if r["observed_max_c"]}


def block(rs, obs=None):
    n = len(rs)
    if not n:
        return {"n": 0}
    m = [r for r in rs if r["ll_m"] is not None]
    out = {"n": n, "hit": round(sum(r["hit"] for r in rs) / n, 4), "log_loss": round(sum(r["ll"] for r in rs) / n, 4),
           "market_n": len(m),
           "market_hit": round(sum(r["hit_m"] for r in m) / len(m), 4) if m else None,
           "market_log_loss": round(sum(r["ll_m"] for r in m) / len(m), 4) if m else None}
    if obs is not None:
        e = [obs[(r["city_key"], r["target_date"])] - float(r["centre_c"]) for r in rs
             if r["centre_c"] and (r["city_key"], r["target_date"]) in obs]
        out["centre_n"] = len(e)
        out["centre_bias_c"] = round(sum(e) / len(e), 3) if e else None
        out["centre_mae_c"] = round(sum(abs(x) for x in e) / len(e), 3) if e else None
    return out


def reliability(rs):
    out = []
    for lo, hi in zip(BINS, BINS[1:]):
        b = [r for r in rs if r["top_prob"] and lo <= float(r["top_prob"]) < hi]
        if b:
            out.append({"from": lo, "n": len(b), "stated": round(sum(float(r["top_prob"]) for r in b) / len(b), 4),
                        "won": round(sum(r["hit"] for r in b) / len(b), 4)})
    return out


def gain(rs):
    g = F.cluster_boot([(r["date"], r["ll_s"] - r["ll_c"]) for r in rs])
    n = len(rs)
    return {"n": n, "dates": g["dates"], "log_loss_gain_served_minus_corrected": g["mean"], "ci90": g["ci90"],
            "top1_served": round(sum(r["top_s"] for r in rs) / n, 4),
            "top1_corrected": round(sum(r["top_c"] for r in rs) / n, 4)}


def same_day_path(root=ROOT):
    rs, _left, _newest = L.rows(root)
    by = {}
    for path in ("forecast", "wxpredict"):
        for cp in L.CHECKPOINTS:
            x = [r for r in rs if r["checkpoint"] == cp and r["model_path"] == path]
            if len(x) >= 50:
                by[f"{path}:{cp}"] = gain(x)
    morning = [r for r in rs if r["checkpoint"] == "morning"]
    return {"served_by": by,
            "morning_first_look_dates": gain([r for r in morning if r["date"] <= FIRST_LOOK_LAST]),
            "morning_later_dates": gain([r for r in morning if r["date"] > FIRST_LOOK_LAST])}


def audit(root=ROOT, since=SINCE):
    rs, obs = scored(root), observed(root)
    recent = [r for r in rs if r["target_date"] >= since]
    cities = sorted({r["city_key"] for r in recent})
    wx = ("noon", "prepeak_2h", "prepeak_1h")
    return {
        "rows": len(rs), "first_date": min(r["target_date"] for r in rs), "last_date": max(r["target_date"] for r in rs),
        "by_checkpoint": {cp: {f"{lo}..{hi}": block([r for r in rs if r["checkpoint"] == cp and lo <= r["target_date"] <= hi])
                               for lo, hi in PERIODS} for cp in CHECKPOINTS},
        "per_city_since": since,
        "per_city": {c: {"d1_eve": block([r for r in recent if r["city_key"] == c and r["checkpoint"] == "d1_eve"], obs),
                         "morning": block([r for r in recent if r["city_key"] == c and r["checkpoint"] == "morning"], obs),
                         "noon_to_prepeak_1h": block([r for r in recent if r["city_key"] == c and r["checkpoint"] in wx]),
                         "postpeak_1h": block([r for r in recent if r["city_key"] == c and r["checkpoint"] == "postpeak_1h"])}
                     for c in cities},
        "reliability_top_pick_since": {"d1_eve": reliability([r for r in recent if r["checkpoint"] == "d1_eve"]),
                                       "morning": reliability([r for r in recent if r["checkpoint"] == "morning"]),
                                       "noon_to_prepeak_1h": reliability([r for r in recent if r["checkpoint"] in wx]),
                                       "postpeak_1h": reliability([r for r in recent if r["checkpoint"] == "postpeak_1h"])},
        "same_day_path": same_day_path(root),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    out = audit()
    out["mirror_files"] = {t: {"files": len(names), "newest": max(n[-17:-7] for n in names)}
                           for t in ("prediction_checkpoints", "fact_checkpoint_outcome", "fact_forecast_outcome",
                                     "variant_shadow_checkpoints")
                           for names in [glob.glob(os.path.join(ROOT, "data", "mirror", t, "*.csv.gz"))]}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "served.json"), "w") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"by_checkpoint": out["by_checkpoint"], "same_day_path": out["same_day_path"]}, indent=1))
    return out


if __name__ == "__main__":
    main()
