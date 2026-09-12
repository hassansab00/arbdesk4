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
import sys, time, os, json, datetime as dt
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
SOFT_DEADLINE_MIN = min(20, max(1, int(os.environ.get('FORECAST_DEADLINE_MINUTES', '20'))))

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

def existing_dates(city_key, start, end):
    # A date is complete only when every requested lead is present for this
    # specific source product. Other providers cannot satisfy this backfill.
    from common import rest_all
    rows = rest_all('weather_forecasts', [
        ('select', 'for_date,lead_days'), ('city_key', f'eq.{city_key}'),
        ('model', f'eq.{MODEL_LABEL}'), ('for_date', f'gte.{start.isoformat()}'),
        ('for_date', f'lte.{end.isoformat()}')], order='for_date,lead_days,forecast_id')
    leads = defaultdict(set)
    for row in rows:
        leads[row['for_date']].add(row['lead_days'])
    return {date for date, present in leads.items() if set(LEADS).issubset(present)}


def coverage_count(city_key, start, end):
    return len(existing_dates(city_key, start, end))

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
    if start > end:
        raise ValueError('start must be on or before end')
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

    done_ct = sum(1 for n, _ in ranked if n >= total_days)
    print(f"{done_ct}/{len(ranked)} cities complete ({total_days} days, all requested leads)\n")

    total, ran_out, missing_chunks, completed_dates = 0, False, 0, 0
    for i, (n_before, c) in enumerate(ranked, 1):
        elapsed_min = (time.monotonic() - t0) / 60
        if elapsed_min > SOFT_DEADLINE_MIN:
            print(f"\n! soft deadline at {elapsed_min:.0f} min, stopping before "
                  f"city {i}/{len(ranked)} ({c['city_key']}, had {n_before}d). "
                  f"Re-run the identical command - least-covered cities go first "
                  f"automatically, so progress is never lost or reprocessed.")
            ran_out = True
            break

        if n_before >= total_days:
            print(f"  [{i}/{len(ranked)}] {c['city_key']:16s} already complete ({n_before}d), skipping")
            continue

        have = existing_dates(c["city_key"], start, end)
        got, skipped, misses = 0, 0, 0
        for (cs, ce) in windows:
            if (time.monotonic()-t0)/60 > SOFT_DEADLINE_MIN:
                ran_out = True
                break
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
        missing_chunks += misses
        completed_dates += max(0, coverage_count(c["city_key"], start, end)-n_before)
        note = []
        if skipped: note.append(f"{skipped} chunks pre-existing")
        if misses:  note.append(f"{misses} chunks missed")
        flag = "  (" + ", ".join(note) + ")" if note else ""
        print(f"  [{i}/{len(ranked)}] {c['city_key']:16s} +{got:6d} rows "
              f"(had {n_before}d){flag}", flush=True)

    print(f"\ntotal {total} forecast rows written this run")
    incomplete = ran_out or missing_chunks > 0 or not all_cities
    if incomplete:
        print("INCOMPLETE - re-run the identical command to continue.")
    else:
        print("ALL CITIES COMPLETE.")
    result = {'incomplete': incomplete, 'rows_offered': total, 'missing_chunks': missing_chunks, 'completed_dates': completed_dates}
    result_path = os.environ.get('FORECAST_RESULT_PATH')
    if result_path:
        with open(result_path, 'w') as handle:
            json.dump(result, handle)
    log_run("ingest_forecasts", "partial" if incomplete else "ok", total,
            {"start": str(start), "end": str(end), "leads": LEADS,
             "chunk_days": CHUNK_DAYS, "ran_out": ran_out})

if __name__ == "__main__":
    main()
