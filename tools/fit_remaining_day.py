"""Fit the remaining-day model (scripts/remaining_day.py, plan v2 P7.2 stage 1)
for every local decision hour it serves (remaining_day.HOURS) and write the
parameters the tick's shadow step reads (scripts/s10_shadow.py, P7.4 part 1).

    python tools/fit_remaining_day.py \
        data/training/previous_runs/best_match_hourly_day1_utc.csv.gz \
        data/replay/inputs_2026-09-26/obs_utc.csv.gz \
        data/replay/inputs_2026-09-26/labels_whole.json.gz \
        data/replay/inputs_2026-09-26/units.json data/replay/inputs_2026-09-26/tz.json \
        data/training/previous_runs/models_daily.csv.gz \
        --out data/models/remaining_day/current.json

The same loaders as the walk-forward (tools/experiments_p72_stage1.py), so the
fitted model is the one that was measured. Labels must be whole days only.
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    for name in ("fc", "obs", "labels", "units", "tz", "models"):
        ap.add_argument(name)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    fc, obs, Y, unit, spread, med = E.load(a.fc, a.obs, _labels_path(a.labels), a.units, a.tz, a.models)
    rows_by_hour = {h: E.rows_for(h, fc, obs, Y, spread, med) for h in rd.HOURS}
    params, version = rd.fit(rows_by_hour)
    blob = json.loads(rd.to_json(params, version))
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
