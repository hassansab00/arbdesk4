"""
AD4 Phase 1 - lead-time-stratified forecast ingest (Open-Meteo Previous Runs).

SELF-CONTINUING: processes cities with the LEAST existing coverage first, so a
run that stops partway through always makes forward progress on the cities that
need it most - it can never get stuck reprocessing complete cities while
incomplete ones wait. Combined with the workflow's on-timeout re-trigger, this
requires zero manual re-runs to reach full coverage.

API shape: the _previous_dayN suffix applies to HOURLY variables
(temperature_2m), not daily aggregates. We pull hourly and compute the daily
max ourselves.

Usage:
  python scripts/ingest_forecasts.py                          # last 10 days
  python scripts/ingest_forecasts.py 2024-01-01 2026-08-27    # backfill range
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
SOFT_DEADLINE_MIN = 330

def fetch(lat, lon, start, end, label):
    fields = ["temperature_2m"] + [f"temperature_2m_previous_day{d}" for d in LEADS]
    p = {
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(fields),
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        # LOCAL DAYS, not UTC ones. A daily maximum is a local-calendar
        # quantity - it is the thing the market settles on - and every other
        # writer of weather_forecasts (n8n P1.3 and P1.5) groups by the city's
        # own day. Asking for UTC here put a different quantity in the same
        # column: for New York the "UTC day" runs from 20:00 the previous
        # local evening to 19:59, so a warm evening ahead of a cold front
        # became the next day's forecast maximum. Small, systematic, warm, and
        # invisible - and derived_forecast_skill is measured from these rows,
        # so it fed straight into every sigma and every band probability.
        "timezone": "auto", "temperature_unit": "celsius",
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
            # timezone=auto means these timestamps are already the city's
            # local time, so slicing the date off gives the local calendar day.
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
    out = []
    while cur <= end:
        stop = min(cur + dt.timedelta(days=days - 1), end)
        out.append((cur, stop))
        cur = stop + dt.timedelta(days=1)
    return out

def coverage_count(city_key, start, end):
    """How many distinct for_dates this city already has for lead_days=1
    within [start, end]. Used only to ORDER cities, not to skip chunks -
    keeps the logic simple and correct rather than cleverly wrong."""
    rows, offset, page = [], 0, 10000
    while True:
        r = rest("weather_forecasts", {
            "select": "for_date", "city_key": f"eq.{city_key}", "lead_days": "eq.1",
            "for_date": f"gte.{start.isoformat()}",
            "and": f"(for_date.lte.{end.isoformat()})",
            "limit": str(page), "offset": str(offset),
        })
        if not r:
            break
        rows.extend(x["for_date"] for x in r)
        if len(r) < page:
            break
        offset += page
    return len(set(rows))

def existing_dates(city_key, start, end):
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

def chunk_is_covered(have, cs, ce):
    d = cs
    while d <= ce:
        if d.isoformat() not in have:
            return False
        d += dt.timedelta(days=1)
    return True

def main():
    if len(sys.argv) >= 3:
        start = dt.date.fromisoformat(sys.argv[1])
        end   = dt.date.fromisoformat(sys.argv[2])
    else:
        end   = dt.datetime.now(dt.timezone.utc).date()
        start = end - dt.timedelta(days=10)

    t0 = time.monotonic()
    all_cities = get_cities(require_coords=True)
    windows = chunks(start, end, CHUNK_DAYS)
    total_days = (end - start).days + 1

    print(f"cities: {len(all_cities)}  window: {start} -> {end}  chunks/city: {len(windows)}")
    print("ranking cities by existing coverage (least first)...", flush=True)

    ranked = []
    for c in all_cities:
        n = coverage_count(c["city_key"], start, end)
        ranked.append((n, c))
    ranked.sort(key=lambda x: x[0])   # LEAST covered first - always makes progress where it matters

    done_ct = sum(1 for n, _ in ranked if n >= total_days - 5)
    print(f"{done_ct}/{len(ranked)} cities already essentially complete (>= {total_days-5} days)\n")

    total, ran_out = 0, False
    for i, (n_before, c) in enumerate(ranked, 1):
        elapsed_min = (time.monotonic() - t0) / 60
        if elapsed_min > SOFT_DEADLINE_MIN:
            print(f"\n! soft deadline at {elapsed_min:.0f} min, stopping before "
                  f"city {i}/{len(ranked)} ({c['city_key']}, had {n_before}d). "
                  f"Re-run the identical command - least-covered cities go first "
                  f"automatically, so progress is never lost or reprocessed.")
            ran_out = True
            break

        if n_before >= total_days - 5:
            print(f"  [{i}/{len(ranked)}] {c['city_key']:16s} already complete ({n_before}d), skipping")
            continue

        have = existing_dates(c["city_key"], start, end)
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
        if skipped: note.append(f"{skipped} chunks pre-existing")
        if misses:  note.append(f"{misses} chunks missed")
        flag = "  (" + ", ".join(note) + ")" if note else ""
        print(f"  [{i}/{len(ranked)}] {c['city_key']:16s} +{got:6d} rows "
              f"(had {n_before}d){flag}", flush=True)

    print(f"\ntotal {total} forecast rows written this run")
    if ran_out:
        print("INCOMPLETE - re-run the identical command to continue.")
    else:
        print("ALL CITIES COMPLETE.")
    log_run("ingest_forecasts", "partial" if ran_out else "ok", total,
            {"start": str(start), "end": str(end), "leads": LEADS,
             "chunk_days": CHUNK_DAYS, "ran_out": ran_out})

if __name__ == "__main__":
    main()
