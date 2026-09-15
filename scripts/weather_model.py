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

PREDICTING FORWARD. Fitting on observed mornings only ever explains a day
after its morning has happened. sql/ad4_24_nws_gridpoint.sql removed that
limit: weather_forecast_features carries the SAME COLUMN NAMES for days that
have not happened yet, so the fitted coefficients apply to a forecast day with
no translation step. This script fits, then predicts forward in the same run,
and writes both - derived_weather_model (the fit) and derived_model_forecast
(the prediction, with the arithmetic that produced it).

Two cadences, one script. Fitting is WEEKLY - the relationship is seasonal
and a day of new observations cannot move it. Predicting is every few hours,
because that is how often new forecast conditions arrive, so --predict-only
reads the stored coefficients back rather than paying for a fit it would
only reproduce.

  python scripts/weather_model.py [--min-days 120] [--dry-run] [--no-forecast]
  python scripts/weather_model.py --predict-only [--dry-run]
"""
import argparse
import datetime as dt
import json
import sys

from common import rest, rest_all, log_run, _cfg, _headers
import requests

# Fewer than this and the fit is describing noise. Weather is seasonal: a few
# months of days does not tell you how the relationship behaves in another
# part of the year.
MIN_DAYS = 120
HOLDOUT = 0.25

# The features every fit starts from. Proven, and all of them exist on BOTH
# sides - v_city_day_features for what happened, weather_forecast_features for
# what is forecast. A feature present on only one side can be fitted and then
# never applied, and the failure is silent: forecast_city skips any row with a
# missing feature, so the model would just stop predicting.
BASE_FEATURES = [
    "prev_max_c",
    "morning_temp_c",
    "dewpoint_depression_c",
    "cloud_mean",
    "wind_mean",
    "precip_total",
]

# Collected all along and never used. Each is offered to the fit and kept only
# if it earns its place on data the fit has not seen - see select_features.
CANDIDATE_FEATURES = [
    "morning_humidity",
    "cloud_max",
    "wind_max",
]

# NEVER usable, whatever they look like in the table.
#
# diurnal_range_c is max_c - min_c. Using it to predict max_c is asking the
# answer to help predict itself: a model with it in would report a spectacular
# MAE, beat persistence by a mile, and be worth exactly nothing forward, where
# the day's minimum is not yet known either. morning_to_max_c and delta_max_c
# are the same mistake in different clothes.
LEAKY_FEATURES = {
    "max_c", "min_c", "diurnal_range_c", "morning_to_max_c", "delta_max_c",
}

# Kept for callers that want the starting set by its old name.
FEATURES = BASE_FEATURES

# What each coefficient means, so the output is a finding and not six numbers.
MEANING = {
    "prev_max_c": "carry-over from yesterday's max (1.0 would be pure persistence)",
    "morning_temp_c": "per °C the morning starts warmer",
    "dewpoint_depression_c": "per °C of dryness at 08:00 — dry air heats faster",
    "cloud_mean": "per okta of daytime cloud — sunlight that never lands",
    "wind_mean": "per unit of daytime wind — mixing flattens the peak",
    "precip_total": "per unit of rain — a wet surface evaporates instead of warming",
    "morning_humidity": "per % of morning relative humidity — moisture the sun must boil off first",
    "cloud_max": "per okta of the cloudiest daytime hour — one overcast hour at the peak costs the whole day",
    "wind_max": "per unit of the windiest daytime hour — a gust front ends the climb",
}

# Two features are collinear when one is nearly a linear function of the other.
# The normal equations then have no stable solution: the fit still returns
# numbers, but enormous ones of opposite sign that cancel, and they change
# wildly with one more day of data. morning_humidity and dewpoint_depression_c
# are the pair to watch - relative humidity IS a function of temperature and
# dewpoint, so on a city with a narrow temperature range they can be almost the
# same column.
MAX_ABS_CORRELATION = 0.95

# A feature also has to matter, not merely to be real.
#
# The paired test below is easy to pass: adding any variable to a least-squares
# fit nudges the error down slightly and CONSISTENTLY, so the differences are
# tiny but systematically positive, the standard error is tinier still, and the
# test says "clear of chance" for a gain of four thousandths of a degree. It
# did exactly that.
#
# The floor comes from the decision this model feeds. Polymarket's buckets are
# 1C wide, so a feature that moves the forecast by less than about 0.05C cannot
# change which bucket a day lands in - and a feature that cannot change the
# answer has no business being in the model, however real its effect.
MIN_MATERIAL_GAIN_C = 0.05


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


def correlation(rows, a, b):
    """Pearson r between two features, over rows where both are present."""
    xs, ys = [], []
    for r in rows:
        if r.get(a) is None or r.get(b) is None:
            continue
        xs.append(float(r[a]))
        ys.append(float(r[b]))
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / ((sxx ** 0.5) * (syy ** 0.5))


def abs_errors(rows, features, target, coef):
    return [abs(predict(coef, r, features) - float(r[target])) for r in rows]


def mae_on(rows, features, target, coef):
    errs = abs_errors(rows, features, target, coef)
    return sum(errs) / len(errs) if errs else None


def beats(base_errs, trial_errs, k=1.5, floor=None):
    """Is the trial's improvement bigger than chance? A PAIRED test.

    The first version of this asked whether the validation MAE fell by more
    than 0.001C, which is not a threshold - it is noise. With ~75 validation
    days and half a degree of scatter, the standard error on an MAE is around
    0.05C, so a feature made of pure random numbers clears 0.001 most of the
    time. It did: a humidity column with no effect on the answer was selected.

    The same days are scored both ways, so the honest comparison is the
    per-day DIFFERENCE in absolute error. Its mean is the gain; its standard
    error says how much of that gain is luck. Keep the feature only when the
    gain is k standard errors clear of zero.

    Two conditions, and both are needed: the gain must clear chance AND clear
    MIN_MATERIAL_GAIN_C, because a statistically certain 0.004C is still 0.004C.

    Returns (gain, kept).
    """
    if floor is None:
        floor = MIN_MATERIAL_GAIN_C
    n = len(base_errs)
    if n < 10 or n != len(trial_errs):
        return 0.0, False
    d = [b - t for b, t in zip(base_errs, trial_errs)]
    mean = sum(d) / n
    if mean <= 0:
        return mean, False
    if mean < floor:
        return mean, False
    var = sum((x - mean) ** 2 for x in d) / (n - 1)
    se = (var / n) ** 0.5
    if se <= 0:
        return mean, mean >= floor
    return mean, mean > k * se


def select_features(train, target):
    """Start from the base set; add a candidate only if it earns its place.

    FORWARD SELECTION ON A HELD-OUT SLICE OF THE TRAINING DATA. Going from six
    features to nine on a few hundred days is exactly how a model learns the
    noise in its own training set and reports a wonderful error on it. So each
    candidate is judged on days the fit did not see - the last quarter of the
    training window, in time order - and kept only if it actually lowers the
    error there.

    The validation slice is carved out of TRAIN, never out of the holdout. The
    holdout has one job, which is to be the first data the finished model has
    ever seen; selecting against it would make its score a description of the
    selection rather than of the model.

    Returns (features, notes) where notes records every candidate and why it
    was kept or rejected - a rejected feature is a finding, not a silence.
    """
    cut = int(len(train) * 0.75)
    inner, val = train[:cut], train[cut:]
    notes = []
    if len(val) < 15 or len(inner) < 40:
        return list(BASE_FEATURES), [("selection skipped", "too few training days to validate on")]

    chosen = [f for f in BASE_FEATURES if f in varying(inner, BASE_FEATURES)[0]]
    coef = ols(inner, chosen, target)
    if coef is None:
        return chosen, [("selection skipped", "base fit is singular")]
    base_errs = abs_errors(val, chosen, target, coef)

    remaining = [f for f in CANDIDATE_FEATURES if f not in chosen]
    while remaining:
        scored = []
        for f in remaining:
            usable = [r for r in inner if r.get(f) is not None]
            if len(usable) < len(inner):
                notes.append((f, "not present on every training day"))
                continue
            if not varying(inner, [f])[0]:
                notes.append((f, "constant across the training window"))
                continue
            worst = max((abs(correlation(inner, f, c)), c) for c in chosen)
            if worst[0] > MAX_ABS_CORRELATION:
                notes.append((f, f"collinear with {worst[1]} (r={worst[0]:.2f})"))
                continue
            trial = chosen + [f]
            c2 = ols(inner, trial, target)
            if c2 is None:
                notes.append((f, "makes the fit singular"))
                continue
            errs = abs_errors(val, trial, target, c2)
            gain, ok = beats(base_errs, errs)
            scored.append((gain, ok, f, errs))

        remaining = [f for f in remaining if f not in {n[0] for n in notes}]
        if not scored:
            break
        scored.sort(key=lambda t: -t[0])
        gain, ok, f, errs = scored[0]
        if not ok:
            for g, _, name, _ in scored:
                notes.append((name, f"no material improvement on held-out days ({g:+.3f}°C, floor {MIN_MATERIAL_GAIN_C}°C)"))
            break
        base_errs = errs
        chosen.append(f)
        notes.append((f, f"kept — held-out error fell {gain:.3f}°C, clear of chance"))
        remaining = [x for x in remaining if x != f]

    return chosen, notes


def fit_city(rows, features=None, target="max_c"):
    """Time-ordered split, fit on the first part, score on the last.

    `features` names the STARTING set. Candidates are added on top of it by
    select_features, so callers get the same behaviour as before if they pass
    the base list, and a better model if they pass nothing.
    """
    if features is None:
        features = BASE_FEATURES
    leaks = LEAKY_FEATURES & set(features)
    if leaks:
        # Loud, not silent. A leaked feature produces a model that looks
        # extraordinary and is worth nothing, and the number that would give it
        # away is the one it improves.
        raise ValueError(
            f"{sorted(leaks)} contain the target or are derived from it - "
            f"a model using them cannot be applied to a day that has not happened"
        )

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

    # ...then offer the unused variables, judged on a slice of TRAIN.
    features, selection = select_features(train, target)
    features, more_dropped = varying(train, features)
    dropped = dropped + [d for d in more_dropped if d not in dropped]

    # A selected feature has to be present on the held-out days too, or the
    # score is computed on a different set of days than it looks like.
    test = [r for r in test if all(r.get(f) is not None for f in features)]
    if len(test) < 20:
        features = [f for f in features if f in BASE_FEATURES]
        test = [r for r in rows[cut:] if all(r.get(f) is not None for f in features)]
        selection = selection + [("fell back to the base set", "too few held-out days with every selected feature")]
    if len(test) < 20:
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
        # every candidate and what happened to it - a rejection is a finding
        "selection": [{"feature": f, "verdict": why} for f, why in selection],
        "added_features": [f for f in features if f not in BASE_FEATURES],
        "n_days": len(rows), "n_train": len(train), "n_test": len(test),
        "mae_c": round(mae, 4), "persistence_mae_c": round(pmae, 4),
        "beats_persistence": mae < pmae,
        "improvement_pct": round((pmae - mae) / pmae * 100, 1) if pmae > 0 else None,
        "r2": round(r2, 4) if r2 is not None else None,
    }, len(rows)


def contributions(coef, row, features):
    """Each feature's share of the prediction, in degrees.

    Storing this alongside the number is the difference between a forecast and
    a reason. The desk's claim is that it can say WHY it disagrees with the
    market; a decomposition reconstructed later, from coefficients that may
    since have been refitted, is a plausible story rather than the actual
    arithmetic.
    """
    out = {"intercept": round(coef["intercept"], 3)}
    for f in features:
        out[f] = round(coef[f] * float(row[f]), 3)
    return out


def forecast_city(city, fit, fc_rows, last_observed_max):
    """Apply the fitted coefficients to forecast days, nearest lead first.

    prev_max_c is the problem. For the first day it is a real archived
    maximum. Beyond that there is no observation yet, so the model's own
    prediction for the previous day is chained in - and error compounds along
    a chain. Every row records which of the two it used, because a day-5
    prediction resting on four of its own guesses is not the same object as a
    day-0 one and must not be presented as though it were.

    A gap in the forecast series breaks the chain rather than skipping over
    it: chaining across a missing day would quietly pass off a two-day-old
    prediction as yesterday's number.
    """
    features = fit["features"]
    fc_rows = sorted(fc_rows, key=lambda r: r["for_date"])
    prev, prev_src = last_observed_max, "observed"
    prev_date = None
    out = []

    for r in fc_rows:
        if prev_date is not None:
            gap = (dt.date.fromisoformat(r["for_date"])
                   - dt.date.fromisoformat(prev_date)).days
            if gap != 1:
                prev, prev_src = None, None      # chain broken; nothing to carry
        prev_date = r["for_date"]

        row = dict(r)
        row["prev_max_c"] = prev
        if any(row.get(f) is None for f in features):
            prev, prev_src = None, None
            continue

        pred = predict(fit["coefficients"], row, features)
        out.append({
            "city_key": city,
            "for_date": r["for_date"],
            "run_at": r["run_at"],
            "lead_days": r.get("lead_days"),
            "predicted_max_c": round(pred, 2),
            "nws_max_c": r.get("forecast_max_c"),
            "prev_max_c": None if prev is None else round(float(prev), 2),
            "prev_source": prev_src,
            "contributions": contributions(fit["coefficients"], row, features),
            "inputs": {f: row[f] for f in features},
            "model_mae_c": fit["mae_c"],
            "persistence_mae_c": fit["persistence_mae_c"],
            "beats_persistence": fit["beats_persistence"],
        })
        prev, prev_src = round(pred, 2), "chained"

    return out


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


def write_rows(table, rows, on_conflict, chunk=500):
    """Upsert, merging duplicates. Raises on anything but success."""
    written = 0
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        r = requests.post(
            f"{_cfg()['url']}/rest/v1/{table}",
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": on_conflict},
            data=json.dumps(batch), timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"{table} write failed {r.status_code}: {r.text[:300]}")
        written += len(batch)
    return written


# How stale an observed maximum may be and still anchor a chain. Beyond this
# the anchor is guesswork dressed as an observation, and the city is skipped
# with a reason rather than predicted badly.
MAX_ANCHOR_AGE_DAYS = 3


def predict_forward(fits, by_city):
    """Apply each city's fit to its forecast days.

    Returns (rows, note). The note is the human-readable reason when nothing
    came back - an empty result here has several very different causes and
    "0 predictions" tells the operator none of them.
    """
    try:
        fc = rest_all("v_forecast_features", [
            ("select", "city_key,for_date,run_at,lead_days,forecast_max_c,"
                       "morning_temp_c,dewpoint_depression_c,cloud_mean,"
                       "wind_mean,precip_total"),
            ("for_date", f"gte.{dt.date.today().isoformat()}"),
        ], order="city_key.asc,for_date.asc,run_at.asc", page_size=1000)
    except Exception as e:
        return [], (f"No forward predictions: v_forecast_features unavailable ({e}). "
                    f"Run sql/ad4_24_nws_gridpoint.sql.")
    if not fc:
        return [], ("No forward predictions: weather_forecast_features has no days "
                    "from today on. Run n8n P1.4 (NWS Gridpoint).")

    fc_by_city = {}
    for r in fc:
        fc_by_city.setdefault(r["city_key"], []).append(r)

    preds, no_anchor = [], []
    for city, fit in fits.items():
        rows = fc_by_city.get(city)
        if not rows:
            continue
        first = min(dt.date.fromisoformat(r["for_date"]) for r in rows)

        # The chain has to start from a real number. Prefer the day before the
        # first forecast day; walk back a little if that day is missing, and
        # give up rather than anchor on something a week old.
        observed = {r["obs_date"]: r["max_c"] for r in by_city.get(city, [])
                    if r.get("max_c") is not None and (r.get("n_obs") or 0) >= 12}
        anchor = None
        for back in range(1, MAX_ANCHOR_AGE_DAYS + 1):
            d = (first - dt.timedelta(days=back)).isoformat()
            if d in observed:
                anchor = float(observed[d])
                break
        if anchor is None:
            no_anchor.append(city)
            continue

        preds.extend(forecast_city(city, fit, rows, anchor))

    note = None
    if no_anchor and not preds:
        note = ("No forward predictions: no city has an observed maximum within "
                f"{MAX_ANCHOR_AGE_DAYS} days of its first forecast day to start the "
                "chain from. Run the observation ingest (n8n P1.2 or "
                "scripts/ingest_observations.py) first.")
    elif no_anchor:
        note = (f"{len(no_anchor)} city/cities skipped for no recent observed maximum "
                f"to anchor the chain: " + ", ".join(sorted(no_anchor)[:8]))
    return preds, note


def stored_fits():
    """Rebuild each city's fit from derived_weather_model.

    Refitting is weekly - the relationship is seasonal and a day of new
    observations cannot move it. Predicting is every few hours, because that
    is how often new forecast conditions arrive. So the frequent run reads the
    coefficients back rather than paying for a fit it would only reproduce.

    The feature list is recovered from the coefficient keys, not from FEATURES.
    A city whose fit dropped a zero-variance feature has fewer coefficients
    than the constant does, and using FEATURES here would look up a
    coefficient that was never fitted.
    """
    rows = rest_all("derived_weather_model", [
        ("select", "city_key,target,coefficients,mae_c,persistence_mae_c,beats_persistence"),
        ("target", "eq.max_c"),
    ], order="city_key.asc", page_size=1000)
    fits = {}
    for r in rows:
        coef = r.get("coefficients") or {}
        if "intercept" not in coef:
            continue
        fits[r["city_key"]] = {
            "coefficients": {k: float(v) for k, v in coef.items()},
            "features": [k for k in coef if k != "intercept"],
            "mae_c": r.get("mae_c"),
            "persistence_mae_c": r.get("persistence_mae_c"),
            "beats_persistence": r.get("beats_persistence"),
        }
    return fits


def recent_days(days=10):
    """Just enough observed history to anchor a chain - not the whole archive.

    The fit needs every day there is; the prediction needs one real maximum
    per city. Pulling 200k rows for that would make the frequent run as
    expensive as the weekly one.
    """
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = rest_all("v_city_day_features", [
        ("select", "city_key,obs_date,max_c,n_obs"),
        ("obs_date", f"gte.{since}"),
    ], order="city_key.asc,obs_date.asc", page_size=1000)
    by_city = {}
    for r in rows:
        by_city.setdefault(r["city_key"], []).append(r)
    return by_city


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-days", type=int, default=MIN_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-forecast", action="store_true",
                    help="fit only; do not predict forward")
    ap.add_argument("--predict-only", action="store_true",
                    help="skip the fit; predict forward from the stored coefficients")
    args = ap.parse_args()
    globals()["MIN_DAYS"] = args.min_days

    if args.predict_only:
        return predict_only(args)

    try:
        # THE READ THAT KEPT THE MODEL FROM EVER FITTING. It asked for 200,000
        # rows in one request and received the server's 1,000-row cap: 1,000
        # rows across 54 cities is ~18 days each, under MIN_DAYS=120, so every
        # city was skipped and every weekly run said "no fitted model" while
        # 21,939 cached city-days sat in the table.
        rows = rest_all("v_city_day_features", [
            ("select", "city_key,obs_date,max_c,n_obs,prev_max_c,morning_temp_c,"
                       "dewpoint_depression_c,cloud_mean,wind_mean,precip_total"),
        ], order="city_key.asc,obs_date.asc", page_size=1000)
    except Exception as e:
        print(f"v_city_day_features unavailable ({e}). Run sql/ad4_21_weather_features.sql.",
              file=sys.stderr)
        log_run("weather_model", "attention", 0, {"error": str(e)})
        return 1

    by_city = {}
    for r in rows:
        by_city.setdefault(r["city_key"], []).append(r)

    out, skipped, beat = [], [], 0
    fits = {}
    for city, rs in sorted(by_city.items()):
        fit, n = fit_city(rs, FEATURES)
        if fit is None:
            skipped.append((city, n))
            continue
        fits[city] = fit
        if fit["beats_persistence"]:
            beat += 1
        print(f"{city:<14} n={fit['n_days']:<5} model MAE {fit['mae_c']:.2f}°C  "
              f"persistence {fit['persistence_mae_c']:.2f}°C  "
              f"{'BEATS' if fit['beats_persistence'] else 'loses to'} persistence"
              + (f" by {fit['improvement_pct']}%" if fit["beats_persistence"] else ""))
        print(f"               {describe(fit)}")
        if fit["dropped_features"]:
            print(f"               (no variation in {', '.join(fit['dropped_features'])} — dropped)")
        # A rejected candidate is a finding: it says this variable, which the
        # desk collects on every reading, does not move this city's afternoon.
        for v in fit.get("selection", []):
            print(f"               · {v['feature']}: {v['verdict']}")
        out.append({
            "city_key": city, "target": "max_c", "n_days": fit["n_days"],
            "coefficients": fit["coefficients"], "mae_c": fit["mae_c"],
            # stored so the reason a variable is absent survives the run
            "selection": fit.get("selection"),
            "persistence_mae_c": fit["persistence_mae_c"],
            "beats_persistence": fit["beats_persistence"], "r2": fit["r2"],
            "notes": describe(fit), "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })

    if skipped:
        print(f"\n{len(skipped)} city/cities skipped for too few usable days "
              f"(need {MIN_DAYS}): " + ", ".join(f"{c}({n})" for c, n in skipped[:8]))
    if out:
        extra = {}
        for o in out:
            for f in [k for k in o["coefficients"] if k not in BASE_FEATURES and k != "intercept"]:
                extra[f] = extra.get(f, 0) + 1
        if extra:
            print("\nVariables that earned a place beyond the base six: "
                  + ", ".join(f"{f} in {n} city/cities" for f, n in sorted(extra.items())))
        else:
            print("\nNo city kept a variable beyond the base six. On this data the "
                  "extra measurements do not move the afternoon - a finding, not a gap.")
        print(f"\n{beat} of {len(out)} cities beat persistence on held-out days.")
        if beat == 0:
            print("  None of them beat it. That is a real result, not a bug: on this data the "
                  "morning conditions add nothing over yesterday's maximum, and no forecast "
                  "built on them should be trusted yet.")

    # ---- forward predictions ------------------------------------------------
    preds, fc_note = [], None
    if not args.no_forecast and fits:
        preds, fc_note = predict_forward(fits, by_city)
        if fc_note:
            print(f"\n{fc_note}")
        if preds:
            trade = sum(1 for p in preds
                        if p["beats_persistence"] and p["nws_max_c"] is not None
                        and abs(p["predicted_max_c"] - float(p["nws_max_c"])) > p["model_mae_c"])
            print(f"\n{len(preds)} forward prediction(s) across "
                  f"{len({p['city_key'] for p in preds})} city/cities. "
                  f"{trade} differ from NWS by more than the model's own error.")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0
    if not out:
        log_run("weather_model", "attention", 0, {"skipped": len(skipped), "min_days": MIN_DAYS})
        return 0

    write_rows("derived_weather_model", out, "city_key,target")

    n_pred = 0
    if preds:
        try:
            n_pred = write_rows("derived_model_forecast", preds,
                                "city_key,for_date,run_at")
            print(f"wrote {n_pred} forward prediction(s) to derived_model_forecast")
        except Exception as e:
            # The fit is the durable asset and it is already written. A missing
            # ad4_25 must not throw away a successful fit.
            print(f"  ! derived_model_forecast unavailable ({e}). "
                  f"Run sql/ad4_25_model_forecast.sql.", file=sys.stderr)

    log_run("weather_model", "ok", len(out),
            {"cities": len(out), "beat_persistence": beat, "skipped": len(skipped),
             "forward_predictions": n_pred})
    print(f"\nwrote {len(out)} city model(s) to derived_weather_model")
    return 0


def predict_only(args):
    """Predict forward from the stored fit. No refit, no full-history read."""
    try:
        fits = stored_fits()
    except Exception as e:
        print(f"derived_weather_model unavailable ({e}). "
              f"Run sql/ad4_21_weather_features.sql, then this script without "
              f"--predict-only to fit.", file=sys.stderr)
        log_run("weather_model_forecast", "attention", 0, {"error": str(e)})
        return 1
    if not fits:
        print("No fitted model yet. Run scripts/weather_model.py without "
              "--predict-only first (weekly, via the Weather Model action).")
        log_run("weather_model_forecast", "attention", 0, {"reason": "no fitted model"})
        return 0

    preds, note = predict_forward(fits, recent_days())
    if note:
        print(note)
    if not preds:
        log_run("weather_model_forecast", "attention", 0,
                {"cities_fitted": len(fits), "reason": note})
        return 0

    trade = sum(1 for p in preds
                if p["beats_persistence"] and p["nws_max_c"] is not None
                and p["model_mae_c"] is not None
                and abs(p["predicted_max_c"] - float(p["nws_max_c"])) > p["model_mae_c"])
    cities = len({p["city_key"] for p in preds})
    print(f"{len(preds)} forward prediction(s) across {cities} city/cities from "
          f"{len(fits)} stored fit(s). {trade} differ from NWS by more than the "
          f"model's own error.")
    for p in sorted(preds, key=lambda x: (x["city_key"], x["for_date"]))[:12]:
        gap = ("" if p["nws_max_c"] is None
               else f"  NWS {float(p['nws_max_c']):.1f}  "
                    f"({p['predicted_max_c'] - float(p['nws_max_c']):+.1f})")
        print(f"  {p['city_key']:<14} {p['for_date']}  "
              f"{p['predicted_max_c']:.1f}°C{gap}  [{p['prev_source']}]")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    try:
        n = write_rows("derived_model_forecast", preds, "city_key,for_date,run_at")
    except Exception as e:
        print(f"  ! derived_model_forecast unavailable ({e}). "
              f"Run sql/ad4_25_model_forecast.sql.", file=sys.stderr)
        log_run("weather_model_forecast", "attention", 0, {"error": str(e)})
        return 1

    log_run("weather_model_forecast", "ok", n,
            {"predictions": n, "cities": cities, "disagreements": trade,
             "cities_fitted": len(fits)})
    print(f"\nwrote {n} forward prediction(s) to derived_model_forecast")
    return 0


if __name__ == "__main__":
    sys.exit(main())
