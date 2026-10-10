#!/usr/bin/env python3
"""
The honest station model (plan v2.2 P2.9 part 2): what the station's maximum
will be, learned only from what the public forecasts said beforehand.

WHAT IT LEARNS. Per city and lead, a ridge regression of the RESIDUAL - the
station's maximum minus the seven models' mean maximum over 00-17 local - on:
that mean, the models' spread, four models' deviations from it, the best_match
run's 08:00 temperature and dewpoint depression, its 09-17 dewpoint, cloud and
wind, radiation, rain, 08:00 pressure, the last station maximum known when the
job runs, and the season. Every input comes from data/training/previous_runs
(scripts/honest_record.py): nothing observed after the cutoff. Coefficients are
shrunk toward a pooled fit across cities, the pooled fit toward zero (= the
models' mean), and recent days weigh more.

THE EVIDENCE BEFORE BUILDING (tools/experiments_p29_honest_mos.py, 26 Sep,
walk-forward Nov 2025-Sep 2026, 48 cities, lead 1, 15,652 city-days, the last
known station max one day older than the target's eve, as this job sees it):
P3.9 as coded MAE 0.938 C, this model 0.918, their mean 0.902 - better than
P3.9 by 0.036 [0.031, 0.041] and the same whole degree +1.1 pts [+0.5, +1.6],
in 11 of 11 months.

RULE 11, every learned number:
  prior        the pooled fit; the pooled fit's prior is zero correction
  bounds       |station model - models' mean| <= BOUND_C
  minimum      a city with fewer than MIN_CITY_DAYS training days uses the pool
  step         a city's prediction on its own recent average day moves at most
               MAX_STEP_C per night; a bigger move is taken as that fraction of
               the way from last night's coefficients
  version      on every coefficient and forecast row
  held out     each night scores itself on the last EVAL_DAYS days with a fit
               that ended before them, against P3.9 replayed the same way

SHADOW. It writes derived_mos_coefficients and derived_mos_forecast and logs
P2.9_station_mos; the engine prices from the blend only when
settings.station_mos_pricing.enabled is on.

  python scripts/station_mos.py [--as-of 2026-09-27] [--dry-run]
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import sys
import time

import honest_record as hr
import station_correction as sc
from weather_model import solve

JOB = "P2.9_station_mos"
LEADS = hr.LEADS
DEV_MODELS = ("ecmwf_ifs025", "gfs_seamless", "icon_seamless", "jma_seamless")
MIN_MODELS = 4
FEATURES = ["models_mean", "models_spread"] + [f"dev_{m}" for m in DEV_MODELS] + [
    "t_08", "dewpoint_depression_08", "td_09_17", "cloud_09_17", "cloud_max_09_17", "shortwave_06_17",
    "wind_09_17", "precip_00_17", "pressure_08", "last_obs_minus_mean", "doy_sin", "doy_cos"]
HALF_LIVES = (30, 90, 365)     # days; the one whose inner split scores best is used
LAM_POOL = (10, 100)
LAM_CITY = (30, 100, 300)
INNER_SHARE = 0.2              # the last fifth of the training days, in time order

# Rule 11. Priors of this module, not measurements; logged on every run.
BOUND_C = 4.0
MIN_CITY_DAYS = 90
MAX_STEP_C = 0.25
EVAL_DAYS = 30

# A NIGHT OPEN-METEO HANGS. The current runs come through honest_record's
# requests, and this job never set their deadline: each hung request waited
# 60 s, twice, and once more after the pass. On 6 Oct four cities' requests
# read-timed out, the second pass recovered two, and the step ended 5 s inside
# its 5 minutes; on 7 Oct eight cities' did, the step was killed at 5 min 12 s,
# and nothing was written, the cities that had answered included. Now no
# request waits past FETCH_SECONDS after main() starts; what arrived is fitted
# and written (status partial), and a city left out prices on P3.9 alone, as
# houston and shenzhen did on 6 Oct (92 of 96 rows). The rest of the
# run (the labels, the two files, four fits, two held-out checks) took 14.8 s
# on 7 Oct, run locally on the repo mirror's rows: 210 s leaves it 90 s of
# the step's 300 (tests/test_the_honest_station_model.py).
#
# From 10 Oct a request with no answer is asked again in rounds spread over
# these 210 s (honest_record.RETRY_WAITS), the missing part only. The run was
# partial on 5 of its 7 logged runs from 2 to 9 Oct (ingest_log; 7 Oct's was
# killed and logged nothing); on 8 Oct it missed wuhan, lucknow, paris and
# houston.
FETCH_SECONDS = 210


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------
def _f(v):
    return None if v in (None, "") else float(v)


def last_known_obs(labels, city, for_date, lead):
    """The station max this job can know: it runs before the target's eve has
    ended, so one day older than the eve (lead + 1 days before the target)."""
    return labels.get((city, (for_date - dt.timedelta(days=lead + 1)).isoformat()))


def features(bm_row, model_maxes, last_obs, for_date):
    """The feature vector, or None if any input is missing. `bm_row` is a
    best_match record row (strings or numbers), `model_maxes` {model: tmax17}."""
    vals = [v for v in model_maxes.values() if v is not None]
    if len(vals) < MIN_MODELS or bm_row is None or last_obs is None:
        return None, None
    mean = sum(vals) / len(vals)
    spread = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    devs = [model_maxes.get(m) - mean if model_maxes.get(m) is not None else None for m in DEV_MODELS]
    t08, td08, td_day, cc, ccmax, sw, ws, pr, p08 = (_f(bm_row[i]) for i in (6, 7, 8, 9, 10, 11, 12, 13, 14))
    doy = 2 * math.pi * for_date.timetuple().tm_yday / 365.25
    x = [mean, spread] + devs + [t08, (t08 - td08) if None not in (t08, td08) else None, td_day, cc, ccmax,
                                 sw, ws, pr, p08, last_obs - mean, math.sin(doy), math.cos(doy)]
    if any(v is None for v in x):
        return None, None
    return x, mean


def assemble(bm_rows, model_rows, labels):
    """{lead: [row]} with row = dict(city, date, x, base, y (None forward),
    full {model: tmax_c} for P3.9)."""
    bm = {(r[0], int(r[1]), r[2]): r for r in bm_rows}
    mods = {}
    for r in model_rows:
        mods.setdefault((r[0], int(r[1]), r[3]), {})[r[2]] = (_f(r[4]), _f(r[5]))
    out = {lead: [] for lead in LEADS}
    for key, row in bm.items():
        city, lead, ds = key
        if lead not in out:
            continue
        m = mods.get(key, {})
        d = dt.date.fromisoformat(ds)
        x, base = features(row, {k: v[1] for k, v in m.items()}, last_known_obs(labels, city, d, lead), d)
        if x is None:
            continue
        out[lead].append({"city": city, "date": d, "x": x, "base": base, "y": labels.get((city, ds)),
                          "full": {k: v[0] for k, v in m.items() if v[0] is not None}})
    for rows in out.values():
        rows.sort(key=lambda r: (r["date"], r["city"]))
    return out


# ---------------------------------------------------------------------------
# the fit: weighted ridge by sufficient statistics (pure Python)
# ---------------------------------------------------------------------------
def standardiser(rows):
    n = len(rows)
    p = len(FEATURES)
    mu = [sum(r["x"][j] for r in rows) / n for j in range(p)]
    sd = [math.sqrt(sum((r["x"][j] - mu[j]) ** 2 for r in rows) / n) or 1.0 for j in range(p)]
    return mu, sd


def _z(r, mu, sd):
    return [1.0] + [(r["x"][j] - mu[j]) / sd[j] for j in range(len(mu))]


def city_stats(rows, mu, sd, half_life, end):
    """{city: (XtWX, XtWy, n)} with weight 0.5 ** (age / half_life)."""
    out = {}
    for r in rows:
        z = _z(r, mu, sd)
        w = 0.5 ** ((end - r["date"]).days / half_life)
        e = w * (r["y"] - r["base"])
        A, b, n = out.setdefault(r["city"], ([[0.0] * len(z) for _ in z], [0.0] * len(z), [0]))
        for i, zi in enumerate(z):
            wzi = w * zi
            Ai = A[i]
            for j in range(i, len(z)):
                Ai[j] += wzi * z[j]
            b[i] += e * zi
        n[0] += 1
    for A, _, _ in out.values():
        for i in range(len(A)):
            for j in range(i):
                A[i][j] = A[j][i]
    return {c: (A, b, n[0]) for c, (A, b, n) in out.items()}


def ridge(A, b, lam, prior):
    """(A + lam I') beta = b + lam I' prior, the intercept unpenalised."""
    p = len(b)
    M = [[A[i][j] + (lam if i == j and i > 0 else 0.0) for j in range(p)] for i in range(p)]
    rhs = [b[i] + (lam * prior[i] if i > 0 else 0.0) for i in range(p)]
    return solve(M, rhs)


def fit(stats, lam_pool, lam_city):
    """(pooled coefficients, {city: coefficients}) in standardised units. A
    city below MIN_CITY_DAYS keeps the pool (Rule 11 minimum)."""
    p = len(FEATURES) + 1
    A = [[sum(s[0][i][j] for s in stats.values()) for j in range(p)] for i in range(p)]
    b = [sum(s[1][i] for s in stats.values()) for i in range(p)]
    pooled = ridge(A, b, lam_pool, [0.0] * p) or [0.0] * p
    cities = {}
    for c, (Ac, bc, n) in stats.items():
        cities[c] = (ridge(Ac, bc, lam_city, pooled) or pooled) if n >= MIN_CITY_DAYS else pooled
    return pooled, cities


def to_raw(beta, mu, sd):
    """Standardised coefficients -> {feature: per unit, 'intercept': C}."""
    raw = {f: beta[j + 1] / sd[j] for j, f in enumerate(FEATURES)}
    raw["intercept"] = beta[0] - sum(beta[j + 1] * mu[j] / sd[j] for j in range(len(FEATURES)))
    return raw


def correction(coef, x):
    return coef["intercept"] + sum(coef[f] * x[j] for j, f in enumerate(FEATURES))


def predict(coef, row):
    """The station model's maximum, its correction bounded (Rule 11)."""
    c = max(-BOUND_C, min(BOUND_C, correction(coef, row["x"])))
    return row["base"] + c


def choose_and_fit(train):
    """Hyperparameters on the last INNER_SHARE of the days (time order), then
    refitted on everything. Returns (pooled raw, {city: raw}, chosen)."""
    days = sorted({r["date"] for r in train})
    cut = days[int(len(days) * (1 - INNER_SHARE))]
    inner, held = [r for r in train if r["date"] < cut], [r for r in train if r["date"] >= cut]
    mu, sd = standardiser(inner)
    best = None
    for hl in HALF_LIVES:
        stats = city_stats(inner, mu, sd, hl, cut)
        for lp in LAM_POOL:
            for lc in LAM_CITY:
                pooled, cities = fit(stats, lp, lc)
                raw = {c: to_raw(b, mu, sd) for c, b in cities.items()}
                praw = to_raw(pooled, mu, sd)
                err = sum(abs(predict(raw.get(r["city"], praw), r) - r["y"]) for r in held) / len(held)
                if best is None or err < best[0]:
                    best = (err, hl, lp, lc)
    _, hl, lp, lc = best
    mu, sd = standardiser(train)
    end = max(r["date"] for r in train) + dt.timedelta(days=1)
    pooled, cities = fit(city_stats(train, mu, sd, hl, end), lp, lc)
    return to_raw(pooled, mu, sd), {c: to_raw(b, mu, sd) for c, b in cities.items()}, \
        {"half_life_days": hl, "lambda_pool": lp, "lambda_city": lc, "inner_mae": round(best[0], 4)}


def reference_day(rows, city):
    """A city's average day over its last 30 training days: where the step is measured."""
    mine = [r for r in rows if r["city"] == city][-30:]
    if not mine:
        return None
    return {"x": [sum(r["x"][j] for r in mine) / len(mine) for j in range(len(FEATURES))],
            "base": sum(r["base"] for r in mine) / len(mine)}


def bounded_step(new, old, ref):
    """Rule 11 step: at most MAX_STEP_C of movement on the city's reference day."""
    if old is None or ref is None:
        return new, 1.0
    move = correction(new, ref["x"]) - correction(old, ref["x"])
    if abs(move) <= MAX_STEP_C:
        return new, 1.0
    a = MAX_STEP_C / abs(move)
    return {k: old.get(k, 0.0) + a * (new[k] - old.get(k, 0.0)) for k in new}, a


def version_of(coefs, as_of, chosen):
    body = json.dumps({"as_of": str(as_of), "chosen": chosen, "bound": BOUND_C, "min": MIN_CITY_DAYS,
                       "step": MAX_STEP_C,
                       "coef": {f"{c}|{l}": {k: round(v, 6) for k, v in sorted(cf.items())}
                                for (c, l), cf in sorted(coefs.items())}}, sort_keys=True)
    return f"station-mos:{as_of}:{hashlib.sha256(body.encode()).hexdigest()[:10]}"


# ---------------------------------------------------------------------------
# held out: the last EVAL_DAYS, against P3.9 replayed the same way
# ---------------------------------------------------------------------------
def p39_replay(rows, lead, days):
    """P3.9 as coded: refitted each day on the WINDOW_DAYS before it."""
    by_day = {}
    for r in rows:
        if r["y"] is not None:
            by_day.setdefault(r["date"], []).append(r)
    out, previous = {}, None
    for d in sorted(days):
        train = [(r["city"], dd.isoformat(), m, lead, fc, r["y"])
                 for k in range(1, sc.WINDOW_DAYS + 1)
                 for dd in [d - dt.timedelta(days=k)] for r in by_day.get(dd, []) for m, fc in r["full"].items()]
        if not train:
            continue
        table = sc.fit(train, previous)
        previous = {k: v[0] for k, v in table["cells"].items()}
        for r in by_day.get(d, []):
            res = sc.combine(r["full"], table, r["city"], lead)
            if res is not None:
                out[(r["city"], d)] = res[0]
    return out


def held_out(rows, lead, as_of):
    start = as_of - dt.timedelta(days=EVAL_DAYS)
    train = [r for r in rows if r["y"] is not None and r["date"] < start]
    test = [r for r in rows if r["y"] is not None and start <= r["date"] < as_of]
    if len(train) < 1000 or not test:
        return {"n": 0}
    praw, craw, chosen = choose_and_fit(train)
    p39 = p39_replay([r for r in rows if r["date"] < as_of], lead, sorted({r["date"] for r in test}))
    err = {"base": [], "mos": [], "p39": [], "blend": []}
    for r in test:
        p = p39.get((r["city"], r["date"]))
        if p is None:
            continue
        m = predict(craw.get(r["city"], praw), r)
        err["base"].append(abs(r["base"] - r["y"]))
        err["mos"].append(abs(m - r["y"]))
        err["p39"].append(abs(p - r["y"]))
        err["blend"].append(abs((m + p) / 2 - r["y"]))
    n = len(err["mos"])
    if not n:
        return {"n": 0}
    return {"n": n, "from": start.isoformat(), "to": (as_of - dt.timedelta(days=1)).isoformat(),
            "chosen": chosen, **{f"mae_{k}": round(sum(v) / n, 4) for k, v in err.items()}}


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def load_labels(rest_all):
    """Whole days only - both as the label and as the last station maximum a
    forward row starts from (common.day_had_ended)."""
    from common import day_had_ended
    tz = {r["city_key"]: r.get("timezone") for r in rest_all(
        "cities", [("select", "city_key,timezone")], order="city_key.asc")}
    rows = rest_all("derived_city_day_features",
                    [("select", "city_key,obs_date,max_c,computed_at"), ("max_c", "not.is.null")],
                    order="city_key.asc,obs_date.asc")
    return {(r["city_key"], str(r["obs_date"])): float(r["max_c"]) for r in rows
            if day_had_ended(r["obs_date"], r.get("computed_at"), tz.get(r["city_key"]))}


def load_previous(rest_all, as_of):
    """{(city, lead): (coef, as_of)} in force BEFORE the night `as_of` (plan v2.3
    P5.14): a re-run tonight steps from what the night started with, not from
    the first run's output (station_correction.anchor_before)."""
    rows = rest_all("derived_mos_coefficients",
                    [("select", "city_key,lead_days,coef,as_of,prev_coef,prev_as_of")],
                    order="city_key.asc,lead_days.asc")
    out = {}
    for r in rows:
        a = sc.anchor_before(r, as_of, value="coef", prev="prev_coef")
        if a is not None:
            out[(r["city_key"], int(r["lead_days"]))] = a
    return out


def load_p39(rest_all, today):
    rows = rest_all("derived_corrected_forecast", [("select", "city_key,for_date,combined_c,version"),
                                                   ("for_date", f"gte.{today.isoformat()}")],
                    order="city_key.asc,for_date.asc")
    return {(r["city_key"], str(r["for_date"])): (float(r["combined_c"]), r["version"]) for r in rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-eval", action="store_true")
    args = ap.parse_args(argv)
    from common import rest_all, upsert_replace, log_run, get_cities
    hr._deadline = time.monotonic() + FETCH_SECONDS

    now = dt.datetime.now(dt.timezone.utc)
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else now.date()
    # At ~05Z UTC yesterday has not ended west of about UTC-5: only the days
    # before it are complete everywhere.
    complete = as_of - dt.timedelta(days=1)
    labels = load_labels(rest_all)
    cities = get_cities()
    active = {c["city_key"] for c in cities}
    history = assemble([r for r in hr.read_rows(hr.BEST_MATCH_FILE) if r[0] in active],
                       [r for r in hr.read_rows(hr.MODELS_FILE) if r[0] in active], labels)
    rounds = []
    fbm, fmd, unreached = hr.forward_rows(cities, now, rounds)
    forward = assemble([hr._text(r) for r in fbm], [hr._text(r) for r in fmd], labels)
    previous = load_previous(rest_all, as_of)
    p39 = load_p39(rest_all, as_of)

    coefs, steps, chosen_by, scores, out_rows, coef_rows = {}, {}, {}, {}, [], []
    for lead in LEADS:
        train = [r for r in history[lead] if r["y"] is not None and r["date"] < complete]
        if len(train) < 1000:
            scores[lead] = {"n": 0, "reason": f"{len(train)} training rows"}
            continue
        praw, craw, chosen = choose_and_fit(train)
        chosen_by[lead] = chosen
        for city in sorted({r["city"] for r in train}):
            n_city = sum(1 for r in train if r["city"] == city)
            new = craw.get(city, praw)
            ref = reference_day(train, city)
            new, a = bounded_step(new, (previous.get((city, lead)) or (None,))[0], ref)
            coefs[(city, lead)] = new
            steps[(city, lead)] = (a, n_city)
        if not args.no_eval:
            scores[lead] = held_out(history[lead], lead, complete)
    version = version_of(coefs, as_of, chosen_by)

    for (city, lead), cf in coefs.items():
        a, n_city = steps[(city, lead)]
        prev = previous.get((city, lead))
        # The coefficients it stepped from ride along, so a re-run tonight
        # steps from the same place and writes the same numbers (P5.14).
        coef_rows.append({"city_key": city, "lead_days": lead, "coef": {k: round(v, 6) for k, v in cf.items()},
                          "n": n_city, "pooled": n_city < MIN_CITY_DAYS, "step_fraction": round(a, 4),
                          "version": version, "as_of": as_of.isoformat(), "computed_at": now.isoformat(),
                          "prev_coef": prev[0] if prev else None, "prev_as_of": prev[1] if prev else None})
    for lead in LEADS:
        for r in forward[lead]:
            cf = coefs.get((r["city"], lead))
            if cf is None:
                continue
            m = predict(cf, r)
            p = p39.get((r["city"], r["date"].isoformat()))
            out_rows.append({
                "city_key": r["city"], "for_date": r["date"].isoformat(), "lead_days": lead,
                "mos_c": round(m, 3), "base_c": round(r["base"], 3),
                "p39_c": round(p[0], 3) if p else None,
                "blend_c": round((m + p[0]) / 2, 3) if p else None,
                "inputs": dict(zip(FEATURES, [round(v, 4) for v in r["x"]])),
                "p39_version": p[1] if p else None, "version": version, "computed_at": now.isoformat()})

    detail = {"as_of": as_of.isoformat(), "version": version, "cells": len(coef_rows),
              "forward": len(out_rows), "forward_with_p39": sum(1 for r in out_rows if r["blend_c"] is not None),
              "unreached": unreached, "rounds": rounds, "chosen": chosen_by, "held_out": scores,
              "stepped": sum(1 for a, _ in steps.values() if a < 1.0),
              "pooled_cities": sum(1 for _, n in steps.values() if n < MIN_CITY_DAYS),
              "priors": {"bound_c": BOUND_C, "min_city_days": MIN_CITY_DAYS, "max_step_c": MAX_STEP_C,
                         "eval_days": EVAL_DAYS}}
    print(json.dumps(detail, indent=1, default=str))
    if args.dry_run:
        return 0
    if coef_rows:
        upsert_replace("derived_mos_coefficients", coef_rows, "city_key,lead_days")
    if out_rows:
        upsert_replace("derived_mos_forecast", out_rows, "city_key,for_date")
    status = "ok" if coef_rows and out_rows and not unreached else ("partial" if coef_rows else "error")
    log_run(JOB, status, len(out_rows), detail)
    return 0 if status != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
