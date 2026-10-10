#!/usr/bin/env python3
"""The ensemble record (plan v2.4 P2.10 part 1): what each ensemble said about
the next days, kept from now on, with the run it came from.

WHY. On the venue's record the desk's day-ahead model adds nothing to the
market's price (docs/MODEL_VS_MARKET_2026-09-28.md). Hassan, 28 Sep: new
information first. An ensemble's spread says how sure the atmosphere is about
a day, which the desk's width now guesses from past errors alone. Whether it
adds to the price is a test on the record, and the record cannot be made
afterwards: Open-Meteo keeps ensemble members for about four days only
(measured 28 Sep: 96 of 168 hours of past_days=7 had members; 1 Sep had none).
So this starts the clock.

WHAT. Once a night, for every active city, the latest run of each ENSEMBLE
model (hourly temperature, every member), reduced to one row per (city,
model, run, local day): the members' daily maxima over 00-23 and over 00-17
local (the honest record's window), as count, mean, standard deviation and the
10/25/50/75/90th percentiles. Each row carries the run's initialisation time
and the time Open-Meteo made it available (its static meta.json), so a later
study knows exactly what was known when (plan P2.6).

COST, measured 28 Sep on one city: ECMWF IFS 0.25 ensemble 50 members and a
control, GFS 0.25 ensemble 30 and a control; 72 hourly values each. Open-Meteo
counts a request's variables in tens, so a city costs about 6 + 4 weighted calls
(~480 a night for 48 cities), against 2,058-3,792 a day used today (P2.10
budget, 28 Sep) and a free limit of 10,000.

STORAGE. data/training/ensembles/ensemble_daily.csv.gz in the repository (not
Postgres: P1.6), appended and committed by archive_observations.yml's mirror
commit. A key already stored is kept, not rewritten.

DEADLINE. As honest_record (#239): the run stops asking RUN_SECONDS after it
starts and writes what arrived; a city left out is simply missing that night.
Until 9 Oct it was 90 s inside a 2-minute step, because the scheduled workflows
cost 2,926 of the 3,000 budgeted minutes a month (28 Sep). The repository is
public from 9 Oct and its standard runners are not billed, and the deadline
was what cut the cities: on 9 Oct the step ran 91 s and 23 of 47 cities were
unreached. 360 s inside a 7-minute step; the run ends as soon as every city
has answered.

LEAST RECENTLY RECORDED FIRST (8 Oct). The deadline does not reach every
city: on 8 Oct 29 of 48 were recorded whole; the run log named 15 "not asked:
the run's deadline" for both models, 3 a read timeout and then not asked, and
Istanbul's GFS a read timeout. The cities came in the table's own order, so
the cut fell on the same tail every night: on main on 8 Oct, 17 of the 48 had
no row at all since the record began on 29 Sep (London, NYC, Paris and Madrid
among them), and a night missed cannot be fetched later. So the cities are
asked in the order of their oldest model's newest row, a city never recorded
first, and the cut moves round the list.

  python scripts/ensemble_record.py [--dry-run]
"""
import argparse
import csv
import datetime as dt
import gzip
import os
import statistics
import sys
import time
from zoneinfo import ZoneInfo

import requests

JOB = "P2.10_ensemble_record"
API = "https://ensemble-api.open-meteo.com/v1/ensemble"
# model -> the meta.json that names its latest run
MODELS = {"ecmwf_ifs025": "ecmwf_ifs025_ensemble", "gfs025": "ncep_gefs025"}
META = "https://ensemble-api.open-meteo.com/data/{}/static/meta.json"
FORECAST_DAYS = 3
LAST_HOUR = 17              # the honest record's window, 00-17 local
MIN_HOURS = 20              # a local day with fewer hourly values is left out
# MANY CITIES A REQUEST, ONE REQUEST AT A TIME, ASKED AGAIN IN ROUNDS (10 Oct),
# as honest_record's requests are and for the same reasons (see CHUNK there).
# The ensemble API answers a list of places with each place's answer as asked
# alone (3 places, 10 Oct, through pg_net: identical but for `location_id`).
# On 10 Oct (job log, step from 02:52:12Z) four asks hung together - denver's
# GFS, istanbul's, paris's and houston's ECMWF - each hung its full 60 s
# twice, and the run ended at 136 s of its 360 with those four missing.
CHUNK = 12
WORKERS = 1
RETRY_WAITS = (10, 30, 60, 90, 120, 150)   # honest_record.RETRY_WAITS
TIMEOUT = 30                # honest_record.TIMEOUT
RUN_SECONDS = 360           # inside the step's 7 minutes (see DEADLINE above)
MIN_LEFT = 5
QUANTILES = (10, 25, 50, 75, 90)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FILE = os.path.join(ROOT, "data", "training", "ensembles", "ensemble_daily.csv.gz")
HEADER = ["city_key", "model", "run_init", "run_available", "local_date", "window", "n_members",
          "mean_c", "sd_c"] + [f"p{q}_c" for q in QUANTILES] + ["fetched_at"]
KEY = 6                     # city_key, model, run_init, run_available, local_date, window

_deadline = None


def _left():
    return None if _deadline is None else _deadline - time.monotonic()


def _get(url, params, label, tries=1):
    """The answer, or None. A chunk's request is asked once here and again by
    the rounds in main(); the run's meta files, twice, 5 s apart."""
    for attempt in range(tries):
        left = _left()
        if left is not None and left < MIN_LEFT:
            print(f"  ! {label} not asked: the run's deadline", file=sys.stderr)
            return None
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT if left is None else min(TIMEOUT, left))
            if r.status_code == 400:
                print(f"  ! {label} 400: {r.text[:200]}", file=sys.stderr)
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:                         # timeout, reset, 429, 5xx
            if attempt == tries - 1:
                print(f"  ! {label} unreached: {str(e)[:120]}", file=sys.stderr)
                return None
            time.sleep(5)
    return None


def run_times(meta):
    """(init, available) as ISO UTC from a meta.json, or (None, None)."""
    def iso(v):
        return dt.datetime.fromtimestamp(int(v), dt.timezone.utc).isoformat() if v else None
    if not meta:
        return None, None
    return iso(meta.get("last_run_initialisation_time")), iso(meta.get("last_run_availability_time"))


def percentile(sorted_vals, q):
    """Linear interpolation between closest ranks (numpy's default)."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def daily_rows(city_key, tz, model, js, run_init, run_available, fetched_at):
    """One row per (local day, window) from an ensemble response: every
    member's maximum over the window, summarised. Days with fewer than
    MIN_HOURS hourly values for any member are left out."""
    hourly = (js or {}).get("hourly") or {}
    times = hourly.get("time") or []
    members = {k: v for k, v in hourly.items() if k == "temperature_2m" or k.startswith("temperature_2m_member")}
    if not times or not members:
        return []
    zone = ZoneInfo(tz)
    stamps = [dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc).astimezone(zone) for t in times]
    days = sorted({s.date().isoformat() for s in stamps})
    out = []
    for day in days:
        idx = [i for i, s in enumerate(stamps) if s.date().isoformat() == day]
        for window, hours in (("00_23", range(0, 24)), ("00_17", range(0, LAST_HOUR + 1))):
            maxima, whole = [], True
            for vals in members.values():
                got = [vals[i] for i in idx if stamps[i].hour in hours and i < len(vals) and vals[i] is not None]
                need = MIN_HOURS if window == "00_23" else LAST_HOUR + 1 - (24 - MIN_HOURS)
                if len(got) < need:
                    whole = False
                    break
                maxima.append(max(got))
            if not whole or not maxima:
                continue
            s = sorted(maxima)
            out.append([city_key, model, run_init, run_available, day, window, len(s),
                        round(statistics.mean(s), 2), round(statistics.pstdev(s), 3)]
                       + [round(percentile(s, q), 2) for q in QUANTILES] + [fetched_at])
    return out


def fetch_model(cities, model):
    """A chunk of cities' latest run of one ensemble model: one answer per
    city in the order asked, or None."""
    label = f"{cities[0]['city_key']}..{cities[-1]['city_key']} ({len(cities)}) {model}"
    js = _get(API, {"latitude": ",".join(str(c["latitude"]) for c in cities),
                    "longitude": ",".join(str(c["longitude"]) for c in cities),
                    "hourly": "temperature_2m", "models": model, "forecast_days": FORECAST_DAYS, "timezone": "GMT"},
              label)
    if js is None:
        return None
    answers = [js] if isinstance(js, dict) else js
    if not isinstance(answers, list) or len(answers) != len(cities):
        print(f"  ! {label}: {len(answers) if isinstance(answers, list) else 'no'} answers "
              f"for {len(cities)} places", file=sys.stderr)
        return None
    return answers


def least_recorded_first(cities, rows):
    """The cities in the order to ask them: by when the record last had both
    models for the city, oldest first, so a city missing either model comes
    first, never recorded before all. Ties keep the order given."""
    newest = {}
    for r in rows:
        k = (r[0], r[1])
        if r[-1] > newest.get(k, ""):
            newest[k] = r[-1]
    return sorted(cities, key=lambda c: min(newest.get((c["city_key"], m), "") for m in MODELS))


def read_rows(path=FILE):
    if not os.path.exists(path):
        return []
    with gzip.open(path, "rt", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        return [row for row in rd]


def merge(existing, new):
    """Keep what is stored; add keys not seen. Sorted for a stable file."""
    seen = {tuple(r[:KEY]) for r in existing}
    out = list(existing)
    for r in new:
        k = tuple("" if v is None else str(v) for v in r[:KEY])
        if k not in seen:
            seen.add(k)
            out.append(["" if v is None else str(v) for v in r])
    out.sort(key=lambda r: (r[4], r[0], r[1], r[2], r[5]))
    return out


def write_rows(rows, path=FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from common import ask_in_rounds, get_cities, log_run
    global _deadline
    _deadline = time.monotonic() + RUN_SECONDS

    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    runs = {m: run_times(_get(META.format(meta), {}, f"{m} meta", tries=2)) for m, meta in MODELS.items()}
    old = read_rows()
    cities = least_recorded_first(
        [c for c in get_cities() if c.get("latitude") is not None and c.get("timezone")], old)
    chunks = [cities[i:i + CHUNK] for i in range(0, len(cities), CHUNK)]
    keys = [(k, model) for k in range(len(chunks)) for model in MODELS]
    answers, rounds = ask_in_rounds(keys, lambda key: fetch_model(chunks[key[0]], key[1]), WORKERS, RETRY_WAITS,
                                    _left, MIN_LEFT)
    new, missing = [], {}
    for k, chunk in enumerate(chunks):
        for j, city in enumerate(chunk):
            for model in MODELS:
                got = answers.get((k, model))
                if got is None:
                    missing.setdefault(city["city_key"], []).append(model)
                    continue
                init, avail = runs.get(model, (None, None))
                new += daily_rows(city["city_key"], city["timezone"], model, got[j], init, avail, fetched_at)
    merged = merge(old, new)
    detail = {"fetched_at": fetched_at, "runs": runs, "cities": len(cities), "rows_new": len(new),
              "rows_total": len(merged), "added": len(merged) - len(old), "unreached": missing,
              "asked_first": [c["city_key"] for c in cities[:3]], "rounds": rounds}
    print(detail)
    if args.dry_run:
        return 0
    write_rows(merged)
    status = "ok" if not missing else ("partial" if len(missing) < len(cities) else "error")
    log_run(JOB, status, len(merged) - len(old), detail)
    return 0 if status != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
