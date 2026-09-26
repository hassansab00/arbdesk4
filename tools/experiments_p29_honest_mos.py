"""The experiment behind plan v2.2 P2.9 (26 Sep), kept so its numbers can be re-run.

QUESTION. The desk's own weather model (scripts/weather_model.py) was fitted on
afternoon weather that had already been OBSERVED, then asked to predict from
FORECAST weather. Forward it lost to the public forecast by 0.969 C on average
(derived_model_promotion, 26 Sep). Does a model trained only on what was
knowable in advance do better - and better than what the desk now runs, the
P3.9 station-corrected combination of seven models?

INPUTS, all from before the cutoff:
  * Open-Meteo's Previous Runs archive, hourly in local time, 14 Jul 2025 to
    25 Sep 2026, fetched 26 Sep through pg_net (the sandbox's shared address
    was over Open-Meteo's daily limit) and reduced to daily values in SQL:
      - best_match `_previous_day1/2`: temperature, dewpoint, cloud cover,
        shortwave radiation, wind, precipitation, pressure  (rows.jsonl)
      - seven models' `_previous_day1/2` temperature: ECMWF IFS 0.25, GFS,
        ICON, UKMO, JMA, GEM, Meteo-France                   (models.jsonl)
    A `_previous_dayN` value for hour H is an N*24..N*24+23 h forecast, so its
    run started by H - N*24 h. Lead 1 uses hours 00-17 only for the maximum
    (tmax17): a run started by 17:00 the day before, published a few hours
    later, is out before the local midnight the day-ahead call is frozen at.
    The exact publication time of each run is not verified here (plan P2.6,
    P7.2); daily sums of precipitation and radiation still include later hours.
  * The station maximum last known at the cutoff: the day before the target
    at lead 1, two days before at lead 2.
  * The day of the year.
LABEL: the station's observed maximum, derived_city_day_features.max_c.

METHODS, all walk-forward. Each test month is predicted only by models fitted
on days before it; P3.9 is refitted every day on the 45 days before it,
exactly as scripts/station_correction.py does at night (its own fit, combine
and step limit are imported, not re-written).
  raw      the best_match forecast maximum, as the engine reads it
  p39      the P3.9 combination: each of the seven models plus its shrunk
           station bias, equal-weighted (the live input from 27 Sep)
  mos      ridge regression per city on the residual (station max minus the
           seven models' mean tmax17), its coefficients shrunk toward a pooled
           fit; inputs: that mean, the models' spread, four models' deviations
           from it, the best_match heating variables, the last known station
           max, the season. Recency-weighted. Penalty and half-life chosen on
           the last 20% of the training days, in time order.
  blend    the mean of p39 and mos

Scored on the city-days where every method has a value. Bootstrap by date.

    python tools/experiments_p29_honest_mos.py data/training/previous_runs/best_match_daily.csv.gz \
        data/training/previous_runs/models_daily.csv.gz labels.json units.json

labels.json is the saved answer to
    select json_agg(json_build_array(city_key, obs_date, max_c, n_obs, morning_temp_c)
                    order by city_key, obs_date)
      from derived_city_day_features where max_c is not null;
(22,527 city-days on 26 Sep) and units.json to
    select json_object_agg(city_key, unit) from cities;

Needs numpy (research only, not a platform dependency).

HOW THE RECORD WAS MADE (26 Sep), so it can be made again.

1. Request, four cities at a time, from the database (pg_net is asynchronous;
   the answers land in net._http_response and are kept 6 hours):

    select c.city_key, net.http_get(format('https://previous-runs-api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&hourly=%s&start_date=2025-07-14&end_date=2026-09-25&timezone=auto',
      c.latitude, c.longitude,
      (select string_agg(v || '_previous_day' || l, ',') from unnest(array['temperature_2m','dew_point_2m','cloud_cover','shortwave_radiation','wind_speed_10m','precipitation','pressure_msl']) v, unnest(array[1,2]) l)),
      timeout_milliseconds := 60000) id
    from cities c where c.city_key in (...four at a time: Open-Meteo refuses more than ~5 concurrent...);

   and for the seven models the same with
   hourly=temperature_2m_previous_day1,temperature_2m_previous_day2
   &models=ecmwf_ifs025,gfs_seamless,icon_seamless,ukmo_seamless,jma_seamless,gem_seamless,meteofrance_seamless

2. Reduce each answer to days (__MAP__ = the (city, request id) pairs):

    with r as materialized (
      select m.city_key, h.content::jsonb j
        from (values __MAP__) m(city_key, id)
        join net._http_response h on h.id = m.id and h.status_code = 200
    ), hrs as (
      select r.city_key, l.lead, u.*
        from r cross join (values (1),(2)) l(lead)
        cross join lateral unnest(
          array(select x::timestamp from jsonb_array_elements_text(r.j->'hourly'->'time') x),
          array(select x::numeric from jsonb_array_elements_text(r.j->'hourly'->('temperature_2m_previous_day'||l.lead)) x),
          array(select x::numeric from jsonb_array_elements_text(r.j->'hourly'->('dew_point_2m_previous_day'||l.lead)) x),
          array(select x::numeric from jsonb_array_elements_text(r.j->'hourly'->('cloud_cover_previous_day'||l.lead)) x),
          array(select x::numeric from jsonb_array_elements_text(r.j->'hourly'->('shortwave_radiation_previous_day'||l.lead)) x),
          array(select x::numeric from jsonb_array_elements_text(r.j->'hourly'->('wind_speed_10m_previous_day'||l.lead)) x),
          array(select x::numeric from jsonb_array_elements_text(r.j->'hourly'->('precipitation_previous_day'||l.lead)) x),
          array(select x::numeric from jsonb_array_elements_text(r.j->'hourly'->('pressure_msl_previous_day'||l.lead)) x)
        ) u(ts, tt, td, cc, sw, ws, pr, pm)
    ), d as (
      select city_key, lead, ts::date d,
        max(tt) tmax, max(tt) filter (where extract(hour from ts) <= 17) tmax17, min(tt) filter (where extract(hour from ts) <= 8) tmin,
        max(tt) filter (where extract(hour from ts) = 8) t08, max(td) filter (where extract(hour from ts) = 8) td08,
        avg(td) filter (where extract(hour from ts) between 9 and 17) td_day,
        avg(cc) filter (where extract(hour from ts) between 9 and 17) cc_day, max(cc) filter (where extract(hour from ts) between 9 and 17) cc_max,
        sum(sw) filter (where extract(hour from ts) between 6 and 17) sw_sum,
        avg(ws) filter (where extract(hour from ts) between 9 and 17) ws_day,
        sum(pr) filter (where extract(hour from ts) <= 17) pr_sum, max(pm) filter (where extract(hour from ts) = 8) p08,
        count(tt) n
      from hrs group by 1,2,3
    )
    select json_agg(json_build_array(city_key, lead, d, round(tmax,1), round(tmax17,1), round(tmin,1), round(t08,1), round(td08,1), round(td_day,2),
      round(cc_day,1), cc_max, round(sw_sum), round(ws_day,2), round(pr_sum,2), round(p08,1), n) order by city_key, lead, d)
    from d where n >= 20;

   and for the models:

    with fire as materialized (
      select c.city_key, net.http_get(format('https://previous-runs-api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&hourly=temperature_2m_previous_day1,temperature_2m_previous_day2&models=ecmwf_ifs025,gfs_seamless,icon_seamless,ukmo_seamless,jma_seamless,gem_seamless,meteofrance_seamless&start_date=2025-07-14&end_date=2026-09-25&timezone=auto',
        c.latitude, c.longitude), timeout_milliseconds := 60000) id
      from cities c where c.city_key = any(__NEXT__) and __GUARD__
    ), r as materialized (
      select m.city_key, h.content::jsonb j
        from (values __MAP__) m(city_key, id)
        join net._http_response h on h.id = m.id and h.status_code = 200
    ), t as (
      select r.city_key, array(select x::timestamp from jsonb_array_elements_text(r.j->'hourly'->'time') x) ts, r.j from r
    ), s as (
      select t.city_key, k.key, u.ts, u.v
        from t cross join lateral jsonb_each(t.j->'hourly') k
        cross join lateral unnest(t.ts, array(select x::numeric from jsonb_array_elements_text(k.value) x)) u(ts, v)
       where k.key like 'temperature_2m_previous_day%'
    ), d as (
      select city_key, substr(key, 28, 1)::int lead, substr(key, 30) model, ts::date d,
             max(v) tmax, max(v) filter (where extract(hour from ts) <= 17) tmax17, count(v) n
        from s group by 1,2,3,4
    )
    select (select json_agg(json_build_array(city_key, id)) from fire) fired,
           json_agg(json_build_array(city_key, lead, model, d, round(tmax,1), round(tmax17,1)) order by city_key, lead, model, d)
      from d where n >= 20;

   (the models query's `fire` CTE requested the next four cities in the same
   call; drop it to reduce only.)
"""
import json, sys, math, random, datetime as dt
from collections import defaultdict
import numpy as np

sys.path.insert(0, "scripts")
import station_correction as sc

rows_path, models_path, labels_path, units_path = sys.argv[1:5]
# rows: city, lead, date, tmax, tmax17, tmin, t08, td08, td_day, cc_day, cc_max, sw_sum, ws_day, pr_sum, p08, n
def records(path):
    """The committed data/training/previous_runs/*.csv.gz, or the JSON lines
    the SQL above returns; numbers as floats, empty as None."""
    if path.endswith(".csv.gz"):
        import csv, gzip
        with gzip.open(path, "rt") as f:
            rd = csv.reader(f)
            header = next(rd)
            text = {i for i, h in enumerate(header) if h in ("city_key", "model", "for_date")}
            for row in rd:
                yield [v if i in text else (None if v == "" else float(v)) for i, v in enumerate(row)]
    else:
        yield from map(json.loads, open(path))

F = {(r[0], int(r[1]), r[2]): r for r in records(rows_path)}
# models: city, lead, model, date, tmax, tmax17
M = defaultdict(dict)
for c, L, m, d, tmax, tmax17 in records(models_path):
    M[(c, int(L), d)][m] = (tmax, tmax17)
Y = {(c, d): float(m) for c, d, m, *_ in json.load(open(labels_path))}
UNIT = json.load(open(units_path))
MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "ukmo_seamless", "jma_seamless", "gem_seamless",
          "meteofrance_seamless"]
DEV = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "jma_seamless"]
FIRST_TEST = dt.date(2025, 11, 1)
# The last station max the model may see: `lead` days before the target at a
# midnight cutoff; one day more for a job that runs before the day has ended
# (the nightly fit at ~05Z UTC). P2.9_PREV_EXTRA_LAG=1 measures that case.
import os
PREV_EXTRA_LAG = int(os.environ.get("P2.9_PREV_EXTRA_LAG".replace(".", "_"), "0"))

def day(s): return dt.date.fromisoformat(s)

# ---------------------------------------------------------------- the rows
def build(lead):
    out = []
    for (city, L, ds), r in F.items():
        if L != lead:
            continue
        y = Y.get((city, ds))
        mods = M.get((city, lead, ds), {})
        m17 = [mods[m][1] for m in MODELS if m in mods and mods[m][1] is not None]
        if y is None or r[3] is None or len(m17) < 4:
            continue
        d = day(ds)
        prev = Y.get((city, (d - dt.timedelta(days=lead + PREV_EXTRA_LAG)).isoformat()))
        _, _, _, tmax, tmax17, tmin, t08, td08, td_day, cc_day, cc_max, sw, ws, pr, p08, n = r
        mean = sum(m17) / len(m17)
        spread = float(np.std(m17))
        devs = [mods[m][1] - mean if m in mods and mods[m][1] is not None else None for m in DEV]
        doy = 2 * math.pi * d.timetuple().tm_yday / 365.25
        x = [mean, spread] + devs + [t08, (t08 - td08) if None not in (t08, td08) else None, td_day, cc_day,
                                     cc_max, sw, ws, pr, p08, (prev - mean) if prev is not None else None,
                                     math.sin(doy), math.cos(doy)]
        if any(v is None for v in x):
            continue
        out.append({"city": city, "date": d, "y": y, "raw": float(tmax), "mean": mean,
                    "full": {m: mods[m][0] for m in MODELS if m in mods and mods[m][0] is not None},
                    "x": np.array(x, float)})
    return out

# ---------------------------------------------------------------- P3.9, as live
def p39_predictions(data, lead, test_days):
    """Refit every day on the 45 days before it, stepping from the previous
    night's table, and combine that day's runs - scripts/station_correction."""
    by_day = defaultdict(list)
    for r in data:
        by_day[r["date"]].append(r)
    pairs_by_day = {d: [(r["city"], d.isoformat(), m, lead, fc, r["y"]) for r in rs for m, fc in r["full"].items()]
                    for d, rs in by_day.items()}
    out, previous = {}, None
    for d in sorted(test_days):
        train = [p for k in range(1, sc.WINDOW_DAYS + 1)
                 for p in pairs_by_day.get(d - dt.timedelta(days=k), [])]
        if not train:
            continue
        table = sc.fit(train, previous)
        previous = {k: v[0] for k, v in table["cells"].items()}
        for r in by_day.get(d, []):
            res = sc.combine(r["full"], table, r["city"], lead)
            if res is not None:
                out[(r["city"], d)] = res[0]
    return out

# ---------------------------------------------------------------- the MOS
LAMS = [3, 10, 30, 100, 300, 1000]

def ridge(X, y, lam, prior, w):
    P = lam * np.eye(X.shape[1]); P[0, 0] = 0.0
    Xw = X * w[:, None]
    return np.linalg.solve(Xw.T @ X + P, Xw.T @ y + P @ prior)

def mos_predict(train, test):
    allx = np.array([r["x"] for r in train]); mu, sd = allx.mean(0), allx.std(0) + 1e-9
    D = lambda rows: np.hstack([np.ones((len(rows), 1)), (np.array([r["x"] for r in rows]) - mu) / sd])
    res = lambda rows: np.array([r["y"] - r["mean"] for r in rows])
    def w(rows, hl):
        end = max(r["date"] for r in rows)
        return np.array([0.5 ** ((end - r["date"]).days / hl) for r in rows])
    def fit(tr, lp, lc, hl):
        bpool = ridge(D(tr), res(tr), lp, np.zeros(allx.shape[1] + 1), w(tr, hl))
        by = defaultdict(list)
        for r in tr: by[r["city"]].append(r)
        return bpool, {c: ridge(D(rs), res(rs), lc, bpool, w(rs, hl)) for c, rs in by.items()}
    def pred(model, rows):
        bpool, bc = model
        X = D(rows)
        return np.array([r["mean"] + X[i] @ bc.get(r["city"], bpool) for i, r in enumerate(rows)])
    dates = sorted({r["date"] for r in train}); cut = dates[int(len(dates) * 0.8)]
    itr = [r for r in train if r["date"] < cut]; ite = [r for r in train if r["date"] >= cut]
    best = None
    for hl in (30, 90, 365):
        for lp in (1, 10, 100):
            for lc in LAMS:
                e = np.mean(np.abs(pred(fit(itr, lp, lc, hl), ite) - np.array([r["y"] for r in ite])))
                if best is None or e < best[0]:
                    best = (e, lp, lc, hl)
    return pred(fit(train, *best[1:]), test), best[1:]

# ---------------------------------------------------------------- scoring
def whole(v, unit): return round(v * 9 / 5 + 32) if unit == "F" else round(v)

METHODS = ("raw", "p39", "mos", "blend")

def run(lead):
    data = build(lead)
    months = sorted({(r["date"].year, r["date"].month) for r in data})
    test_days = sorted({r["date"] for r in data if r["date"] >= FIRST_TEST})
    p39 = p39_predictions(data, lead, test_days)
    scored, chosen = [], {}
    for yy, mm in months:
        start = dt.date(yy, mm, 1)
        if start < FIRST_TEST:
            continue
        train = [r for r in data if r["date"] < start]
        test = [r for r in data if (r["date"].year, r["date"].month) == (yy, mm)]
        pm, ch = mos_predict(train, test)
        chosen[f"{yy}-{mm:02d}"] = ch
        for i, r in enumerate(test):
            p = p39.get((r["city"], r["date"]))
            if p is None:
                continue
            scored.append({"city": r["city"], "date": r["date"], "y": r["y"], "raw": r["raw"], "p39": p,
                           "mos": float(pm[i]), "blend": (p + float(pm[i])) / 2})
    return scored, chosen

def summary(scored, lead, chosen):
    out = {"lead": lead, "n": len(scored), "days": len({s["date"] for s in scored}),
           "cities": len({s["city"] for s in scored})}
    for k in METHODS:
        e = np.array([s[k] - s["y"] for s in scored])
        hit = np.mean([whole(s[k], UNIT.get(s["city"], "C")) == whole(s["y"], UNIT.get(s["city"], "C"))
                       for s in scored])
        out[k] = {"mae": round(float(np.mean(np.abs(e))), 3), "rmse": round(float(np.sqrt(np.mean(e * e))), 3),
                  "bias": round(float(np.mean(e)), 3), "same_whole": round(float(hit), 4)}
    by = defaultdict(list)
    for s in scored: by[s["date"]].append(s)
    days = list(by); rng = random.Random(7)
    def gain(sample, a, b):
        return (np.mean([abs(s[a] - s["y"]) for d in sample for s in by[d]])
                - np.mean([abs(s[b] - s["y"]) for d in sample for s in by[d]]))
    def hitgain(sample, a, b):
        return np.mean([(whole(s[b], UNIT.get(s["city"], "C")) == whole(s["y"], UNIT.get(s["city"], "C")))
                        - (whole(s[a], UNIT.get(s["city"], "C")) == whole(s["y"], UNIT.get(s["city"], "C")))
                        for d in sample for s in by[d]])
    for a, b in (("p39", "mos"), ("p39", "blend"), ("raw", "mos")):
        draws = [[rng.choice(days) for _ in days] for _ in range(500)]
        g = [gain(x, a, b) for x in draws]
        h = [hitgain(x, a, b) for x in draws]
        out[f"{b}_vs_{a}"] = {"mae_gain": round(float(gain(days, a, b)), 3),
                              "mae_ci95": [round(float(np.percentile(g, 2.5)), 3), round(float(np.percentile(g, 97.5)), 3)],
                              "whole_gain": round(float(hitgain(days, a, b)), 4),
                              "whole_ci95": [round(float(np.percentile(h, 2.5)), 4), round(float(np.percentile(h, 97.5)), 4)]}
    months = defaultdict(list)
    for s in scored: months[s["date"].strftime("%Y-%m")].append(s)
    out["by_month"] = {m: {k: round(float(np.mean([abs(s[k] - s["y"]) for s in ss])), 3) for k in METHODS}
                       | {"n": len(ss), "chosen": chosen.get(m)} for m, ss in sorted(months.items())}
    return out

if __name__ == "__main__":
    res = {}
    for lead in (1, 2):
        scored, chosen = run(lead)
        res[lead] = summary(scored, lead, chosen)
        print(json.dumps(res[lead], indent=1, default=str), flush=True)
