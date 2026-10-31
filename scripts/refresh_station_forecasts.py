#!/usr/bin/env python3
"""Refresh future weather inputs without fitting or stepping learned parameters.

Uses complete city-local days from the current four-day weather grids. Existing
nightly coefficients, biases, widths and calibration remain unchanged. Frozen
predictive calls are not read or written here.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import sys
import time
from zoneinfo import ZoneInfo

import honest_record as hr
import station_correction as sc
import station_mos as sm

JOB = "early_weather_forward_refresh"
MAX_FIT_AGE = dt.timedelta(hours=36)  # existing served correction freshness ceiling
MIN_STATION_OBSERVATIONS = 12  # existing weather_model/databank daily-maximum floor
FORWARD_REQUEST_SECONDS = 10


def finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def complete(hourly, series, day, timezone):
    """Every UTC hour inside the local day, including DST's shorter/longer day.

    Compare timestamps, not a count of 24: duplicate hours cannot conceal gaps.
    """
    zone = ZoneInfo(timezone)
    date = dt.date.fromisoformat(day)
    start = dt.datetime.combine(date, dt.time(), zone).astimezone(dt.timezone.utc)
    end = dt.datetime.combine(date + dt.timedelta(days=1), dt.time(), zone).astimezone(dt.timezone.utc)
    cursor = start.replace(minute=0, second=0, microsecond=0)
    if cursor < start:
        cursor += dt.timedelta(hours=1)
    expected = set()
    while cursor < end:
        expected.add(cursor)
        cursor += dt.timedelta(hours=1)
    stamps = [dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
              for t in hourly.get("time", [])]
    values = hourly.get(series) or []
    got = [stamp for stamp, value in zip(stamps, values) if start <= stamp < end and finite(value)]
    return len(got) == len(expected) and set(got) == expected


EXTRA_HOURLY = ('wind_direction_10m', 'relative_humidity_2m', 'apparent_temperature', 'precipitation_probability')


def forecast_features(city, answer, received):
    """Complete local days, same observed feature units; missing stays missing."""
    hourly = (answer or {}).get('hourly') or {}
    zone = ZoneInfo(city['timezone'])
    by_day = {}
    for i, stamp in enumerate(hourly.get('time', [])):
        local = dt.datetime.fromisoformat(stamp).replace(tzinfo=dt.timezone.utc).astimezone(zone)
        by_day.setdefault(local.date(), []).append((i, local.hour))
    def values(variable, indexes):
        series = hourly.get(variable) or []
        return [float(series[i]) for i in indexes if i < len(series) and finite(series[i])]
    def at(variable, i):
        v = values(variable, [i])
        return v[0] if v else None
    def mean(v):
        return sum(v)/len(v) if v else None
    out = []
    today = received.astimezone(zone).date()
    for day, entries in sorted(by_day.items()):
        if not 0 <= (day-today).days <= 2 or not complete(hourly, 'temperature_2m', day.isoformat(), city['timezone']):
            continue
        indexes = [i for i, _ in entries]
        daytime = [i for i, hour in entries if 9 <= hour <= 17]
        morning = next((i for i, hour in entries if hour == 8), None)
        previous = next((i for i, hour in by_day.get(day-dt.timedelta(days=1), []) if hour == 8), None)
        temp = values('temperature_2m', indexes)
        dew = at('dew_point_2m', morning) if morning is not None else None
        mt = at('temperature_2m', morning) if morning is not None else None
        pressure = at('pressure_msl', morning) if morning is not None else None
        prev_pressure = at('pressure_msl', previous) if previous is not None else None
        cloud = [x*8/100 for x in values('cloud_cover', daytime)]
        wind = [x/1.852 for x in values('wind_speed_10m', daytime)]
        precip = values('precipitation', indexes)
        apparent = values('apparent_temperature', indexes)
        pop = values('precipitation_probability', indexes)
        vectors = [(at('wind_speed_10m', i), at('wind_direction_10m', i)) for i in daytime]
        vectors = [(w, math.radians(d)) for w, d in vectors if w is not None and d is not None]
        total = sum(w for w, _ in vectors)
        out.append(dict(city_key=city['city_key'], for_date=day.isoformat(), run_at=received.isoformat(),
            source='open-meteo-forward-features-v1', lead_days=(day-today).days,
            forecast_max_c=max(temp), forecast_min_c=min(temp), apparent_max_c=max(apparent) if apparent else None,
            morning_temp_c=mt, morning_dewpoint_c=dew,
            dewpoint_depression_c=mt-dew if mt is not None and dew is not None else None,
            morning_humidity=at('relative_humidity_2m', morning) if morning is not None else None,
            morning_pressure_hpa=pressure,
            pressure_change_24h_hpa=pressure-prev_pressure if pressure is not None and prev_pressure is not None else None,
            cloud_mean=mean(cloud), cloud_max=max(cloud) if cloud else None,
            wind_mean=mean(wind), wind_max=max(wind) if wind else None,
            precip_total=sum(precip)/25.4 if len(precip)==len(indexes) else None,
            precip_probability=max(pop) if pop else None, n_hours=len(indexes),
            wind_u_mean=sum(-w*math.sin(d) for w,d in vectors)/total if len(vectors)>=3 and total>0 else None,
            wind_v_mean=sum(-w*math.cos(d) for w,d in vectors)/total if len(vectors)>=3 and total>0 else None))
    return out


def fetch(cities, started, rounds, features=None):
    """Reuse bounded batched requests, retaining only complete input days."""
    bm, md, missing = [], [], []
    def ask(chunk, part):
        return hr.current_request(chunk, part, timeout_s=FORWARD_REQUEST_SECONDS, extra_hourly=EXTRA_HOURLY)
    for city, (heating, models) in hr.fetch_each(ask, cities, rounds):
        if features is not None:
            features.extend(forecast_features(city, heating, started))
        timezone = city["timezone"]
        b, m = hr.current_rows(city["city_key"], heating, models, started, timezone)
        b = [row for row in b if all(complete((heating or {}).get("hourly", {}), var, row[2], timezone)
                                    for var in hr.HEATING)]
        m = [row for row in m if complete((models or {}).get("hourly", {}),
                                          f"temperature_2m_{row[2]}", row[3], timezone)]
        bm.extend(b)
        md.extend(m)
        if len(b) != len(hr.LEADS) or any(sum(r[1] == lead for r in m) < sc.MIN_SOURCES for lead in hr.LEADS):
            missing.append(city["city_key"])
    return bm, md, missing


def fresh_fit(row, received, target):
    try:
        at = dt.datetime.fromisoformat(str(row["computed_at"]).replace("Z", "+00:00"))
        as_of = dt.date.fromisoformat(str(row["as_of"]))
        return (dt.timedelta(0) <= received - at <= MAX_FIT_AGE
                and as_of <= received.date() and as_of < target and bool(row.get("version")))
    except (KeyError, ValueError, TypeError):
        return False


def version(base, received):
    # Input identity differs even when the fitted parameters did not change.
    return f"{base}:forward:{hashlib.sha256(received.isoformat().encode()).hexdigest()[:16]}"


def forward_labels(rest_all, cities, before):
    """Supply only the last completed station day needed by future features.

    The nightly cache can have been built before that day ended. Read the
    exact view that fills it, with its existing settlement-feed preference;
    never substitute a different source or a still-running local day. This
    is an input to forward prediction, not a training-label/cache update.
    """
    labels = sm.load_labels(rest_all)
    required = {(c['city_key'], (before.astimezone(ZoneInfo(c['timezone'])).date()
                               - dt.timedelta(days=1)).isoformat()) for c in cities}
    missing = required - labels.keys()
    detail = {'source': 'v_city_day_features', 'days': [], 'error': None}
    if not missing:
        return labels, detail
    first, last = min(day for _, day in missing), max(day for _, day in missing)
    try:
        rows = rest_all('v_city_day_features', [
            ('select', 'city_key,obs_date,max_c,n_obs'), ('obs_date', 'gte.'+first),
            ('obs_date', 'lte.'+last),
            ('city_key', 'in.('+','.join(sorted({city for city, _ in missing}))+')')],
            order='city_key.asc,obs_date.asc')
        for row in rows:
            key = (row['city_key'], str(row['obs_date']))
            if key not in missing or not finite(row.get('max_c')) or not finite(row.get('n_obs')):
                continue
            if float(row['n_obs']) < MIN_STATION_OBSERVATIONS:
                continue
            labels[key] = float(row['max_c'])
            detail['days'].append({'city_key': key[0], 'obs_date': key[1],
                                   'max_c': labels[key], 'n_obs': row['n_obs']})
    except Exception as exc:
        detail['error'] = f'{type(exc).__name__}: {str(exc)[:200]}'
        print('completed forward station days unavailable: '+detail['error'], file=sys.stderr)
    return labels, detail


def build(cities, bm, md, cells, widths, coefficients, labels, started, received):
    """Pure forward calculation. No historical forecasts or parameter writes."""
    zones = {c["city_key"]: c["timezone"] for c in cities}
    valid_cities = {city for city, zone in zones.items()
                    if started.astimezone(ZoneInfo(zone)).date() == received.astimezone(ZoneInfo(zone)).date()}
    width = {(r["city_key"], int(r["lead_days"])): r for r in widths}
    coefs = {(r["city_key"], int(r["lead_days"])): r for r in coefficients}
    groups = {}
    raw = []
    for row in md:
        city, lead, model, day, full, _early = row
        if city not in valid_cities or model not in hr.MODELS or not finite(full):
            continue
        target = dt.date.fromisoformat(day)
        if (target - received.astimezone(ZoneInfo(zones[city])).date()).days != int(lead) or lead not in hr.LEADS:
            continue
        groups.setdefault((city, day, lead), {})["open_meteo_" + model] = float(full)
        raw.append(dict(city_key=city, model="open_meteo_" + model, for_date=day, lead_days=lead,
                        forecast_max_c=float(full), source=sc.FORWARD_SOURCE,
                        run_at=received.isoformat(), observed_at=received.isoformat()))
    corrected = []
    for (city, day, lead), forecasts in sorted(groups.items()):
        target = dt.date.fromisoformat(day)
        fitted = [r for r in cells if r["city_key"] == city and int(r["lead_days"]) == lead
                  and fresh_fit(r, received, target) and finite(r.get("bias_c"))]
        # Do not mix partial nightly generations or borrow another city's fit.
        if not fitted or len({r["version"] for r in fitted}) != 1:
            continue
        table = {"cells": {(r["source"], lead, city): (float(r["bias_c"]), r["n"]) for r in fitted}, "pooled": {}}
        combined = sc.combine(forecasts, table, city, lead)
        if combined is None:
            continue
        mean, spread, used = combined
        w = width.get((city, lead), {})
        has_width = fresh_fit(w, received, target) and finite(w.get("sigma_c")) and float(w["sigma_c"]) > 0
        corrected.append(dict(city_key=city, for_date=day, lead_days=lead, combined_c=round(mean, 3),
                              spread_c=round(spread, 3), n_sources=len(used), sources=used,
                              version=version(fitted[0]["version"], received),
                              width_c=float(w["sigma_c"]) if has_width else None,
                              width_version=w["version"] if has_width else None,
                              inputs_oldest_run_at=received.isoformat(), inputs_newest_run_at=received.isoformat(),
                              computed_at=received.isoformat()))
    by_day = {(r["city_key"], r["for_date"]): r for r in corrected}
    mos = []
    for lead, rows in sm.assemble(bm, md, labels).items():
        for row in rows:
            city, day = row["city"], row["date"].isoformat()
            p = by_day.get((city, day))
            cf = coefs.get((city, lead), {})
            coef = cf.get("coef") or {}
            if (p is None or not fresh_fit(cf, received, row["date"])
                    or not all(finite(coef.get(f)) for f in ["intercept"] + sm.FEATURES)
                    or not all(finite(x) for x in row["x"])):
                continue
            maximum = sm.predict(coef, row)
            mos.append(dict(city_key=city, for_date=day, lead_days=lead, mos_c=round(maximum, 3),
                            base_c=round(row["base"], 3), p39_c=p["combined_c"],
                            blend_c=round((maximum + p["combined_c"]) / 2, 3),
                            inputs=dict(zip(sm.FEATURES, [round(x, 4) for x in row["x"]])),
                            calibration=cf.get("calibration"), p39_version=p["version"],
                            version=version(cf["version"], received), computed_at=received.isoformat()))
    return raw, corrected, mos


def publish(raw, corrected, mos, upsert):
    # Evidence first. A new P39 input identity rejects an old MOS row if the
    # last write fails; pricing can safely use fresh P39 alone.
    if raw:
        upsert("weather_forecast_models", raw, "city_key,model,run_at,for_date")
    if corrected:
        upsert("derived_corrected_forecast", corrected, "city_key,for_date")
    if mos:
        upsert("derived_mos_forecast", mos, "city_key,for_date")


def main(argv=None):
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--dry-run", action="store_true")
    args = args.parse_args(argv)
    from common import get_cities, rest_all, upsert_replace, log_run
    started = dt.datetime.now(dt.timezone.utc)
    cities = get_cities()
    # Read each fitted table once before asking for new weather.
    cells = rest_all("derived_station_correction", [("select", "*")], order="city_key.asc,source.asc,lead_days.asc")
    widths = rest_all("derived_station_width", [("select", "*")], order="city_key.asc,lead_days.asc")
    coefficients = rest_all("derived_mos_coefficients", [("select", "*")], order="city_key.asc,lead_days.asc")
    labels, station_days = forward_labels(rest_all, cities, started)
    rounds = []
    features = []
    previous_deadline = hr._deadline
    try:
        hr._deadline = time.monotonic() + sm.FETCH_SECONDS
        bm, md, missing = fetch(cities, started, rounds, features)
    finally:
        hr._deadline = previous_deadline
    received = dt.datetime.now(dt.timezone.utc)
    zones = {c['city_key']: c['timezone'] for c in cities}
    for feature in features:
        feature['run_at'] = received.isoformat()
        feature['lead_days'] = (dt.date.fromisoformat(feature['for_date'])-
            received.astimezone(ZoneInfo(zones[feature['city_key']])).date()).days
    features = [f for f in features if 0 <= f['lead_days'] <= 2]
    raw, corrected, mos = build(cities, bm, md, cells, widths, coefficients, labels, started, received)
    expected = {(c["city_key"], lead) for c in cities for lead in hr.LEADS}
    detail = dict(inputs_received_at=received.isoformat(), raw_rows=len(raw), corrected_rows=len(corrected),
                  mos_rows=len(mos), feature_rows=len(features), unreached=missing, rounds=rounds, refit=False, station_days=station_days,
                  missing_corrected=sorted(expected - {(r["city_key"], r["lead_days"]) for r in corrected}),
                  missing_mos=sorted(expected - {(r["city_key"], r["lead_days"]) for r in mos}))
    print(json.dumps(detail, default=str))
    if not args.dry_run:
        if features:
            upsert_replace("weather_forecast_features", features, "city_key,for_date,run_at")
        publish(raw, corrected, mos, upsert_replace)
        log_run(JOB, "ok" if not missing and not detail["missing_mos"] else ("partial" if corrected else "error"),
                len(corrected) + len(mos), detail)
    return 0 if corrected else 1


if __name__ == "__main__":
    raise SystemExit(main())
