#!/usr/bin/env python3
"""
The honest training record (plan v2.2 P2.9): what each public forecast said
about a day BEFORE that day.

WHY. scripts/weather_model.py trained on derived_city_day_features, where wind,
cloud and rain are the afternoon's OBSERVED values, then predicted from the
forecast versions of the same columns. Forward it lost to the public forecast
by 0.969 C on average (26 Sep). A model may only learn from what it will have
when it is used.

WHAT. Open-Meteo's Previous Runs API: for an hour H, `<var>_previous_dayN` is
the value from a run started at least N x 24 h before H. Reduced to one row per
(city, lead N, local day), EVERY WINDOW ENDING BY 17:00 LOCAL: a run started by
17:00 the day before and published within about 7 hours is out before the
local midnight the day-ahead call is frozen at. The delay is the providers'
schedule, assumed and not verified run by run (plan P2.6, P7.2). `tmax_c`
(00-23) is kept only as the comparison the engine used to read.

The same functions build the FORWARD rows - each model's current run for the
open days - so training and prediction read every feature from one definition
(the P2.9 test).

The record lives in the repository (data/training/previous_runs, README
there), not Postgres: the database is over its size target (P1.6) and a
training record is read whole once a night. This job appends the last
APPEND_DAYS days and archive_observations.yml commits it.

  python scripts/honest_record.py [--days 10] [--dry-run]
"""
import argparse
import csv
import datetime as dt
import gzip
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import requests

JOB = "P2.9_honest_record"
PREVIOUS_API = "https://previous-runs-api.open-meteo.com/v1/forecast"
CURRENT_API = "https://api.open-meteo.com/v1/forecast"
HEATING = ("temperature_2m", "dew_point_2m", "cloud_cover", "shortwave_radiation",
           "wind_speed_10m", "precipitation", "pressure_msl")
MODELS = ("ecmwf_ifs025", "gfs_seamless", "icon_seamless", "ukmo_seamless", "jma_seamless",
          "gem_seamless", "meteofrance_seamless")
LEADS = (1, 2)
LAST_HOUR = 17          # every window ends by 17:00 local
MIN_HOURS = 20          # a day with fewer hourly temperatures is left out
APPEND_DAYS = 10
WORKERS = 4             # Open-Meteo refused a fifth concurrent request on 26 Sep
TIMEOUT = 60
TRIES = 2

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DIR = os.path.join(ROOT, "data", "training", "previous_runs")
BEST_MATCH_FILE = os.path.join(DIR, "best_match_daily.csv.gz")
MODELS_FILE = os.path.join(DIR, "models_daily.csv.gz")
BEST_MATCH_HEADER = ["city_key", "lead_days", "for_date", "tmax_c", "tmax_00_17_c", "tmin_00_08_c", "t_08_c",
                     "td_08_c", "td_09_17_c", "cloud_09_17_pct", "cloud_max_09_17_pct", "shortwave_06_17_wh_m2",
                     "wind_09_17_kmh", "precip_00_17_mm", "pressure_msl_08_hpa", "n_hours"]
MODELS_HEADER = ["city_key", "lead_days", "model", "for_date", "tmax_c", "tmax_00_17_c"]

# The hours each best_match column reads, (first, last) inclusive, local time.
# tests/test_honest_record.py holds every window to LAST_HOUR.
WINDOWS = {
    "tmax_c": (0, 23),              # comparison only, never a training input
    "tmax_00_17_c": (0, 17),
    "tmin_00_08_c": (0, 8),
    "t_08_c": (8, 8), "td_08_c": (8, 8), "pressure_msl_08_hpa": (8, 8),
    "td_09_17_c": (9, 17), "cloud_09_17_pct": (9, 17), "cloud_max_09_17_pct": (9, 17),
    "wind_09_17_kmh": (9, 17),
    "shortwave_06_17_wh_m2": (6, 17),
    "precip_00_17_mm": (0, 17),
}
COMPARISON_ONLY = {"tmax_c"}


# ---------------------------------------------------------------------------
# hourly -> daily, one definition for the record and for tomorrow
# ---------------------------------------------------------------------------
def _dec(v):
    return None if v is None else Decimal(str(v))


def _round(v, places):
    """Postgres round(numeric, n): half away from zero. The record was made in
    SQL, so the nightly append must round the same way to write the same row."""
    if v is None:
        return None
    q = v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return int(q) if places == 0 else float(q)


def local_stamps(times, tz):
    """[(local date, local hour)] for UTC hourly stamps, on the city's own wall
    clock.

    THE REQUESTS ASK FOR UTC (timezone=GMT), NOT timezone=auto. Measured 26 Sep
    on 48 cities x 439 days: with timezone=auto Open-Meteo returns the WHOLE
    range at ONE fixed offset - the city's offset at the moment of the request
    (every series exactly 439 x 24 hours, no daylight-saving step anywhere;
    Wellington +13 even for July, when it is +12). So a winter day fetched in
    summer sat an hour away from its wall-clock day, and the same day fetched
    again in winter would have come out differently. Converting UTC with the
    city's IANA zone gives the same row whenever it is fetched."""
    zone = ZoneInfo(tz)
    out = []
    for t in times:
        loc = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc).astimezone(zone)
        out.append((loc.date().isoformat(), loc.hour))
    return out


def _by_day(hourly, key, stamps):
    """{local date: [(hour, value)]} for one series."""
    vals = hourly.get(key) or []
    out = {}
    for (d, h), v in zip(stamps, vals):
        out.setdefault(d, []).append((h, v))
    return out


def _pick(pairs, lo, hi):
    return [v for h, v in pairs if lo <= h <= hi and v is not None]


def daily_best_match(hourly, key, tz):
    """[[lead-less row from tmax_c to n_hours], keyed by local date] for one
    lead. `key(var)` names that variable's hourly series in the response;
    the response's times are UTC and `tz` is the city's IANA zone."""
    stamps = local_stamps(hourly.get("time") or [], tz)
    days = {var: _by_day(hourly, key(var), stamps) for var in HEATING}
    rows = {}
    for d, tt_pairs in days["temperature_2m"].items():
        tt = [(h, _dec(v)) for h, v in tt_pairs]
        n = sum(1 for _, v in tt if v is not None)
        if n < MIN_HOURS:
            continue
        get = lambda var, lo, hi: [_dec(v) for v in _pick(days[var].get(d, []), lo, hi)]
        mx = lambda xs: max(xs) if xs else None
        mn = lambda xs: min(xs) if xs else None
        avg = lambda xs: (sum(xs) / len(xs)) if xs else None
        sm = lambda xs: sum(xs) if xs else None
        raw_cloud = _pick(days["cloud_cover"].get(d, []), *WINDOWS["cloud_max_09_17_pct"])
        rows[d] = [
            _round(mx(_pick(tt, 0, 23)), 1),
            _round(mx(_pick(tt, *WINDOWS["tmax_00_17_c"])), 1),
            _round(mn(_pick(tt, *WINDOWS["tmin_00_08_c"])), 1),
            _round(mx(get("temperature_2m", *WINDOWS["t_08_c"])), 1),
            _round(mx(get("dew_point_2m", *WINDOWS["td_08_c"])), 1),
            _round(avg(get("dew_point_2m", *WINDOWS["td_09_17_c"])), 2),
            _round(avg(get("cloud_cover", *WINDOWS["cloud_09_17_pct"])), 1),
            max(raw_cloud) if raw_cloud else None,
            _round(sm(get("shortwave_radiation", *WINDOWS["shortwave_06_17_wh_m2"])), 0),
            _round(avg(get("wind_speed_10m", *WINDOWS["wind_09_17_kmh"])), 2),
            _round(sm(get("precipitation", *WINDOWS["precip_00_17_mm"])), 2),
            _round(mx(get("pressure_msl", *WINDOWS["pressure_msl_08_hpa"])), 1),
            n,
        ]
    return rows


def daily_models(hourly, key, tz):
    """{(model, date): [tmax_c, tmax_00_17_c]} for one lead; `key(model)`
    names that model's hourly temperature series (times UTC, `tz` the zone)."""
    stamps = local_stamps(hourly.get("time") or [], tz)
    out = {}
    for model in MODELS:
        for d, pairs in _by_day(hourly, key(model), stamps).items():
            vals = [(h, _dec(v)) for h, v in pairs]
            if sum(1 for _, v in vals if v is not None) < MIN_HOURS:
                continue
            all_day = _pick(vals, 0, 23)
            early = _pick(vals, 0, LAST_HOUR)
            out[(model, d)] = [_round(max(all_day), 1), _round(max(early), 1) if early else None]
    return out


def previous_rows(city_key, heating_js, models_js, tz, first=None, last=None):
    """(best_match rows, model rows) for both leads, in the files' column order,
    for local days from `first` to `last` (ISO dates, inclusive) if given - the
    request reaches a day further each side, so the edge days are partial."""
    keep = lambda d: (first is None or d >= first) and (last is None or d <= last)
    bm, md = [], []
    for lead in LEADS:
        if heating_js:
            for d, vals in daily_best_match(heating_js.get("hourly") or {},
                                            lambda v: f"{v}_previous_day{lead}", tz).items():
                if keep(d):
                    bm.append([city_key, lead, d] + vals)
        if models_js:
            for (m, d), vals in daily_models(models_js.get("hourly") or {},
                                             lambda m: f"temperature_2m_previous_day{lead}_{m}", tz).items():
                if keep(d):
                    md.append([city_key, lead, m, d] + vals)
    return bm, md


def current_rows(city_key, heating_js, models_js, fetched_at, tz):
    """The same rows from each model's CURRENT run, for the days after the
    city's own today: lead = days ahead of its wall-clock date at fetch time."""
    today = fetched_at.astimezone(ZoneInfo(tz)).date()
    bm, md = [], []
    if heating_js:
        for d, vals in daily_best_match(heating_js.get("hourly") or {}, lambda v: v, tz).items():
            lead = (dt.date.fromisoformat(d) - today).days
            if lead in LEADS:
                bm.append([city_key, lead, d] + vals)
    if models_js:
        for (m, d), vals in daily_models(models_js.get("hourly") or {}, lambda m: f"temperature_2m_{m}", tz).items():
            lead = (dt.date.fromisoformat(d) - today).days
            if lead in LEADS:
                md.append([city_key, lead, m, d] + vals)
    return bm, md


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------
def _get(url, params, label):
    for attempt in range(TRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 400:
                print(f"  ! {label} 400: {r.text[:200]}", file=sys.stderr)
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:                         # timeout, reset, 429, 5xx
            if attempt == TRIES - 1:
                print(f"  ! {label} unreached: {str(e)[:120]}", file=sys.stderr)
                return None
            time.sleep(5)
    return None


def fetch_previous(city, start, end):
    """UTC hours from the day before `start` to the day after `end`, so every
    local day between them is whole whatever the city's offset."""
    base = {"latitude": city["latitude"], "longitude": city["longitude"], "timezone": "GMT",
            "start_date": (start - dt.timedelta(days=1)).isoformat(),
            "end_date": (end + dt.timedelta(days=1)).isoformat()}
    heating = _get(PREVIOUS_API, dict(base, hourly=",".join(f"{v}_previous_day{l}" for v in HEATING for l in LEADS)),
                   f"{city['city_key']} previous heating")
    models = _get(PREVIOUS_API, dict(base, hourly=",".join(f"temperature_2m_previous_day{l}" for l in LEADS),
                                     models=",".join(MODELS)), f"{city['city_key']} previous models")
    return heating, models


def fetch_current(city):
    base = {"latitude": city["latitude"], "longitude": city["longitude"], "timezone": "GMT",
            "forecast_days": 4}          # UTC days: enough for local day+2 at any offset
    heating = _get(CURRENT_API, dict(base, hourly=",".join(HEATING)), f"{city['city_key']} current heating")
    models = _get(CURRENT_API, dict(base, hourly="temperature_2m", models=",".join(MODELS)),
                  f"{city['city_key']} current models")
    return heating, models


def forward_rows(cities, fetched_at=None):
    """Tomorrow's and the day after's rows from each model's current run."""
    fetched_at = fetched_at or dt.datetime.now(dt.timezone.utc)
    bm, md, missing = [], [], []
    with ThreadPoolExecutor(WORKERS) as pool:
        for city, (h, m) in zip(cities, pool.map(fetch_current, cities)):
            if h is None or m is None:
                missing.append(city["city_key"])
            b, x = current_rows(city["city_key"], h, m, fetched_at, city["timezone"])
            bm += b
            md += x
    return bm, md, missing


# ---------------------------------------------------------------------------
# the files
# ---------------------------------------------------------------------------
def read_rows(path):
    if not os.path.exists(path):
        return []
    with gzip.open(path, "rt", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        return [row for row in rd]


def write_rows(path, header, rows):
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


def _text(row):
    """One spelling per number, the record's: a whole number without '.0'
    (the record was first written through JSON, which drops it)."""
    def one(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return str(int(v)) if v.is_integer() else repr(v)
        return str(v)
    return [one(v) for v in row]


def merge(existing, new, key_len):
    """Rows keyed by their first `key_len` columns; a new row replaces the old
    one with the same key (a run re-read later is the same run). Sorted."""
    by = {tuple(r[:key_len]): r for r in existing}
    for r in new:
        t = _text(r)
        by[tuple(t[:key_len])] = t
    return [by[k] for k in sorted(by)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=APPEND_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from common import get_cities, log_run

    today = dt.datetime.now(dt.timezone.utc).date()
    start, end = today - dt.timedelta(days=args.days), today - dt.timedelta(days=1)
    cities = [c for c in get_cities() if c.get("latitude") is not None and c.get("timezone")
              and c.get("status", "active") == "active"]
    bm, md, missing = [], [], []
    with ThreadPoolExecutor(WORKERS) as pool:
        for city, (h, m) in zip(cities, pool.map(lambda c: fetch_previous(c, start, end), cities)):
            if h is None or m is None:
                missing.append(city["city_key"])
            b, x = previous_rows(city["city_key"], h, m, city["timezone"], start.isoformat(), end.isoformat())
            bm += b
            md += x
    old_bm, old_md = read_rows(BEST_MATCH_FILE), read_rows(MODELS_FILE)
    new_bm, new_md = merge(old_bm, bm, 3), merge(old_md, md, 4)
    detail = {"from": start.isoformat(), "to": end.isoformat(), "cities": len(cities),
              "unreached": missing, "best_match_rows": len(bm), "model_rows": len(md),
              "best_match_total": len(new_bm), "models_total": len(new_md)}
    print(detail)
    if args.dry_run:
        return 0
    write_rows(BEST_MATCH_FILE, BEST_MATCH_HEADER, new_bm)
    write_rows(MODELS_FILE, MODELS_HEADER, new_md)
    status = "ok" if not missing else ("partial" if len(missing) < len(cities) else "error")
    log_run(JOB, status, len(bm) + len(md), detail)
    return 0 if status != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
