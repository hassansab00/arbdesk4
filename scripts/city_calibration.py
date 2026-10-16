"""Uncertainty for the station-model blend, learned and checked per city.

The temperature model is frozen before the calibration window. Widths fit
the first part of that window and are judged on its later dates, never the
dates used to fit them. The comparator learns the P3.9 residual width on the
same earlier dates, then both widths surround the SAME blended centre.

After the recipe passes a city's date-block CRPS check, its live width is
refitted on the completed window, with station_correction's existing prior,
bounds and nightly step. This is recipe validation, not a forward score of
that final refit. Recorded checkpoints remain the forward evidence.
"""
import collections
import datetime as dt
import hashlib
import json
import math
import random

import station_correction as sc
from station_width_score import crps_normal

VALIDATION_DAYS = 14
MIN_VALIDATION_DAYS = 10
BOOT = 1000
SEED = 17


def _width(rows, field, lead, previous=None):
    errors = collections.defaultdict(list)
    for r in rows:
        errors[(lead, r["city"])].append(r["y"] - r[field])
    return sc.fit_width(errors, previous)


def fit(rows, lead, as_of, previous=None):
    """{city: JSON evidence and bounded width}. rows are held-out predictions
    with city, date, y, p39, blend; every date is before as_of. previous is
    {city: the calibration in force BEFORE tonight}, including held widths.
    """
    previous = previous or {}
    rows = [r for r in rows if r["date"] < as_of]
    if len({(r["city"], r["date"]) for r in rows}) != len(rows):
        raise ValueError("duplicate city/date calibration prediction")
    if any(not all(math.isfinite(r[k]) for k in ("y", "p39", "blend")) for r in rows):
        raise ValueError("non-finite calibration prediction")
    split = as_of - dt.timedelta(days=VALIDATION_DAYS)
    train = [r for r in rows if r["date"] < split]
    valid = [r for r in rows if r["date"] >= split]
    candidate = _width(train, "blend", lead)
    baseline = _width(train, "p39", lead)
    anchors = {(lead, c): float(v["sigma_c"]) for c, v in previous.items() if v.get("sigma_c") is not None}
    final = _width(rows, "blend", lead, anchors)
    by_city = collections.defaultdict(list)
    for r in valid:
        by_city[r["city"]].append(r)
    out = {}
    for city in sorted({r["city"] for r in rows}):
        mine = sorted(by_city[city], key=lambda r: r["date"])
        n_fit = sum(r["city"] == city for r in train)
        cw = candidate["cells"].get((lead, city))
        bw = baseline["cells"].get((lead, city))
        fw = final["cells"].get((lead, city))
        evidence = {"as_of": as_of.isoformat(), "city_key": city, "lead_days": lead, "applies": False,
                    "n_fit": n_fit, "n_validation": len(mine),
                    "validation_from": split.isoformat(),
                    "validation_to": (as_of - dt.timedelta(days=1)).isoformat(),
                    "evidence": "chronological_recipe_validation",
                    "truth": "venue_truth",
                    "temperature_fit_before": min((r["date"] for r in rows), default=as_of).isoformat(),
                    "sigma_c": round(fw[0], 4) if fw else None,
                    "n": fw[2] if fw else 0,
                    "prior": "city residual MAE shrunk to the lead pool",
                    "bounds_c": [sc.WIDTH_LO_C, sc.WIDTH_HI_C],
                    "max_nightly_fraction": sc.WIDTH_MAX_STEP}
        evidence["priors"] = {"min_city_errors": sc.MIN_N, "min_pool_errors": sc.WIDTH_MIN_POOL,
                              "shrinkage_days": sc.WIDTH_K, "validation_days": VALIDATION_DAYS,
                              "min_validation_days": MIN_VALIDATION_DAYS, "bootstrap": BOOT, "seed": SEED}
        if cw is None or bw is None or fw is None or n_fit < sc.MIN_N:
            evidence["reason"] = "insufficient earlier calibration errors"
        elif len({r["date"] for r in mine}) < MIN_VALIDATION_DAYS:
            evidence["reason"] = "insufficient later validation dates"
        else:
            # One city: one error per date. Keep dates as the sampling unit.
            by_date = collections.defaultdict(list)
            for r in mine:
                by_date[r["date"]].append(crps_normal(r["y"], r["blend"], bw[0]) -
                                           crps_normal(r["y"], r["blend"], cw[0]))
            gains = [sum(v) / len(v) for _, v in sorted(by_date.items())]
            rng = random.Random(SEED)
            draws = sorted(sum(rng.choice(gains) for _ in gains) / len(gains) for _ in range(BOOT))
            lo, hi = draws[int(.05 * BOOT)], draws[int(.95 * BOOT) - 1]
            evidence.update(crps_gain_c=round(sum(gains) / len(gains), 6),
                            crps_gain_90=[round(lo, 6), round(hi, 6)],
                            validation_sigma_c=round(cw[0], 4),
                            baseline_sigma_c=round(bw[0], 4),
                            applies=lo > 0,
                            reason="later-date CRPS improves" if lo > 0 else "later-date improvement unproven")
        payload = json.dumps({"lead": lead, "city": city, **evidence}, sort_keys=True)
        evidence["version"] = f"city-blend-width:{as_of}:{hashlib.sha256(payload.encode()).hexdigest()[:12]}"
        out[city] = evidence
    return out
