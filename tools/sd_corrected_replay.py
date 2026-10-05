"""The same-day station-corrected candidate (`sd_corr`), replayed on design dates.

P1.1 (docs/P11_INTRADAY_DIAGNOSIS_2026-10-04.md) found that at local midnight the
engine drops the station-corrected centre it prices the day before and moves to
the public forecast with a narrower width, and that this costs every checkpoint
before the peak. `da_floor:v1` (docs/P11_DA_FLOOR_PREREG.md) keeps the
day-before call instead. This asks the other question: what if the engine kept
its station-corrected path on the day itself, with what that path has learned?

  sd_corr      the station-corrected combination LIVE at the checkpoint
               (derived_corrected_forecast.combined_c - P3.9 from each model's
               newest run, lead 0 taking lead 1's corrections, as
               station_correction.correction does) with the station width
               stored beside it (width_c: P3.9 part 3, fitted nightly under
               Rule 11 on this combination's own errors, lead 0 taking lead
               1's), cut by the checkpoint's floor. Nothing new is fitted.
  sd_corr_daw  the same centre with the day-ahead call's width   (centre alone)
  dac_sw       the day-ahead centre with the live station width  (width alone)

beside the served ladder and `da_floor`, on the P1.1 rows (identical city-days
and checkpoints, the engine's first capture), with the engine's own integrator
and the q the engine read (tools/p11_intraday_ablation.ladder).

WHAT WAS LIVE. data/mirror/derived_corrected_forecast/<D>.csv.gz is the table
at about 02:40Z on D. The nightly fit rewrites every open day's row at about
05:00Z, so each snapshot holds the rows of the night before. A checkpoint sees
the row with the latest computed_at at or before its decision, if that row is
younger than the engine's max_age_hours (36, settings.station_correction_pricing
on every date here): exactly what the tick would read. The mirror has no
snapshot between two nights, so a row is taken from the snapshot that kept it.

MOS is not part of it: derived_mos_forecast has no lead-0 rows.

Design data only. These dates are the ones P1.1 looked at; whatever this
shows chooses the candidate, it does not prove it (fec-v1).

The mirror keeps one file per night and may not keep them for ever, so the
rows these dates need are frozen into the repository first, and the score
reads only committed inputs:

  python3 tools/sd_corrected_replay.py freeze data/eval/p11/rows_*.json.gz \
      --out data/eval/sd_corr/corrected_rows.json.gz
  python3 tools/sd_corrected_replay.py score data/eval/p11/rows_*.json.gz \
      --corrected data/eval/sd_corr/corrected_rows.json.gz --out data/eval/sd_corr/report.json
"""
import argparse
import csv
import datetime as dt
import glob
import gzip
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import p11_intraday_ablation as p11          # noqa: E402
from fec_same_day import cluster_boot, wilson   # noqa: E402

MAX_AGE_H = 36.0          # settings.station_correction_pricing.max_age_hours, live on every date here
SNAPSHOTS = os.path.join(HERE, "..", "data", "mirror", "derived_corrected_forecast")
ORDER = p11.ORDER
VARIANTS = ["served", "da_floor", "sd_corr", "sd_corr_daw", "dac_sw"]


def _ts(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def mirror_rows(directory, keys):
    """Every distinct computation the mirror kept for the (city, for_date) keys,
    as committed rows (plain JSON, sorted)."""
    out, seen = [], set()
    for p in sorted(glob.glob(os.path.join(directory, "derived_corrected_forecast-*.csv.gz"))):
        with gzip.open(p, "rt") as f:
            for r in csv.DictReader(f):
                key = (r["city_key"], r["for_date"], r["computed_at"])
                if (r["city_key"], r["for_date"]) not in keys or key in seen:
                    continue
                seen.add(key)
                out.append({"city": r["city_key"], "for_date": r["for_date"], "computed_at": r["computed_at"],
                            "lead": int(r["lead_days"]), "centre": float(r["combined_c"]),
                            "width": float(r["width_c"]) if r.get("width_c") else None,
                            "version": r["version"], "width_version": r.get("width_version") or None,
                            "n_sources": int(r["n_sources"])})
    return sorted(out, key=lambda x: (x["city"], x["for_date"], x["computed_at"]))


def corrected_rows(frozen):
    """{(city, for_date): [row, ...]} from the committed rows."""
    out = defaultdict(list)
    for r in frozen:
        out[(r["city"], r["for_date"])].append(dict(r, computed_at=_ts(r["computed_at"])))
    return out


def live_row(rows, decided_at, max_age_h=MAX_AGE_H):
    """The corrected row the tick would have read at `decided_at`, or (None, why)."""
    t = _ts(decided_at)
    before = [r for r in rows if r["computed_at"] <= t]
    if not before:
        return None, "no_row_computed_before"
    r = max(before, key=lambda x: x["computed_at"])
    if (t - r["computed_at"]).total_seconds() / 3600 > max_age_h:
        return None, "row_older_than_max_age"
    return r, None


def candidate_variants(r, live):
    """{variant: ladder} on the row; sd_* only when the live row has what it needs."""
    c, s, fl = p11._f(r["centre"]), p11._f(r["sigma"]), p11._f(r["floor"])
    dc, ds = p11._f(r.get("da_centre")), p11._f(r.get("da_sigma"))
    out = {"served": p11.ladder(r, c, s, fl), "da_floor": p11.ladder(r, dc, ds, fl),
           "sd_corr": p11.ladder(r, live["centre"], live["width"], fl),
           "sd_corr_daw": p11.ladder(r, live["centre"], ds, fl),
           "dac_sw": p11.ladder(r, dc, live["width"], fl)}
    return out


def run(rows, corrected):
    report = {"replay": p11.replay_check(rows), "by_checkpoint": {}, "pooled": {}, "centre_mae": {},
              "live_rows": {}}
    per = defaultdict(list)
    for r in rows:
        per[r["cp"]].append(r)
    pooled = defaultdict(list)                     # variant -> [(date, ll, hit)]
    pooled_diff = defaultdict(list)                # name -> [(date, diff)]
    for cp in ORDER:
        rs = per.get(cp, [])
        left = defaultdict(int)
        used = []
        leads = defaultdict(int)
        for r in rs:
            if r.get("da_centre") is None or r.get("da_sigma") is None:
                left["no_day_ahead_centre_or_width"] += 1
                continue
            live, why = live_row(corrected.get((r["city"], r["date"]), []), r["decided_at"])
            if live is None:
                left[why] += 1
                continue
            if live["width"] is None:
                left["live_row_without_width"] += 1
                continue
            leads[live["lead"]] += 1
            used.append((r, live))
        sc = defaultdict(list)
        mae = defaultdict(list)
        for r, live in used:
            v = candidate_variants(r, live)
            for name in VARIANTS:
                ll, hit = p11.score(v[name], r["winner"])
                sc[name].append((r["date"], ll, hit))
                pooled[name].append((r["date"], ll, hit))
            obs = p11._f(r.get("obs"))
            if obs is not None:
                mae["served"].append((r["date"], abs(p11._f(r["centre"]) - obs)))
                mae["day_ahead"].append((r["date"], abs(p11._f(r["da_centre"]) - obs)))
                mae["sd_corr"].append((r["date"], abs(live["centre"] - obs)))

        def top1(hits):
            k, n = int(sum(hits)), len(hits)
            return {"rate": round(k / n, 4) if n else None, "hits": k, "n": n, "wilson95": wilson(k, n)}

        def diff(a, b):
            return cluster_boot([(x[0], x[1] - y[1]) for x, y in zip(sc[a], sc[b])])

        report["by_checkpoint"][cp] = {
            "rows": len(rs), "scored": len(used), "dates": len({r["date"] for r, _ in used}),
            "cities": len({r["city"] for r, _ in used}), "left_out": dict(left),
            "live_row_lead": {str(k): v for k, v in sorted(leads.items())},
            "variants": {name: {"log_loss": cluster_boot([(d, ll) for d, ll, _ in sc[name]]),
                                "top1": top1([h for _, _, h in sc[name]])} for name in VARIANTS},
            # positive = the first is WORSE (higher log loss)
            "differences_log_loss": {
                "served_minus_sd_corr": diff("served", "sd_corr"),
                "da_floor_minus_sd_corr": diff("da_floor", "sd_corr"),
                "sd_corr_daw_minus_da_floor (centre alone, day-ahead width)": diff("sd_corr_daw", "da_floor"),
                "dac_sw_minus_da_floor (width alone, day-ahead centre)": diff("dac_sw", "da_floor"),
                "sd_corr_minus_sd_corr_daw (station width vs day-ahead width, same-day centre)":
                    diff("sd_corr", "sd_corr_daw"),
            },
        }
        # The market where its book was whole, every variant on those rows (fec-v1 section 6).
        mk = [(i, r["date"], -math.log(max(float(r["mkt_p"]), p11.FLOOR)))
              for i, (r, _live) in enumerate(used) if r.get("mkt_p") is not None]
        if mk:
            report["by_checkpoint"][cp]["market"] = {
                "rows": len(mk), "log_loss": cluster_boot([(d, ll) for _, d, ll in mk]),
                "market_minus_variant_log_loss": {
                    name: cluster_boot([(d, ll - sc[name][i][1]) for i, d, ll in mk]) for name in VARIANTS},
            }
        report["centre_mae"][cp] = {k: cluster_boot(v) for k, v in mae.items()}
        for a, b in (("served", "sd_corr"), ("da_floor", "sd_corr")):
            pooled_diff[f"{a}_minus_{b}"] += [(x[0], x[1] - y[1]) for x, y in zip(sc[a], sc[b])]
    report["pooled"] = {
        "rows": len(pooled["served"]),
        "variants": {name: {"log_loss": cluster_boot([(d, ll) for d, ll, _ in v])} for name, v in pooled.items()},
        "differences_log_loss": {k: cluster_boot(v) for k, v in pooled_diff.items()},
    }
    return report


def load_frozen(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    fz = sub.add_parser("freeze", help="copy the mirror rows these dates need into the repository")
    fz.add_argument("paths", nargs="+")
    fz.add_argument("--snapshots", default=SNAPSHOTS)
    fz.add_argument("--out", required=True)
    sc = sub.add_parser("score", help="score the variants on the committed inputs")
    sc.add_argument("paths", nargs="+")
    sc.add_argument("--corrected", required=True)
    sc.add_argument("--out")
    a = ap.parse_args(argv)
    rows = p11.load(sorted(a.paths))
    if a.cmd == "freeze":
        keys = {(r["city"], r["date"]) for r in rows}
        frozen = mirror_rows(a.snapshots, keys)
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with gzip.GzipFile(a.out, "wb", mtime=0) as f:
            f.write(json.dumps(frozen, sort_keys=True).encode())
        print(f"{len(frozen)} rows for {len(keys)} city-days -> {a.out}")
        return 0
    rep = run(rows, corrected_rows(load_frozen(a.corrected)))
    text = json.dumps(rep, indent=1, sort_keys=True)
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
