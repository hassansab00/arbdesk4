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

  nightly   (12 Oct) each night, one request from two days before the newest
            report held to today: the reports the record does not hold yet go
            into station_reports_nightly/reports-<date>.csv.gz, and the days
            those reports touch, recomputed from every report held, into
            station_daily_nightly/days-<date>.csv.gz when they differ from the
            day held (common.station_report_rows and station_daily_rows read
            the files and the nights together). New files only: the files
            above are not rewritten each night, and a report the night's
            answer holds differently from the record is counted, not applied.

    python tools/wxpredict/fetch_obs.py reports [--end YYYY-MM-DD]
    python tools/wxpredict/fetch_obs.py climate
    python tools/wxpredict/fetch_obs.py daily      # station_daily from the cache + reports
    python tools/wxpredict/fetch_obs.py update [--end YYYY-MM-DD]   # the night's increment

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
UPDATE_OVERLAP_DAYS = 2      # asked again before the newest report held: late and special reports
UPDATE_MAX_DAYS = 14         # the most a night asks for; a station further behind is caught up by `reports`
UPDATE_TIMEOUT_S = 120       # per request, in the nightly job
UPDATE_TRIES = 3
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


def request(stations, start, end, data, timeout=600, tries=TRIES):
    p = {"station": [common.iem_id(s) for s in stations], "data": data,
         "year1": start.year, "month1": start.month, "day1": start.day,
         "year2": end.year, "month2": end.month, "day2": end.day,
         "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no", "elev": "no",
         "missing": "empty", "trace": "T", "direct": "no", "report_type": ["3", "4"]}
    err = None
    for i in range(tries):
        try:
            r = requests.get(IEM, params=p, headers=UA, timeout=timeout)
            if r.status_code == 200 and r.text.startswith("station,"):
                return r.text
            err = RuntimeError(f"{r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            err = e
        if i + 1 < tries:
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


def report_out(rec):
    """(valid, the report row as REPORT_HEADER's values) of one IEM record."""
    valid = rec["valid"].replace(" ", "T") + "Z"
    return valid, [valid, *[(rec.get(k) or "").strip() for k in FIELDS], *remarks(rec.get("metar"))]


def as_written(values):
    """Values as common.write_csv writes and read_csv reads them back."""
    return ["" if v is None else str(v) for v in values]


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
                valid, out = report_out(rec)
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


def day_row(station, city, day, d):
    """One station_daily row (DAILY_HEADER) of reduce_daily's summary, as written."""
    return [station, city, day.isoformat(), str(d["n"]), d["first"].strftime("%Y-%m-%dT%H:%MZ"),
            d["last"].strftime("%Y-%m-%dT%H:%MZ"), f"{d['tmax_f']:.2f}", f"{d['tmax_c']:.2f}",
            d["tmax_at"].strftime("%Y-%m-%dT%H:%MZ"), f"{d['tmin_f']:.2f}", f"{d['tmin_c']:.2f}",
            f"{d['max_gap_h']:.2f}"]


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
        nightly = common.nightly_reports()
        for st in stations:
            for r in common.station_report_rows(st, nightly):
                valid = parse_valid(r["valid"])
                if valid.date() < REPORTS_FROM:
                    continue              # the climate fetch ends where the reports begin (review of #314)
                yield st, valid, num(r["tmpf"]), num(r["t10_c"])

    days = reduce_daily(readings(), tz_of)
    rows = [day_row(st, stations[st], day, d) for (st, day), d in sorted(days.items())]
    # A STATION NOT ASKED THIS TIME KEEPS ITS DAYS (11 Oct). The stations are the
    # active cities' (common.event_stations); a city that has left the list
    # (Zhengzhou, ZHCC: 2,470 days on 11 Oct) is not fetched, and rewriting the
    # file from this run alone dropped its whole history. Its rows are carried
    # over unchanged.
    kept = []
    if os.path.exists(common.STATION_DAILY):
        kept = [[r[h] for h in DAILY_HEADER] for r in common.read_csv(common.STATION_DAILY)
                if r["station"] not in stations]
    rows = sorted(rows + kept, key=lambda r: (r[0], r[2]))
    n = common.write_csv(common.STATION_DAILY, DAILY_HEADER, rows)
    print(json.dumps({"station_days": n, "kept_from_stations_not_asked": len(kept)}), file=sys.stderr)


def cmd_update(args):
    """The night's increment (module docstring, `nightly`). Returns its summary."""
    stations = common.event_stations()
    cities = common.cities()
    tz_of = {s: common.zone(cities[c]) for s, c in stations.items()}
    today = dt.date.fromisoformat(args.end) if args.end else dt.datetime.now(common.UTC).date()
    nightly = common.nightly_reports()
    held = {st: {r["valid"]: r for r in common.station_report_rows(st, nightly)} for st in stations}
    newest = {st: max(v) for st, v in held.items() if v}
    if not newest:
        raise SystemExit("no station holds a report: run `reports` first")
    start = max(parse_valid(min(newest.values())).date() - dt.timedelta(days=UPDATE_OVERLAP_DAYS),
                today - dt.timedelta(days=UPDATE_MAX_DAYS))
    end = today + dt.timedelta(days=1)
    text = request(list(stations), start, end, ",".join(FIELDS + ["metar"]),
                   timeout=UPDATE_TIMEOUT_S, tries=UPDATE_TRIES)

    icao = back_to_icao(stations)
    new = defaultdict(dict)
    differ = defaultdict(int)
    unknown = defaultdict(int)
    for rec in csv.DictReader(io.StringIO(text)):
        st = icao.get((rec.get("station") or "").strip().upper())
        if st is None:
            unknown[rec.get("station")] += 1
            continue
        valid, out = report_out(rec)
        out = as_written(out)
        old = held[st].get(valid)
        if old is None:
            new[st][valid] = out
        elif [old.get(h, "") for h in REPORT_HEADER] != out:
            differ[st] += 1                   # the record keeps what it read first

    stamp = today.isoformat()
    rep_path = os.path.join(common.reports_nightly_dir(), f"reports-{stamp}.csv.gz")
    rep_rows = {}
    if os.path.exists(rep_path):              # a second run the same day keeps the first's rows
        for r in common.read_csv(rep_path):
            rep_rows[(r["station"], r["valid"])] = [r["station"], *[r[h] for h in REPORT_HEADER]]
    for st, by in new.items():
        for valid, out in by.items():
            rep_rows[(st, valid)] = [st, *out]
    if rep_rows:
        common.write_csv(rep_path, ["station", *REPORT_HEADER], [rep_rows[k] for k in sorted(rep_rows)])

    # the days the asked window covers whole, from every report held now
    t0 = dt.datetime.combine(start, dt.time(0), common.UTC)

    def readings():
        for st in stations:
            rows = dict(held[st])
            rows.update({v: dict(zip(REPORT_HEADER, out)) for v, out in new[st].items()})
            for valid_s, r in rows.items():
                valid = parse_valid(valid_s)
                if valid >= t0:
                    yield st, valid, num(r.get("tmpf")), num(r.get("t10_c"))

    days = reduce_daily(readings(), tz_of)
    current = {(r["station"], r["local_date"]): [r[h] for h in DAILY_HEADER] for r in common.station_daily_rows()}
    changed = {}
    for (st, day), d in sorted(days.items()):
        if common.local_day_bounds(day, tz_of[st])[0] < t0.timestamp():
            continue                          # the window starts inside this day
        row = day_row(st, stations[st], day, d)
        if current.get((st, row[2])) != row:
            changed[(st, row[2])] = row
    day_path = os.path.join(common.station_daily_nightly_dir(), f"days-{stamp}.csv.gz")
    day_rows = {}
    if os.path.exists(day_path):
        for r in common.read_csv(day_path):
            day_rows[(r["station"], r["local_date"])] = [r[h] for h in DAILY_HEADER]
    day_rows.update(changed)
    if day_rows:
        common.write_csv(day_path, DAILY_HEADER, [day_rows[k] for k in sorted(day_rows)])

    after = {st: max([newest.get(st, ""), *new[st]]) for st in stations if newest.get(st) or new[st]}
    summary = {"night": stamp, "asked": [start.isoformat(), end.isoformat()], "stations": len(stations),
               "new_reports": sum(len(v) for v in new.values()),
               "stations_without_a_new_report": sorted(st for st in stations if not new[st]),
               "reports_differing_from_the_record_not_applied": dict(differ),
               "unknown_station_rows": dict(unknown), "days_written": len(changed),
               "newest_report": {"oldest_station": min(after.values()) if after else None,
                                 "newest_station": max(after.values()) if after else None}}
    print(json.dumps(summary), file=sys.stderr)
    return summary


JOB = "P2.13_station_record"


def ops_log():
    """scripts/common.py's log_run when the job has the database's keys, else
    None. Loaded by path: this script's own directory holds wxpredict's
    common.py. Importing it also makes a crash leave its row (crash:<script>)."""
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY")):
        return None
    return common.ops().log_run


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["reports", "climate", "daily", "update"])
    ap.add_argument("--cache", default=os.environ.get("WXPREDICT_CACHE", ".wxpredict_cache/iem"))
    ap.add_argument("--end", default="")
    args = ap.parse_args(argv)
    if args.cmd != "update":
        {"reports": cmd_reports, "climate": cmd_climate, "daily": cmd_daily}[args.cmd](args)
        return 0
    log = ops_log()
    s = cmd_update(args)
    # partial: a station had no new report all night (a station gone quiet,
    # or one IEM no longer answers for)
    status = "ok" if not s["stations_without_a_new_report"] else "partial"
    if log:
        log(JOB, status, s["new_reports"], s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
