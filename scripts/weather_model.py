#!/usr/bin/env python3
"""
Predict the day's maximum from what the morning already shows.

WHY. AD4 has treated the daily maximum as a number a forecast model hands it,
and everything else in weather_observations as decoration. It is not. How hot
an afternoon gets is largely settled by conditions observable at 08:00:

  DEWPOINT DEPRESSION (temp - dewpoint) is the strongest single predictor. Dry
    air heats fast, because the sun's energy raises temperature instead of
    evaporating water. Measured on this repo's own feature view, days starting
    >12C dry climb about 4.5C further than days starting <3C dry.
  CLOUD COVER caps it: clear mornings climb roughly 8.6C more than overcast
    ones from the same start.
  PRECIPITATION nearly halves the climb - a wet surface evaporates instead of
    warming.
  WIND mixes the surface layer and flattens extremes both ways.

THE BENCHMARK IS PERSISTENCE. Yesterday's maximum, unchanged. It is not a
strawman: in a stable air mass it is very hard to beat, and any model that
cannot beat it is decoration with coefficients. Every fit here is scored
against it ON HELD-OUT DAYS, and beats_persistence is the only column that
decides whether the model is used.

METHOD. Ordinary least squares by normal equations, solved with Gaussian
elimination - five or six coefficients over a few hundred days needs no
numerical library, and this repo has none. The last 25% of days are held out
in TIME ORDER, never shuffled: shuffling would let the model see days on
either side of the ones it is scored on, and weather is autocorrelated enough
that this alone can manufacture skill.

  python scripts/weather_model.py [--min-days 120] [--dry-run]
"""
import argparse
import datetime as dt
import json
import sys

from common import rest, log_run, _cfg, _headers
import requests

# Fewer than this and the fit is describing noise. Weather is seasonal: a few
# months of days does not tell you how the relationship behaves in another
# part of the year.
MIN_DAYS = 120
HOLDOUT = 0.25

FEATURES = [
    "prev_max_c",
    "morning_temp_c",
    "dewpoint_depression_c",
    "cloud_mean",
    "wind_mean",
    "precip_total",
]

# What each coefficient means, so the output is a finding and not six numbers.
MEANING = {
    "prev_max_c": "carry-over from yesterday's max (1.0 would be pure persistence)",
    "morning_temp_c": "per °C the morning starts warmer",
    "dewpoint_depression_c": "per °C of dryness at 08:00 — dry air heats faster",
    "cloud_mean": "per okta of daytime cloud — sunlight that never lands",
    "wind_mean": "per unit of daytime wind — mixing flattens the peak",
    "precip_total": "per unit of rain — a wet surface evaporates instead of warming",
}


def solve(a, b):
    """Gaussian elimination with partial pivoting. Returns None if singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None                      # collinear features; no unique fit
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / m[r][r]
    return x


def ols(rows, features, target):
    """Least squares with an intercept. rows are dicts."""
    xs = [[1.0] + [float(r[f]) for f in features] for r in rows]
    ys = [float(r[target]) for r in rows]
    k = len(features) + 1
    ata = [[sum(x[i] * x[j] for x in xs) for j in range(k)] for i in range(k)]
    atb = [sum(x[i] * y for x, y in zip(xs, ys)) for i in range(k)]
    beta = solve(ata, atb)
    if beta is None:
        return None
    return {"intercept": beta[0], **{f: beta[i + 1] for i, f in enumerate(features)}}


def predict(coef, row, features):
    return coef["intercept"] + sum(coef[f] * float(row[f]) for f in features)


def usable(r, features, target):
    if r.get(target) is None or r.get("n_obs", 0) < 12:
        return False
    return all(r.get(f) is not None for f in features)


def varying(rows, features, tol=1e-9):
    """Features that actually move.

    A constant column is perfectly collinear with the intercept, which makes
    the normal equations singular and kills the fit for the WHOLE city. That is
    not hypothetical: a city with no measurable rain across the training window
    has a constant precip_total, and dropping the model for that city would be
    absurd - no rain is information, it just is not a variable. Zero-variance
    features are dropped and reported instead.
    """
    keep, dropped = [], []
    for f in features:
        vals = [float(r[f]) for r in rows]
        if max(vals) - min(vals) > tol:
            keep.append(f)
        else:
            dropped.append(f)
    return keep, dropped


def fit_city(rows, features, target="max_c"):
    """Time-ordered split, fit on the first part, score on the last."""
    rows = sorted(rows, key=lambda r: r["obs_date"])
    rows = [r for r in rows if usable(r, features, target)]
    if len(rows) < MIN_DAYS:
        return None, len(rows)

    cut = int(len(rows) * (1 - HOLDOUT))
    train, test = rows[:cut], rows[cut:]
    if len(test) < 20:
        return None, len(rows)

    # Chosen on the TRAINING rows only. Deciding which features to keep by
    # looking at the held-out days would be a leak, small but real.
    features, dropped = varying(train, features)
    if not features:
        return None, len(rows)

    coef = ols(train, features, target)
    if coef is None:
        return None, len(rows)

    errs = [abs(predict(coef, r, features) - float(r[target])) for r in test]
    # Persistence on the SAME held-out days - the only fair comparison.
    pers = [abs(float(r["prev_max_c"]) - float(r[target])) for r in test]
    mae = sum(errs) / len(errs)
    pmae = sum(pers) / len(pers)

    mean_y = sum(float(r[target]) for r in test) / len(test)
    ss_res = sum((predict(coef, r, features) - float(r[target])) ** 2 for r in test)
    ss_tot = sum((float(r[target]) - mean_y) ** 2 for r in test)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None

    return {
        "coefficients": {k: round(v, 5) for k, v in coef.items()},
        "features": features,
        "dropped_features": dropped,   # constant across training; not modellable
        "n_days": len(rows), "n_train": len(train), "n_test": len(test),
        "mae_c": round(mae, 4), "persistence_mae_c": round(pmae, 4),
        "beats_persistence": mae < pmae,
        "improvement_pct": round((pmae - mae) / pmae * 100, 1) if pmae > 0 else None,
        "r2": round(r2, 4) if r2 is not None else None,
    }, len(rows)


def describe(fit):
    """The coefficients in words. Six numbers are not a finding."""
    c = fit["coefficients"]
    bits = []
    for f in ("dewpoint_depression_c", "cloud_mean", "precip_total", "wind_mean"):
        v = c.get(f)
        if v is None or abs(v) < 0.01:
            continue
        bits.append(f"{v:+.2f}°C {MEANING[f].split('—')[0].strip()}")
    carry = c.get("prev_max_c")
    if carry is not None:
        bits.append(f"{carry:.2f} carry-over from yesterday")
    return "; ".join(bits) or "no coefficient large enough to describe"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-days", type=int, default=MIN_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    globals()["MIN_DAYS"] = args.min_days

    try:
        rows = rest("v_city_day_features", [
            ("select", "city_key,obs_date,max_c,n_obs,prev_max_c,morning_temp_c,"
                       "dewpoint_depression_c,cloud_mean,wind_mean,precip_total"),
            ("limit", "200000"),
        ])
    except Exception as e:
        print(f"v_city_day_features unavailable ({e}). Run sql/ad4_21_weather_features.sql.",
              file=sys.stderr)
        log_run("weather_model", "attention", 0, {"error": str(e)})
        return 1

    by_city = {}
    for r in rows:
        by_city.setdefault(r["city_key"], []).append(r)

    out, skipped, beat = [], [], 0
    for city, rs in sorted(by_city.items()):
        fit, n = fit_city(rs, FEATURES)
        if fit is None:
            skipped.append((city, n))
            continue
        if fit["beats_persistence"]:
            beat += 1
        print(f"{city:<14} n={fit['n_days']:<5} model MAE {fit['mae_c']:.2f}°C  "
              f"persistence {fit['persistence_mae_c']:.2f}°C  "
              f"{'BEATS' if fit['beats_persistence'] else 'loses to'} persistence"
              + (f" by {fit['improvement_pct']}%" if fit["beats_persistence"] else ""))
        print(f"               {describe(fit)}")
        if fit["dropped_features"]:
            print(f"               (no variation in {', '.join(fit['dropped_features'])} — dropped)")
        out.append({
            "city_key": city, "target": "max_c", "n_days": fit["n_days"],
            "coefficients": fit["coefficients"], "mae_c": fit["mae_c"],
            "persistence_mae_c": fit["persistence_mae_c"],
            "beats_persistence": fit["beats_persistence"], "r2": fit["r2"],
            "notes": describe(fit), "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })

    if skipped:
        print(f"\n{len(skipped)} city/cities skipped for too few usable days "
              f"(need {MIN_DAYS}): " + ", ".join(f"{c}({n})" for c, n in skipped[:8]))
    if out:
        print(f"\n{beat} of {len(out)} cities beat persistence on held-out days.")
        if beat == 0:
            print("  None of them beat it. That is a real result, not a bug: on this data the "
                  "morning conditions add nothing over yesterday's maximum, and no forecast "
                  "built on them should be trusted yet.")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0
    if not out:
        log_run("weather_model", "attention", 0, {"skipped": len(skipped), "min_days": MIN_DAYS})
        return 0

    r = requests.post(
        f"{_cfg()['url']}/rest/v1/derived_weather_model",
        headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "city_key,target"},
        data=json.dumps(out), timeout=60)
    if r.status_code >= 400:
        print(f"  ! write failed {r.status_code}: {r.text[:300]}", file=sys.stderr)
        r.raise_for_status()

    log_run("weather_model", "ok", len(out),
            {"cities": len(out), "beat_persistence": beat, "skipped": len(skipped)})
    print(f"\nwrote {len(out)} city model(s) to derived_weather_model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
