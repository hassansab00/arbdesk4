"""S10 in shadow, observe only (plan v2 P7.4 part 1).

WHY. The P7.3 replay scored the remaining-day model (scripts/remaining_day.py,
P7.2 stage 1) on the same months its form was chosen on. The clean test is
days it has never seen. So at each of the tick's checkpoints this records what
the model would have said, beside the engine's own call, for the scoreboard to
grade against the venue later. NOTHING HERE PRICES OR TRADES.

TWO STEPS, both inside the hourly tick and inside its budget:

  fetch   once per city-day, between 07:00 and 09:00 local, the day-before
          forecast run for that local day - Open-Meteo Previous Runs
          `_previous_day1`, hourly temperature, cloud and radiation (the series
          the model was trained on, data/training/previous_runs) - and the
          seven models' `_previous_day1` daily maxima (the spread feature).
          By 07:00 local every hour of the day has its day-before value.
  shadow  for each checkpoint the tick finds due: the station's readings of
          that local day up to the decision hour (IEM, as trained), that
          day's inputs row, the fitted parameters in PARAMS_PATH ->
          remaining_day.distribution -> the venue ladder
          (remaining_day.ladder_probabilities, the engine's P3.1 atom and the
          pooled q defaults). One row per city, day and checkpoint.

A row can be skipped (no inputs yet, too few readings, an hour the model has
no fit for); the reason is counted in the tick's log, never raised.
"""
import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from statistics import pstdev
from zoneinfo import ZoneInfo

import remaining_day as rd

JOB = "P7.4_s10_shadow"
CONTRACT = "s10-contract-v1"
PREVIOUS_API = "https://previous-runs-api.open-meteo.com/v1/forecast"
MODELS = ("ecmwf_ifs025", "gfs_seamless", "icon_seamless", "ukmo_seamless", "jma_seamless",
          "gem_seamless", "meteofrance_seamless")
MIN_MODELS = 4
FETCH_FROM_H, FETCH_UNTIL_H = 7, 9          # local hours, [from, until)
MAX_FETCH_PER_TICK = 12
TIMEOUT_S = 12
PARAMS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "models", "remaining_day", "current.json")
CHECKPOINTS = ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
MIN_HOURLY = 20


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------
def load_params(path=PARAMS_PATH):
    """({hour: parameters}, version, spread_fallback) or (None, None, None)."""
    try:
        with open(path) as f:
            blob = json.load(f)
    except (OSError, ValueError):
        return None, None, None
    hours = {int(h): p for h, p in blob["hours"].items()}
    return hours, blob["version"], blob.get("spread_median_c")


def local_hours(times, tz):
    """[(local date, local hour)] for Open-Meteo's UTC 'YYYY-MM-DDTHH:MM' stamps."""
    zone = ZoneInfo(tz)
    out = []
    for t in times:
        u = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc).astimezone(zone)
        out.append((u.date(), u.hour))
    return out


def day1_row(city_key, day, tz, heating_js, models_js):
    """The s10_day1_inputs row for one local day, or None when the day is not
    whole. hourly: {"H": [temp C, cloud %, shortwave W/m2]}."""
    h = (heating_js or {}).get("hourly") or {}
    stamps = local_hours(h.get("time") or [], tz)
    t = h.get("temperature_2m_previous_day1") or []
    cl = h.get("cloud_cover_previous_day1") or []
    sw = h.get("shortwave_radiation_previous_day1") or []
    hourly = {}
    for i, (d, hr) in enumerate(stamps):
        if d != day or i >= len(t) or t[i] is None:
            continue
        hourly[str(hr)] = [float(t[i]),
                           float(cl[i]) if i < len(cl) and cl[i] is not None else None,
                           float(sw[i]) if i < len(sw) and sw[i] is not None else None]
    if len(hourly) < MIN_HOURLY:
        return None
    models = {}
    m = (models_js or {}).get("hourly") or {}
    mstamps = local_hours(m.get("time") or [], tz)
    for model in MODELS:
        vals = m.get(f"temperature_2m_previous_day1_{model}") or []
        day_vals = [float(v) for (d, _), v in zip(mstamps, vals) if d == day and v is not None]
        if len(day_vals) >= MIN_HOURLY:
            models[model] = round(max(day_vals), 2)
    spread = round(pstdev(models.values()), 4) if len(models) >= MIN_MODELS else None
    return {"city_key": city_key, "local_date": day.isoformat(), "hourly": hourly,
            "models": models or None, "models_spread_c": spread}


def due_fetches(now, cities, have):
    """[(city, local date)] whose local clock is in [FETCH_FROM_H, FETCH_UNTIL_H)
    and whose inputs row is not stored yet; `have` is a set of (city, date iso)."""
    out = []
    for c in cities:
        tz = c.get("timezone")
        if not tz or c.get("latitude") is None:
            continue
        local = now.astimezone(ZoneInfo(tz))
        if FETCH_FROM_H <= local.hour < FETCH_UNTIL_H and (c["city_key"], local.date().isoformat()) not in have:
            out.append((c, local.date()))
    return out[:MAX_FETCH_PER_TICK]


def readings_for(obs, tz, day):
    """[(local hour as a float, temp C)] of `day` on the city's clock."""
    zone = ZoneInfo(tz)
    out = []
    for r in obs:
        if r.get("temp_c") is None:
            continue
        t = dt.datetime.fromisoformat(str(r["valid_at"]).replace("Z", "+00:00")).astimezone(zone)
        if t.date() == day:
            out.append((t.hour + t.minute / 60, float(r["temp_c"])))
    return out


def shadow_rows(due, params, version, inputs, obs_by_city, tz_of, unit_of, bands_of,
                q_down, q_up, spread_fallback):
    """(rows, skipped reasons). due: [(city, target iso, checkpoint, local naive
    datetime)]; inputs: {(city, date iso): s10_day1_inputs row};
    bands_of: {(city, target iso): [band dicts]}."""
    rows, skipped = [], {}

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1

    for city, target, name, local in due:
        if name not in CHECKPOINTS:
            continue
        hour = local.hour
        p = params.get(hour)
        if p is None:
            skip(f"no fit for local hour {hour}")
            continue
        row_in = inputs.get((city, target))
        if row_in is None:
            skip("no day-before inputs for the day")
            continue
        bands = bands_of.get((city, target))
        if not bands:
            skip("no ladder")
            continue
        day = dt.date.fromisoformat(target)
        forecast = {int(h): tuple(v) for h, v in (row_in.get("hourly") or {}).items()}
        spread = row_in.get("models_spread_c")
        spread = float(spread) if spread is not None else spread_fallback
        readings = readings_for(obs_by_city.get(city, []), tz_of[city], day)
        feat = rd.features(readings, forecast, hour, day, spread)
        if feat is None:
            skip("too few readings or forecast hours")
            continue
        d = rd.distribution(p, city, feat["x"], feat["R"])
        probs = dict(rd.ladder_probabilities(d, unit_of.get(city, "C"), bands, q_down, q_up))
        ranked = sorted(probs.items(), key=lambda kv: (-kv[1], str(kv[0])))
        rows.append({
            "city_key": city, "target_date": target, "checkpoint": name,
            "local_decision_time": local.isoformat(timespec="minutes"),
            "model_hour": hour, "model_version": version, "contract": CONTRACT,
            "probs": {str(k): round(v, 6) for k, v in probs.items()},
            "top_band_id": str(ranked[0][0]), "top_prob": round(ranked[0][1], 6),
            "median_c": round(rd.median(d), 3), "q10_c": round(rd.quantile(d, 0.1), 3),
            "q90_c": round(rd.quantile(d, 0.9), 3), "running_max_c": round(feat["R"], 2),
            "inputs": {"x": [round(v, 4) for v in feat["x"]], "features": rd.FEATURES,
                       "readings": len(readings), "spread_from": "models" if row_in.get(
                           "models_spread_c") is not None else "fallback"},
        })
    return rows, skipped


# ---------------------------------------------------------------------------
# I/O, called from tick.run; never raises
# ---------------------------------------------------------------------------
def _get(url, params):
    import requests
    r = requests.get(url, params=params, timeout=TIMEOUT_S)
    r.raise_for_status()
    return r.json()


def fetch_day1(city, day):
    base = {"latitude": city["latitude"], "longitude": city["longitude"], "timezone": "GMT",
            "temperature_unit": "celsius",
            "start_date": (day - dt.timedelta(days=1)).isoformat(),
            "end_date": (day + dt.timedelta(days=1)).isoformat()}
    heating = _get(PREVIOUS_API, dict(base, hourly="temperature_2m_previous_day1,"
                                                   "cloud_cover_previous_day1,shortwave_radiation_previous_day1"))
    models = _get(PREVIOUS_API, dict(base, hourly="temperature_2m_previous_day1", models=",".join(MODELS)))
    return day1_row(city["city_key"], day, city["timezone"], heating, models)


def fetch_inputs(now, cities, dry_run=False, budget_s=10.0):
    """Fetch and store the day-before inputs for the cities whose window is open."""
    from common import rest_all, upsert
    t0 = time.monotonic()
    out = {"due": 0, "stored": 0, "failed": 0}
    try:
        today = {now.astimezone(ZoneInfo(c["timezone"])).date().isoformat()
                 for c in cities if c.get("timezone")}
        have = {(r["city_key"], str(r["local_date"])) for r in rest_all(
            "s10_day1_inputs", [("select", "city_key,local_date"),
                                ("local_date", f"in.({','.join(sorted(today))})")],
            order="city_key.asc,local_date.asc")}
        todo = due_fetches(now, cities, have)
        out["due"] = len(todo)
        if not todo:
            return out
        rows = []
        with ThreadPoolExecutor(4) as pool:
            futs = {pool.submit(fetch_day1, c, d): c["city_key"] for c, d in todo}
            done, not_done = wait(futs, timeout=max(1.0, budget_s - (time.monotonic() - t0)))
            for f in done:
                try:
                    r = f.result()
                except Exception:
                    r = None
                if r is None:
                    out["failed"] += 1
                else:
                    rows.append(r)
            out["failed"] += len(not_done)
        if rows and not dry_run:
            upsert("s10_day1_inputs", rows, "city_key,local_date")
        out["stored"] = len(rows)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


def record(due, market_of, bands_by_market, tz_of, unit_of, dry_run=False):
    """Write the shadow ladders for the tick's due checkpoints."""
    from common import rest_all, upsert
    import probability_engine as pe
    t0 = time.monotonic()
    out = {"due": 0, "written": 0, "skipped": {}}
    try:
        params, version, spread_fallback = load_params()
        if not params:
            out["error"] = "no fitted parameters at " + os.path.relpath(PARAMS_PATH)
            return out
        todo = [d for d in due if d[2] in CHECKPOINTS]
        out["due"] = len(todo)
        if not todo:
            return out
        cities = sorted({d[0] for d in todo})
        dates = sorted({d[1] for d in todo})
        inputs = {(r["city_key"], str(r["local_date"])): r for r in rest_all(
            "s10_day1_inputs", [("select", "city_key,local_date,hourly,models_spread_c"),
                                ("city_key", f"in.({','.join(cities)})"),
                                ("local_date", f"in.({','.join(dates)})")],
            order="city_key.asc,local_date.asc")}
        since = (dt.datetime.fromisoformat(min(dates)) - dt.timedelta(days=1)).isoformat()
        obs_by_city = {}
        for r in rest_all("weather_observations", [
                ("select", "city_key,valid_at,temp_c"), ("source", "eq.IEM"),
                ("city_key", f"in.({','.join(cities)})"), ("valid_at", f"gte.{since}")],
                order="city_key.asc,valid_at.asc", page_size=1000):
            obs_by_city.setdefault(r["city_key"], []).append(r)
        bands_of = {}
        for city, target, _, _ in todo:
            m = market_of.get((city, target))
            if m:
                bands_of[(city, target)] = bands_by_market.get(m["market_id"], [])
        rows, skipped = shadow_rows(todo, params, version, inputs, obs_by_city, tz_of, unit_of,
                                    bands_of, pe.DEFAULT_Q_DOWN, pe.DEFAULT_Q_UP, spread_fallback)
        out["skipped"] = skipped
        if rows and not dry_run:
            out["written"] = upsert("s10_shadow_checkpoints", rows,
                                    "city_key,target_date,checkpoint,model_version")
        elif rows:
            out["would_write"] = len(rows)
        out["version"] = version
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out
