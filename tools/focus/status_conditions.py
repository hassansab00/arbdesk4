"""Which forecast conditions make a city's day-ahead call less reliable? This
fixes the "Watch" thresholds of the daily city status (WXPredict build F.2;
Hassan, 6 Oct: "Watch: forecast disagreement, uncertain cloud clearance or
changing winds").

Research only: nothing here prices or trades. Reads committed files only (no
database). Standard library only. The method below was fixed before any number
was computed.

    python3 tools/focus/status_conditions.py            # prints, writes the JSON
    python3 tools/focus/status_conditions.py --no-write # prints only

DATA RULES (enforced in code)
-----------------------------
The focus study's rules and loaders (tools/focus/season_predictability.py):
nothing dated on or after 2026-09-01 (the sealed test) enters a computation,
asserted row by row by its guard(); no blinded challenger is read; the active
cities, stations, units and zones of the 6 Oct 2026 cities mirror.

METHOD (fixed before computing)
-------------------------------
Days: every local day from 14 Jul 2025 (the first forecast) to 31 Aug 2026
with a whole station day at the city's settlement station today, the primary
forecast and a trailing bias (at least 10 whole days in D-31..D-2), in every
active city. The focus study scored only 6 Oct - 30 Nov; this scores all the
days the record has before the cutoff.

Outcome: hit = the bias-corrected best_match lead-1 tmax_00_17_c lands in the
bucket holding the venue's reading of the observed maximum (the focus study's
rule exactly: season_predictability.unit_bucket / obs_bucket / trailing_bias).

Signals, each known by the day-ahead call: lead-1 Previous Runs values for D
and, where a change is measured, for D-1.
  S1 disagreement: max - min of the seven models' lead-1 whole-day tmax_c
     (models_daily.csv.gz; at least 4 models), in C. Whole-day, because that is
     what weather_forecast_models.forecast_max_c holds live (ingest_forecasts
     build_rows takes the maximum over the whole local day).
  S2 cloud uncertainty: 50 - |cloud_09_17_pct - 50| (best_match_daily): 0 at a
     clear or an overcast day, 50 at half cover, where clearance is least
     certain.
  S3 pressure change: |pressure_msl_08_hpa(D) - pressure_msl_08_hpa(D-1)|, in
     hPa. A front's passage, the usual cause of a wind shift; the record has no
     wind direction.
  S4 wind change: |wind_09_17_kmh(D) - wind_09_17_kmh(D-1)|, in km/h.

Rule: each signal's threshold is its 80th percentile over all scored days with
the signal (cities pooled). Watch = signal >= threshold. A signal is ADOPTED
when the hit rate on its Watch days is lower than on its other days and the
98.75% interval (Bonferroni over the 4 signals) of that difference lies wholly
below zero. The interval comes from a date-clustered bootstrap: 1,000
resamples of dates with replacement, every city's day of a drawn date kept
together, seed 11; percentile interval.

Reported for each signal: the threshold, days in and out, both hit rates, the
difference with its interval, adopted or not; the hit rate by quintile; and the
same split for the Seasonal Focus 10 alone (a check, not a rule input). Also
the share of days any adopted signal puts on Watch, and their hit rate.

Output: data/eval/focus/status_conditions_2026-10-06.json (sorted keys). Its
thresholds, under version "status-v1", are what the status view reads; the
migration records the file's sha256.
"""
import argparse
import collections
import datetime as dt
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import season_predictability as sp  # noqa: E402

FIRST = dt.date(2025, 7, 14)
OUT = os.path.join(sp.ROOT, "data", "eval", "focus", "status_conditions_2026-10-06.json")
FOCUS = ["lucknow", "karachi", "helsinki", "wellington", "tel_aviv",
         "milan", "chicago", "moscow", "miami", "amsterdam"]
SIGNALS = ("S1_disagreement_c", "S2_cloud_uncertainty", "S3_pressure_change_hpa", "S4_wind_change_kmh")
QUANTILE = 0.80
LEVEL = 1 - 0.05 / len(SIGNALS)        # 98.75%
BOOT_N, BOOT_SEED = 1000, 11
VERSION = "status-v1"


def load_daily():
    """{(city, date): row} of best_match lead-1, and {(city, date): [tmax_c]}
    of the seven models' lead-1 whole-day maxima; nothing on/after the cutoff."""
    bm = {}
    for r in sp.read_csv(sp.BM_DAILY):
        if r["lead_days"] == "1" and r["for_date"] < sp.CUTOFF.isoformat():
            bm[(r["city_key"], r["for_date"])] = r
    models = collections.defaultdict(list)
    for r in sp.read_csv(sp.MODELS_DAILY):
        if r["lead_days"] == "1" and r["for_date"] < sp.CUTOFF.isoformat():
            v = sp.num(r["tmax_c"])
            if v is not None:
                models[(r["city_key"], r["for_date"])].append(v)
    return bm, models


def signals(bm, models, city, d):
    k, p = d.isoformat(), (d - dt.timedelta(days=1)).isoformat()
    row, prev = bm.get((city, k)), bm.get((city, p))
    out = {}
    ms = models.get((city, k)) or []
    if len(ms) >= 4:
        out["S1_disagreement_c"] = max(ms) - min(ms)
    cloud = sp.num(row["cloud_09_17_pct"]) if row else None
    if cloud is not None:
        out["S2_cloud_uncertainty"] = 50 - abs(cloud - 50)
    if row and prev:
        a, b = sp.num(row["pressure_msl_08_hpa"]), sp.num(prev["pressure_msl_08_hpa"])
        if a is not None and b is not None:
            out["S3_pressure_change_hpa"] = abs(a - b)
        a, b = sp.num(row["wind_09_17_kmh"]), sp.num(prev["wind_09_17_kmh"])
        if a is not None and b is not None:
            out["S4_wind_change_kmh"] = abs(a - b)
    return out


def scored_days(cities, whole, fc, bm, models):
    """[(city, date, hit, {signal: value})] over every scorable pre-cutoff day."""
    out = []
    for city, c in sorted(cities.items()):
        unit = (c.get("unit") or "C").upper()
        days = whole.get(city, {})
        d = FIRST
        while d < sp.CUTOFF:
            k = d.isoformat()
            f, o = fc.get((city, k)), days.get(k)
            if f is not None and o is not None:
                sp.guard(k, "station days")
                sp.guard(k, "forecasts")
                b, _ = sp.trailing_bias(fc, days, city, d)
                if b is not None:
                    hit = int(sp.unit_bucket(f - b, unit) == sp.obs_bucket(o, unit))
                    if (city, (d - dt.timedelta(days=1)).isoformat()) in bm:
                        sp.guard((d - dt.timedelta(days=1)).isoformat(), "forecasts (D-1 signals)")
                    out.append((city, d, hit, signals(bm, models, city, d)))
            d += dt.timedelta(days=1)
    return out


def quantile(xs, q):
    s = sorted(xs)
    i = q * (len(s) - 1)
    lo = int(i)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def rate(rows):
    return sum(h for h, _ in rows) / len(rows) if rows else None


def diff_by_date(rows_by_date, dates):
    """hit rate on Watch days minus on the other days, over the drawn dates."""
    hw = nw = ho = no = 0
    for d in dates:
        for hit, watch in rows_by_date[d]:
            if watch:
                hw += hit
                nw += 1
            else:
                ho += hit
                no += 1
    if not nw or not no:
        return None
    return hw / nw - ho / no


def judge(days, name, threshold, rng):
    rows = [(h, s[name] >= threshold, d) for _, d, h, s in days if name in s]
    watch = [(h, w) for h, w, _ in rows if w]
    other = [(h, w) for h, w, _ in rows if not w]
    by_date = collections.defaultdict(list)
    for h, w, d in rows:
        by_date[d].append((h, w))
    dates = sorted(by_date)
    point = diff_by_date(by_date, dates)
    boots = []
    for _ in range(BOOT_N):
        v = diff_by_date(by_date, [dates[rng.randrange(len(dates))] for _ in dates])
        if v is not None:
            boots.append(v)
    boots.sort()
    a = (1 - LEVEL) / 2
    lo, hi = quantile(boots, a), quantile(boots, 1 - a)
    hw, ho = rate(watch), rate(other)
    return {
        "threshold": round(threshold, 3),
        "days_watch": len(watch), "days_other": len(other),
        "hit_rate_watch": round(hw, 4), "hit_rate_other": round(ho, 4),
        "difference": round(point, 4),
        "interval": [round(lo, 4), round(hi, 4)], "interval_level": LEVEL,
        "adopted": bool(hw < ho and hi < 0),
    }


def by_quintile(days, name):
    vals = [s[name] for _, _, _, s in days if name in s]
    edges = [quantile(vals, q) for q in (0.2, 0.4, 0.6, 0.8)]
    bins = collections.defaultdict(list)
    for _, _, h, s in days:
        if name in s:
            i = sum(s[name] >= e for e in edges)
            bins[i].append(h)
    return {"edges": [round(e, 3) for e in edges],
            "hit_rate": [round(sum(bins[i]) / len(bins[i]), 4) if bins[i] else None for i in range(5)],
            "days": [len(bins[i]) for i in range(5)]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    cities = sp.load_cities()
    whole, _, _ = sp.load_station_days(cities)
    fcs, _ = sp.load_forecasts()
    fc = fcs["primary_l1_00_17"]
    bm, models = load_daily()
    days = scored_days(cities, whole, fc, bm, models)
    rng = random.Random(BOOT_SEED)
    out = {"version": VERSION, "cutoff": sp.CUTOFF.isoformat(), "first_day": FIRST.isoformat(),
           "days_scored": len(days), "cities": len({c for c, _, _, _ in days}),
           "hit_rate_all": round(sum(h for _, _, h, _ in days) / len(days), 4),
           "last_day_used": max(d for _, d, _, _ in days).isoformat(),
           "quantile": QUANTILE, "bootstrap": {"n": BOOT_N, "seed": BOOT_SEED, "clustered_by": "date"},
           "signals": {}}
    for name in SIGNALS:
        vals = [s[name] for _, _, _, s in days if name in s]
        thr = quantile(vals, QUANTILE)
        res = judge(days, name, thr, rng)
        res["days_with_signal"] = len(vals)
        res["by_quintile"] = by_quintile(days, name)
        focus_days = [x for x in days if x[0] in FOCUS]
        res["focus10"] = {k: v for k, v in judge(focus_days, name, thr, random.Random(BOOT_SEED)).items()
                          if k in ("days_watch", "days_other", "hit_rate_watch", "hit_rate_other",
                                   "difference", "interval")}
        out["signals"][name] = res
    adopted = [n for n in SIGNALS if out["signals"][n]["adopted"]]
    out["adopted"] = adopted
    if adopted:
        thr = {n: out["signals"][n]["threshold"] for n in adopted}
        any_watch = [(h, any(n in s and s[n] >= thr[n] for n in adopted)) for _, _, h, s in days]
        w = [h for h, x in any_watch if x]
        o = [h for h, x in any_watch if not x]
        out["combined"] = {"share_watch": round(len(w) / len(any_watch), 4),
                           "hit_rate_watch": round(sum(w) / len(w), 4) if w else None,
                           "hit_rate_other": round(sum(o) / len(o), 4) if o else None}
    assert out["last_day_used"] < sp.CUTOFF.isoformat()
    text = json.dumps(out, indent=1, sort_keys=True)
    print(text)
    if not a.no_write:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
