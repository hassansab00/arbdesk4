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

# EVERY MODEL, NOT ONE BLEND (plan v2.1 P2.8). best_match is Open-Meteo's own
# pick of a model per location, so on 23 Sep weather_forecasts held one
# forecast series per city (plus NWS for 12) and nothing could learn which
# model a city should trust. These come in ONE extra request per city - the
# API suffixes each variable with the model name - and go to their OWN table,
# weather_forecast_models, under "open_meteo_<model>". Kept apart from
# best_match twice over: a model the API refuses must not cost the series
# every consumer reads, and a per-model row in weather_forecasts would tie
# with best_match on its synthetic run_at in readers that pick "the newest
# row, whatever the model" (probability_engine._forecast_for does).
# FORECAST_MODELS="" switches them off; a comma list replaces the default.
DEFAULT_MODELS = ("ecmwf_ifs025,gfs_seamless,icon_seamless,ukmo_seamless,"
                  "jma_seamless,gem_seamless,meteofrance_seamless")
MODELS = [m for m in os.environ.get("FORECAST_MODELS", DEFAULT_MODELS).split(",") if m.strip()]

CHUNK_DAYS = 60
# 30 s, not 100. Measured on the 23 Sep 08:31Z run: a city that answered took
# about 2.3 s, and five that did not each spent 2 x 100 s + 4 s timing out -
# about 17 of the run's 22 minutes, billed, for nothing. A read that has not
# answered in 30 s is left to the next run, which asks least-covered first.
TIMEOUT    = 30
PAUSE      = 0.2
TRIES      = 2
SOFT_DEADLINE_MIN = min(20, max(1, int(os.environ.get('FORECAST_DEADLINE_MINUTES', '20'))))

def fetch(lat, lon, start, end, label, models=None):
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
    if models:
        p["models"] = ",".join(models)
    # TWO WAYS TO COME BACK EMPTY, AND THEY ARE NOT THE SAME THING.
    #
    # This used to return None for both, and the caller counted both as a
    # "missing chunk" - which the job then reported as "the gap is real and
    # will not close by retrying blindly". For a read timeout that sentence is
    # simply false: it is precisely what closes by retrying. Measured on the
    # 20 Sep run, 47 of 49 cities got all 77 rows for the same date window and
    # two timed out, so the source was answering fine; the workflow went red
    # anyway, and had done every day since 16 Sep.
    #
    # REFUSED is the source answering and declining - a 400 carrying its own
    # explanation of why that window cannot be served. Asking again gets the
    # same 400, so that is a gap worth failing on.
    #
    # UNREACHED is a request that never completed: a read timeout, a reset, a
    # 5xx. Nothing was learned about the data, so nothing is proven about a
    # gap. The chunk stays uncovered, the next run sorts least-covered cities
    # first and asks again, and a source that is genuinely down still fails
    # the job through "zero dates completed" rather than through this count.
    for attempt in range(TRIES):
        try:
            r = requests.get(API, params=p, timeout=TIMEOUT)
            if r.status_code == 400:
                print(f"  ! {label} 400: {r.text[:200]}", file=sys.stderr)
                return None, "refused"
            r.raise_for_status()
            return r.json(), "ok"
        except Exception as e:
            if attempt == TRIES - 1:
                print(f"  ! {label} unreached: {str(e)[:100]}", file=sys.stderr)
                return None, "unreached"
            time.sleep(4)
    return None, "unreached"

def build_rows(city_key, js, model=None):
    """One row per (local date, lead). With `model`, read that model's columns:
    a multi-model response names them temperature_2m_previous_day<N>_<model>."""
    hourly = (js or {}).get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return []
    suffix = f"_{model}" if model else ""
    label = f"open_meteo_{model}" if model else MODEL_LABEL
    rows = []
    for lead in LEADS:
        vals = hourly.get(f"temperature_2m_previous_day{lead}{suffix}")
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
                "city_key": city_key, "model": label,
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
        # THE CATCH-UP WINDOW, AND WHY IT IS NOT TEN DAYS.
        #
        # A chunk that times out is reported as `unreached` and the night's
        # run logs 'partial'. That part works - the continuation picks the
        # city up 45 seconds later, and the 2026-09-11..09-21 window has no
        # gap at all. What did NOT work is the case where a city loses its
        # whole window and the window then rolls past it: measured on the
        # live table, 13 of the 48 active cities are missing 278 city-days
        # (1,946 rows, every one of them all seven leads) between 2026-06-23
        # and 2026-09-03, and four nightly runs after the hole opened
        # reported status 'ok' having written 0 rows, because a ten-day
        # window can no longer see an eleven-day-old gap.
        #
        # The cost is not cosmetic: at lead 1 those 13 cities carry 18-24
        # scored days against 31 for the rest of the board, so their sigma is
        # fitted from a quarter to a third less evidence with nothing saying
        # so, and qingdao, ankara and paris are within three days of
        # measure_skill's MIN_SAMPLE at lead 7, below which a band loses its
        # price basis entirely.
        #
        # 35 days is still ONE request per city - CHUNK_DAYS is 60 - so the
        # cost of the wider window is zero on a night with nothing to catch
        # up, and a hole now has five weeks to be noticed instead of ten
        # days. upsert() ignores duplicates, so the days already present are
        # offered and dropped rather than rewritten.
        start = end - dt.timedelta(
            days=int(os.environ.get("FORECAST_CATCHUP_DAYS", "35")))

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

    total, ran_out, missing_chunks, unreached_chunks, completed_dates = 0, False, 0, 0, 0
    # The per-model request is reported, never raised: it is additive, and a
    # refusal there must not stop best_match or start a paid continuation.
    model_rows, model_failed, model_empty = defaultdict(int), defaultdict(int), set()
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
        got, skipped, refused, unreached = 0, 0, 0, 0
        for (cs, ce) in windows:
            if (time.monotonic()-t0)/60 > SOFT_DEADLINE_MIN:
                ran_out = True
                break
            if have and chunk_is_covered(have, cs, ce):
                skipped += 1
                continue
            js, outcome = fetch(c["latitude"], c["longitude"], cs, ce, f"{c['city_key']} {cs}")
            if outcome == "refused":
                refused += 1
                continue
            if outcome != "ok":
                unreached += 1
                continue
            rows = build_rows(c["city_key"], js)
            if rows:
                got += upsert("weather_forecasts", rows, "city_key,model,run_at,for_date")
            time.sleep(PAUSE)
            if MODELS:
                mjs, mout = fetch(c["latitude"], c["longitude"], cs, ce,
                                  f"{c['city_key']} {cs} models", models=MODELS)
                if mout != "ok":
                    model_failed[mout] += 1
                else:
                    for model in MODELS:
                        mrows = [{k: v for k, v in r.items() if k != "variables"}
                                 for r in build_rows(c["city_key"], mjs, model)]
                        if mrows:
                            n = upsert("weather_forecast_models", mrows,
                                       "city_key,model,run_at,for_date")
                            model_rows[model] += n
                            got += n
                        else:
                            model_empty.add(model)
                time.sleep(PAUSE)
        total += got
        missing_chunks += refused
        unreached_chunks += unreached
        completed_dates += max(0, coverage_count(c["city_key"], start, end)-n_before)
        note = []
        if skipped:  note.append(f"{skipped} chunks pre-existing")
        if refused:  note.append(f"{refused} chunks refused")
        if unreached: note.append(f"{unreached} chunks unreached")
        flag = "  (" + ", ".join(note) + ")" if note else ""
        print(f"  [{i}/{len(ranked)}] {c['city_key']:16s} +{got:6d} rows "
              f"(had {n_before}d){flag}", flush=True)

    print(f"\ntotal {total} forecast rows written this run")
    if MODELS:
        print("per model: " + ", ".join(f"{m} {model_rows[m]}" for m in MODELS)
              + (f"; model requests refused {model_failed['refused']}, unreached "
                 f"{model_failed['unreached']}" if model_failed else "")
              + (f"; no rows for {sorted(model_empty)}" if model_empty else ""))
    incomplete = ran_out or missing_chunks > 0 or unreached_chunks > 0 or not all_cities
    if incomplete:
        print("INCOMPLETE - re-run the identical command to continue.")
    else:
        print("ALL CITIES COMPLETE.")
    # missing_chunks is refusals ONLY. An unreached chunk leaves the run
    # incomplete so it is asked for again, but it is not evidence of a gap.
    result = {'incomplete': incomplete, 'rows_offered': total, 'missing_chunks': missing_chunks,
              'unreached_chunks': unreached_chunks, 'completed_dates': completed_dates}
    result_path = os.environ.get('FORECAST_RESULT_PATH')
    if result_path:
        with open(result_path, 'w') as handle:
            json.dump(result, handle)
    log_run("ingest_forecasts", "partial" if incomplete else "ok", total,
            {"start": str(start), "end": str(end), "leads": LEADS,
             "chunk_days": CHUNK_DAYS, "ran_out": ran_out,
             "refused_chunks": missing_chunks, "unreached_chunks": unreached_chunks,
             "models": MODELS, "model_rows": dict(model_rows),
             "model_requests_failed": dict(model_failed),
             "models_without_rows": sorted(model_empty)})

if __name__ == "__main__":
    main()
