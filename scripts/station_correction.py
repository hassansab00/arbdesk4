#!/usr/bin/env python3
"""Each forecast source's error at the settlement station, then their
combination (plan v2.2 P3.9).

The public weather models already hold most of what is knowable about the
atmosphere. What they do not know is the settlement station: its exposure, its
height against the model's grid cell, its coast. So each source's error at the
station is learned, per city and lead, and the corrected sources are combined.

    error(source, lead, city) = station max - source max

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

SHADOW. Nothing prices from the output until it has beaten the engine's input
on later dates.

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


# --------------------------------------------------------------------------
# reading and writing
# --------------------------------------------------------------------------

def load_pairs(rest_all, as_of):
    """Only WHOLE days are truth: a row computed before its local day ended is
    the part of the day seen so far (common.day_had_ended)."""
    from common import day_had_ended
    start = (as_of - dt.timedelta(days=WINDOW_DAYS + EVAL_DAYS)).isoformat()
    tz = {r["city_key"]: r.get("timezone") for r in rest_all(
        "cities", [("select", "city_key,timezone")], order="city_key.asc")}
    obs = rest_all("derived_city_day_features",
                   [("select", "city_key,obs_date,max_c,computed_at"), ("obs_date", f"gte.{start}"),
                    ("obs_date", f"lt.{as_of}"), ("max_c", "not.is.null")],
                   order="city_key.asc,obs_date.asc")
    y = {(r["city_key"], str(r["obs_date"])): float(r["max_c"]) for r in obs
         if day_had_ended(r["obs_date"], r.get("computed_at"), tz.get(r["city_key"]))}
    fcs = rest_all("weather_forecast_models",
                   [("select", "city_key,model,for_date,lead_days,forecast_max_c"),
                    ("source", f"eq.{FIT_SOURCE}"), ("lead_days", f"lte.{max(LEADS)}"),
                    ("for_date", f"gte.{start}"), ("for_date", f"lt.{as_of}"),
                    ("forecast_max_c", "not.is.null")],
                   order="city_key.asc,for_date.asc,model.asc,lead_days.asc")
    pairs = []
    for r in fcs:
        key = (r["city_key"], str(r["for_date"]))
        if key in y and r["lead_days"] in LEADS:
            pairs.append((key[0], key[1], r["model"], int(r["lead_days"]), float(r["forecast_max_c"]), y[key]))
    return pairs


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


def main(argv=None, today=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from common import rest_all, upsert_replace, log_run

    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else (
        today or dt.datetime.now(dt.timezone.utc).date())
    pairs = load_pairs(rest_all, as_of)
    days = sorted({p[1] for p in pairs})
    score = walk_forward(pairs, days[-EVAL_DAYS:])

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
    fwd_rows = []
    for (city, day), (lead, fcs) in sorted(load_forward(rest_all, as_of).items()):
        out = combine(fcs, table, city, lead)
        if out is None:
            continue
        mean, spread, used = out
        fwd_rows.append({"city_key": city, "for_date": day, "lead_days": lead, "combined_c": round(mean, 3),
                         "spread_c": round(spread, 3) if spread is not None else None,
                         "n_sources": len(used), "sources": used, "version": version,
                         "computed_at": computed_at})

    written = 0
    if not args.dry_run:
        written += upsert_replace("derived_station_correction", cell_rows, "city_key,source,lead_days")
        written += upsert_replace("derived_corrected_forecast", fwd_rows, "city_key,for_date")
    detail = {"as_of": str(as_of), "version": version, "pairs": len(pairs), "cells": len(cell_rows),
              "cities": len({r["city_key"] for r in cell_rows}), "forward_days": len(fwd_rows),
              "walk_forward": score,
              "priors": {"k": K, "min_n": MIN_N, "bound_c": BOUND_C, "max_step_c": MAX_STEP_C,
                         "min_sources": MIN_SOURCES, "window_days": WINDOW_DAYS},
              "dry_run": args.dry_run}
    print(json.dumps(detail, indent=2))
    if not args.dry_run:
        log_run(JOB, "ok" if cell_rows else "attention", written, detail)
    return detail


if __name__ == "__main__":
    main()
    sys.exit(0)
