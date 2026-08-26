"""
AD4 Phase 1 - lead-time-stratified forecast ingest (Open-Meteo Previous Runs).

Why this API and not the plain forecast endpoint: Previous Runs returns what was
predicted at FIXED lead times (1-7 days before valid time). That is exactly what
H4 needs - model skill at D-2 - and it is the only way to train bias correction
without look-ahead. Archived from Jan 2024 for most models.

Each row stores run_at (when the forecast was made) separately from for_date
(what it describes), so point-in-time replay is possible.

CHUNKED: a single request for ~970 days x 8 fields times out server-side every
time. The range is split into windows so each request stays small. More requests,
but they actually complete.

Usage:
  python scripts/ingest_forecasts.py              # last 10 days
  python scripts/ingest_forecasts.py 2024-01-01 2026-08-25   # backfill range
"""
import sys, time, datetime as dt
import requests
from common import get_cities, upsert, log_run, retry

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
LEADS = [1, 2, 3, 4, 5, 6, 7]
MODEL_LABEL = "open_meteo_best_match"   # per-model split is a later step

CHUNK_DAYS = 90        # keeps each response small enough to return in time
TIMEOUT    = 120
PAUSE      = 0.4       # be polite; free tier, non-commercial

def fetch(lat, lon, start, end):
    fields = ["temperature_2m_max"] + [f"temperature_2m_max_previous_day{d}" for d in LEADS]
    p = {
        "latitude": lat, "longitude": lon,
        "daily": ",".join(fields),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "timezone": "UTC", "temperature_unit": "celsius",
    }
    r = requests.get(API, params=p, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def build_rows(city_key, js):
    daily = (js or {}).get("daily") or {}
    dates = daily.get("time") or []
    rows = []
    for i, d in enumerate(dates):
        try:
            for_date = dt.date.fromisoformat(d)
        except ValueError:
            continue
        for lead in LEADS:
            vals = daily.get(f"temperature_2m_max_previous_day{lead}") or []
            if i >= len(vals):
                continue
            v = vals[i]
            if v is None:
                continue
            run_at = dt.datetime.combine(for_date - dt.timedelta(days=lead),
                                         dt.time(0, 0), tzinfo=dt.timezone.utc)
            rows.append({
                "city_key": city_key,
                "model": MODEL_LABEL,
                "run_at": run_at.isoformat(),
                "for_date": for_date.isoformat(),
                "lead_days": lead,
                "forecast_max_c": round(float(v), 2),
                "variables": None,
                "source": "open-meteo-previous-runs",
            })
    return rows

def chunks(start, end, days):
    cur = start
    while cur <= end:
        stop = min(cur + dt.timedelta(days=days - 1), end)
        yield cur, stop
        cur = stop + dt.timedelta(days=1)

def main():
    if len(sys.argv) >= 3:
        start = dt.date.fromisoformat(sys.argv[1])
        end   = dt.date.fromisoformat(sys.argv[2])
    else:
        end   = dt.datetime.now(dt.timezone.utc).date()
        start = end - dt.timedelta(days=10)

    cities = get_cities(require_coords=True)
    windows = list(chunks(start, end, CHUNK_DAYS))
    print(f"cities: {len(cities)}  window: {start} -> {end}")
    print(f"chunks of {CHUNK_DAYS}d: {len(windows)} per city "
          f"({len(cities) * len(windows)} requests total)\n")

    total, failed = 0, []
    for i, c in enumerate(cities, 1):
        got, misses = 0, 0
        for (cs, ce) in windows:
            js = retry(lambda: fetch(c["latitude"], c["longitude"], cs, ce),
                       tries=3, wait=5, label=f"{c['city_key']} {cs}")
            if not js:
                misses += 1
                continue
            rows = build_rows(c["city_key"], js)
            if rows:
                got += upsert("weather_forecasts", rows,
                              "city_key,model,run_at,for_date")
            time.sleep(PAUSE)
        total += got
        flag = f"  ({misses} chunks missed)" if misses else ""
        print(f"  [{i}/{len(cities)}] {c['city_key']:16s} {got:7d} rows{flag}")
        if got == 0:
            failed.append(c["city_key"])

    print(f"\ntotal {total} forecast rows, {len(failed)} cities with nothing")
    if failed:
        print("empty:", ", ".join(failed))
    log_run("ingest_forecasts", "ok" if not failed else "partial", total,
            {"start": str(start), "end": str(end), "leads": LEADS,
             "chunk_days": CHUNK_DAYS, "empty_cities": failed})

if __name__ == "__main__":
    main()
