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


def correction(table, source, lead, city):
    """The bias to add to `source` at `lead` for `city`: its cell, else its
    pool, else nothing. Lead 0 has no previous-run history; it uses lead 1."""
    lead = max(1, min(lead, max(LEADS)))
    cell = table["cells"].get((source, lead, city))
    if cell is not None:
        return cell[0]
    pool = table["pooled"].get((source, lead))
    return pool[0] if pool is not None else None


def combine(forecasts, table, city, lead):
    """(combined, spread, used) from {source: forecast max}, or None with
    fewer than MIN_SOURCES corrected sources."""
    used = {}
    for source, fc in forecasts.items():
        if fc is None:
            continue
        b = correction(table, source, lead, city)
        if b is None:
            continue
        used[source] = round(fc + b, 3)
    if len(used) < MIN_SOURCES:
        return None
    vals = list(used.values())
    mean = sum(vals) / len(vals)
    spread = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else None
    return mean, spread, used


def version_of(table, as_of):
    body = json.dumps({"as_of": str(as_of), "k": K, "min_n": MIN_N, "bound": BOUND_C, "step": MAX_STEP_C,
                       "cells": sorted((list(k) + [round(v[0], 6), v[1]]) for k, v in table["cells"].items())},
                      default=str)
    return f"station-correction:{as_of}:{hashlib.sha256(body.encode()).hexdigest()[:10]}"


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
    """{(city, for_date): (lead, {source: max})} from each model's latest run."""
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
        lead, fcs = out.setdefault((r["city_key"], str(r["for_date"])), [int(r["lead_days"]), {}])
        fcs[r["model"]] = float(r["forecast_max_c"])
        out[(r["city_key"], str(r["for_date"]))][0] = min(lead, int(r["lead_days"]))
    return out


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


def load_previous(rest_all, as_of):
    """{cell: (bias, as_of)} in force before the night `as_of`."""
    rows = rest_all("derived_station_correction",
                    [("select", "city_key,source,lead_days,bias_c,as_of,prev_bias_c,prev_as_of")],
                    order="city_key.asc,source.asc,lead_days.asc")
    out = {}
    for r in rows:
        a = anchor_before(r, as_of)
        if a is not None:
            out[(r["source"], int(r["lead_days"]), r["city_key"])] = (float(a[0]), a[1])
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
    previous = load_previous(rest_all, as_of)
    table = fit([p for p in pairs if p[1] >= window_start], {k: v[0] for k, v in previous.items()})
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
    for (city, day), (lead, fcs) in sorted(forward.items()):
        out = combine(fcs, table, city, lead)
        if out is None:
            continue
        mean, spread, used = out
        width = width_for(wtable, city, lead) if wversion else None
        fwd_rows.append({"city_key": city, "for_date": day, "lead_days": lead, "combined_c": round(mean, 3),
                         "spread_c": round(spread, 3) if spread is not None else None,
                         "n_sources": len(used), "sources": used, "version": version,
                         "width_c": round(width, 4) if width is not None else None,
                         "width_version": wversion if width is not None else None,
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
    detail = {"as_of": str(as_of), "version": version, "pairs": len(pairs), "cells": len(cell_rows),
              "cities": len({r["city_key"] for r in cell_rows}), "forward_days": len(fwd_rows),
              "load_days": WINDOW_DAYS + max(EVAL_DAYS, WIDTH_DAYS),
              "walk_forward": score, "width": width_detail,
              "labels": {"source": label_source, "venue_days": len(labels["venue"]),
                         "station_days": len(labels["station"]), "scored_on_venue_truth": against},
              "priors": {"k": K, "min_n": MIN_N, "bound_c": BOUND_C, "max_step_c": MAX_STEP_C,
                         "min_sources": MIN_SOURCES, "window_days": WINDOW_DAYS,
                         "width_days": WIDTH_DAYS, "width_k": WIDTH_K, "width_min_pool": WIDTH_MIN_POOL,
                         "width_bounds_c": [WIDTH_LO_C, WIDTH_HI_C], "width_max_step": WIDTH_MAX_STEP},
              "dry_run": args.dry_run}
    print(json.dumps(detail, indent=2))
    if not args.dry_run:
        log_run(JOB, "ok" if cell_rows and not width_error else "attention", written, detail)
    return detail


if __name__ == "__main__":
    main()
    sys.exit(0)
