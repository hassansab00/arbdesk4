"""
AD4 Phase 1 - lead-time-stratified forecast ingest (Open-Meteo Previous Runs).

Why this API: Previous Runs returns what was predicted at FIXED lead times
(1-7 days before valid time). That is what H4 needs - model skill at D-2 - and
it is the only way to train bias correction without look-ahead. Most models
archived from January 2024.

IMPORTANT - API shape:
  The _previous_dayN suffix applies to HOURLY variables (temperature_2m), NOT to
  daily aggregates. Requesting daily=temperature_2m_max_previous_day1 returns
  400 Bad Request. So we pull the hourly series per lead time and compute the
  daily maximum ourselves.

  Side benefit: the hourly curve is what the heating-curve-shape work needs
  later, so nothing is lost by doing it this way.

Day definition:
  Both this job and measure_skill.py bucket by UTC date. Markets resolve on the
  station's LOCAL day, so this introduces some noise for cities far from UTC.
  It is consistent between forecast and observation, so MAE remains valid.
  Revisit if per-city local-day bucketing is needed.

Usage:
  python scripts/ingest_forecasts.py              # last 10 days
  python scripts/ingest_forecasts.py 2024-01-01 2026-08-25   # backfill range
"""
import sys, time, datetime as dt
from collections import defaultdict
import requests
from common import get_cities, upsert, log_run

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
LEADS = [1, 2, 3, 4, 5, 6, 7]
MODEL_LABEL = "open_meteo_best_match"

CHUNK_DAYS = 60
TIMEOUT    = 120
PAUSE      = 0.3
TRIES      = 3

def fetch(lat, lon, start, end, label):
    """Hourly temperature at each lead-time offset. Prints the API's own reason on 4xx."""
    fields = ["temperature_2m"] + [f"temperature_2m_previous_day{d}" for d in LEADS]
    p = {
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(fields),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "timezone": "UTC", "temperature_unit": "celsius",
    }
    for attempt in range(TRIES):
        try:
            r = requests.get(API, params=p, timeout=TIMEOUT)
            if r.status_code == 400:
                # the API returns {"error":true,"reason":"..."} - show it, do not retry
                print(f"  ! {label} 400: {r.text[:200]}", file=sys.stderr)
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == TRIES - 1:
                print(f"  ! {label} gave up: {str(e)[:120]}", file=sys.stderr)
                return None
            time.sleep(5 * (attempt + 1))
    return None

def build_rows(city_key, js):
    """Collapse the hourly series into one daily max per (for_date, lead)."""
    hourly = (js or {}).get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return []

    rows = []
    for lead in LEADS:
        vals = hourly.get(f"temperature_2m_previous_day{lead}")
        if not vals:
            continue
        daily = defaultdict(lambda: None)
        for i, t in enumerate(times):
            if i >= len(vals):
                break
            v = vals[i]
            if v is None:
                continue
            d = t[:10]                      # "2024-01-01T13:00" -> "2024-01-01"
            cur = daily[d]
            if cur is None or v > cur:
                daily[d] = v

        for d, mx in daily.items():
            if mx is None:
                continue
            try:
                for_date = dt.date.fromisoformat(d)
            except ValueError:
                continue
            run_at = dt.datetime.combine(for_date - dt.timedelta(days=lead),
                                         dt.time(0, 0), tzinfo=dt.timezone.utc)
            rows.append({
                "city_key": city_key,
                "model": MODEL_LABEL,
                "run_at": run_at.isoformat(),
                "for_date": for_date.isoformat(),
                "lead_days": lead,
                "forecast_max_c": round(float(mx), 2),
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
          f"({len(cities) * len(windows)} requests)\n")

    total, empty = 0, []
    for i, c in enumerate(cities, 1):
        got, misses = 0, 0
        for (cs, ce) in windows:
            js = fetch(c["latitude"], c["longitude"], cs, ce, f"{c['city_key']} {cs}")
            if not js:
                misses += 1
                continue
            rows = build_rows(c["city_key"], js)
            if rows:
                got += upsert("weather_forecasts", rows, "city_key,model,run_at,for_date")
            time.sleep(PAUSE)
        total += got
        flag = f"  ({misses}/{len(windows)} chunks missed)" if misses else ""
        print(f"  [{i}/{len(cities)}] {c['city_key']:16s} {got:7d} rows{flag}", flush=True)
        if got == 0:
            empty.append(c["city_key"])

    print(f"\ntotal {total} forecast rows, {len(empty)} cities with nothing")
    if empty:
        print("empty:", ", ".join(empty))
    log_run("ingest_forecasts", "ok" if not empty else "partial", total,
            {"start": str(start), "end": str(end), "leads": LEADS,
             "chunk_days": CHUNK_DAYS, "empty_cities": empty})

if __name__ == "__main__":
    main()
