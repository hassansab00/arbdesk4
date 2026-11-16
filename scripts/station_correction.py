#!/usr/bin/env python3
"""Each forecast source's error at the settlement station, then their
combination (plan v2.2 P3.9).

The public weather models already hold most of what is knowable about the
atmosphere. What they do not know is the settlement station: its exposure, its
height against the model's grid cell, its coast. So each source's error at the
station is learned, per city and lead, and the corrected sources are combined.

    error(source, lead, city) = the day's truth - source max

THE TRUTH (plan v2.4 P3.10 part 3.1) is the venue's, the one the model is
scored on (scripts/venue_truth.py, v_venue_truth): its verified reading, or its
winning bucket's midpoint reading. The station maximum as the desk read it
(derived_city_day_features) disagreed with it: 10.3% of C city-days a bucket
low before Sep, and F labels 0.21 C high on average, 24 Aug-27 Sep.
settings.truth_labels {"source": "station"} puts the station labels back;
either way both sets are scored on the venue's truth every night (the log's
labels.scored_on_venue_truth).

    pooled(source, lead)  = mean error over every city      (bounded)
    bias(source, lead, city) = pooled + sum(error - pooled) / (n + K)
                               when n >= MIN_N, else pooled

    corrected(source) = source max + bias
    combination       = the EQUAL-WEIGHT mean of the corrected sources

Equal weights are the benchmark the plan requires; learned weights come only
if they beat it on later dates. One pooled shrinkage per source, not a model
per city, hour and regime: a few dozen days per city cannot support that.

MEASURED BEFORE BUILDING (26 Sep; fitted on 2-19 Sep, scored on 317 city-days
of 20-26 Sep): raw forecast MAE 1.390 C; forecast + shrunk city bias 1.237;
seven models each corrected, combined 1.077; averaged with the city-bias line
1.068. Seven test dates: the reason to build it, not the proof. The proof is
the walk-forward score this script logs every night and the replay (P7.3).

RULE 11. Prior: no correction (bias 0 at the pool, the pool at a city).
Bounds: |bias| <= BOUND_C. Minimum sample: MIN_N city-days before a city moves
off its source's pool. Maximum step: MAX_STEP_C per night from the stored
cell. Version: every cell and every combination carries the fit's version.
Walk-forward: a fit reads only target days before as_of, and the nightly
score fits each scored day on the days before it.

LIVE since 27 Sep 06:32Z: the engine prices days ahead from the combination
while settings.station_correction_pricing is on. The width stored beside it
(P3.9 part 3, the WIDTH_ priors below) is shadow until
settings.station_width_pricing is on.

    python scripts/station_correction.py [--as-of YYYY-MM-DD] [--dry-run]
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import sys

JOB = "P3.9_station_correction"
FIT_SOURCE = "open-meteo-previous-runs"      # each model's run from N days before
FORWARD_SOURCE = "open-meteo-models-current"  # each model's latest run, open days
LEADS = (1, 2, 3)
WINDOW_DAYS = 45
K = 10.0            # prior strength, in city-days, toward the source's pool
MIN_N = 5           # a city moves off its pool only with this many days
BOUND_C = 3.0       # |bias| never exceeds this
MAX_STEP_C = 0.25   # a stored cell moves at most this far in one night
MIN_SOURCES = 4     # fewer corrected sources than this is not a combination
EVAL_DAYS = 14      # the walk-forward score covers this many latest days
# K, MIN_N, BOUND_C, MAX_STEP_C and MIN_SOURCES are this module's priors, not
# measurements, and are written into every run's log.

# PLAN v2.3 P3.9 PART 3: THE WIDTH AROUND THIS CENTRE.
#
# P3.9 replaced the day-ahead centre and kept the engine's width, which was
# fitted to the RAW forecast's errors. docs/P39_SERVED_WIDTH_2026-09-27.md
# scored widths fitted to this combination's own out-of-sample errors on the
# venue's ladders, day-ahead: on the 9 dates when the served width was at
# today's level (17-25 Sep, 416 city-days), a per-city width shrunk to the
# pool gained +0.138 log loss per city-day, 90% [+0.105, +0.171], over the
# width served. Those dates came before this design, so it is SHADOW: stored
# nightly, recorded beside every price, priced from only while
# settings.station_width_pricing is on.
#
#   error(city, day, lead) = station max - the combination for that day and
#                            lead, fitted only on the WINDOW_DAYS before it
#   pool(lead)   = mean |error| over every city in the last WIDTH_DAYS days
#   width        = MAE_TO_SIGMA x (sum |error| + WIDTH_K x pool) / (n + WIDTH_K)
#                  for a city with MIN_N days or more, else MAE_TO_SIGMA x pool
#
# RULE 11. Prior: the pool. Bounds: WIDTH_LO_C to WIDTH_HI_C. Minimum sample:
# MIN_N days before a city moves off its pool, and WIDTH_MIN_POOL errors
# before a lead has a width at all. Step: at most WIDTH_MAX_STEP of the width
# in force before tonight (P5.14), per night. Version: on every row and on
# every price that uses it. These are priors, not measurements, and are
# written into every run's log.
WIDTH_DAYS = 30
WIDTH_K = 10.0
WIDTH_MIN_POOL = 200
WIDTH_LO_C = 0.5
WIDTH_HI_C = 3.0
WIDTH_MAX_STEP = 0.10
MAE_TO_SIGMA = 1.2533   # sigma = MAE x sqrt(pi/2) for a normal distribution

# OWN_W (Hassan, 16 Nov 2026: "yes for improved station bias learner"). The
# learner that tools/own_w_forward.py ran in shadow (docs/OWN_W_PREREG.md,
# own_w:v1), built in while settings.station_correction_learner names it:
#
#   for leads 0, 1 and 2 the bias is learned from the forecasts the engine is
#   served (FORWARD_SOURCE, each model's newest run of each run date), not the
#   previous-runs record, and each model is weighted by its skill at the city:
#   its precision (1 / its shrunk mean squared error) over the mean precision
#   of the models with MIN_N city-days there, bounded REL_LO to REL_HI. Lead 3
#   (no forward history) keeps the previous-runs fit, at equal weight.
#
#   MEASURED before building (data/eval/model_audit_2026-11-15/station_weights_
#   study.json, forward.by_lead, rows served 12 Oct - 14 Nov, mean absolute
#   error at the station): lead 0 0.752 -> 0.709 C, lead 1 0.892 -> 0.850,
#   lead 2 0.994 -> 0.963. On the long record (lead 1, 1 Oct 2025 - 15 Nov 2026,
#   19,592 city-days) with the fit ending two days before the day, as at the
#   evening call: 0.910 -> 0.883 C (16 Nov, scratch measurement in the PR).
#
# RULE 11. Prior: the source's pool for a bias; for a weight, REL_PRIOR (the
# equal weight in force before it). Minimum sample: MIN_N city-days before a
# cell leaves its pool. Bounds: |bias| <= BOUND_C; weight REL_LO to REL_HI, so
# no model counts more than four times another. Step a night: MAX_STEP_C for a
# bias, REL_MAX_STEP for a weight, from the value in force before tonight (a
# weight with none steps from REL_PRIOR; a lead-0 bias with none steps from the
# lead-1 cell the engine used for lead 0). Version: the learner, its priors and
# every bias and weight are in the version on every cell and combination.
# Truth: the same labels as the previous-runs fit (settings.truth_labels).
OWN_W = "own_w:v1"
P39 = "p39"
OWN_LEADS = (0, 1, 2)
REL_LO, REL_HI = 0.5, 2.0
REL_MAX_STEP = 0.10
REL_PRIOR = 1.0


def _clip(x, lo, hi):
    return min(hi, max(lo, x))


def fit(pairs, previous=None):
    """{"cells": {(source, lead, city): (bias, n)}, "pooled": {(source, lead): (bias, n)}}
    from pairs (city, day, source, lead, forecast, observed)."""
    by_pool, by_cell = {}, {}
    for city, _day, source, lead, fc, y in pairs:
        e = y - fc
        by_pool.setdefault((source, lead), []).append(e)
        by_cell.setdefault((source, lead, city), []).append(e)
    pooled = {k: (_clip(sum(v) / len(v), -BOUND_C, BOUND_C), len(v)) for k, v in by_pool.items()}
    cells = {}
    for (source, lead, city), errs in by_cell.items():
        pool = pooled[(source, lead)][0]
        n = len(errs)
        bias = pool if n < MIN_N else pool + sum(e - pool for e in errs) / (n + K)
        bias = _clip(bias, -BOUND_C, BOUND_C)
        prev = (previous or {}).get((source, lead, city))
        if prev is not None:
            bias = _clip(bias, prev - MAX_STEP_C, prev + MAX_STEP_C)
        cells[(source, lead, city)] = (bias, n)
    return {"cells": cells, "pooled": pooled}


def _cell_lead(table, source, lead, city):
    """The lead whose cell serves `lead`: its own when the table has one (the
    own_w learner fits lead 0), else lead 0 uses lead 1 and the rest clamp to
    LEADS, as before."""
    if (source, lead, city) in table["cells"] or (source, lead) in table["pooled"]:
        return lead
    return max(1, min(lead, max(LEADS)))


def correction(table, source, lead, city):
    """The bias to add to `source` at `lead` for `city`: its cell, else its
    pool, else nothing. Lead 0 has no previous-run history; without a lead-0
    cell it uses lead 1."""
    lead = _cell_lead(table, source, lead, city)
    cell = table["cells"].get((source, lead, city))
    if cell is not None:
        return cell[0]
    pool = table["pooled"].get((source, lead))
    return pool[0] if pool is not None else None


def weight(table, source, lead, city):
    """The source's skill weight at the city (own_w): its cell; else its pool's
    weight, at most REL_MAX_STEP from the weight in force (prev_weights, or
    REL_PRIOR); else REL_PRIOR, so a previous-runs table combines with equal
    weights."""
    weights = table.get("weights") or {}
    lead = _cell_lead(table, source, lead, city)
    if (source, lead, city) in weights:
        return weights[(source, lead, city)]
    pool = (table.get("pool_weights") or {}).get((source, lead))
    if pool is None:
        return REL_PRIOR
    # A city with no data at this lead tonight still moves at most one step
    # from the weight in force (Codex on #521): its pool's weight, stepped.
    anchor = (table.get("prev_weights") or {}).get((source, lead, city), REL_PRIOR)
    return _clip(pool, anchor - REL_MAX_STEP, anchor + REL_MAX_STEP)


def combine(forecasts, table, city, lead):
    """(combined, spread, used) from {source: forecast max}, or None with
    fewer than MIN_SOURCES corrected sources. The combination is the
    skill-weighted mean when the table carries weights, else the equal mean."""
    used, ws = {}, {}
    for source, fc in forecasts.items():
        if fc is None:
            continue
        b = correction(table, source, lead, city)
        if b is None:
            continue
        used[source] = round(fc + b, 3)
        ws[source] = weight(table, source, lead, city)
    if len(used) < MIN_SOURCES:
        return None
    vals = list(used.values())
    if table.get("weights"):
        mean = sum(used[k] * ws[k] for k in used) / sum(ws.values())
    else:
        mean = sum(vals) / len(vals)
    plain = sum(vals) / len(vals)
    spread = math.sqrt(sum((v - plain) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else None
    return mean, spread, used


def fit_weights(pairs, previous=None, stepped=True):
    """{(source, lead, city): weight} and {(source, lead): pool weight} for the
    own_w learner from pairs (city, day, source, lead, forecast, observed),
    stepped from `previous` {(source, lead, city): weight in force} (missing:
    REL_PRIOR). The target is tools/own_w_forward.target: each source's bias
    and mean squared error shrunk to its pool (K, MIN_N, BOUND_C as fit), a
    source's skill its precision over the mean precision of the sources with
    MIN_N city-days at the city; where only some have that many, they share out
    the pool skill of those same sources and the rest keep their pool's.
    stepped=False returns the targets themselves (the walk-forward score)."""
    by_lead = {}
    for city, _day, source, lead, fc, y in pairs:
        by_lead.setdefault(lead, {}).setdefault((city, source), []).append(y - fc)
    out, pools = {}, {}
    for lead, errors in sorted(by_lead.items()):
        pooled, pmse, mse, n = {}, {}, {}, {}
        for m in sorted({m for _c, m in errors}):
            allv = [x for (_c, mm), v in errors.items() if mm == m for x in v]
            pooled[m] = _clip(sum(allv) / len(allv), -BOUND_C, BOUND_C)
            pmse[m] = sum((x - pooled[m]) ** 2 for x in allv) / len(allv)
        for (c, m), v in errors.items():
            n[(c, m)] = len(v)
            b = pooled[m] if len(v) < MIN_N else pooled[m] + sum(x - pooled[m] for x in v) / (len(v) + K)
            b = _clip(b, -BOUND_C, BOUND_C)
            mse[(c, m)] = (sum((x - b) ** 2 for x in v) + K * pmse[m]) / (len(v) + K)
        pprec = {m: 1.0 / max(pmse[m], 1e-6) for m in pooled}
        pmean = sum(pprec.values()) / len(pprec)
        pool_rel = {m: _clip(pprec[m] / pmean, REL_LO, REL_HI) for m in pooled}
        for m in pooled:
            pools[(m, lead)] = pool_rel[m]
        for c in sorted({c for c, _m in errors}):
            full = [m for m in pooled if n.get((c, m), 0) >= MIN_N]
            prec = {m: 1.0 / max(mse[(c, m)], 1e-6) for m in full}
            mean = sum(prec.values()) / len(full) if full else None
            scale = 1.0 if len(full) == len(pooled) else sum(pprec[m] for m in full) / max(len(full), 1) / pmean
            for m in pooled:
                if (c, m) not in n:
                    continue
                tgt = _clip(prec[m] / mean * scale, REL_LO, REL_HI) if m in prec else pool_rel[m]
                prev = (previous or {}).get((m, lead, c), REL_PRIOR)
                out[(m, lead, c)] = _clip(tgt, prev - REL_MAX_STEP, prev + REL_MAX_STEP) if stepped else tgt
    return out, pools


def version_of(table, as_of):
    body = {"as_of": str(as_of), "k": K, "min_n": MIN_N, "bound": BOUND_C, "step": MAX_STEP_C,
            "cells": sorted((list(k) + [round(v[0], 6), v[1]]) for k, v in table["cells"].items())}
    if table.get("weights"):
        body["learner"] = OWN_W
        body["rel"] = [REL_LO, REL_HI, REL_MAX_STEP, REL_PRIOR]
        body["weights"] = sorted(list(k) + [round(w, 6)] for k, w in table["weights"].items())
        body["pool_weights"] = sorted(list(k) + [round(w, 6)] for k, w in (table.get("pool_weights") or {}).items())
    return f"station-correction:{as_of}:{hashlib.sha256(json.dumps(body, default=str).encode()).hexdigest()[:10]}"


def walk_forward(pairs, days):
    """For each scored day: fit on the days before it (inside WINDOW_DAYS),
    combine that day's lead-1 runs. Raw equal-weight mean against the
    corrected combination, on the same city-days."""
    by_day = {}
    for p in pairs:
        by_day.setdefault(p[1], []).append(p)
    raw_err, cor_err, raw_hit, cor_hit = [], [], 0, 0
    for day in days:
        start = (dt.date.fromisoformat(day) - dt.timedelta(days=WINDOW_DAYS)).isoformat()
        train = [p for p in pairs if start <= p[1] < day]
        if not train:
            continue
        table = fit(train)
        cities = {}
        for city, _d, source, lead, fc, y in by_day.get(day, []):
            if lead == 1:
                cities.setdefault(city, ({}, y))[0][source] = fc
        for city, (fcs, y) in cities.items():
            out = combine(fcs, table, city, 1)
            if out is None:
                continue
            raw = sum(fcs.values()) / len(fcs)
            raw_err.append(abs(raw - y))
            cor_err.append(abs(out[0] - y))
            raw_hit += round(raw) == round(y)
            cor_hit += round(out[0]) == round(y)
    n = len(raw_err)
    if not n:
        return {"n": 0}
    return {"n": n, "days": len(days),
            "mae_raw_mean": round(sum(raw_err) / n, 4), "mae_corrected": round(sum(cor_err) / n, 4),
            "same_integer_raw_mean": round(raw_hit / n, 4), "same_integer_corrected": round(cor_hit / n, 4)}


def walk_forward_own(pairs, days):
    """The own_w learner scored the way walk_forward scores P3.9 (Codex on
    #521): for each scored day, the bias and the weight targets fitted on the
    served-forecast pairs of the WINDOW_DAYS before it, then that day's runs of
    each lead combined. The raw mean, the own bias at equal weight and the own
    bias at its weights, on the same city-days. The nightly cells are stepped
    toward these targets, so a weighted gain here is where they are heading."""
    by_day = {}
    for p in pairs:
        by_day.setdefault(p[1], []).append(p)
    err = {lead: {"raw": [], "equal": [], "weighted": []} for lead in OWN_LEADS}
    for day in days:
        start = (dt.date.fromisoformat(day) - dt.timedelta(days=WINDOW_DAYS)).isoformat()
        train = [p for p in pairs if start <= p[1] < day]
        if not train:
            continue
        table = fit(train)
        weights, pools = fit_weights(train, stepped=False)
        wtable = dict(table, weights=weights, pool_weights=pools)
        cells = {}
        for city, _d, source, lead, fc, y in by_day.get(day, []):
            cells.setdefault((city, lead), ({}, y))[0][source] = fc
        for (city, lead), (fcs, y) in cells.items():
            eq, wt = combine(fcs, table, city, lead), combine(fcs, wtable, city, lead)
            if eq is None or wt is None:
                continue
            err[lead]["raw"].append(abs(sum(fcs.values()) / len(fcs) - y))
            err[lead]["equal"].append(abs(eq[0] - y))
            err[lead]["weighted"].append(abs(wt[0] - y))
    out = {}
    for lead, e in err.items():
        n = len(e["raw"])
        out[str(lead)] = {"n": n} if not n else {
            "n": n, "mae_raw_mean": round(sum(e["raw"]) / n, 4), "mae_own_bias_equal": round(sum(e["equal"]) / n, 4),
            "mae_own_bias_weighted": round(sum(e["weighted"]) / n, 4)}
    return {"days": len(days), "by_lead": out}


def walk_forward_against(pairs, truth, days, day_fcs):
    """walk_forward's fits (each scored day fitted on the WINDOW_DAYS before it,
    from `pairs`), scored against `truth` {(city, day): C} on the city-days of
    `day_fcs` {(city, day): {source: lead-1 forecast}} that `truth` covers.
    Two label sets given the same truth and day_fcs are scored on the same
    city-days (plan v2.4 P3.10 part 3.1)."""
    err = []
    for day in days:
        start = (dt.date.fromisoformat(day) - dt.timedelta(days=WINDOW_DAYS)).isoformat()
        train = [p for p in pairs if start <= p[1] < day]
        if not train:
            continue
        table = fit(train)
        for (city, d), fcs in day_fcs.items():
            y = truth.get((city, d))
            if d != day or y is None:
                continue
            out = combine(fcs, table, city, 1)
            if out is not None:
                err.append(out[0] - y)
    n = len(err)
    if not n:
        return {"n": 0}
    return {"n": n, "mae_c": round(sum(abs(e) for e in err) / n, 4), "bias_c": round(sum(err) / n, 4)}


def oos_errors(pairs, days):
    """{(lead, city): [station max - combination]} over `days`, each day's
    combination fitted only on the WINDOW_DAYS before it (as walk_forward)."""
    by_day = {}
    for p in pairs:
        by_day.setdefault(p[1], []).append(p)
    out = {}
    for day in days:
        start = (dt.date.fromisoformat(day) - dt.timedelta(days=WINDOW_DAYS)).isoformat()
        train = [p for p in pairs if start <= p[1] < day]
        if not train:
            continue
        table = fit(train)
        runs = {}
        for city, _d, source, lead, fc, y in by_day.get(day, []):
            runs.setdefault((city, lead), ({}, y))[0][source] = fc
        for (city, lead), (fcs, y) in sorted(runs.items()):
            res = combine(fcs, table, city, lead)
            if res is not None:
                out.setdefault((lead, city), []).append(y - res[0])
    return out


def fit_width(errors, previous=None, cities=()):
    """{"cells": {(lead, city): (width, fitted, n, mae)}, "pooled": {lead: (width, mae, n)}}.
    `fitted` is the width before tonight's step; `cities` also get a cell
    (on the pool) when they have no errors, so every width served is stepped."""
    pooled = {}
    for lead in sorted({lead for lead, _c in errors}):
        errs = [abs(e) for (l, _c), v in errors.items() if l == lead for e in v]
        if len(errs) < WIDTH_MIN_POOL:
            continue
        mae = sum(errs) / len(errs)
        pooled[lead] = (_clip(MAE_TO_SIGMA * mae, WIDTH_LO_C, WIDTH_HI_C), mae, len(errs))
    cells = {}
    for lead, (pool_width, pool_mae, _n) in pooled.items():
        for city in sorted({c for l, c in errors if l == lead} | set(cities)):
            mine = [abs(e) for e in errors.get((lead, city), [])]
            n = len(mine)
            mae = sum(mine) / n if n else None
            if n < MIN_N:
                fitted = pool_width
            else:
                fitted = _clip(MAE_TO_SIGMA * (sum(mine) + WIDTH_K * pool_mae) / (n + WIDTH_K),
                               WIDTH_LO_C, WIDTH_HI_C)
            width = fitted
            prev = (previous or {}).get((lead, city))
            if prev is not None:
                width = _clip(width, prev * (1 - WIDTH_MAX_STEP), prev * (1 + WIDTH_MAX_STEP))
                width = _clip(width, WIDTH_LO_C, WIDTH_HI_C)
            cells[(lead, city)] = (width, fitted, n, mae)
    return {"cells": cells, "pooled": pooled}


def width_for(wtable, city, lead):
    """The width for `city` at `lead`: its cell, else its lead's pool, else
    None. Lead 0 has no previous-run history; it uses lead 1 (as correction)."""
    lead = max(1, min(lead, max(LEADS)))
    cell = wtable["cells"].get((lead, city))
    if cell is not None:
        return cell[0]
    pool = wtable["pooled"].get(lead)
    return pool[0] if pool is not None else None


def width_version_of(wtable, as_of):
    body = json.dumps({"as_of": str(as_of), "days": WIDTH_DAYS, "k": WIDTH_K, "min_n": MIN_N,
                       "min_pool": WIDTH_MIN_POOL, "bounds": [WIDTH_LO_C, WIDTH_HI_C], "step": WIDTH_MAX_STEP,
                       "cells": sorted([lead, city, round(v[0], 6), v[2]]
                                       for (lead, city), v in wtable["cells"].items())},
                      default=str)
    return f"station-width:{as_of}:{hashlib.sha256(body.encode()).hexdigest()[:10]}"


# --------------------------------------------------------------------------
# reading and writing
# --------------------------------------------------------------------------

def load_start(as_of):
    """The first day any fit, score or width tonight reads: the fit's window
    before every day the walk-forward score or the width reads."""
    return (as_of - dt.timedelta(days=WINDOW_DAYS + max(EVAL_DAYS, WIDTH_DAYS))).isoformat()


def load_station_labels(rest_all, as_of):
    """The station maximum as the desk read it (derived_city_day_features).
    Only WHOLE days are truth: a row computed before its local day ended is
    the part of the day seen so far (common.day_had_ended)."""
    from common import day_had_ended
    start = load_start(as_of)
    tz = {r["city_key"]: r.get("timezone") for r in rest_all(
        "cities", [("select", "city_key,timezone")], order="city_key.asc")}
    obs = rest_all("derived_city_day_features",
                   [("select", "city_key,obs_date,max_c,computed_at"), ("obs_date", f"gte.{start}"),
                    ("obs_date", f"lt.{as_of}"), ("max_c", "not.is.null")],
                   order="city_key.asc,obs_date.asc")
    return {(r["city_key"], str(r["obs_date"])): float(r["max_c"]) for r in obs
            if day_had_ended(r["obs_date"], r.get("computed_at"), tz.get(r["city_key"]))}


def load_fit_forecasts(rest_all, as_of):
    """Each source's previous-runs maximum for every day in the window.

    Through weather_history (plan v2 P1.6 phase 2): the window is 75 days and
    the archive's keep is heading to 30, so the days the database no longer
    holds come from the archive's forecast_models dataset once it has one
    (step 4). Until the first prune this is the same read."""
    import weather_history
    return weather_history.read(
        "weather_forecast_models",
        [("select", "city_key,model,for_date,lead_days,forecast_max_c"),
         ("source", f"eq.{FIT_SOURCE}"), ("lead_days", f"lte.{max(LEADS)}"),
         ("for_date", f"gte.{load_start(as_of)}"), ("for_date", f"lt.{as_of}"),
         ("forecast_max_c", "not.is.null")],
        rest_all_fn=rest_all, order="city_key.asc,for_date.asc,model.asc,lead_days.asc", page_size=500)


def pairs_from(fcs, y):
    """(city, day, source, lead, forecast, label) for every labelled day."""
    pairs = []
    for r in fcs:
        key = (r["city_key"], str(r["for_date"]))
        if key in y and r["lead_days"] in LEADS:
            pairs.append((key[0], key[1], r["model"], int(r["lead_days"]), float(r["forecast_max_c"]), y[key]))
    return pairs


def load_pairs(rest_all, as_of, y=None):
    """pairs_from the window's forecasts. y: {(city, day): label C}; the
    station labels when not given."""
    if y is None:
        y = load_station_labels(rest_all, as_of)
    return pairs_from(load_fit_forecasts(rest_all, as_of), y)


def load_forward(rest_all, as_of):
    """{(city, for_date): (lead, {source: max}, {source: run_at})} from each
    model's latest run, whatever its age: the run times travel with the row
    (inputs_oldest_run_at / inputs_newest_run_at) so a reader can see how old
    the inputs were, not only when they were combined (audit P3, 5 Oct)."""
    rows = rest_all("weather_forecast_models",
                    [("select", "city_key,model,for_date,lead_days,forecast_max_c,run_at"),
                     ("source", f"eq.{FORWARD_SOURCE}"), ("for_date", f"gte.{as_of - dt.timedelta(days=1)}"),
                     ("forecast_max_c", "not.is.null")],
                    order="city_key.asc,for_date.asc,model.asc,run_at.desc")
    out, seen = {}, set()
    for r in rows:
        k = (r["city_key"], str(r["for_date"]), r["model"])
        if k in seen:
            continue                        # newest run first
        seen.add(k)
        lead, fcs, runs = out.setdefault((r["city_key"], str(r["for_date"])), [int(r["lead_days"]), {}, {}])
        fcs[r["model"]] = float(r["forecast_max_c"])
        runs[r["model"]] = r["run_at"]
        out[(r["city_key"], str(r["for_date"]))][0] = min(lead, int(r["lead_days"]))
    return out


def load_own_forecasts(rest_all, as_of):
    """The served forecasts (FORWARD_SOURCE) of OWN_LEADS for every day in the
    window, with their run times, through weather_history as the fit's
    previous-runs read is."""
    import weather_history
    return weather_history.read(
        "weather_forecast_models",
        [("select", "city_key,model,for_date,lead_days,forecast_max_c,run_at"),
         ("source", f"eq.{FORWARD_SOURCE}"), ("lead_days", f"lte.{max(OWN_LEADS)}"),
         ("for_date", f"gte.{load_start(as_of)}"), ("for_date", f"lt.{as_of}"),
         ("forecast_max_c", "not.is.null")],
        rest_all_fn=rest_all, order="city_key.asc,for_date.asc,model.asc,run_at.asc", page_size=500)


def own_pairs(rows, y):
    """(city, day, source, lead, forecast, label) from the served forecasts:
    one sample per city, target day and recorded lead, each source's newest
    run at that lead. Not one per UTC run date (Codex on #521): west of UTC
    two refreshes of the same local day fall on two UTC dates, and counting
    both would let fewer than MIN_N distinct days pass as MIN_N."""
    groups = {}
    for r in rows:
        key = (r["city_key"], str(r["for_date"]))
        lead = int(r["lead_days"])
        if key not in y or lead not in OWN_LEADS:
            continue
        run = str(r["run_at"]).replace(" ", "T")
        g = groups.setdefault((key, lead), {})
        if r["model"] not in g or run >= g[r["model"]][0]:
            g[r["model"]] = (run, float(r["forecast_max_c"]))
    pairs = []
    for ((city, day), lead), by_model in sorted(groups.items()):
        for model, (_run, fc) in sorted(by_model.items()):
            pairs.append((city, day, model, lead, fc, y[(city, day)]))
    return pairs


def learner_of(rest_all):
    """OWN_W while settings.station_correction_learner names it and is enabled,
    else P39 (the previous-runs fit at equal weights)."""
    try:
        rows = rest_all("settings", [("select", "key,value"), ("key", "eq.station_correction_learner")],
                        order="key.asc")
    except Exception:
        return P39
    v = (rows[0].get("value") if rows else None) or {}
    if isinstance(v, str):
        v = json.loads(v)
    return OWN_W if v.get("enabled") is True and v.get("learner") == OWN_W else P39


def input_runs(runs, used):
    """(oldest, newest) run_at, as ISO UTC, of the sources the combination used."""
    at = sorted(dt.datetime.fromisoformat(str(runs[s]).replace("Z", "+00:00")).astimezone(dt.timezone.utc)
                for s in used)
    return at[0].isoformat(), at[-1].isoformat()


def anchor_before(row, as_of, value="bias_c", prev="prev_bias_c"):
    """(value, its as_of) in force BEFORE the night `as_of`, or None (plan v2.3 P5.14).

    A row carries tonight's value and the one it stepped from. A second run on
    the same night (the Relearn webhook, a manual dispatch, a GitHub re-run)
    must step from what the night started with, not from the first run's
    output: stepping from its own output moves a held-back cell 0.50 C in one
    night instead of 0.25. So the anchor is the newer of the two that is dated
    before `as_of`."""
    tonight = str(as_of)
    for v, when in ((row.get(value), row.get("as_of")), (row.get(prev), row.get("prev_as_of"))):
        if v is not None and when is not None and str(when) < tonight:
            return v, str(when)
    return None


def load_previous(rest_all, as_of, weights=None):
    """{cell: (bias, as_of)} in force before the night `as_of`. With `weights`
    a dict, it is filled with {cell: (weight, as_of)} the same way."""
    cols = "city_key,source,lead_days,bias_c,as_of,prev_bias_c,prev_as_of"
    if weights is not None:
        cols += ",rel_weight,prev_rel_weight"
    rows = rest_all("derived_station_correction", [("select", cols)],
                    order="city_key.asc,source.asc,lead_days.asc")
    out = {}
    for r in rows:
        cell = (r["source"], int(r["lead_days"]), r["city_key"])
        a = anchor_before(r, as_of)
        if a is not None:
            out[cell] = (float(a[0]), a[1])
        if weights is not None:
            w = anchor_before(r, as_of, value="rel_weight", prev="prev_rel_weight")
            if w is not None:
                weights[cell] = (float(w[0]), w[1])
    return out


def load_previous_width(rest_all, as_of):
    """{(lead, city): (width, as_of)} in force before the night `as_of` (P5.14)."""
    rows = rest_all("derived_station_width",
                    [("select", "city_key,lead_days,sigma_c,as_of,prev_sigma_c,prev_as_of")],
                    order="city_key.asc,lead_days.asc")
    out = {}
    for r in rows:
        a = anchor_before(r, as_of, value="sigma_c", prev="prev_sigma_c")
        if a is not None:
            out[(int(r["lead_days"]), r["city_key"])] = (float(a[0]), a[1])
    return out


def main(argv=None, today=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from common import rest_all, upsert_replace, log_run, get_cities
    import venue_truth

    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else (
        today or dt.datetime.now(dt.timezone.utc).date())
    # What the corrections learn from (plan v2.4 P3.10 part 3.1): the venue's
    # truth unless settings.truth_labels says "station". The other set is
    # fitted too, and both are scored on the venue's truth on the same
    # city-days, so every night's log says whether the choice holds.
    label_source = venue_truth.source(rest_all)
    icao = {c["city_key"]: c.get("icao") for c in get_cities(require_coords=False)}
    labels = {"venue": venue_truth.load(rest_all, load_start(as_of), as_of, icao=icao),
              "station": load_station_labels(rest_all, as_of)}
    fcs = load_fit_forecasts(rest_all, as_of)
    pairs = pairs_from(fcs, labels[label_source])
    days = sorted({p[1] for p in pairs})
    score = walk_forward(pairs, days[-EVAL_DAYS:])
    day_fcs = {}
    for r in fcs:
        if r["lead_days"] == 1:
            day_fcs.setdefault((r["city_key"], str(r["for_date"])), {})[r["model"]] = float(r["forecast_max_c"])
    truth_days = sorted({d for _c, d in labels["venue"] if d < str(as_of)})[-EVAL_DAYS:]
    against = {name: walk_forward_against(pairs_from(fcs, y), labels["venue"], truth_days, day_fcs)
               for name, y in labels.items()}

    window_start = (as_of - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    # No fallback to "no previous": a read that fails would otherwise fit every
    # cell with no step bound at all, and the upsert below needs the same table.
    # A failed night leaves last night's cells in force (the engine reads rows
    # younger than 36 h), which is Rule 11's safe side.
    learner = learner_of(rest_all)
    prev_weights = {} if learner == OWN_W else None
    previous = load_previous(rest_all, as_of, prev_weights)
    table = fit([p for p in pairs if p[1] >= window_start], {k: v[0] for k, v in previous.items()})
    own_detail = None
    if learner == OWN_W:
        # Leads 0-2 from the served forecasts, skill-weighted; lead 3 keeps the
        # previous-runs fit. A lead-0 cell with no night before it steps from
        # the lead-1 cell the engine used for lead 0 until tonight.
        own = own_pairs(load_own_forecasts(rest_all, as_of), labels[label_source])
        own_in = [p for p in own if p[1] >= window_start]
        anchors = {k: v[0] for k, v in previous.items()}
        for (source, lead, city), b in list(anchors.items()):
            if lead == 1:
                anchors.setdefault((source, 0, city), b)
        own_table = fit(own_in, anchors)
        weights, pool_weights = fit_weights(own_in, {k: v[0] for k, v in prev_weights.items()})
        table = {"cells": {**{k: v for k, v in table["cells"].items() if k[1] not in OWN_LEADS}, **own_table["cells"]},
                 "pooled": {**{k: v for k, v in table["pooled"].items() if k[1] not in OWN_LEADS}, **own_table["pooled"]},
                 "weights": weights, "pool_weights": pool_weights,
                 "prev_weights": {k: v[0] for k, v in prev_weights.items()}}
        ws = list(weights.values())
        own_days = sorted({p[1] for p in own})
        own_detail = {"walk_forward": walk_forward_own(own, own_days[-EVAL_DAYS:]),
                      "pairs": len(own_in), "cells": len(own_table["cells"]), "weights": len(ws),
                      "weight_range": [round(min(ws), 4), round(max(ws), 4)] if ws else None,
                      "weights_at_a_bound": sum(1 for w in ws if w in (REL_LO, REL_HI)),
                      "weights_held_by_step": sum(1 for k, w in weights.items()
                                                  if abs(w - prev_weights.get(k, (REL_PRIOR,))[0]) >= REL_MAX_STEP - 1e-12)}
    version = version_of(table, as_of)

    # computed_at travels on every row. merge-duplicates updates only the
    # columns the payload carries: a default of now() fires on INSERT and
    # never again (measured on derived_forecast_postprocess, 22 Sep). The
    # engine takes derived_corrected_forecast rows younger than max_age_hours
    # by this column, so an open day's row first written two nights ago and
    # re-fitted since would look 36 h old and price from the public forecast.
    computed_at = dt.datetime.now(dt.timezone.utc).isoformat()

    # Each cell keeps the value it stepped from, so a re-run tonight steps from
    # the same place and writes the same numbers (plan v2.3 P5.14).
    cell_rows = [{"city_key": city, "source": source, "lead_days": lead, "bias_c": round(b, 4),
                  "pooled_bias_c": round(table["pooled"][(source, lead)][0], 4), "n": n,
                  "n_pooled": table["pooled"][(source, lead)][1], "version": version, "as_of": str(as_of),
                  "prev_bias_c": previous[(source, lead, city)][0] if (source, lead, city) in previous else None,
                  "prev_as_of": previous[(source, lead, city)][1] if (source, lead, city) in previous else None,
                  "learner": learner if (learner == P39 or lead in OWN_LEADS) else P39,
                  "rel_weight": round(table["weights"][(source, lead, city)], 4)
                  if (source, lead, city) in table.get("weights", {}) else None,
                  "pooled_rel_weight": round(table["pool_weights"][(source, lead)], 4)
                  if (source, lead) in table.get("pool_weights", {}) else None,
                  "prev_rel_weight": prev_weights[(source, lead, city)][0]
                  if prev_weights and (source, lead, city) in prev_weights else None,
                  "computed_at": computed_at}
                 for (source, lead, city), (b, n) in sorted(table["cells"].items())]
    forward = load_forward(rest_all, as_of)

    # The width around this centre (plan v2.3 P3.9 part 3), from the same
    # pairs. Shadow: a failure here must not cost the correction the engine
    # prices from, so it leaves tonight without widths (every open day's
    # width_c null, the engine keeps its own) and says so in the log. It never
    # fits without the step bound: a failed read of last night's widths is a
    # failure, not "no previous".
    width_days = [d for d in days if d >= (as_of - dt.timedelta(days=WIDTH_DAYS)).isoformat()]
    wtable, wversion, width_error, prev_width = None, None, None, {}
    try:
        prev_width = load_previous_width(rest_all, as_of)
        wtable = fit_width(oos_errors(pairs, width_days), {k: v[0] for k, v in prev_width.items()},
                           cities={city for city, _day in forward})
        wversion = width_version_of(wtable, as_of) if wtable["cells"] else None
    except Exception as e:
        wtable, width_error = None, f"{type(e).__name__}: {str(e)[:200]}"
    width_rows = []
    if wversion:
        width_rows = [{"city_key": city, "lead_days": lead, "sigma_c": round(w, 4),
                       "fitted_sigma_c": round(fitted, 4),
                       "pooled_sigma_c": round(wtable["pooled"][lead][0], 4),
                       "mae_c": round(mae, 4) if mae is not None else None,
                       "pooled_mae_c": round(wtable["pooled"][lead][1], 4), "n": n,
                       "n_pooled": wtable["pooled"][lead][2], "version": wversion, "as_of": str(as_of),
                       "prev_sigma_c": prev_width[(lead, city)][0] if (lead, city) in prev_width else None,
                       "prev_as_of": prev_width[(lead, city)][1] if (lead, city) in prev_width else None,
                       "computed_at": computed_at}
                      for (lead, city), (w, fitted, n, mae) in sorted(wtable["cells"].items())]

    fwd_rows = []
    for (city, day), (lead, fcs, runs) in sorted(forward.items()):
        out = combine(fcs, table, city, lead)
        if out is None:
            continue
        mean, spread, used = out
        oldest, newest = input_runs(runs, used)
        width = width_for(wtable, city, lead) if wversion else None
        fwd_rows.append({"city_key": city, "for_date": day, "lead_days": lead, "combined_c": round(mean, 3),
                         "spread_c": round(spread, 3) if spread is not None else None,
                         "n_sources": len(used), "sources": used, "version": version,
                         "width_c": round(width, 4) if width is not None else None,
                         "width_version": wversion if width is not None else None,
                         "inputs_oldest_run_at": oldest, "inputs_newest_run_at": newest,
                         "computed_at": computed_at})

    written = 0
    if not args.dry_run:
        written += upsert_replace("derived_station_correction", cell_rows, "city_key,source,lead_days")
        if width_rows:
            written += upsert_replace("derived_station_width", width_rows, "city_key,lead_days")
        written += upsert_replace("derived_corrected_forecast", fwd_rows, "city_key,for_date")
    width_detail = {"error": width_error} if width_error else {
        "version": wversion, "cells": len(width_rows), "days": len(width_days),
        "stepped": sum(1 for r in width_rows if r["sigma_c"] != r["fitted_sigma_c"]),
        "pooled": {str(lead): {"width_c": round(w, 4), "mae_c": round(m, 4), "n": n}
                   for lead, (w, m, n) in sorted((wtable or {"pooled": {}})["pooled"].items())}}
    detail = {"as_of": str(as_of), "version": version, "learner": learner, "own_w": own_detail,
              "pairs": len(pairs), "cells": len(cell_rows),
              "cities": len({r["city_key"] for r in cell_rows}), "forward_days": len(fwd_rows),
              "load_days": WINDOW_DAYS + max(EVAL_DAYS, WIDTH_DAYS),
              "walk_forward": score, "walk_forward_is": "the previous-runs fit (P3.9); own_w's is under own_w",
              "width": width_detail,
              "labels": {"source": label_source, "venue_days": len(labels["venue"]),
                         "station_days": len(labels["station"]), "scored_on_venue_truth": against},
              "priors": {"k": K, "min_n": MIN_N, "bound_c": BOUND_C, "max_step_c": MAX_STEP_C,
                         "min_sources": MIN_SOURCES, "window_days": WINDOW_DAYS,
                         "width_days": WIDTH_DAYS, "width_k": WIDTH_K, "width_min_pool": WIDTH_MIN_POOL,
                         "width_bounds_c": [WIDTH_LO_C, WIDTH_HI_C], "width_max_step": WIDTH_MAX_STEP,
                         "own_w": {"leads": list(OWN_LEADS), "rel_bounds": [REL_LO, REL_HI],
                                   "rel_max_step": REL_MAX_STEP, "rel_prior": REL_PRIOR}},
              "dry_run": args.dry_run}
    print(json.dumps(detail, indent=2))
    if not args.dry_run:
        log_run(JOB, "ok" if cell_rows and not width_error else "attention", written, detail)
    return detail


if __name__ == "__main__":
    main()
    sys.exit(0)
