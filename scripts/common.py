"""Shared Supabase helpers for AD4 jobs."""
import os, sys, time, json
import requests
import threading

# ONE CONNECTION POOL FOR THE PROCESS (plan v2 P6.1). Every call used to be a
# bare requests.get/post: a new TCP and TLS handshake per request. Measured on
# the 24 Sep 04:43Z intraday run, "Band probabilities" took 231 s for 65
# city-days - about a dozen sequential round trips each, and the handshake is
# most of a round trip. A Session keeps the connections alive. Tests replace
# requests.get / requests.post with fakes; _get/_post honour that, so a fake
# is always what a test talks to.
_REAL_GET, _REAL_POST = requests.get, requests.post
_session_obj = None
_session_lock = threading.Lock()


def _session():
    global _session_obj
    if _session_obj is None:
        with _session_lock:
            if _session_obj is None:
                s = requests.Session()
                adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16)
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                _session_obj = s
    return _session_obj


def _get(url, **kw):
    if requests.get is not _REAL_GET:
        return requests.get(url, **kw)
    return _session().get(url, **kw)


def _post(url, **kw):
    if requests.post is not _REAL_POST:
        return requests.post(url, **kw)
    return _session().post(url, **kw)

_config = None

def _cfg():
    """Lazy env lookup - importing this module (e.g. for unit tests of pure
    functions elsewhere) must not require SUPABASE_URL/SUPABASE_SERVICE_KEY
    to be set. Only actually calling rest()/insert()/upsert()/log_run() does."""
    global _config
    if _config is None:
        _config = {
            "url": os.environ["SUPABASE_URL"].rstrip("/"),
            "key": os.environ["SUPABASE_SERVICE_KEY"],
        }
    return _config

def _headers():
    key = _cfg()["key"]
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def rest(path, params=None, tries=4):
    """GET from PostgREST, retrying transient failures.

    WHY THIS EXISTS, AND WHY IT IS THE SAME LIST AS WRITES.

    _post_batch below has retried transient 5xx since the observations ingest
    died at `[11/53] KAUS` on a single 504. Reads were left alone, on the
    reasoning that individual callers could wrap themselves in retry(). Most
    never did, and one 502 is all it takes:

        Intraday Pipeline, 2026-09-14 - all three stages dead in 30s
        edge_engine.py line 170 rest("v_canonical_markets", ...)
        502 Bad Gateway

    Nothing was wrong with the query. The same request returned 101 rows a
    few minutes later. PostgREST had briefly gone away - it reloads its schema
    cache whenever the schema changes, and a function had just been created -
    and a read with no retry turned a two-second blip into a failed pipeline
    and an empty board until somebody noticed and pressed the button again.

    A read is SAFE to repeat, which is what makes this strictly easier than
    the write case: no idempotency to reason about, no risk of double-writing.
    Giving up still raises, for the same reason it does for writes - a read
    that quietly returned nothing would let a job compute over an empty set
    and report success.
    """
    url = f"{_cfg()['url']}/rest/v1/{path}"
    for attempt in range(tries):
        last = attempt == tries - 1
        try:
            r = _get(url, headers=_headers(), params=params or {}, timeout=90)
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            if last:
                print(f"  ! GET {path} gave up after {tries}: {e}", file=sys.stderr)
                raise
            print(f"  . GET {path} {type(e).__name__}, retrying "
                  f"({attempt + 1}/{tries})", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
            continue

        if r.status_code < 400:
            return r.json()

        if r.status_code not in TRANSIENT_WRITE_STATUS or last:
            r.raise_for_status()

        delay = _retry_after(r) if r.status_code == 429 else None
        if delay is None:
            delay = 5 * (attempt + 1)
        print(f"  . GET {path} {r.status_code}, retrying in {delay:.0f}s "
              f"({attempt + 1}/{tries}): {r.text[:120]}", file=sys.stderr)
        time.sleep(min(delay, 120))

def rest_all(path, params=None, *, order, page_size=500):
    """Read a bounded scope completely, including under smaller server caps.

    Callers supply a stable unique ordering (including a tie breaker). Offset
    advances by rows actually received, never by the requested page size.
    Do not use this for an unbounded live stream; capture a cutoff first.
    """
    if not order or page_size < 1:
        raise ValueError("stable order and positive page_size required")
    pairs = list((params or {}).items()) if isinstance(params, dict) else list(params or [])
    pairs = [(k, v) for k, v in pairs if k not in ('limit', 'offset', 'order')]
    out, offset = [], 0
    previous = None
    while True:
        rows = rest(path, pairs + [('order', order), ('offset', str(offset)),
                                   ('limit', str(page_size))])
        if not rows:
            return out
        fingerprint = json.dumps(rows, sort_keys=True)
        if fingerprint == previous:
            raise RuntimeError(f"{path}: pagination did not advance")
        previous = fingerprint
        out.extend(rows)
        offset += len(rows)

def by_shape(rows):
    """Split rows into groups that all carry the SAME set of keys.

    PostgREST refuses a bulk POST whose objects differ in their keys -
    `PGRST102: All object keys must match` - because one INSERT statement has
    one column list. Live Weather hit this the moment a poll produced two
    different kinds of event in the same batch: a SPIKE carries change_c, a
    CONDITION_CHANGE does not, and the whole write failed with none of it
    landing.

    Grouping rather than filling the gaps with nulls is deliberate. An absent
    key takes the column's DEFAULT; an explicit null overrides it. Normalising
    would have quietly turned `detected_at default now()` into NULL for any
    row that did not mention it.

    Order is preserved within each group, and groups come back in the order
    their first row appeared, so a caller's rows are written in the order it
    built them.
    """
    groups, order = {}, []
    for r in rows:
        sig = tuple(sorted(r.keys()))
        if sig not in groups:
            groups[sig] = []
            order.append(sig)
        groups[sig].append(r)
    return [groups[sig] for sig in order]

# The statuses worth trying again. Everything else is the payload's fault and
# will fail identically four more times: 400 PGRST102 (mismatched keys), 401,
# 403 (42501 permission denied), 404 (no such table), 409 (a conflict with no
# on_conflict target). Retrying those costs ~35s per batch and changes nothing.
TRANSIENT_WRITE_STATUS = {429, 500, 502, 503, 504, 408, 522, 524}

def _retry_after(resp):
    try:
        return float((getattr(resp, "headers", None) or {}).get("Retry-After", ""))
    except (TypeError, ValueError):
        return None

def _post_batch(url, headers, params, batch, table, verb, tries=4):
    """POST one batch, retrying transient failures. Raises if it never lands.

    WHY THIS EXISTS. Supabase returns a 5xx on roughly 0.5-2% of writes in a
    busy hour - measured over this project's own request log, most hours have
    none and the worst had 2 in 241. That is harmless for a job that writes
    once. It is not harmless for a job that writes once per city: at 53 cities
    a single-batch failure rate of 1% means a ~40% chance that some batch
    fails, and _post_rows used to call raise_for_status() on the first one.
    The observations ingest died at `[11/53] KAUS` on one 504, throwing away
    the ten cities it had written and never reaching the other forty-two.

    Reads have had retry() since live_weather.py started hitting 429s. Writes
    never did, which is why the action workflows looked unstable while the
    code was in fact correct - they were writing real data right up until one
    transient 5xx ended the run.

    Giving up still raises. A write that silently returns after failing would
    be a far worse bug than the one being fixed: the run would go green having
    persisted nothing.
    """
    for attempt in range(tries):
        last = attempt == tries - 1
        try:
            r = _post(url, headers=headers, params=params or {},
                              data=json.dumps(batch), timeout=120)
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            if last:
                print(f"  ! {table} {verb} gave up after {tries}: {e}", file=sys.stderr)
                raise
            print(f"  . {table} {verb} {type(e).__name__}, retrying "
                  f"({attempt + 1}/{tries})", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
            continue

        if r.status_code < 400:
            return

        body = r.text[:300]
        if r.status_code not in TRANSIENT_WRITE_STATUS or last:
            print(f"  ! {table} {verb} failed {r.status_code}: {body}", file=sys.stderr)
            r.raise_for_status()

        delay = _retry_after(r) if r.status_code == 429 else None
        if delay is None:
            delay = 5 * (attempt + 1)
        print(f"  . {table} {verb} {r.status_code}, retrying in {delay:.0f}s "
              f"({attempt + 1}/{tries}): {body[:120]}", file=sys.stderr)
        time.sleep(min(delay, 120))

def _post_rows(table, rows, headers, params, chunk, verb):
    written = 0
    url = f"{_cfg()['url']}/rest/v1/{table}"
    for group in by_shape(rows):
        for i in range(0, len(group), chunk):
            batch = group[i:i+chunk]
            _post_batch(url, headers, params, batch, table, verb)
            written += len(batch)
    return written

def insert(table, rows, chunk=500):
    """Plain append - for time-series tables where every row is new (each
    run stamps its own computed_at/detected_at), no on_conflict needed.

    Rows may differ in which optional keys they carry; see by_shape."""
    if not rows:
        return 0
    h = _headers()
    h["Prefer"] = "return=minimal"
    return _post_rows(table, rows, h, None, chunk, "insert")

def upsert(table, rows, on_conflict, chunk=500):
    """Insert rows, ignoring duplicates on the given unique key.

    THE RIGHT DEFAULT FOR A FACT. An observation, a book snapshot, a filled
    order: the row records something that happened, and a second run that sees
    the same event must not be able to rewrite the first record of it. See
    databank.py - ignore-duplicates is what keeps that guarantee.

    IT IS THE WRONG CALL FOR A VERDICT. Where the row is not an event but the
    CURRENT ANSWER to a standing question - is this model promoted, is this
    city fresh - ignoring the duplicate freezes the first answer forever and
    every later run computes a correct row and throws it away. Use
    upsert_replace() for those; the two are one word apart and the difference
    is the whole meaning of the table.
    """
    if not rows:
        return 0
    h = _headers()
    h["Prefer"] = "resolution=ignore-duplicates,return=minimal"
    return _post_rows(table, rows, h, {"on_conflict": on_conflict}, chunk, "write")

def upsert_replace(table, rows, on_conflict, chunk=500):
    """Insert rows, OVERWRITING the existing row on the given unique key.

    WHAT THIS IS FOR, and what it must never be used for.

    A derived verdict table holds one row per question, and the row is the
    answer as of the last run. derived_model_promotion is the case this was
    written for: one row per (city, lead, target) saying whether that model
    may move a price. It is recomputed from scratch on every run, from
    predictions and outcomes that are themselves append-only - so nothing is
    lost by overwriting it, and everything is lost by not.

    MEASURED, 2026-09-21. derived_model_promotion was written with upsert(),
    which is ignore-duplicates. The first run wrote 392 rows at 09-20 09:18.
    Every run since recomputed them and the database discarded all of it:

        rows in the table                                          392
        distinct computed_at dates                                   1  (09-20)
        rows the 09-21 run computed and the database ignored       384

    So leads 1 to 7 read `stale` on all 49 cities, permanently, no matter how
    many days settled - not because the evidence was missing but because the
    verdict could not be rewritten. A promotion gate that cannot change its
    mind is not a gate.

    NOT FOR FACT TABLES. weather_observations, book_snapshots, paper_trades,
    fact_* - all append-only, several with a database trigger that refuses an
    UPDATE outright (sql/ad4_70_archive_exemption.sql). Reach for upsert()
    there and let the duplicate be ignored.
    """
    if not rows:
        return 0
    h = _headers()
    h["Prefer"] = "resolution=merge-duplicates,return=minimal"
    return _post_rows(table, rows, h, {"on_conflict": on_conflict}, chunk, "replace")

def rpc(fn, params=None, timeout=120):
    """POST to a PostgREST RPC, putting the SERVER's message in the exception.

    raise_for_status() throws the response body away, and the body is where
    Postgres puts the reason. Every failure of refresh_feature_cache - a
    cancelled statement, a missing dependency, a genuine bug - arrived here as
    the identical string "500 Server Error for url: .../refresh_feature_cache",
    which is not a diagnosis.
    """
    r = _post(f"{_cfg()['url']}/rest/v1/rpc/{fn}", headers=_headers(),
                      data=json.dumps(params or {}), timeout=timeout)
    if r.status_code >= 400:
        raise requests.HTTPError(f"{fn} -> HTTP {r.status_code}: {r.text[:400]}",
                                 response=r)
    return r.json()


def refresh_feature_cache(days=None, quiet=False):
    """Refresh the derived caches ONE CITY AT A TIME, and raise if any fail.

    WHY PER CITY. statement_timeout is measured from the start of the TOP-LEVEL
    statement and is never reset by the statements a function runs inside
    itself. So `select refresh_feature_cache()` is one statement no matter how
    the function is written internally, and over a 710k-row archive it takes
    ~6 seconds - past what Supabase allows, which reaches a caller over
    PostgREST as a bare HTTP 500. Splitting has to happen out here, where each
    slice is genuinely its own statement: ~200ms a city, each with a fresh
    timeout, at any archive size.

    Raises on the first city that fails. Callers that prune must not proceed on
    a partial cache - the cache is what survives the prune.
    """
    # Active only. A retired city's cached days are kept forever, but there is
    # no reason to spend a per-city statement refreshing history nobody prices.
    cities = sorted(active_city_keys())
    if not cities:
        return {"cities": 0, "city_days_total": 0}

    params = {"p_days": days} if days is not None else {}
    try:
        first = rpc("refresh_feature_cache", {**params, "p_city": cities[0]})
    except requests.HTTPError as e:
        body = getattr(e.response, "text", "") or ""
        if "PGRST202" in body or "PGRST203" in body:
            raise RuntimeError(
                "refresh_feature_cache does not accept p_city on this database - "
                "re-run sql/ad4_28_feature_cache.sql and sql/ad4_29_retention.sql "
                "(they changed: the refresh is per city now, because the "
                "whole-archive version exceeds the statement timeout)") from e
        raise

    results = [first or {}]
    for city in cities[1:]:
        results.append(rpc("refresh_feature_cache", {**params, "p_city": city}) or {})

    # THE EVIDENCE THAT OUTLASTS THE WEATHER TABLES (plan v2 P1.6 phase 2,
    # step 6): each city's hourly maxima for v_trajectory_evidence, and
    # v_hit_forecasts frozen. EVERY CITY, retired ones too, in one call: the
    # prune cuts every city's readings (5 retired cities held 7,279 on
    # 29 Sep) and refuses a day these hours do not hold. Measured live on
    # 29 Sep: 54 cities, 3,154 city-days in 3.4 s; the freeze, one query
    # whichever city is asked for, 16,560 rows in 7.5 s - inside the service
    # role's 180 s. Both raise like the rest: the archive does not prune on a
    # partial cache.
    hours = rpc("refresh_city_day_hours") or {}
    frozen = rpc("freeze_hit_forecasts") or {}

    def total_of(key):
        return sum(int(r.get(key) or 0) for r in results)

    out = {
        "cities": len(cities),
        "city_days_touched": total_of("city_days_touched"),
        "city_hours": total_of("city_hours"),
        # city_days_total is a count of the whole cache, the same on every call
        "city_days_total": int(results[-1].get("city_days_total") or 0),
        "refreshed_from": results[-1].get("refreshed_from"),
        "slowest_call_ms": max((int(r.get("ms") or 0) for r in results), default=0),
        "total_ms": total_of("ms"),
        "day_hours_written": int(hours.get("days_written") or 0),
        "day_hours_ms": int(hours.get("ms") or 0),
        "hit_forecasts_frozen": int(frozen.get("rows_written") or 0),
        "hit_forecasts_ms": int(frozen.get("ms") or 0),
    }
    if not quiet:
        print(f"feature cache: {out}")
    return out


def log_run(job, status, rows, detail):
    try:
        response = _post(f"{_cfg()['url']}/rest/v1/rpc/log_ingest", headers=_headers(),
                      data=json.dumps({"p_job": job, "p_status": status,
                                       "p_rows": rows, "p_detail": detail}), timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"  ! log_ingest failed: {e}", file=sys.stderr)

# ===========================================================================
# THE DAY A READING BELONGS TO.
#
# A daily maximum is a LOCAL-CALENDAR quantity. It is what the market settles
# on and what weather_forecasts.for_date holds.
#
# It is NOT quite what Open-Meteo returns with timezone=auto (measured 26 Sep):
# that answers the whole range at ONE fixed offset, the city's offset at the
# moment of the request - 48 cities x 439 days with no daylight-saving step,
# Wellington at +13 for July. Right for days near the request, an hour off
# across a clock change. The Python jobs ask for UTC and use this function.
#
# Three jobs did not agree with that. measure_skill.py, databank.py and the
# backtest runner all took the day from `valid_at[:10]` - a slice of the UTC
# string PostgREST returns - and then joined it against a forecast keyed by
# the city's LOCAL date. For any city away from UTC that silently moves
# readings into the wrong day:
#
#     23:00 UTC on 7 September   ->  Tokyo says 8 September
#                                ->  New York says 7 September
#
# In Tokyo the late-evening readings of one local day were being scored
# against the next day's forecast; in New York the early-morning ones against
# the previous day's. The error is small, systematic, and lands in mae_c -
# which sets sigma, which sets every band probability, which sets every edge.
# It flatters or damns a city's measured skill for a reason that has nothing
# to do with the forecast.
#
# One function, used by every job that turns a timestamp into a day.
# ===========================================================================
_ZONE_CACHE = {}
_ZONE_WARNED = set()

def day_had_ended(obs_date, computed_at, timezone):
    """True when a per-day row (a city's daily maximum) was computed after that
    local day ended - so it is the whole day, not the part of it seen so far.

    derived_city_day_features is recomputed once a night (capacity.py), and the
    row for "today" is written with the day's readings so far. Measured 26 Sep
    22:45Z: the 35 rows for 26 Sep computed at 05:18Z were on average 4.1 C
    below the day's maximum (17 C at most). A model that learns from, or
    starts from, such a row learns a day that did not happen."""
    import datetime as _dt
    if obs_date is None or computed_at is None:
        return False
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(timezone) if timezone else _dt.timezone.utc
    except Exception:
        zone = _dt.timezone.utc
    day = _dt.date.fromisoformat(str(obs_date)[:10])
    end = _dt.datetime.combine(day + _dt.timedelta(days=1), _dt.time(0, 0), tzinfo=zone)
    at = computed_at
    if isinstance(at, str):
        at = _dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
    if at.tzinfo is None:
        at = at.replace(tzinfo=_dt.timezone.utc)
    return at >= end


def city_local_date(valid_at, timezone):
    """The calendar date `valid_at` falls on in `timezone`, as 'YYYY-MM-DD'.

    valid_at may be a datetime or an ISO string (PostgREST returns strings).
    A naive timestamp is treated as UTC, which is what PostgREST sends for a
    timestamptz column. An unknown or missing timezone falls back to UTC and
    says so once, rather than silently guessing - a city with no timezone is
    a roster problem, not something to paper over per row.
    """
    import datetime as _dt
    if valid_at is None:
        return None
    if isinstance(valid_at, str):
        t = valid_at.replace("Z", "+00:00")
        try:
            ts = _dt.datetime.fromisoformat(t)
        except ValueError:
            # Not parseable: the old behaviour, so a malformed row degrades
            # to what it did before rather than dropping out entirely.
            return valid_at[:10]
    else:
        ts = valid_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)

    if not timezone:
        return ts.astimezone(_dt.timezone.utc).date().isoformat()

    zone = _ZONE_CACHE.get(timezone)
    if zone is None:
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(timezone)
        except Exception:
            if timezone not in _ZONE_WARNED:
                _ZONE_WARNED.add(timezone)
                print(f"  ! unknown timezone {timezone!r} - using UTC for its days",
                      file=sys.stderr)
            zone = _dt.timezone.utc
        _ZONE_CACHE[timezone] = zone
    return ts.astimezone(zone).date().isoformat()


def timezone_of(cities):
    """city_key -> timezone, from whatever get_cities() returned."""
    return {c["city_key"]: c.get("timezone") for c in cities}


def get_cities(require_coords=True, require_icao=False, extra=()):
    # observation_q_down / _up: the measurement layer P3.1 prices the floor
    # atom with, fitted per city by refresh_observation_trust() (plan v2 P2.3).
    # extra: more columns for one caller (ingest_nws.py reads the nws_* ids),
    # so it need not read the table around this definition of active.
    cols = ("city_key,icao,unit,latitude,longitude,timezone,resolution_source,status,"
            "observation_q_down,observation_q_up") + "".join("," + c for c in extra)
    rows = rest("cities", {"select": cols, "status": "eq.active", "limit": "500"})
    out = []
    for c in rows:
        if require_coords and (c.get("latitude") is None or c.get("longitude") is None):
            continue
        if require_icao and not c.get("icao"):
            continue
        out.append(c)
    return out


def active_city_keys():
    """The city keys that are still ours to work on.

    ONE DEFINITION OF ACTIVE, because there were four. get_cities() gates on
    status='active' and every ingest, pricing and settlement script goes
    through it - but three paths read the cities table raw, and two more read
    derived_city_day_features with no city filter at all. A city retired in the
    cities table would go on being fitted from its cached days, go on having
    its cache refreshed, and go on appearing in capacity, because none of those
    five asked.

    Retirement is not deletion here (see the 2026-09-19 migration): the rows
    stay forever, so "is this city retired" can only ever be a question about
    cities.status, never about whether data exists.
    """
    return {c["city_key"] for c in
            rest("cities", {"select": "city_key", "status": "eq.active",
                            "limit": "500"})}


def drop_retired(rows, active=None, key="city_key"):
    """Keep only rows belonging to an active city; say what went.

    Returns (kept, dropped_city_keys). The second half is not optional: a
    silently shorter city list is the exact shape of the bug that left six
    cities unpredicted on 2026-09-19, and a caller that cannot say which
    cities it dropped cannot tell a retirement from a defect.
    """
    active = active_city_keys() if active is None else active
    kept = [r for r in rows if r.get(key) in active]
    dropped = sorted({r.get(key) for r in rows if r.get(key) not in active})
    return kept, dropped

SKY_CONDITION_MAP = {
    "CLR": "CLEAR", "SKC": "CLEAR",
    "FEW": "PARTLY_CLOUDY", "SCT": "PARTLY_CLOUDY",
    "BKN": "CLOUDY", "OVC": "OVERCAST",
}
PRESENT_WEATHER_MAP = {
    "RA": "RAIN", "SN": "SNOW", "FG": "FOG", "TS": "STORM",
    "DZ": "RAIN", "SH": "RAIN", "GR": "STORM", "GS": "STORM",
}

# Oktas (eighths of sky covered) -> the same buckets SKY_CONDITION_MAP uses.
# weather_observations.cloud_cover is NUMERIC on the real database, so anything
# read back from it - or produced by ingest_observations.sky_oktas() - arrives
# as a number, not a METAR code. The boundaries mirror the code mapping exactly:
# FEW(2) and SCT(4) are PARTLY_CLOUDY, BKN(6) is CLOUDY, OVC(8) is OVERCAST.
def _sky_from_oktas(oktas):
    if oktas <= 0:  return "CLEAR"
    if oktas <= 4:  return "PARTLY_CLOUDY"
    if oktas <= 7:  return "CLOUDY"
    return "OVERCAST"

def normalize_sky_condition(sky_raw, present_weather_raw=None):
    """
    Sky cover plus present-weather codes (RA/SN/FG/TS/...) -> one of
    CLEAR/PARTLY_CLOUDY/CLOUDY/OVERCAST/RAIN/SNOW/FOG/STORM. Present weather
    takes priority (spec 8.3) - rain matters more than "also cloudy."

    sky_raw accepts EITHER form, because both exist in this codebase:
      * a METAR code   - CLR/SKC/FEW/SCT/BKN/OVC, straight off the IEM feed
      * oktas 0-8      - what ingest_observations writes and what the numeric
                         cloud_cover column reads back

    Taking only the code is what broke the Live Weather Monitor with
    "'int' object has no attribute 'strip'" after cloud_cover became numeric.
    """
    if present_weather_raw:
        code = str(present_weather_raw).strip().upper()
        for k, v in PRESENT_WEATHER_MAP.items():
            if k in code:
                return v

    if sky_raw is None or sky_raw == "":
        return None

    # numeric oktas, whether as a number or a numeric string from PostgREST
    if isinstance(sky_raw, bool):
        return None
    if isinstance(sky_raw, (int, float)):
        return _sky_from_oktas(float(sky_raw))
    text = str(sky_raw).strip()
    try:
        return _sky_from_oktas(float(text))
    except ValueError:
        pass

    return SKY_CONDITION_MAP.get(text.upper())

def retry(fn, tries=4, wait=5, label=""):
    """Call fn(), retrying transient failures. Returns None once it gives up.

    429 is treated as its own case. The IEM station API rate-limits a caller
    that walks 50+ stations back to back, and a linear 5s/10s backoff is not
    enough to clear it - live_weather.py was dying outright on 429 because it
    called fetch_station directly with no retry at all. Retry-After is
    honoured when sent; otherwise the wait grows exponentially from 15s.
    """
    for a in range(tries):
        try:
            return fn()
        except Exception as e:
            last = a == tries - 1
            resp = getattr(e, "response", None)
            status = getattr(resp, "status_code", None)

            if status == 429:
                after = None
                try:
                    after = float((resp.headers or {}).get("Retry-After", ""))
                except (TypeError, ValueError):
                    after = None
                delay = after if after else 15 * (2 ** a)
                if last:
                    print(f"  ! {label} still rate-limited after {tries} tries",
                          file=sys.stderr)
                    return None
                print(f"  . {label} rate-limited, waiting {delay:.0f}s", file=sys.stderr)
                time.sleep(min(delay, 120))
                continue

            if last:
                print(f"  ! {label} gave up after {tries}: {e}", file=sys.stderr)
                return None
            time.sleep(wait * (a + 1))


# model_versions cache, so one run resolves each label once
_version_ids = {}

def model_version_id(kind, label, config=None, structural=False, active=True):
    """Resolve a readable version label to its model_versions.version_id uuid.

    band_probabilities.forecast_version / calibration_version are uuid on the
    real schema - they are references into model_versions, whose `label`
    column is where the readable string belongs. Writing the label straight
    into the uuid column is what made every Probability + Edge run fail with

        invalid input syntax for type uuid: "v0_normal_lattice_no_calibration"

    Looks the row up by (kind, label) and creates it if absent, so a new
    model version registers itself the first time it is used. Returns None if
    model_versions is missing entirely, which leaves the caller free to omit
    the field rather than crash.
    """
    key = (kind, label)
    if key in _version_ids:
        return _version_ids[key]
    # One registration at a time: the engine prices city-days on a thread pool
    # (plan v2 P6.1), and two threads meeting the same new label must not both
    # try to create it.
    with _version_lock:
        if key in _version_ids:
            return _version_ids[key]
        return _model_version_id_locked(kind, label, key, config, structural, active)


_version_lock = threading.Lock()


def _model_version_id_locked(kind, label, key, config, structural, active):
    try:
        found = rest("model_versions", {
            "kind": f"eq.{kind}", "label": f"eq.{label}",
            "select": "version_id", "limit": "1",
        })
        if found:
            _version_ids[key] = found[0]["version_id"]
            return _version_ids[key]

        h = _headers()
        h["Prefer"] = "return=representation"
        r = _post(f"{_cfg()['url']}/rest/v1/model_versions", headers=h,
                          data=json.dumps([{
                              "kind": kind, "label": label,
                              "config": config or {}, "structural": structural,
                              "active": active,
                          }]), timeout=30)
        r.raise_for_status()
        body = r.json()
        vid = body[0]["version_id"] if body else None
        _version_ids[key] = vid
        return vid
    except Exception as e:
        print(f"  ! could not resolve model version {kind}/{label}: {e}", file=sys.stderr)
        _version_ids[key] = None
        return None
