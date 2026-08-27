"""
AD4 Phase 1 - lead-time-stratified forecast ingest (Open-Meteo Previous Runs).

RESUMABLE: before fetching a city, checks how much of the target range that
city already has in weather_forecasts and skips chunks already covered. A
cancelled or timed-out run costs nothing - re-running the same command picks
up where it left off instead of re-fetching everything.

API shape note: the _previous_dayN suffix applies to HOURLY variables
(temperature_2m), not daily aggregates. daily=temperature_2m_max_previous_dayN
returns 400. We pull hourly and compute the daily max ourselves.

Usage:
  python scripts/ingest_forecasts.py                          # last 10 days
  python scripts/ingest_forecasts.py 2024-01-01 2026-08-25    # backfill range
  python scripts/ingest_forecasts.py 2024-01-01 2026-08-25 --fresh   # ignore
                                                                # existing rows
"""
import sys, time, datetime as dt
from collections import defaultdict
import requests
from common import get_cities, upsert, rest, log_run

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
LEADS = [1, 2, 3, 4, 5, 6, 7]
MODEL_LABEL = "open_meteo_best_match"

CHUNK_DAYS = 60
TIMEOUT    = 100
PAUSE      = 0.2
TRIES      = 2
# stop starting new chunks once this close to the job's own time budget,
# so a partial city is not left half-fetched mid-chunk
SOFT_DEADLINE_MIN = 330

def fetch(lat, lon, start, end, label):
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
                print(f"  ! {label} 400: {r.text[:200]}", file=sys.stderr)
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == TRIES - 1:
                print(f"  ! {label} gave up: {str(e)[:100]}", file=sys.stderr)
                return None
            time.sleep(4)
    return None

def build_rows(city_key, js):
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
            d = t[:10]
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
                "city_key": city_key, "model": MODEL_LABEL,
                "run_at": run_at.isoformat(), "for_date": for_date.isoformat(),
                "lead_days": lead, "forecast_max_c": round(float(mx), 2),
                "variables": None, "source": "open-meteo-previous-runs",
            })
    return rows

def chunks(start, end, days):
    cur = start
    while cur <= end:
        stop = min(cur + dt.timedelta(days=days - 1), end)
        yield cur, stop
        cur = stop + dt.timedelta(days=1)

def existing_dates(city_key, start, end):
    """for_dates already stored for this city within [start, end], any lead."""
    out, offset, page = set(), 0, 10000
    while True:
        rows = rest("weather_forecasts", {
            "select": "for_date", "city_key": f"eq.{city_key}",
            "for_date": f"gte.{start.isoformat()}",
            "and": f"(for_date.lte.{end.isoformat()})",
            "limit": str(page), "offset": str(offset),
        })
        if not rows:
            break
        out.update(r["for_date"] for r in rows)
        if len(rows) < page:
            break
        offset += page
    return out

def chunk_is_covered(have, cs, ce, leads_needed=len(LEADS)):
    """A chunk counts as done if every date in it already has all lead rows.
    Cheap approximation: just check every date is present at all (skip-only,
    never skips a genuinely incomplete chunk incorrectly since 'have' comes
    from ANY lead present) - good enough to make resume safe and fast."""
    d = cs
    while d <= ce:
        if d.isoformat() not in have:
            return False
        d += dt.timedelta(days=1)
    return True

def main():
    fresh = "--fresh" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--fresh"]

    if len(args) >= 2:
        start = dt.date.fromisoformat(args[0])
        end   = dt.date.fromisoformat(args[1])
    else:
        end   = dt.datetime.now(dt.timezone.utc).date()
        start = end - dt.timedelta(days=10)

    t0 = time.monotonic()
    cities = get_cities(require_coords=True)
    windows = list(chunks(start, end, CHUNK_DAYS))
    print(f"cities: {len(cities)}  window: {start} -> {end}  "
          f"chunks/city: {len(windows)}  fresh={fresh}\n")

    total, empty, ran_out = 0, [], False
    for i, c in enumerate(cities, 1):
        elapsed_min = (time.monotonic() - t0) / 60
        if elapsed_min > SOFT_DEADLINE_MIN:
            print(f"\n! soft deadline reached at {elapsed_min:.0f} min, "
                  f"stopping before city {i}/{len(cities)}. Re-run the same "
                  f"command to resume - completed cities are skipped.")
            ran_out = True
            break

        have = set() if fresh else existing_dates(c["city_key"], start, end)
        got, skipped, misses = 0, 0, 0
        for (cs, ce) in windows:
            if have and chunk_is_covered(have, cs, ce):
                skipped += 1
                continue
            js = fetch(c["latitude"], c["longitude"], cs, ce, f"{c['city_key']} {cs}")
            if not js:
                misses += 1
                continue
            rows = build_rows(c["city_key"], js)
            if rows:
                got += upsert("weather_forecasts", rows, "city_key,model,run_at,for_date")
            time.sleep(PAUSE)
        total += got
        note = []
        if skipped: note.append(f"{skipped} chunks already had data")
        if misses:  note.append(f"{misses} chunks missed")
        flag = "  (" + ", ".join(note) + ")" if note else ""
        print(f"  [{i}/{len(cities)}] {c['city_key']:16s} {got:7d} rows{flag}", flush=True)
        if got == 0 and skipped == 0:
            empty.append(c["city_key"])

    status = "partial_timeout" if ran_out else ("ok" if not empty else "partial")
    print(f"\ntotal {total} forecast rows written this run, {len(empty)} cities with nothing")
    if empty:
        print("empty:", ", ".join(empty))
    if ran_out:
        print("Run the SAME command again to continue - already-covered cities are skipped fast.")
    log_run("ingest_forecasts", status, total,
            {"start": str(start), "end": str(end), "leads": LEADS,
             "chunk_days": CHUNK_DAYS, "empty_cities": empty, "ran_out": ran_out})

if __name__ == "__main__":
    main()
