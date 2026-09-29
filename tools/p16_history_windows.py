#!/usr/bin/env python3
"""How much history should the long-window weather measurements read?
(plan v2 P1.6 phase 2, step 1; 29 Sep)

Phase 2 was going to give three SQL measurements a durable hourly cache so
they could read years of history once raw weather_observations kept 30 days:
the climb profile (v_city_climb_profile_live, declared 2 years), the peak
hour (refresh_weather_peak_city, 3 years, per calendar month) and the
correlation of forecast errors (recompute_correlation, 180 days). In practice
each has only ever read what retention left - 60 days lately. Before building
anything, this asks the question the cache presupposes: does more history
help? It scores each window out of sample on every raw reading the repository
holds (data/archive/observations and data/mirror/weather_observations, back
to 22 Jul 2025), computed exactly as the SQL computes it:

  climb profile  per city and local hour, over days with >= 12 hours: climb
                 left = the highest hourly reading from this hour on minus
                 this hour's; a cell needs >= 20 days. Each test day is
                 predicted from days strictly before it; scored by the CRPS
                 of Normal(mean, sd) - what the trajectory prices from - and
                 compared on the city-day-hours every window can serve, with
                 a day-block bootstrap.
  peak hour      per city and calendar month, over days with >= 12 readings
                 whose highest reading (the earliest of equals) fell between
                 08:00 and 22:00 local: p50 and the p10-p90 window, >= 20
                 days. Scored by |peak - p50| and the window's coverage.

The per-day computation was checked against the live table first: for all
2,938 city-days whole in weather_observations on 29 Sep (local 2 Aug-27 Sep),
the hourly maxima, hour and reading counts and peak minute computed here from
the repository equal the database's, 0 differences.

  python tools/p16_history_windows.py [--out docs/HISTORY_WINDOWS_2026-09-29.md]
"""
import argparse
import bisect
import csv
import datetime as dt
import glob
import gzip
import json
import math
import os
import random
import statistics as st
import sys
from decimal import Decimal
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEZONES = os.path.join(ROOT, "data", "repairs", "2026-09-29-day-features", "timezones.json")
START, END = dt.date(2025, 9, 1), dt.date(2026, 9, 27)
CLIMB_WINDOWS = (30, 60, 90, None)          # None: every earlier day
PEAK_VARIANTS = {"recent30": 30, "recent60": 60, "all": None}
MIN_DAYS = 20


def load(tz):
    """{city: [(valid_at, temp_c)]}, one reading per (city, valid_at, source);
    the archive's copy wins over the mirror's."""
    seen = {}
    files = (sorted(glob.glob(os.path.join(ROOT, "data", "archive", "observations", "*.csv.gz")))
             + sorted(glob.glob(os.path.join(ROOT, "data", "mirror", "weather_observations", "*.csv.gz"))))
    for path in files:
        from_archive = os.sep + "archive" + os.sep in path
        with gzip.open(path, "rt") as fh:
            for r in csv.DictReader(fh):
                c = r["city_key"]
                if c not in tz:
                    continue
                at = dt.datetime.fromisoformat(r["valid_at"].replace("Z", "+00:00"))
                key = (c, at, r.get("source"))
                if key in seen and seen[key][1] and not from_archive:
                    continue
                t = r.get("temp_c")
                seen[key] = (Decimal(t) if t not in (None, "") else None, from_archive)
    out = {}
    for (c, at, _s), (t, _a) in seen.items():
        out.setdefault(c, []).append((at, t))
    return out


def local_days(readings, tz):
    """{local_date: {hour_max_c[24], n_hours, n_readings, peak_minute}} as the
    SQL derives them from (valid_at at time zone tz)."""
    by_day = {}
    for at, temp in readings:
        if temp is None:
            continue
        loc = at.astimezone(tz)
        by_day.setdefault(loc.date(), []).append((at, loc.hour, loc.hour * 60 + loc.minute, temp))
    out = {}
    for d, rows in by_day.items():
        hour_max = [None] * 24
        for _at, h, _m, t in rows:
            if hour_max[h] is None or t > hour_max[h]:
                hour_max[h] = t
        top = min(rows, key=lambda r: (-r[3], r[0]))      # order by temp_c desc, valid_at
        out[d] = {"hour_max_c": hour_max, "n_hours": sum(1 for x in hour_max if x is not None),
                  "n_readings": len(rows), "peak_minute": top[2]}
    return out


def climbs(row):
    hm = [None if x is None else float(x) for x in row["hour_max_c"]]
    return {h: max(x for x in hm[h:] if x is not None) - hm[h] for h in range(24) if hm[h] is not None}


def crps_norm(mu, sd, x):
    if sd <= 0:
        return abs(x - mu)
    z = (x - mu) / sd
    pdf = math.exp(-z * z / 2) / math.sqrt(2 * math.pi)
    cdf = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return sd * (z * (2 * cdf - 1) + 2 * pdf - 1 / math.sqrt(math.pi))


def pct(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def score_climb(cl):
    score = {w: {} for w in CLIMB_WINDOWS}
    for c, dd in cl.items():
        for h in range(8, 21):
            seq = sorted((d, v[h]) for d, v in dd.items() if h in v)
            ds = [d for d, _ in seq]
            s1, s2 = [0.0], [0.0]
            for _d, x in seq:
                s1.append(s1[-1] + x)
                s2.append(s2[-1] + x * x)
            for i, (D, actual) in enumerate(seq):
                if not START <= D <= END:
                    continue
                for w in CLIMB_WINDOWS:
                    lo = bisect.bisect_left(ds, D - dt.timedelta(days=w)) if w else 0
                    n = i - lo
                    if n < MIN_DAYS:
                        continue
                    m = (s1[i] - s1[lo]) / n
                    var = max((s2[i] - s2[lo] - n * m * m) / (n - 1), 0.0)
                    score[w][(c, D, h)] = crps_norm(m, math.sqrt(var), actual)
    return score


def boot(score, both, a, b, n=1000, seed=11):
    rng = random.Random(seed)
    by = {}
    for k in both:
        by.setdefault(k[1], []).append(score[a][k] - score[b][k])
    keys = list(by)
    total = [x for d in keys for x in by[d]]
    bs = sorted(sum(s) / len(s) for s in
                ([x for d in rng.choices(keys, k=len(keys)) for x in by[d]] for _ in range(n)))
    return sum(total) / len(total), bs[int(0.05 * n)], bs[int(0.95 * n) - 1]


def score_peak(days):
    peaks = {}
    for c, dd in days.items():
        for d, v in dd.items():
            h = v["peak_minute"] / 60.0
            if v["n_readings"] >= 12 and 8 <= h <= 22:
                peaks.setdefault(c, []).append((d, h))
    res = {k: {} for k in PEAK_VARIANTS}
    for c, seq in peaks.items():
        seq.sort()
        for D, actual in seq:
            if not START <= D <= END:
                continue
            for name, n in PEAK_VARIANTS.items():
                lo = D - dt.timedelta(days=n) if n else dt.date(2000, 1, 1)
                hist = [h for d, h in seq if lo <= d < D and d.month == D.month]
                if len(hist) < MIN_DAYS:
                    continue
                p50, p10, p90 = pct(hist, .5), pct(hist, .1), pct(hist, .9)
                res[name][(c, D)] = (abs(actual - p50), 1.0 if p10 <= actual <= p90 else 0.0)
    return res


def cells_served(cl, D, windows=(30, 45, 60)):
    out = {}
    for w in windows:
        per_city = {}
        for c, dd in cl.items():
            for h in range(24):
                if sum(1 for d, v in dd.items() if D - dt.timedelta(days=w) <= d < D and h in v) >= MIN_DAYS:
                    per_city[c] = per_city.get(c, 0) + 1
        out[w] = per_city
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "HISTORY_WINDOWS_2026-09-29.md"))
    ap.add_argument("--served-on", default="2026-09-29")
    a = ap.parse_args()
    tz = {k: ZoneInfo(v) for k, v in json.load(open(TIMEZONES)).items()}
    days = {c: local_days(rs, tz[c]) for c, rs in load(tz).items()}
    cl = {c: {d: climbs(v) for d, v in dd.items() if v["n_hours"] >= 12} for c, dd in days.items()}

    sc = score_climb(cl)
    both = set.intersection(*(set(s) for s in sc.values()))
    lines = ["# How much history the long-window weather measurements should read (29 Sep 2026)", "",
             "Generated by `tools/p16_history_windows.py` from the repository's raw readings "
             "(22 Jul 2025 to 28 Sep 2026). Plan v2 P1.6 phase 2, step 1.", "",
             "## Climb profile (`v_city_climb_profile_live`)", "",
             f"Scored on {len(both):,} city-day-hours (local 08-20) that every window can serve, "
             f"{min(k[1] for k in both)} to {max(k[1] for k in both)}. Lower CRPS is better.", "",
             "| window | CRPS |", "|---|---|"]
    for w in CLIMB_WINDOWS:
        lines.append(f"| {w or 'all history'} days | {st.mean(sc[w][k] for k in both):.4f} |".replace("all history days", "all history"))
    lines += ["", "Paired, day-block bootstrap (1,000 resamples):", ""]
    for x, y in ((60, 30), (60, None), (60, 90)):
        m, lo, hi = boot(sc, both, x, y)
        lines.append(f"- CRPS({x} days) minus CRPS({y or 'all'}): {m:+.4f}, 90% [{lo:+.4f}, {hi:+.4f}] "
                     f"(positive: {y or 'all history'} is better)")
    lines += ["", "By quarter:", "", "| quarter | n | " + " | ".join(str(w or 'all') for w in CLIMB_WINDOWS) + " |",
              "|---|---|" + "---|" * len(CLIMB_WINDOWS)]
    for q0, q1 in ((dt.date(2025, 9, 1), dt.date(2025, 11, 30)), (dt.date(2025, 12, 1), dt.date(2026, 2, 28)),
                   (dt.date(2026, 3, 1), dt.date(2026, 5, 31)), (dt.date(2026, 6, 1), END)):
        ks = [k for k in both if q0 <= k[1] <= q1]
        lines.append(f"| {q0} to {q1} | {len(ks):,} | " + " | ".join(
            f"{st.mean(sc[w][k] for k in ks):.4f}" for w in CLIMB_WINDOWS) + " |")
    served = cells_served(cl, dt.date.fromisoformat(a.served_on))
    lost = {c: served[60].get(c, 0) - served[30].get(c, 0) for c in served[60]
            if served[60].get(c, 0) != served[30].get(c, 0)}
    lines += ["", f"Cells a window can serve on {a.served_on} (>= 20 days): " + ", ".join(
        f"{w} days {sum(v.values()):,}" for w, v in served.items()) + f"; lost at 30 against 60: {lost}.", ""]

    pk = score_peak(days)
    b = set(pk["recent60"]) & set(pk["all"])
    lines += ["## Peak hour (`refresh_weather_peak_city`)", "",
              f"Scored on {len(b):,} city-days both could predict; the recent windows can predict "
              f"{len(pk['recent60']):,} city-days, all history {len(pk['all']):,} (a recent window needs "
              "20 days of the same month first).", "",
              "| history | mean abs error (h) | p10-p90 coverage |", "|---|---|---|"]
    for name in PEAK_VARIANTS:
        lines.append(f"| {name} | {st.mean(pk[name][k][0] for k in b):.3f} | {st.mean(pk[name][k][1] for k in b):.3f} |")
    lines.append("")
    open(a.out, "w").write("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
