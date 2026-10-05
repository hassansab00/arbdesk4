"""Extend the honest hourly forecast record (best_match, `_previous_day1`) for
WXPredict.

data/training/previous_runs/best_match_hourly_day1_utc.csv.gz was built once on
26 Sep (through pg_net; P7.2) and is not appended nightly - only the daily files
are (scripts/honest_record.py). This adds the missing days, the same way it was
built: Open-Meteo's Previous Runs API, the city's coordinates in public.cities,
`timezone=auto` (one fixed offset per answer, measured 26 Sep), each local hour
turned back into UTC with that offset, so a city on a half-hour offset keeps
its :30 stamps.

Rows already in the file are never rewritten. The answer's hours that overlap
the file are compared with it, value by value, and the count that differ is
reported: a refetch that disagrees with the record is a finding, not a merge.

    python tools/wxpredict/fetch_forecasts.py --from 2026-09-24 --to 2026-10-04

Open-Meteo refuses back-to-back requests from a shared address ("Too many
concurrent requests", 5 Oct); requests go one at a time, PAUSE_S apart.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import common  # noqa: E402

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
FILE = os.path.join(common.PR, "best_match_hourly_day1_utc.csv.gz")
HEADER = ["city_key", "utc", "t", "td", "cloud", "sw", "wind", "precip", "pmsl"]
VARS = {"t": "temperature_2m", "td": "dew_point_2m", "cloud": "cloud_cover", "sw": "shortwave_radiation",
        "wind": "wind_speed_10m", "precip": "precipitation", "pmsl": "pressure_msl"}
PAUSE_S = 4
TRIES = 6


def fmt(v):
    """As the record writes a value: an integral number without '.0'."""
    if v is None:
        return ""
    if float(v) == int(float(v)):
        return str(int(float(v)))
    return repr(float(v))


def fetch(city, start, end):
    params = {"latitude": city["latitude"], "longitude": city["longitude"], "timezone": "auto",
              "start_date": start.isoformat(), "end_date": end.isoformat(),
              "hourly": ",".join(f"{v}_previous_day1" for v in VARS.values())}
    err = None
    for i in range(TRIES):
        try:
            r = requests.get(API, params=params, timeout=60, headers={"User-Agent": "arbdesk4-research/1.0"})
            if r.status_code == 200:
                return r.json()
            err = RuntimeError(f"{r.status_code} {r.text[:120]}")
        except requests.RequestException as e:
            err = e
        time.sleep(15 * (i + 1))
    raise err


def rows_of(city_key, answer):
    """[(city_key, utc stamp, values...)] from one answer."""
    h = answer["hourly"]
    off = dt.timedelta(seconds=answer["utc_offset_seconds"])
    out = []
    for i, t in enumerate(h["time"]):
        utc = (dt.datetime.fromisoformat(t) - off).strftime("%Y-%m-%dT%H:%M")
        out.append([city_key, utc, *[fmt(h[f"{v}_previous_day1"][i]) for v in VARS.values()]])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    args = ap.parse_args()
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    cities = common.active_cities()
    have = {}
    for r in common.read_csv(FILE):
        have[(r["city_key"], r["utc"])] = [r[k] for k in HEADER]
    keys = sorted({k for k, _ in have})
    added, same, differ, missing = [], 0, [], []
    for k in keys:
        if k not in cities:
            missing.append(k)
            continue
        ans = fetch(cities[k], start, end)
        tz = common.zone(cities[k])
        for row in rows_of(k, ans):
            if any(v == "" for v in row[2:3]):
                continue                       # no temperature: the hour is not in the run yet
            local = dt.datetime.fromisoformat(row[1]).replace(tzinfo=common.UTC).astimezone(tz).date()
            if local > end:
                continue
            old = have.get((k, row[1]))
            if old is None:
                added.append(row)
            elif [float(x) if x else None for x in old[2:]] == [float(x) if x else None for x in row[2:]]:
                same += 1
            else:
                differ.append((k, row[1], old[2:], row[2:]))
        print(f"{k}: {sum(1 for r in added if r[0] == k)} new hours", file=sys.stderr, flush=True)
        time.sleep(PAUSE_S)
    if differ:
        print(json.dumps({"overlap_differs": len(differ), "first": differ[:5]}), file=sys.stderr)
        raise SystemExit("the refetch disagrees with the record on overlapping hours: not merged")
    merged = dict(have)
    for r in added:
        merged[(r[0], r[1])] = r
    n = common.write_csv(FILE, HEADER, [merged[k] for k in sorted(merged)])
    print(json.dumps({"rows": n, "added": len(added), "overlap_identical": same,
                      "cities_not_active": missing}), file=sys.stderr)


if __name__ == "__main__":
    main()
