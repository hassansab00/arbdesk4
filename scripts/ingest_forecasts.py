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
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import requests
from common import get_cities, upsert, rest, log_run, city_local_date
import weather_history

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
TIMEOUT    = 20
PAUSE      = 0.2
TRIES      = 2
RETRY_WAIT = 2
# NOT ONE CITY AT A TIME (27 Sep). The 03:36Z run asked 36 cities in 21
# minutes and hit its deadline at city 37 of 48: a city that answered took
# about 4 s for its two requests, but 13 of the 36 hung and each hang cost
# 2 x 30 s + 4 s, one after another - about 14 of the 21 minutes - and the
# 44 current-run requests after the loop never started. Three continuation
# runs and pipeline_daily's own ingest step then repeated the job: 67 + 18
# billed minutes a night. A few cities at once overlap the hangs; a hung city
# is asked once more at the end of the same run, not in a new 23-minute job.
# Four requests in flight is far under Open-Meteo's free per-minute limit.
WORKERS    = max(1, int(os.environ.get("FORECAST_WORKERS", "4")))
SOFT_DEADLINE_MIN = min(20, max(1, int(os.environ.get('FORECAST_DEADLINE_MINUTES', '20'))))
# Whose current run to fetch: unset = every city, a comma list (empty = none)
# = only those. scripts/forecast_nightly.py sets it on a continuation pass to
# the cities its first pass missed, so a second pass does not fetch 48 current
# runs again (about 3.6 minutes, 28 Sep) to fill a few archive chunks (4 Oct).
_CURRENT_ONLY = os.environ.get("FORECAST_CURRENT_CITIES")
CURRENT_CITIES = None if _CURRENT_ONLY is None else {c for c in _CURRENT_ONLY.split(",") if c.strip()}
# CURRENT RUNS ONLY (5 Oct, audit P3). A missed current run is not part of
# `incomplete` (see the end of main), so scripts/forecast_nightly.py asked for
# it again only while the archive still needed a pass. Misses are common: 10
# of the 13 passes of 1-5 Oct that asked for every city's current run missed
# 1-4 cities (ingest_log), and 3 of those (1, 3, 4 Oct, 04:4x) had a complete
# archive. Those cities still had that night's 03:4x run from the old chain;
# from 4 Oct nothing else fetches one, so a miss leaves a city on the night
# before's. The wrapper now retries them on their own: "0" skips the catch-up
# window, its coverage reads and its verdict, and asks only for the current
# runs FORECAST_CURRENT_CITIES names.
ARCHIVE = os.environ.get("FORECAST_ARCHIVE", "1") != "0"

def fetch(lat, lon, start, end, label, models=None):
    fields = ["temperature_2m"] + [f"temperature_2m_previous_day{d}" for d in LEADS]
    p = {
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(fields),
        # A DAY EITHER SIDE, IN UTC: every local day of [start, end] is whole
        # whatever the city's offset; build_rows keeps only those days.
        "start_date": (start - dt.timedelta(days=1)).isoformat(),
        "end_date": (end + dt.timedelta(days=1)).isoformat(),
        # LOCAL DAYS, not UTC ones. A daily maximum is a local-calendar
        # quantity - it is the thing the market settles on - and every other
        # writer of weather_forecasts (n8n P1.3 and P1.5) groups by the city's
        # own day. Asking for UTC here put a different quantity in the same
        # column: for New York the "UTC day" runs from 20:00 the previous
        # local evening to 19:59, so a warm evening ahead of a cold front
        # became the next day's forecast maximum. Small, systematic, warm, and
        # invisible - and derived_forecast_skill is measured from these rows,
        # so it fed straight into every sigma and every band probability.
        #
        # AND UTC, NOT timezone=auto (26 Sep). Asked with timezone=auto,
        # Open-Meteo returns the whole range at ONE fixed offset - the city's
        # offset at the moment of the request: 48 cities x 439 days came back
        # with no daylight-saving step anywhere, Wellington at +13 for July. A
        # window fetched just after a clock change put the days before it an
        # hour off, and a backfill run in one season shifted every day of the
        # other. build_rows turns UTC hours into the city's wall-clock day with
        # common.city_local_date, the one function every job uses for that.
        "timezone": "GMT", "temperature_unit": "celsius",
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
            time.sleep(RETRY_WAIT)
    return None, "unreached"

def build_rows(city_key, js, model=None, tz=None, first=None, last=None):
    """One row per (local date, lead). With `model`, read that model's columns:
    a multi-model response names them temperature_2m_previous_day<N>_<model>.
    The response's hours are UTC; `tz` is the city's zone, and only the local
    days from `first` to `last` are kept (the request reaches a day further on
    each side, so the days at its edges are partial)."""
    tz = tz or "UTC"
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
            d = city_local_date(t, tz)
            if (first and d < str(first)) or (last and d > str(last)):
                continue
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

CURRENT_API = "https://api.open-meteo.com/v1/forecast"
CURRENT_SOURCE = "open-meteo-models-current"


def fetch_current(lat, lon, label):
    """Each model's CURRENT run for the next three local days (plan v2.1 P3.8).

    The previous-runs values above are stamped with their ingest time, and the
    job only asks for dates up to today, so a lead-1 value reaches the table
    on its own target day - after the evening-before checkpoint the hit
    tournament grades at. This asks for the days AHEAD, now: a row whose
    observed_at is the fetch time is a forecast provably known by then.
    """
    # UTC hours from yesterday to three days ahead: the city's today and next
    # two days are whole at any offset (see fetch for why not timezone=auto).
    p = {"latitude": lat, "longitude": lon, "hourly": "temperature_2m",
         "models": ",".join(MODELS), "past_days": 1, "forecast_days": 4,
         "timezone": "GMT", "temperature_unit": "celsius"}
    for attempt in range(TRIES):
        try:
            r = requests.get(CURRENT_API, params=p, timeout=TIMEOUT)
            if r.status_code == 400:
                print(f"  ! {label} 400: {r.text[:200]}", file=sys.stderr)
                return None, "refused"
            r.raise_for_status()
            return r.json(), "ok"
        except Exception as e:
            if attempt == TRIES - 1:
                print(f"  ! {label} unreached: {str(e)[:100]}", file=sys.stderr)
                return None, "unreached"
            time.sleep(RETRY_WAIT)
    return None, "unreached"


def current_snapshot_of(city_keys, timeout_s=10):
    """{city: run_at of the newest current run it holds, or None}: the snapshot
    a city whose current run was missed tonight is left on (audit P3).
    One read per city, one attempt of at most timeout_s; a read that fails is
    reported as "unread", never raised."""
    from common import _cfg, _get, _headers
    out = {}
    for c in sorted(set(city_keys)):
        try:
            r = _get(f"{_cfg()['url']}/rest/v1/weather_forecast_models", headers=_headers(),
                     timeout=timeout_s, params={"select": "run_at", "city_key": f"eq.{c}",
                                                "source": f"eq.{CURRENT_SOURCE}",
                                                "order": "run_at.desc", "limit": "1"})
            r.raise_for_status()
            rows = r.json()
            out[c] = rows[0]["run_at"] if rows else None
        except Exception as e:
            print(f"  ! {c}: its newest current run was not read ({type(e).__name__})", file=sys.stderr)
            out[c] = "unread"
    return out


CURRENT_LEADS = (0, 1, 2)
WHOLE_DAY_HOURS = 23      # a local day is 23, 24 or 25 hours; fewer is a partial edge


def build_current_rows(city_key, js, model, fetched_at, tz=None):
    """One row per local date for one model's current run - the city's today
    and the next two days, whole days only; lead is days after the city's own
    today at fetch time. Hours are UTC, `tz` the city's zone."""
    tz = tz or "UTC"
    hourly = (js or {}).get("hourly") or {}
    times = hourly.get("time") or []
    vals = hourly.get(f"temperature_2m_{model}") or []
    if not times or not vals:
        return []
    local_today = dt.date.fromisoformat(city_local_date(fetched_at, tz))
    daily, hours = {}, defaultdict(int)
    for t, v in zip(times, vals):
        d = city_local_date(t, tz)
        hours[d] += 1
        if v is None:
            continue
        daily[d] = v if d not in daily else max(daily[d], v)
    rows = []
    for d, mx in sorted(daily.items()):
        lead = (dt.date.fromisoformat(d) - local_today).days
        if lead not in CURRENT_LEADS or hours[d] < WHOLE_DAY_HOURS:
            continue
        rows.append({"city_key": city_key, "model": f"open_meteo_{model}",
                     "run_at": fetched_at.isoformat(), "for_date": d,
                     "lead_days": lead,
                     "forecast_max_c": round(float(mx), 2), "source": CURRENT_SOURCE})
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
    #
    # THE DATABASE AND THE ARCHIVE (plan v2 P1.6 phase 2, step 3). The
    # catch-up window below is 35 days and the archive's keep is going to 30.
    # Read from the table alone, the days it no longer holds would look
    # missing, and missing_span would stretch from the oldest of them to
    # today: every night, 36 days of best_match and seven models for every
    # city - the cost missing_span was written to stop - and days 31-35
    # written back into the database for the next prune to take out again.
    # Through weather_history, a day the archive holds counts as present.
    # main() no longer reaches below the oldest day the tables hold
    # (first_held_date, step 5), so this is the same read as before; a hole
    # older than that day is no longer fetched.
    from common import rest_all
    rows = weather_history.read('weather_forecasts', [
        ('select', 'for_date,lead_days'), ('city_key', f'eq.{city_key}'),
        ('model', f'eq.{MODEL_LABEL}'), ('for_date', f'gte.{start.isoformat()}'),
        ('for_date', f'lte.{end.isoformat()}')],
        rest_fn=rest, rest_all_fn=rest_all, order='for_date,lead_days,forecast_id', page_size=500)
    leads = defaultdict(set)
    for row in rows:
        leads[row['for_date']].add(row['lead_days'])
    return {date for date, present in leads.items() if set(LEADS).issubset(present)}


def coverage_count(city_key, start, end):
    return len(existing_dates(city_key, start, end))

def missing_span(have, cs, ce):
    """(first, last) missing date inside [cs, ce], or None when all are present.

    THE NIGHTLY RUN ASKED FOR 36 DAYS TO FILL ONE. The catch-up window is 35
    days so an old hole is still seen, and a chunk is 60 days so that window is
    one request - but a chunk with ANY missing date was fetched whole, and the
    newest date is always missing when the run starts. So every night fetched
    36 days of best_match and 36 days of seven models for all 48 cities, and
    chained two continuations to finish: measured 26 Sep, forecasts.yml
    03:36-04:44Z (three jobs, ~68 billed minutes) plus pipeline_daily's own
    ingest step 19 minutes - with 19-26 Sep already complete for every city.
    Asking only for the span that is missing keeps the 35-day view of holes
    and fetches one day on a normal night."""
    first = last = None
    d = cs
    while d <= ce:
        if d.isoformat() not in have:
            first = first or d
            last = d
        d += dt.timedelta(days=1)
    return (first, last) if first else None


def _oldest_held(table):
    rows = rest(table, [("select", "for_date"), ("order", "for_date.asc"), ("limit", "1")])
    return dt.date.fromisoformat(str(rows[0]["for_date"])[:10]) if rows else None


def first_held_date():
    """The oldest for_date weather_forecasts still holds, or None.

    NOTHING IS WRITTEN BELOW IT (plan v2 P1.6 phase 2, step 5). The prune cuts
    both tables by date, so from this day on they hold every row they were
    given, and v_forecast_latest, v_hit_forecasts and their nightly freezes
    (sql/ad4_31, ad4_88, ad4_97) read the table for exactly those days and the
    frozen rows before. A fetch below it wrote whole days back into a table
    that no longer held them: missing_span runs from the first missing day to
    today, so one hole 33 days back re-fetched days 31-32 too, from the
    previous-runs API, with other run_at values than the rows already in
    data/archive - which weather_history keys on, so both would be read.

    ONE TABLE PER BOUNDARY (WXPredict build 2.A, 7 Oct). This was the later of
    both tables' oldest days. weather_forecast_models now keeps a week and
    weather_forecasts 30 days, so the later day would shrink the catch-up
    window to a week for both. The window is judged on weather_forecasts
    (existing_dates), so it starts at weather_forecasts' oldest day; the model
    rows the same fetch returns are held to their own table's oldest day by
    models_first_held_date.

    THREE DAYS SINCE 9 OCT (Fresh Supabase, part 2b): weather_forecasts keeps
    three days, so a hole older than that is no longer filled. Of the month
    the table held on 9 Oct, the only forecasts written more than three days
    after their day were an outage's catch-up on 13-14 Sep: 140 four days
    late and 21 five days late. Those would now stay missing."""
    return _oldest_held("weather_forecasts")


def models_first_held_date():
    """The oldest for_date weather_forecast_models still holds, or None: no
    model row dated before it is written (first_held_date says why). Since
    26 Sep every model row has been written within a day of its date (51,061
    rows, 7 Oct), so a night's fetch loses nothing to it; a hole older than a
    week is no longer filled from here."""
    return _oldest_held("weather_forecast_models")


def held_from(rows, first_held):
    """(the rows dated on or after first_held, how many were dropped). None
    holds nothing back."""
    if not first_held:
        return rows, 0
    keep = [r for r in rows if r["for_date"] >= first_held.isoformat()]
    return keep, len(rows) - len(keep)


def catchup_start(start, first_held):
    """The window's first day, never below what the tables still hold."""
    return max(start, first_held) if first_held else start


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

    # A day the prune has taken is in data/archive, not the tables: it is not
    # fetched again, whoever asked for it (first_held_date says why). A
    # current-only pass reads neither archive table: a slow or failed read
    # there must not cost it the current runs it exists to retry (review of
    # #311).
    if start > end:
        raise ValueError('start must be on or before end')
    first_held = first_held_date() if ARCHIVE else start
    models_held = models_first_held_date() if ARCHIVE else None
    if catchup_start(start, first_held) != start:
        if first_held > end:
            raise ValueError(f"{start} to {end} is older than {first_held}, the oldest day "
                             f"weather_forecasts holds: those days are in data/archive")
        print(f"window starts {first_held}, the oldest day weather_forecasts holds "
              f"(asked from {start}; the days before are archived)")
        start = catchup_start(start, first_held)
    if models_held and models_held > start:
        print(f"model rows from {models_held}, the oldest day weather_forecast_models holds "
              f"(the days before are archived)")

    t0 = time.monotonic()
    all_cities = get_cities(require_coords=True)
    archive_cities = all_cities if ARCHIVE else []
    windows = chunks(start, end, CHUNK_DAYS)
    total_days = (end - start).days + 1

    if ARCHIVE:
        print(f"cities: {len(all_cities)}  window: {start} -> {end}  chunks/city: {len(windows)}")
        print("ranking cities by existing coverage (least first)...", flush=True)
    else:
        print("current runs only (FORECAST_ARCHIVE=0): the archive window is not asked for")

    # Coverage for every city at once; each city's known dates are kept, so
    # its fetch below does not read them a second time.
    with ThreadPoolExecutor(WORKERS) as pool:
        haves = list(pool.map(lambda c: existing_dates(c["city_key"], start, end), archive_cities))
    ranked = sorted(((len(h), c, h) for c, h in zip(archive_cities, haves)), key=lambda x: x[0])
    # LEAST covered first - always makes progress where it matters

    done_ct = sum(1 for n, _, _ in ranked if n >= total_days)
    print(f"{done_ct}/{len(ranked)} cities complete ({total_days} days, all requested leads)\n")

    total, ran_out, missing_chunks, unreached_chunks, completed_dates = 0, False, 0, 0, 0
    # The per-model request is reported, never raised: it is additive, and a
    # refusal there must not stop best_match or start a paid continuation.
    model_rows, model_failed, model_empty = defaultdict(int), defaultdict(int), set()
    model_below_held = 0

    def one_city(item):
        n_before, c, have = item
        out = {"got": 0, "skipped": 0, "refused": 0, "unreached": 0, "ran_out": False,
               "model_rows": defaultdict(int), "model_failed": defaultdict(int), "model_empty": set(),
               "model_below_held": 0}
        if (time.monotonic() - t0) / 60 > SOFT_DEADLINE_MIN:
            out["ran_out"] = True
            return c, n_before, out
        for (cs, ce) in windows:
            if (time.monotonic()-t0)/60 > SOFT_DEADLINE_MIN:
                out["ran_out"] = True
                break
            if have and chunk_is_covered(have, cs, ce):
                out["skipped"] += 1
                continue
            span = missing_span(have, cs, ce)
            if span:
                cs, ce = span
            js, outcome = fetch(c["latitude"], c["longitude"], cs, ce, f"{c['city_key']} {cs}")
            if outcome == "refused":
                out["refused"] += 1
                continue
            if outcome != "ok":
                out["unreached"] += 1
                continue
            rows = build_rows(c["city_key"], js, tz=c.get("timezone"), first=cs, last=ce)
            if rows:
                out["got"] += upsert("weather_forecasts", rows, "city_key,model,run_at,for_date")
            time.sleep(PAUSE)
            if MODELS:
                mjs, mout = fetch(c["latitude"], c["longitude"], cs, ce,
                                  f"{c['city_key']} {cs} models", models=MODELS)
                if mout != "ok":
                    out["model_failed"][mout] += 1
                else:
                    for model in MODELS:
                        mrows = [{k: v for k, v in r.items() if k != "variables"}
                                 for r in build_rows(c["city_key"], mjs, model, c.get("timezone"), cs, ce)]
                        # Never below the oldest day weather_forecast_models
                        # holds: those days are in data/archive.
                        mrows, below = held_from(mrows, models_held)
                        out["model_below_held"] += below
                        if mrows:
                            n = upsert("weather_forecast_models", mrows,
                                       "city_key,model,run_at,for_date")
                            out["model_rows"][model] += n
                            out["got"] += n
                        else:
                            out["model_empty"].add(model)
                time.sleep(PAUSE)
        return c, n_before, out

    todo = [item for item in ranked if item[0] < total_days]
    for n_before, c, _ in ranked:
        if n_before >= total_days:
            print(f"  {c['city_key']:16s} already complete ({n_before}d), skipping")
    def settle(out):
        settled["missing"] += out["refused"]
        settled["unreached"] += out["unreached"]
        for k, n in out["model_failed"].items():
            model_failed[k] += n

    settled = {"missing": 0, "unreached": 0}
    for attempt in (1, 2):
        again, pending = [], {}
        with ThreadPoolExecutor(WORKERS) as pool:
            for c, n_before, out in pool.map(one_city, todo):
                if out["ran_out"]:
                    ran_out = True
                total += out["got"]
                for m, n in out["model_rows"].items():
                    model_rows[m] += n
                model_below_held += out["model_below_held"]
                model_empty |= out["model_empty"]
                # a hung city is asked once more below; only its LAST answer counts
                if out["unreached"] and attempt == 1:
                    again.append(c)
                    pending[c["city_key"]] = out
                else:
                    settle(out)
                note = []
                if out["skipped"]:  note.append(f"{out['skipped']} chunks pre-existing")
                if out["refused"]:  note.append(f"{out['refused']} chunks refused")
                if out["unreached"]: note.append(f"{out['unreached']} chunks unreached")
                flag = "  (" + ", ".join(note) + ")" if note else ""
                print(f"  [{attempt}] {c['city_key']:16s} +{out['got']:6d} rows "
                      f"(had {n_before}d){flag}", flush=True)
        if not again or (time.monotonic() - t0) / 60 > SOFT_DEADLINE_MIN:
            for out in pending.values():   # no second chance left: the first answer stands
                settle(out)
            break
        print(f"\nasking the {len(again)} hung cities once more", flush=True)
        todo = [(len(h), c, h) for c, h in zip(
            again, [existing_dates(c["city_key"], start, end) for c in again])]
    missing_chunks += settled["missing"]
    unreached_chunks += settled["unreached"]
    with ThreadPoolExecutor(WORKERS) as pool:
        after = list(pool.map(lambda c: coverage_count(c["city_key"], start, end), archive_cities))
    completed_dates = sum(max(0, a - len(h)) for a, h in zip(after, haves))
    if ran_out:
        print(f"\n! soft deadline at {(time.monotonic() - t0) / 60:.0f} min. Re-run the identical "
              f"command - least-covered cities go first automatically, so progress is never lost "
              f"or reprocessed.")

    # EVERY CITY'S CURRENT RUN, whatever the loop above skipped: a city complete
    # for best_match still needs today's forecasts of the days ahead.
    current_rows, current_failed, current_missing = 0, defaultdict(int), []

    def one_current(c):
        if (time.monotonic() - t0) / 60 > SOFT_DEADLINE_MIN + 2:
            return c, "deadline", 0
        fetched_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        cjs, cout = fetch_current(c["latitude"], c["longitude"], f"{c['city_key']} current")
        if cout != "ok":
            return c, cout, 0
        n = 0
        for model in MODELS:
            crows = build_current_rows(c["city_key"], cjs, model, fetched_at, c.get("timezone"))
            if crows:
                n += upsert("weather_forecast_models", crows, "city_key,model,run_at,for_date")
        time.sleep(PAUSE)
        return c, "ok", n

    if MODELS:
        todo = [c for c in all_cities if CURRENT_CITIES is None or c["city_key"] in CURRENT_CITIES]
        for attempt in (1, 2):
            with ThreadPoolExecutor(WORKERS) as pool:
                results = list(pool.map(one_current, todo))
            current_rows += sum(n for _, _, n in results)
            hung = [c for c, out, _ in results if out == "unreached"]
            if attempt == 2 or not hung or (time.monotonic() - t0) / 60 > SOFT_DEADLINE_MIN + 2:
                for c, out, _ in results:
                    if out != "ok":
                        current_failed[out] += 1
                        current_missing.append(c["city_key"])
                break
            for c, out, _ in results:
                if out not in ("ok", "unreached"):
                    current_failed[out] += 1
                    current_missing.append(c["city_key"])
            todo = hung
        print(f"current runs: {current_rows} row(s)"
              + (f"; failed {dict(current_failed)}" if current_failed else ""))
    # The cities still without tonight's current run, and the newest each holds.
    snapshot_of = current_snapshot_of(current_missing) if current_missing else {}
    for c, at in snapshot_of.items():
        print(f"  {c}: no current run tonight; its newest is {at}")

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
              'unreached_chunks': unreached_chunks, 'completed_dates': completed_dates,
              'current_cities_missing': sorted(set(current_missing)),
              'current_snapshot_of': snapshot_of, 'archive': ARCHIVE}
    result_path = os.environ.get('FORECAST_RESULT_PATH')
    if result_path:
        with open(result_path, 'w') as handle:
            json.dump(result, handle)
    # A CITY WITHOUT TODAY'S CURRENT RUN IS A PARTIAL COLLECTION (30 Sep). The
    # runs of 29-30 Sep logged 'ok' with current_failed unreached 1-4: those
    # cities priced on an older run and the log read healthy. It does not
    # make the run `incomplete` - that is the archive's verdict, and a missed
    # current run says nothing about the archive - but it is reported, with
    # the run the city is left on (current_snapshot_of), and
    # scripts/forecast_nightly.py retries it in current-only passes (5 Oct).
    status = "partial" if (incomplete or current_failed) else "ok"
    log_run("ingest_forecasts", status, total,
            {"start": str(start), "end": str(end), "leads": LEADS,
             "chunk_days": CHUNK_DAYS, "ran_out": ran_out,
             "refused_chunks": missing_chunks, "unreached_chunks": unreached_chunks,
             "models": MODELS, "model_rows": dict(model_rows),
             "model_requests_failed": dict(model_failed),
             "models_without_rows": sorted(model_empty),
             "model_rows_below_held": model_below_held, "models_held_from": str(models_held) if models_held else None,
             "current_rows": current_rows, "current_failed": dict(current_failed),
             "current_cities_missing": sorted(set(current_missing)),
             "current_snapshot_of": snapshot_of, "archive": ARCHIVE})

if __name__ == "__main__":
    main()
