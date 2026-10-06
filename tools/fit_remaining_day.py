"""Fit the remaining-day model (scripts/remaining_day.py, plan v2 P7.2 stage 1)
for every local decision hour it serves (remaining_day.HOURS) and write the
parameters the tick's shadow step reads (scripts/s10_shadow.py, P7.4 part 1).

    python tools/fit_remaining_day.py \
        data/training/previous_runs/best_match_hourly_day1_utc.csv.gz \
        data/replay/inputs_2026-09-26/obs_utc.csv.gz \
        data/replay/inputs_2026-09-26/labels_whole_repaired.json.gz \
        data/replay/inputs_2026-09-26/units.json data/replay/inputs_2026-09-26/tz.json \
        data/training/previous_runs/models_daily.csv.gz \
        --out data/models/remaining_day/current.json

Challenger C (rd3, docs/CHALLENGER_C_PREREG.md, accepted 30 Sep as a better
forecast, research only) is fitted the same way with --challenger rd3 and
written beside it for the tick's forward shadow, which never prices from it:

    python tools/fit_remaining_day.py <the same six inputs> --challenger rd3 \
        --out data/models/remaining_day/challenger_rd3.json

rd1's late hours (remaining_day.LATE_HOURS; the P.5 report, question 3) are
fitted beside the served fit, under their own version, leaving current.json
and the version Challenger C's forward comparison selects by untouched:

    python tools/fit_remaining_day.py <the same six inputs> --hours 18 \
        --late-of data/models/remaining_day/current.json \
        --out data/models/remaining_day/late_hours.json

rd3 adds the seven models' lead-1 whole-day maxima (tmax_c, as scored and as
s10_day1_inputs.models stores them), bias-corrected per model and city on every
labelled day (remaining_day.fit_model_bias); the table is in the file and in
its version.

The same loaders as the walk-forward (tools/experiments_p72_stage1.py), so the
fitted model is the one that was measured. Labels must be whole days only.
Since 30 Sep (audit repair 6) the labels are labels_whole_repaired: the 29 Sep
day-features repair raised 140 of the maxima the first fit trained on.
Needs no numpy (remaining_day is pure Python).
"""
import argparse
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import experiments_p72_stage1 as E  # noqa: E402
import remaining_day as rd  # noqa: E402


def _labels_path(path):
    """experiments_p72_stage1.load reads plain JSON; unpack a .gz to a temp file."""
    if not path.endswith(".gz"):
        return path
    import tempfile
    out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    with gzip.open(path, "rt") as f:
        out.write(f.read())
    out.close()
    return out.name


def load_models(path):
    """{(city, date iso): {model: lead-1 whole-day max C}} (models_daily tmax_c)."""
    import csv
    out = {}
    with gzip.open(path, "rt") as f:
        for r in csv.DictReader(f):
            if r["lead_days"] == "1" and r["tmax_c"]:
                out.setdefault((r["city_key"], r["for_date"]), {})[r["model"]] = float(r["tmax_c"])
    return out


def challenger_rows(rows_by_hour, models, Y):
    """(rows_by_hour with rd3's inputs, bias JSON, pooled JSON, pairs used).
    The bias is fitted on every labelled city-day; a row whose day has fewer
    than MIN_MODELS_C models is dropped, as in the evaluation."""
    pairs = [(m, c, v - Y[(c, d)]) for (c, d), ms in models.items() if (c, d) in Y for m, v in ms.items()]
    bias, pooled = rd.fit_model_bias(pairs)
    out = {}
    for h, rows in rows_by_hour.items():
        out[h] = [x for x in (rd.row_c(r, models.get((r["city"], r["date"].isoformat())), r["city"], bias, pooled)
                              for r in rows) if x is not None]
    bias_js, pooled_js = rd.bias_to_json(bias, pooled)
    return out, bias_js, pooled_js, len(pairs)


def late(base_path, rows_by_hour, med):
    """rd1's late hours, beside the served fit and never inside it: the P.5
    report, question 3 (Hassan, 6 Oct: S10 decides at each city's own peak, and
    Madrid's post-peak checkpoint falls at 18:xx local). Fitted from
    `rows_by_hour` the same way as the served hours (remaining_day.fit_hour),
    under a version of their own. current.json is not touched: the hours it
    serves keep their parameters AND their version, which Challenger C's
    pre-registered forward comparison selects rows by (tools/fec_s10_forward.py
    INCUMBENT)."""
    with open(base_path) as f:
        base = json.load(f)
    if not base["version"].startswith(rd.VERSION_PREFIX + ":"):
        raise SystemExit(f"--late-of takes the served rd1 fit, not {base['version']}")
    served = sorted(int(h) for h in base["hours"])
    clash = sorted(set(served) & set(rows_by_hour))
    if clash:
        raise SystemExit(f"hours {clash} are served by {base['version']} already")
    added = {h: p for h, p in ((h, rd.fit_hour(rs)) for h, rs in sorted(rows_by_hour.items())) if p}
    missing = sorted(set(rows_by_hour) - set(added))
    if missing:
        raise SystemExit(f"too few rows to fit hours {missing}")
    # As stored: the version is computed over the parameters the file holds.
    added = {int(h): p for h, p in json.loads(json.dumps({str(h): p for h, p in added.items()},
                                                         default=str)).items()}
    version = rd.version_of(added)
    blob = json.loads(rd.to_json(added, version))
    blob["late_of"] = {"version": base["version"], "hours": served}
    blob["spread_median_c"] = round(med, 4)
    blob["rows_by_hour"] = {str(h): len(r) for h, r in rows_by_hour.items()}
    return blob, version


def main(argv=None):
    ap = argparse.ArgumentParser()
    for name in ("fc", "obs", "labels", "units", "tz", "models"):
        ap.add_argument(name)
    ap.add_argument("--out", required=True)
    ap.add_argument("--challenger", choices=["rd3"], help="fit Challenger C instead of rd1")
    ap.add_argument("--hours", help="comma-separated local hours to fit (default: remaining_day.HOURS)")
    ap.add_argument("--late-of", help="the served rd1 fit; fit --hours beside it as its late hours")
    a = ap.parse_args(argv)
    hours = tuple(int(h) for h in a.hours.split(",")) if a.hours else rd.HOURS
    if a.late_of and (a.challenger or not a.hours):
        raise SystemExit("--late-of fits rd1's late --hours only")
    fc, obs, Y, unit, spread, med = E.load(a.fc, a.obs, _labels_path(a.labels), a.units, a.tz, a.models)
    rows_by_hour = {h: E.rows_for(h, fc, obs, Y, spread, med) for h in hours}
    if a.late_of:
        blob, version = late(a.late_of, rows_by_hour, med)
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump(blob, f, sort_keys=True, separators=(",", ":"))
        print(json.dumps({"version": version, "late_of": blob["late_of"]["version"],
                          "hours": sorted(int(h) for h in blob["hours"]), "rows_by_hour": blob["rows_by_hour"]}))
        return
    extra = {}
    if a.challenger == "rd3":
        rows_by_hour, bias_js, pooled_js, n_pairs = challenger_rows(rows_by_hour, load_models(a.models), Y)
        params = {h: p for h, p in ((h, rd.fit_hour(rs)) for h, rs in sorted(rows_by_hour.items())) if p}
        version = rd.version_of_c(params, bias_js, pooled_js)
        extra = {"features": rd.FEATURES_C, "bias": bias_js, "pooled": pooled_js, "bias_pairs": n_pairs,
                 "models_column": "tmax_c", "min_models": rd.MIN_MODELS_C,
                 "prereg": "docs/CHALLENGER_C_PREREG.md"}
    else:
        params, version = rd.fit(rows_by_hour)
    blob = json.loads(rd.to_json(params, version))
    blob.update(extra)
    blob["spread_median_c"] = round(med, 4)
    blob["rows_by_hour"] = {str(h): len(r) for h, r in rows_by_hour.items()}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(blob, f, sort_keys=True, separators=(",", ":"))
    print(json.dumps({"version": version, "hours": sorted(params),
                      "rows_by_hour": blob["rows_by_hour"],
                      "widen": {h: p["widen"] for h, p in sorted(params.items())},
                      "last_date": max(p["last_date"] for p in params.values())}))


if __name__ == "__main__":
    main()
