"""The settlement stations' own reports, every field, for WXPredict.

WHY
---
The observation archive (data/archive/observations, and weather_observations)
keeps temperature, dew point, humidity, wind and precipitation; sky cover is in
1.8% of its rows and pressure in none, and it starts 22 Jul 2025 with one
station per city as the cities table names it today. The predictive engine
reads the weather the station reports at the decision time - all of it - and
must read the station each event settled on (Paris settled on LFPG until
18 Apr 2026, Denver on KDEN for five days in March).

WHAT
----
IEM's ASOS service (`cgi-bin/request/asos.py`), the source the platform's own
ingest uses, routine AND special reports (report_type 3 and 4: what the venue's
station page shows; scripts/ingest_observations.py measured why), every
station in one request (one request per window keeps under IEM's per-IP limit,
measured there too).

  reports   2025-06-01 to the end date, every report with temperature, dew
            point, humidity, wind, gust, precipitation, altimeter, sea-level
            pressure, visibility, three cloud layers, present weather, and
            from the METAR text the T group (tenths of a degree C, US stations)
            and the 6-hour maximum and minimum groups. One gzip CSV per station
            in data/training/wxpredict/station_reports/.
  climate   2020-01-01 to 2025-05-31, temperature only, reduced to one row per
            station and local day (the city's zone) in
            data/training/wxpredict/station_daily.csv.gz, with the same
            reduction applied to the reports, so the file runs to the end date.
            Past-only climatology for every target day.

    python tools/wxpredict/fetch_obs.py reports [--end YYYY-MM-DD]
    python tools/wxpredict/fetch_obs.py climate
    python tools/wxpredict/fetch_obs.py daily      # station_daily from the cache + reports

Responses are cached per window in --cache, so an interrupted run resumes.
"""
import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import re
import sys
import time
from collections import defaultdict

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import common  # noqa: E402

IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
UA = {"User-Agent": "arbdesk4-research/1.0"}
REPORTS_FROM = dt.date(2025, 6, 1)
CLIMATE_FROM = dt.date(2020, 1, 1)
REPORT_WINDOW_DAYS = 10
CLIMATE_WINDOW_DAYS = 60
PAUSE_S = 2
TRIES = 6
FIELDS = ["tmpf", "dwpf", "relh", "drct", "sknt", "gust", "p01i", "alti", "mslp", "vsby",
          "skyc1", "skyc2", "skyc3", "skyl1", "skyl2", "skyl3", "wxcodes"]
REPORT_HEADER = ["valid", *FIELDS, "t10_c", "td10_c", "max6_c", "min6_c"]
DAILY_HEADER = ["station", "city_key", "local_date", "n_reports", "first_valid", "last_valid",
                "tmax_f", "tmax_c", "tmax_at", "tmin_f", "tmin_c", "max_gap_h"]

T_GROUP = re.compile(r"^T([01])(\d{3})(?:([01])(\d{3}))?$")
MAX6 = re.compile(r"^1([01])(\d{3})$")
MIN6 = re.compile(r"^2([01])(\d{3})$")


def tenths(sign, digits):
    v = int(digits) / 10.0
    return -v if sign == "1" else v


def remarks(metar):
    """(t10_c, td10_c, max6_c, min6_c) from a METAR's remarks, each '' when the
    report carries no such group. Only tokens after RMK are read: a 5-digit
    1xxxx/2xxxx token in the body is something else."""
    t = td = mx = mn = ""
    toks = (metar or "").split()
    if "RMK" not in toks:
        return t, td, mx, mn
    for tok in toks[toks.index("RMK") + 1:]:
        m = T_GROUP.match(tok)
        if m:
            t = tenths(m.group(1), m.group(2))
            if m.group(3):
                td = tenths(m.group(3), m.group(4))
            continue
        m = MAX6.match(tok)
        if m:
            mx = tenths(m.group(1), m.group(2))
            continue
        m = MIN6.match(tok)
        if m:
            mn = tenths(m.group(1), m.group(2))
    return t, td, mx, mn


def request(stations, start, end, data):
    p = {"station": [common.iem_id(s) for s in stations], "data": data,
         "year1": start.year, "month1": start.month, "day1": start.day,
         "year2": end.year, "month2": end.month, "day2": end.day,
         "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no", "elev": "no",
         "missing": "empty", "trace": "T", "direct": "no", "report_type": ["3", "4"]}
    err = None
    for i in range(TRIES):
        try:
            r = requests.get(IEM, params=p, headers=UA, timeout=600)
            if r.status_code == 200 and r.text.startswith("station,"):
                return r.text
            err = RuntimeError(f"{r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            err = e
        time.sleep(min(120, 5 * 2 ** i))
    raise err


def windows(start, end, days):
    out, a = [], start
    while a < end:
        b = min(a + dt.timedelta(days=days), end)
        out.append((a, b))
        a = b
    return out


def fetch(kind, stations, start, end, days, data, cache):
    """Fetch every window into the cache (skipping cached ones); returns paths."""
    os.makedirs(cache, exist_ok=True)
    paths = []
    for a, b in windows(start, end, days):
        path = os.path.join(cache, f"{kind}_{a.isoformat()}_{b.isoformat()}.csv")
        paths.append(path)
        if os.path.exists(path):
            continue
        t0 = time.time()
        text = request(stations, a, b, data)
        with open(path + ".tmp", "w") as f:
            f.write(text)
        os.replace(path + ".tmp", path)
        print(f"{kind} {a} - {b}: {text.count(chr(10)) - 1} rows, {len(text) / 1e6:.1f} MB, "
              f"{time.time() - t0:.0f} s", file=sys.stderr, flush=True)
        time.sleep(PAUSE_S)
    return paths


def back_to_icao(stations):
    return {common.iem_id(s): s for s in stations}


def cmd_reports(args):
    stations = list(common.event_stations())
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    paths = fetch("reports", stations, REPORTS_FROM, end, REPORT_WINDOW_DAYS,
                  ",".join(FIELDS + ["metar"]), args.cache)
    icao = back_to_icao(stations)
    rows = defaultdict(dict)
    unknown = defaultdict(int)
    for path in paths:
        with open(path) as f:
            for rec in csv.DictReader(f):
                st = icao.get((rec.get("station") or "").strip().upper())
                if st is None:
                    unknown[rec.get("station")] += 1
                    continue
                valid = rec["valid"].replace(" ", "T") + "Z"
                out = [valid, *[(rec.get(k) or "").strip() for k in FIELDS], *remarks(rec.get("metar"))]
                # One row per station and minute; a window edge can repeat one.
                rows[st][valid] = out
    total = 0
    for st in stations:
        recs = [rows[st][k] for k in sorted(rows[st])]
        total += common.write_csv(os.path.join(common.REPORTS, f"{st}.csv.gz"), REPORT_HEADER, recs)
    print(json.dumps({"stations": len(stations), "rows": total,
                      "per_station": {s: len(rows[s]) for s in stations},
                      "unknown_station_rows": dict(unknown)}), file=sys.stderr)


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


def reduce_daily(readings, tz_of):
    """{(station, local_date): summary} from (station, valid_utc, tmpf, t10_c).
    tmax_c is the greatest reading in C: the T group when the report has one,
    else tmpf converted. tmax_at is the first report reaching the maximum.
    max_gap_h is the day's longest stretch without a reading, midnight to
    midnight (common.max_gap_h): the builder's whole-day rule reads it."""
    days = {}
    times = defaultdict(list)
    for st, valid, tmpf, t10 in readings:
        if tmpf is None:
            continue
        local = valid.astimezone(tz_of[st])
        key = (st, local.date())
        c = t10 if t10 is not None else f_to_c(tmpf)
        d = days.get(key)
        if d is None:
            days[key] = d = {"n": 0, "first": valid, "last": valid, "tmax_f": tmpf, "tmax_c": c,
                             "tmax_at": valid, "tmin_f": tmpf, "tmin_c": c}
        d["n"] += 1
        times[key].append(int(valid.timestamp()))
        d["first"] = min(d["first"], valid)
        d["last"] = max(d["last"], valid)
        if c > d["tmax_c"] or (c == d["tmax_c"] and valid < d["tmax_at"]):
            d["tmax_c"], d["tmax_at"] = c, valid
        d["tmax_f"] = max(d["tmax_f"], tmpf)
        d["tmin_f"] = min(d["tmin_f"], tmpf)
        d["tmin_c"] = min(d["tmin_c"], c)
    for (st, day), d in days.items():
        d["max_gap_h"] = common.max_gap_h(times[(st, day)], *common.local_day_bounds(day, tz_of[st]))
    return days


def parse_valid(s):
    s = s.rstrip("Z").replace(" ", "T")
    return dt.datetime.fromisoformat(s).replace(tzinfo=common.UTC)


def cmd_climate(args):
    stations = list(common.event_stations())
    fetch("climate", stations, CLIMATE_FROM, REPORTS_FROM, CLIMATE_WINDOW_DAYS, "tmpf", args.cache)
    cmd_daily(args)


def cmd_daily(args):
    stations = common.event_stations()
    cities = common.cities()
    tz_of = {s: common.zone(cities[c]) for s, c in stations.items()}
    icao = back_to_icao(stations)

    def readings():
        for name in sorted(os.listdir(args.cache)):
            if not name.startswith("climate_") or not name.endswith(".csv"):
                continue
            with open(os.path.join(args.cache, name)) as f:
                for rec in csv.DictReader(f):
                    st = icao.get((rec.get("station") or "").strip().upper())
                    if st is None:
                        continue
                    valid = parse_valid(rec["valid"])
                    if valid.date() >= REPORTS_FROM:
                        continue          # the reports carry these days, with the T group
                    yield st, valid, num(rec.get("tmpf")), None
        for st in stations:
            path = os.path.join(common.REPORTS, f"{st}.csv.gz")
            if not os.path.exists(path):
                continue
            for r in common.read_csv(path):
                valid = parse_valid(r["valid"])
                if valid.date() < REPORTS_FROM:
                    continue              # the climate fetch ends where the reports begin (review of #314)
                yield st, valid, num(r["tmpf"]), num(r["t10_c"])

    days = reduce_daily(readings(), tz_of)
    rows = []
    for (st, day), d in sorted(days.items()):
        rows.append([st, stations[st], day.isoformat(), d["n"], d["first"].strftime("%Y-%m-%dT%H:%MZ"),
                     d["last"].strftime("%Y-%m-%dT%H:%MZ"), f"{d['tmax_f']:.2f}", f"{d['tmax_c']:.2f}",
                     d["tmax_at"].strftime("%Y-%m-%dT%H:%MZ"), f"{d['tmin_f']:.2f}", f"{d['tmin_c']:.2f}",
                     f"{d['max_gap_h']:.2f}"])
    n = common.write_csv(common.STATION_DAILY, DAILY_HEADER, rows)
    print(json.dumps({"station_days": n}), file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["reports", "climate", "daily"])
    ap.add_argument("--cache", default=os.environ.get("WXPREDICT_CACHE", ".wxpredict_cache/iem"))
    ap.add_argument("--end", default="")
    args = ap.parse_args()
    {"reports": cmd_reports, "climate": cmd_climate, "daily": cmd_daily}[args.cmd](args)


if __name__ == "__main__":
    main()
