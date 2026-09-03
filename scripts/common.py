"""Shared Supabase helpers for AD4 jobs."""
import os, sys, time, json
import requests

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

def rest(path, params=None):
    """GET from PostgREST."""
    r = requests.get(f"{_cfg()['url']}/rest/v1/{path}", headers=_headers(),
                     params=params or {}, timeout=90)
    r.raise_for_status()
    return r.json()

def insert(table, rows, chunk=500):
    """Plain append - for time-series tables where every row is new (each
    run stamps its own computed_at/detected_at), no on_conflict needed."""
    if not rows:
        return 0
    written = 0
    h = _headers()
    h["Prefer"] = "return=minimal"
    for i in range(0, len(rows), chunk):
        batch = rows[i:i+chunk]
        r = requests.post(f"{_cfg()['url']}/rest/v1/{table}",
                          headers=h, data=json.dumps(batch), timeout=120)
        if r.status_code >= 400:
            print(f"  ! {table} insert failed {r.status_code}: {r.text[:300]}", file=sys.stderr)
            r.raise_for_status()
        written += len(batch)
    return written

def upsert(table, rows, on_conflict, chunk=500):
    """Insert rows, ignoring duplicates on the given unique key."""
    if not rows:
        return 0
    written = 0
    h = _headers()
    h["Prefer"] = "resolution=ignore-duplicates,return=minimal"
    for i in range(0, len(rows), chunk):
        batch = rows[i:i+chunk]
        r = requests.post(f"{_cfg()['url']}/rest/v1/{table}",
                          headers=h, params={"on_conflict": on_conflict},
                          data=json.dumps(batch), timeout=120)
        if r.status_code >= 400:
            print(f"  ! {table} write failed {r.status_code}: {r.text[:300]}", file=sys.stderr)
            r.raise_for_status()
        written += len(batch)
    return written

def log_run(job, status, rows, detail):
    try:
        requests.post(f"{_cfg()['url']}/rest/v1/rpc/log_ingest", headers=_headers(),
                      data=json.dumps({"p_job": job, "p_status": status,
                                       "p_rows": rows, "p_detail": detail}), timeout=30)
    except Exception as e:
        print(f"  ! log_ingest failed: {e}", file=sys.stderr)

def get_cities(require_coords=True, require_icao=False):
    cols = "city_key,icao,unit,latitude,longitude,timezone,resolution_source,status"
    rows = rest("cities", {"select": cols, "status": "eq.active", "limit": "500"})
    out = []
    for c in rows:
        if require_coords and (c.get("latitude") is None or c.get("longitude") is None):
            continue
        if require_icao and not c.get("icao"):
            continue
        out.append(c)
    return out

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
        r = requests.post(f"{_cfg()['url']}/rest/v1/model_versions", headers=h,
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
