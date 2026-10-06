"""Seasonal Focus 10: which cities' daily-maximum markets should be most
predictable over 6 Oct - 30 Nov 2026, chosen on 6 Oct 2026 BEFORE any outcome
of that period, by a method fixed here before any number was computed.

Research only: nothing here prices or trades. Reads committed files only (no
database). Standard library only.

    python3 tools/focus/season_predictability.py            # prints the table, writes the JSON
    python3 tools/focus/season_predictability.py --no-write # prints only

DATA RULES (enforced in code)
-----------------------------
- CUTOFF = 2026-09-01. No outcome, market price, observation or forecast dated
  on or after it is used: every loader drops such rows, and `guard()` asserts
  on every row that enters a computation. 2026-09-01 onward is the sealed test
  of a model under evaluation.
- The blinded challengers (rd3, da_floor, sd_corr) and anything under
  data/eval/fec_v1/ are not read.
- The universe and each city's settlement station, unit and zone come from the
  cities mirror of 6 Oct 2026 (metadata, not an outcome), `status = active`.

METHOD (fixed before computing)
-------------------------------
Analog window: calendar days 6 Oct - 30 Nov of every year before the cutoff
that has data. First half 6 Oct - 2 Nov, second half 3 - 30 Nov.

Observation: the station maximum of a whole local day at the city's settlement
station today (data/training/wxpredict/station_daily.csv.gz, written by
tools/wxpredict/fetch_obs.py: tmax_c is the greatest report in C, from the
METAR T group when present, else IEM's tmpf converted; tmax_f is the greatest
tmpf). A day is whole when max_gap_h <= 3 and the station's reports run past
the day's end (tools/wxpredict/build_table.py `whole_day`; the newest report is
taken among pre-cutoff rows only). The venue's reading is whole degrees of the
market's unit, rounded half up (build_table.py `unit_reading`): F from tmax_f,
C from tmax_c.

Forecast (primary): Open-Meteo best_match, Previous Runs `_previous_day1`
(lead_days = 1), field `tmax_00_17_c`: the maximum over local hours 00-17 of
D, each hour from a run started at least 24 h before it
(data/training/previous_runs/best_match_daily.csv.gz). By the builder's rule
(`daily_known`) it is known from local midnight before D: an honest day-ahead
number. Only 14 Jul 2025 - 4 Oct 2026 exists, so the window has forecasts in
2025 only.
Sensitivity forecasts (reported, not ranked on): lead-1 whole-day `tmax_c`
(known from D 06:00), lead-2 whole-day `tmax_c` (known from D-1 06:00), the
mean of the seven models' lead-1 `tmax_00_17_c` (models_daily.csv.gz, at
least 4 models), and the primary with no bias correction.

1. Error e = forecast - observed station maximum, in C (positive = forecast
   too warm). Bias = mean e, SD = sample SD of e, MAE = mean |e|, over the
   window days with a whole station day and a forecast.
2. Bucket width in C (1 C; 2 F = 1.111 C, both verified on the pre-cutoff
   ladders: every inner bucket of a C city is 1 wide, of an F city 2 wide
   with an even lower edge). SD in buckets = error SD / width.
   Bias-corrected forecast-bucket hit rate: the forecast minus the city's
   trailing bias (mean e over whole days D-31 .. D-2 with a forecast; at
   least 10 such days, else the day is not scored), converted to the unit,
   rounded half up to a whole reading, put on the city's bucket grid
   (C: [n, n+1); F: [2k, 2k+2)), against the bucket holding the venue's
   reading of the observed maximum. An unbounded grid: Oct - Nov 2025 had no
   listed ladders, so the open tails are not modelled. Wilson 95% interval.
   D-2 is the newest whole day known at a call frozen at D-1 evening or at
   local midnight (it ended a day earlier; build_table.py REPORT_LAG_S).
3. Regime volatility: SD of the day-to-day change of the station maximum (C),
   over pairs (D-1, D) with D in the window and both days whole, every year
   2020 - 2025. Seasonal transition: error SD (primary, raw e) of the second
   half minus the first half (2025), and the same change in the day-to-day
   SD over 2020 - 2025.
4. The market's skill: top-1 hit rate of the market's favourite bucket at
   18:00 local on D-1 and 12:00 local on D, against the venue's winner. Prices
   as tools/p310_midnight_favourite.py takes them: each bucket's FIRST price at
   or after the decision, at most 60 min later; a city-day counts only when
   every bucket has one; the favourite is the highest price (lowest index on
   a tie). Events: tools/wxpredict/build_table.py's filters (active city,
   closed, exactly one winner, a main listing over an arch twin, contiguous
   ladder), dated in the window and before the cutoff. Supplementary, NOT the
   window and NOT a ranking input: the same over every pre-cutoff event
   (2025-12-30 - 2026-08-31), all seasons.
5. Data sufficiency: window days with a whole station day and the primary
   forecast; days scored for the hit rate (also need the trailing bias).

Checks reported beside the result (not ranking inputs): the station reading's
bucket against the venue's winner on every pre-cutoff listed day settled at
the city's station today; Spearman correlation and top-10 overlap of each
sensitivity forecast's hit rate with the primary; a block bootstrap of the
selection (7-day blocks of window dates, the same dates for every city, 2000
draws, seed 20261006, flags held fixed): the share of draws in which each city
is in the Focus 10.

RANKING RULE (fixed before computing)
-------------------------------------
R1. Eligible: at least 40 scored analog days (item 5).
R2. Order eligible cities by the bias-corrected forecast-bucket hit rate,
    highest first. Ties (equal to 0.1 percentage point) go to the lower
    day-to-day SD (2020 - 2025).
R3. November-transition flag: the error SD rises from the first half to the
    second by at least 0.5 C AND the 2020 - 2025 day-to-day SD also rises.
    A flagged city enters the Focus 10 only if fewer than 10 unflagged
    eligible cities remain; it keeps its place in the full ranked list.
R4. The Focus 10 = the first 10 eligible cities in R2 order after R3.
The market's skill (4) is reported beside the ranking as a check; it is a
ranking input only for a city whose forecast measure is unavailable (none
expected: every active city has the record).
"""
import argparse
import bisect
import calendar
import collections
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import random
import statistics
import sys
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CUTOFF = dt.date(2026, 9, 1)
CUTOFF_UTC = calendar.timegm(CUTOFF.timetuple())
AS_OF = "2026-10-06"
CITIES = os.path.join(ROOT, "data", "mirror", "cities", "cities-2026-10-06.csv.gz")
STATION_DAILY = os.path.join(ROOT, "data", "training", "wxpredict", "station_daily.csv.gz")
BM_DAILY = os.path.join(ROOT, "data", "training", "previous_runs", "best_match_daily.csv.gz")
MODELS_DAILY = os.path.join(ROOT, "data", "training", "previous_runs", "models_daily.csv.gz")
MH = os.path.join(ROOT, "data", "training", "market_history")
OUT = os.path.join(ROOT, "data", "eval", "focus", "season_predictability_2026-10-06.json")
NOTES = os.path.join(ROOT, "tools", "focus", "climate_notes_2026-10-06.json")

WINDOW = ((10, 6), (11, 30))
HALF2_FROM = (11, 3)
YEARS = range(2020, 2026)          # station_daily starts 2019-12-31; the cutoff year's window is after it
WHOLE_DAY_MAX_GAP_H = 3
BIAS_FROM_K, BIAS_TO_K, BIAS_MIN_N = 2, 31, 10
MIN_DAYS = 40
TIE_PP = 0.1
FLAG_ERR_SD_RISE_C = 0.5
MARKET_DECISIONS = {"d1_18": (-1, 18), "d0_12": (0, 12)}
AFTER_MIN = 60
BOOT_N, BOOT_BLOCK, BOOT_SEED = 2000, 7, 20261006
TOP_N = 10

USED_MAX = collections.defaultdict(str)     # source -> newest date used


def guard(date_str, source):
    """Every row entering a computation passes here: nothing on/after the cutoff."""
    assert date_str < CUTOFF.isoformat(), f"{source}: row dated {date_str} is on/after the cutoff {CUTOFF}"
    if date_str > USED_MAX[source]:
        USED_MAX[source] = date_str


def guard_t(t, source):
    assert t < CUTOFF_UTC, f"{source}: instant {t} is on/after the cutoff"
    guard(dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), source)


def read_csv(path):
    with gzip.open(path, "rt", newline="") as f:
        yield from csv.DictReader(f)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def round_half_up(x):
    return int(math.floor(x + 0.5))


def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def in_window(d):
    return WINDOW[0] <= (d.month, d.day) <= WINDOW[1]


def half(d):
    return 2 if (d.month, d.day) >= HALF2_FROM else 1


def window_days(year):
    d, end = dt.date(year, *WINDOW[0]), dt.date(year, *WINDOW[1])
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def sd(xs):
    return statistics.stdev(xs) if len(xs) >= 2 else None


def mean(xs):
    return statistics.fmean(xs) if xs else None


def wilson(k, n, z=1.96):
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num_ = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num_ / den if den else None


def r1(x, n=1):
    return None if x is None else round(x, n)


# ---------------------------------------------------------------------------
# loaders (every one drops rows on/after the cutoff)
# ---------------------------------------------------------------------------
def load_cities():
    return {r["city_key"]: r for r in read_csv(CITIES) if (r["status"] or "active") == "active"}


def load_station_days(cities):
    """{city: {date: (tmax_c, tmax_f)}} whole days only, at the city's station
    today; {city: {date: why}} for the days left out; rows dropped by the cutoff."""
    rows = collections.defaultdict(list)
    dropped = 0
    want = {(c["icao"] or "").upper(): k for k, c in cities.items()}
    for r in read_csv(STATION_DAILY):
        if want.get(r["station"]) != r["city_key"]:
            continue
        if r["local_date"] >= CUTOFF.isoformat():
            dropped += 1
            continue
        rows[r["city_key"]].append(r)
    whole, refused = {}, {}
    for city, rs in rows.items():
        tz = ZoneInfo(cities[city]["timezone"] or "UTC")
        newest = max(calendar.timegm(dt.datetime.strptime(r["last_valid"], "%Y-%m-%dT%H:%MZ").timetuple())
                     for r in rs)
        w, f = {}, {}
        for r in rs:
            d = dt.date.fromisoformat(r["local_date"])
            day_end = int(dt.datetime.combine(d + dt.timedelta(days=1), dt.time(0), tz).timestamp())
            if newest < day_end:
                f[r["local_date"]] = "reports stop inside the day"
            elif float(r["max_gap_h"]) > WHOLE_DAY_MAX_GAP_H:
                f[r["local_date"]] = "gap over 3 h"
            else:
                w[r["local_date"]] = (float(r["tmax_c"]), float(r["tmax_f"]))
        whole[city], refused[city] = w, f
    return whole, refused, dropped


def load_forecasts():
    """{variant: {(city, date): tmax C}} for the primary and the sensitivity forecasts."""
    out = collections.defaultdict(dict)
    dropped = 0
    for r in read_csv(BM_DAILY):
        if r["for_date"] >= CUTOFF.isoformat():
            dropped += 1
            continue
        key = (r["city_key"], r["for_date"])
        if r["lead_days"] == "1":
            if num(r["tmax_00_17_c"]) is not None:
                out["primary_l1_00_17"][key] = num(r["tmax_00_17_c"])
            if num(r["tmax_c"]) is not None:
                out["l1_whole_day"][key] = num(r["tmax_c"])
        elif r["lead_days"] == "2" and num(r["tmax_c"]) is not None:
            out["l2_whole_day"][key] = num(r["tmax_c"])
    models = collections.defaultdict(list)
    for r in read_csv(MODELS_DAILY):
        if r["for_date"] >= CUTOFF.isoformat():
            dropped += 1
            continue
        if r["lead_days"] == "1" and num(r["tmax_00_17_c"]) is not None:
            models[(r["city_key"], r["for_date"])].append(num(r["tmax_00_17_c"]))
    for key, vs in models.items():
        if len(vs) >= 4:
            out["models_mean_l1_00_17"][key] = statistics.fmean(vs)
    out["primary_no_bias_correction"] = out["primary_l1_00_17"]
    return dict(out), dropped


def load_market(cities):
    """Scorable venue events dated before the cutoff, with their ladders and winners."""
    events = list(read_csv(os.path.join(MH, "events.csv.gz")))
    main_days = {(r["city_key"], r["date"]) for r in events if r["listing"] == "main"}
    ev, left = {}, collections.Counter()
    for r in events:
        if r["date"] >= CUTOFF.isoformat():
            left["dated on/after the cutoff"] += 1
            continue
        if r["city_key"] not in cities:
            left["city not active"] += 1
            continue
        if r["closed"] != "1":
            left["not closed"] += 1
            continue
        if r["listing"] == "arch" and (r["city_key"], r["date"]) in main_days:
            left["arch twin of a main listing"] += 1
            continue
        ev[r["event_id"]] = {"city": r["city_key"], "date": r["date"], "unit": r["unit"],
                             "station": (r["station_icao"] or "").upper(), "bands": []}
    for r in read_csv(os.path.join(MH, "bands.csv.gz")):
        e = ev.get(r["event_id"])
        if e is not None:
            e["bands"].append((int(r["band_index"]), num(r["band_lo"]), num(r["band_hi"]),
                               r["open_low"] == "1", r["open_high"] == "1", r["winner"]))
    out = {}
    for eid, e in ev.items():
        bands = sorted(e["bands"])
        wins = [b[0] for b in bands if b[5] == "1"]
        if len(wins) != 1 or any(b[5] not in ("0", "1") for b in bands):
            left["not exactly one winner"] += 1
            continue
        if len(bands) < 2 or not bands[0][3] or not bands[-1][4] or \
                any(a[2] != b[1] for a, b in zip(bands, bands[1:])):
            left["ladder not contiguous"] += 1
            continue
        e["bands"] = [(lo, hi) for _, lo, hi, _, _, _ in bands]
        e["winner"] = wins[0]
        out[eid] = e
    return out, dict(left)


def bucket_of(value, bands):
    for i, (lo, hi) in enumerate(bands):
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return i
    return None


def grid_check(events):
    """Inner-bucket widths and F lower-edge parity per city, pre-cutoff ladders."""
    widths, parity = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    for e in events.values():
        guard(e["date"], "market ladders")
        for lo, hi in e["bands"]:
            if lo is not None and hi is not None:
                widths[e["city"]][hi - lo] += 1
                parity[e["city"]][int(lo) % 2] += 1
    return widths, parity


def market_skill(events, cities):
    """{city: {decision: [hits, n]}} for window events and for all events."""
    times = {}
    for eid, e in events.items():
        tz = ZoneInfo(cities[e["city"]]["timezone"])
        day = dt.date.fromisoformat(e["date"])
        for k, (dd, h) in MARKET_DECISIONS.items():
            times[(eid, k)] = int(dt.datetime.combine(day + dt.timedelta(days=dd), dt.time(h), tz).timestamp())
    best = {}
    for r in read_csv(os.path.join(MH, "prices.csv.gz")):
        eid = r["event_id"]
        if eid not in events:
            continue
        t = int(r["t"])
        for k in MARKET_DECISIONS:
            td = times[(eid, k)]
            if td <= t <= td + AFTER_MIN * 60:
                key = (eid, k, int(r["band_index"]))
                if key not in best or t < best[key][0]:
                    best[key] = (t, float(r["p"]))
    win = collections.defaultdict(lambda: {k: [0, 0] for k in MARKET_DECISIONS})
    alls = collections.defaultdict(lambda: {k: [0, 0] for k in MARKET_DECISIONS})
    for eid, e in events.items():
        d = dt.date.fromisoformat(e["date"])
        for k in MARKET_DECISIONS:
            ps = [best.get((eid, k, i)) for i in range(len(e["bands"]))]
            if any(p is None for p in ps):
                continue
            for p in ps:
                guard_t(p[0], "market prices")
            guard(e["date"], "market outcomes")
            fav = max(range(len(ps)), key=lambda i: (ps[i][1], -i))
            hit = int(fav == e["winner"])
            for tgt in ([win, alls] if in_window(d) else [alls]):
                tgt[e["city"]][k][0] += hit
                tgt[e["city"]][k][1] += 1
    return win, alls


def station_vs_venue(events, cities, whole):
    """The station reading's bucket against the venue's winner, pre-cutoff
    listed days settled at the city's station today, whole days only."""
    out = collections.defaultdict(lambda: [0, 0])
    for e in events.values():
        c = cities[e["city"]]
        if e["station"] != (c["icao"] or "").upper():
            continue
        obs = whole.get(e["city"], {}).get(e["date"])
        if obs is None:
            continue
        guard(e["date"], "venue winners (check)")
        v = round_half_up(obs[1] if e["unit"] == "F" else obs[0])
        out[e["city"]][0] += int(bucket_of(v, e["bands"]) == e["winner"])
        out[e["city"]][1] += 1
    return out


# ---------------------------------------------------------------------------
# the forecast measures
# ---------------------------------------------------------------------------
def unit_bucket(value_c, unit):
    """The bucket index of a temperature in C on the city's grid (unbounded):
    whole readings rounded half up; C [n, n+1), F [2k, 2k+2)."""
    if unit == "F":
        return round_half_up(c_to_f(value_c)) // 2
    return round_half_up(value_c)


def obs_bucket(obs, unit):
    tmax_c, tmax_f = obs
    return round_half_up(tmax_f) // 2 if unit == "F" else round_half_up(tmax_c)


def trailing_bias(fc, days, city, d):
    errs = []
    for k in range(BIAS_FROM_K, BIAS_TO_K + 1):
        p = (d - dt.timedelta(days=k)).isoformat()
        f, o = fc.get((city, p)), days.get(p)
        if f is not None and o is not None:
            guard(p, "station days (bias)")
            guard(p, "forecasts (bias)")
            errs.append(f - o[0])
    return (statistics.fmean(errs), len(errs)) if len(errs) >= BIAS_MIN_N else (None, len(errs))


def forecast_rows(fc, days, city, unit, correct=True):
    """[(date, e, e_corrected, hit, hit_raw)] over the window days with a whole
    station day, a forecast and (when `correct`) a trailing bias."""
    rows, no_bias = [], 0
    for y in YEARS:
        for d in window_days(y):
            k = d.isoformat()
            f, o = fc.get((city, k)), days.get(k)
            if f is None or o is None:
                continue
            guard(k, "station days")
            guard(k, "forecasts")
            b, _ = trailing_bias(fc, days, city, d) if correct else (0.0, 0)
            if b is None:
                no_bias += 1
                continue
            ob = obs_bucket(o, unit)
            rows.append((d, f - o[0], f - b - o[0], int(unit_bucket(f - b, unit) == ob),
                         int(unit_bucket(f, unit) == ob)))
    return rows, no_bias


def day_to_day(days, years=YEARS):
    out = {1: [], 2: [], "all": [], "2025": []}
    for y in years:
        for d in window_days(y):
            a, b = days.get((d - dt.timedelta(days=1)).isoformat()), days.get(d.isoformat())
            if a is None or b is None:
                continue
            guard(d.isoformat(), "station days")
            x = b[0] - a[0]
            out["all"].append(x)
            out[half(d)].append(x)
            if y == 2025:
                out["2025"].append(x)
    return out


def rank_cities(metrics, hit_of=None):
    """R1-R4 on `metrics`; `hit_of` overrides the hit rates (bootstrap)."""
    hit_of = hit_of or {c: m["hit_bc"] for c, m in metrics.items()}
    elig = [c for c, m in metrics.items() if m["n_scored"] >= MIN_DAYS and hit_of.get(c) is not None]
    # R2: hit rate to 0.1 pp, then the lower day-to-day SD
    order = sorted(elig, key=lambda c: (-round(hit_of[c] * 100, 1), metrics[c]["dd_sd_c"], c))
    unflagged = [c for c in order if not metrics[c]["nov_flag"]]
    top = unflagged[:TOP_N]
    if len(top) < TOP_N:
        top += [c for c in order if metrics[c]["nov_flag"]][:TOP_N - len(top)]
    return order, top, elig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    cities = load_cities()
    whole, refused, st_dropped = load_station_days(cities)
    fcs, fc_dropped = load_forecasts()
    events, ev_left = load_market(cities)
    widths, parity = grid_check(events)
    print(f"active cities {len(cities)} ({os.path.relpath(CITIES, ROOT)}); station rows dropped by the cutoff "
          f"{st_dropped}; forecast rows dropped {fc_dropped}; scorable pre-cutoff events {len(events)} "
          f"(left out {ev_left})", file=sys.stderr)

    # bucket grid, verified on the pre-cutoff ladders
    grid = {}
    for city, c in cities.items():
        unit = c["unit"]
        w = widths.get(city)
        want = 2.0 if unit == "F" else 1.0
        ok = None if not w else (set(w) == {want} and (unit != "F" or set(parity[city]) == {0}))
        grid[city] = {"unit": unit, "width_unit": want, "width_c": want * 5 / 9 if unit == "F" else want,
                      "verified_on_ladders": ok, "inner_buckets_seen": sum(w.values()) if w else 0}
        assert ok in (True, None), f"{city}: ladder grid {dict(w)} parity {dict(parity[city])} is not the assumed one"

    win_mkt, all_mkt = market_skill(events, cities)
    sv = station_vs_venue(events, cities, whole)

    metrics, rows_of = {}, {}
    for city, c in sorted(cities.items()):
        unit, days = c["unit"], whole.get(city, {})
        rows, no_bias = forecast_rows(fcs["primary_l1_00_17"], days, city, unit)
        rows_of[city] = rows
        e = [r[1] for r in rows]
        ec = [r[2] for r in rows]
        k = sum(r[3] for r in rows)
        n = len(rows)
        h1 = [r[1] for r in rows if half(r[0]) == 1]
        h2 = [r[1] for r in rows if half(r[0]) == 2]
        dd = day_to_day(days)
        n_fc_obs = sum(1 for y in YEARS for d in window_days(y)
                       if (city, d.isoformat()) in fcs["primary_l1_00_17"] and d.isoformat() in days)
        st_years = {}
        for y in YEARS:
            st_years[str(y)] = sum(1 for d in window_days(y) if d.isoformat() in days)
        fc_years = sorted({r[0].year for r in rows})
        lo, hi = wilson(k, n)
        sd_h1, sd_h2 = sd(h1), sd(h2)
        dd1, dd2 = sd(dd[1]), sd(dd[2])
        err_rise = None if sd_h1 is None or sd_h2 is None else sd_h2 - sd_h1
        dd_rise = None if dd1 is None or dd2 is None else dd2 - dd1
        flag = bool(err_rise is not None and dd_rise is not None and err_rise >= FLAG_ERR_SD_RISE_C and dd_rise > 0)
        m = {
            "unit": unit, "station": c["icao"], "timezone": c["timezone"],
            "latitude": num(c["latitude"]), "longitude": num(c["longitude"]),
            "bucket_width_unit": grid[city]["width_unit"], "bucket_width_c": round(grid[city]["width_c"], 4),
            "grid_verified_on_pre_cutoff_ladders": grid[city]["verified_on_ladders"],
            "forecast_years": fc_years, "station_years_whole_days": st_years,
            "n_window_days_whole_station_and_forecast": n_fc_obs,
            "n_scored": n, "n_no_trailing_bias": no_bias,
            "station_days_not_whole_in_window": sum(1 for y in YEARS for d in window_days(y)
                                                    if d.isoformat() in refused.get(city, {})),
            "bias_c": mean(e), "err_sd_c": sd(e), "mae_c": mean([abs(x) for x in e]),
            "corrected_err_mean_c": mean(ec), "corrected_err_sd_c": sd(ec),
            "corrected_mae_c": mean([abs(x) for x in ec]),
            "err_sd_in_buckets": (sd(e) / grid[city]["width_c"]) if sd(e) is not None else None,
            "hits": k, "hit_bc": k / n if n else None, "hit_bc_wilson95": [lo, hi],
            "hit_raw": (sum(r[4] for r in rows) / n) if n else None,
            "dd_sd_c": sd(dd["all"]), "dd_n": len(dd["all"]), "dd_sd_2025_c": sd(dd["2025"]),
            "dd_sd_h1_c": dd1, "dd_sd_h2_c": dd2, "dd_sd_change_c": dd_rise,
            "err_sd_h1_c": sd_h1, "err_sd_h2_c": sd_h2, "n_h1": len(h1), "n_h2": len(h2),
            "err_sd_change_c": err_rise,
            "mae_h1_c": mean([abs(x) for x in h1]), "mae_h2_c": mean([abs(x) for x in h2]),
            "nov_flag": flag,
            "market_window": {k_: {"hits": v[0], "n": v[1], "rate": (v[0] / v[1]) if v[1] else None}
                              for k_, v in win_mkt[city].items()},
            "market_all_pre_cutoff_supplementary": {k_: {"hits": v[0], "n": v[1], "rate": (v[0] / v[1]) if v[1] else None}
                                                    for k_, v in all_mkt[city].items()},
            "station_reading_in_venue_winner_pre_cutoff": {"agree": sv[city][0], "n": sv[city][1]},
        }
        metrics[city] = m

    order, top, elig = rank_cities(metrics)
    pure_top = order[:TOP_N]
    for i, c in enumerate(order):
        metrics[c]["rank"] = i + 1
    for c in metrics:
        metrics[c]["in_focus_10"] = c in top

    # sensitivity forecasts
    sens = {}
    base = [metrics[c]["hit_bc"] for c in elig]
    for v in ("l1_whole_day", "l2_whole_day", "models_mean_l1_00_17", "primary_no_bias_correction"):
        hv = {}
        for c in elig:
            rows, _ = forecast_rows(fcs[v], whole.get(c, {}), c, cities[c]["unit"],
                                    correct=(v != "primary_no_bias_correction"))
            hv[c] = (sum(r[3] for r in rows) / len(rows)) if rows else None
            metrics[c].setdefault("hit_sensitivity", {})[v] = {"hit": hv[c], "n": len(rows)}
        ok = [c for c in elig if hv[c] is not None]
        v_order = sorted(ok, key=lambda c: (-round(hv[c] * 100, 1), metrics[c]["dd_sd_c"], c))
        sens[v] = {"spearman_vs_primary": spearman([metrics[c]["hit_bc"] for c in ok], [hv[c] for c in ok]),
                   "top10_by_hit_overlap_with_primary_top10_by_hit": len(set(v_order[:TOP_N]) & set(pure_top)),
                   "top10_by_hit": v_order[:TOP_N],
                   "n_cities": len(ok)}

    # block bootstrap of the selection (dates resampled in 7-day blocks, same for every city)
    rng = random.Random(BOOT_SEED)
    all_dates = sorted({r[0] for c in elig for r in rows_of[c]})
    blocks = [all_dates[i:i + BOOT_BLOCK] for i in range(0, len(all_dates), BOOT_BLOCK)]
    by_city_date = {c: {r[0]: r[3] for r in rows_of[c]} for c in elig}
    in_top = collections.Counter()
    for _ in range(BOOT_N):
        pick = [d for _b in range(len(blocks)) for d in rng.choice(blocks)]
        hv = {}
        for c in elig:
            hs = [by_city_date[c][d] for d in pick if d in by_city_date[c]]
            hv[c] = sum(hs) / len(hs) if hs else None
        _, t, _ = rank_cities(metrics, hv)
        in_top.update(t)
    for c in metrics:
        metrics[c]["bootstrap_share_in_focus_10"] = in_top[c] / BOOT_N if c in elig else None

    corr_mkt = {}
    for k_ in MARKET_DECISIONS:
        ok = [c for c in elig if metrics[c]["market_all_pre_cutoff_supplementary"][k_]["n"] >= 30]
        corr_mkt[k_] = {"spearman_hit_bc_vs_market_all_season": spearman(
            [metrics[c]["hit_bc"] for c in ok],
            [metrics[c]["market_all_pre_cutoff_supplementary"][k_]["rate"] for c in ok]), "n_cities": len(ok)}

    # number-grounded one-liners
    for c in metrics:
        m = metrics[c]
        m["reason_numbers"] = (
            f"hit {100 * m['hit_bc']:.0f}% ({m['hits']}/{m['n_scored']}, 95% {100 * m['hit_bc_wilson95'][0]:.0f}-"
            f"{100 * m['hit_bc_wilson95'][1]:.0f}); error SD {m['err_sd_c']:.2f} C = {m['err_sd_in_buckets']:.2f} buckets, "
            f"bias {m['bias_c']:+.2f} C; day-to-day SD {m['dd_sd_c']:.2f} C; error SD Oct->Nov "
            f"{m['err_sd_change_c']:+.2f} C, day-to-day SD {m['dd_sd_change_c']:+.2f} C"
            + ("; November-transition flag" if m["nov_flag"] else ""))

    notes = {}
    if os.path.exists(NOTES):
        with open(NOTES) as f:
            notes = json.load(f)

    # ---- print
    hdr = ("rank city unit width yrs(fc|stn) N bias errSD MAE SDbkt hit% [95%] raw% ddSD dErrSD dDD flag "
           "mkt_d1_18(N) mkt_d0_12(N) | all-season mkt d1_18(N) d0_12(N) | boot")
    print(hdr)
    for c in order + [c for c in sorted(metrics) if c not in order]:
        m = metrics[c]

        def mk(x):
            return f"{100 * x['rate']:.0f}%({x['n']})" if x["n"] else f"-({x['n']})"
        print(f"{m.get('rank', '-'):>2} {c:14s} {m['unit']} {m['bucket_width_unit']:.0f}{m['unit']} "
              f"{','.join(map(str, m['forecast_years']))}|2020-25 {m['n_scored']:3d} {m['bias_c']:+.2f} "
              f"{m['err_sd_c']:.2f} {m['mae_c']:.2f} {m['err_sd_in_buckets']:.2f} {100 * m['hit_bc']:4.1f} "
              f"[{100 * m['hit_bc_wilson95'][0]:.0f}-{100 * m['hit_bc_wilson95'][1]:.0f}] {100 * m['hit_raw']:4.1f} "
              f"{m['dd_sd_c']:.2f} {m['err_sd_change_c']:+.2f} {m['dd_sd_change_c']:+.2f} {'F' if m['nov_flag'] else '.'} "
              f"{mk(m['market_window']['d1_18'])} {mk(m['market_window']['d0_12'])} | "
              f"{mk(m['market_all_pre_cutoff_supplementary']['d1_18'])} "
              f"{mk(m['market_all_pre_cutoff_supplementary']['d0_12'])} | "
              f"{100 * (m['bootstrap_share_in_focus_10'] or 0):.0f}%"
              + ("  *FOCUS10*" if m["in_focus_10"] else ""))
    print("Focus 10:", top)
    print("Top 10 by hit rate alone (R2 without R3):", pure_top)
    print("sensitivity:", json.dumps(sens, indent=None, default=lambda x: x))
    print("market check (all-season, not the window):", json.dumps(corr_mkt))
    print("newest date used per source:", dict(USED_MAX))
    sv_tot = [sum(v[0] for v in sv.values()), sum(v[1] for v in sv.values())]
    print(f"station reading in venue winner, pre-cutoff, station today: {sv_tot[0]}/{sv_tot[1]}")

    if args.no_write:
        return
    doc = {
        "as_of": AS_OF,
        "cutoff_exclusive": CUTOFF.isoformat(),
        "newest_date_used_per_source": dict(USED_MAX),
        "method": __doc__.split("METHOD (fixed before computing)")[1].split("RANKING RULE")[0].strip().lstrip("-").strip(),
        "ranking_rule": __doc__.split("RANKING RULE (fixed before computing)")[1].strip().lstrip("-").strip(),
        "data_rules": __doc__.split("DATA RULES (enforced in code)")[1].split("METHOD (fixed")[0].strip().lstrip("-").strip(),
        "parameters": {"window": "10-06..11-30", "half2_from": "11-03", "years": list(YEARS),
                       "whole_day_max_gap_h": WHOLE_DAY_MAX_GAP_H,
                       "trailing_bias_days": [BIAS_FROM_K, BIAS_TO_K], "trailing_bias_min_n": BIAS_MIN_N,
                       "min_days": MIN_DAYS, "tie_pp": TIE_PP, "flag_err_sd_rise_c": FLAG_ERR_SD_RISE_C,
                       "market_decisions_local": MARKET_DECISIONS, "market_after_min": AFTER_MIN,
                       "bootstrap": {"n": BOOT_N, "block_days": BOOT_BLOCK, "seed": BOOT_SEED}},
        "universe": {"source": os.path.relpath(CITIES, ROOT), "active": sorted(cities)},
        "inputs_sha256": {os.path.relpath(p, ROOT): sha256(p) for p in
                          (CITIES, STATION_DAILY, BM_DAILY, MODELS_DAILY, os.path.join(MH, "events.csv.gz"),
                           os.path.join(MH, "bands.csv.gz"), os.path.join(MH, "prices.csv.gz"))},
        "market_events_left_out": ev_left,
        "focus_10": top,
        "top_10_by_hit_rate_alone": pure_top,
        "ranked": order,
        "not_eligible": sorted(set(metrics) - set(elig)),
        "sensitivity": sens,
        "market_check_all_season": corr_mkt,
        "station_reading_in_venue_winner_pre_cutoff_total": {"agree": sv_tot[0], "n": sv_tot[1]},
        "climate_notes_web_sourced_not_measured": notes,
        "cities": metrics,
    }

    def clean(x):
        if isinstance(x, float):
            return round(x, 4)
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        return x
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(clean(doc), f, indent=1, sort_keys=False)
        f.write("\n")
    print("wrote", os.path.relpath(OUT, ROOT), file=sys.stderr)


if __name__ == "__main__":
    main()
