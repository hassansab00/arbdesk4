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

def retry(fn, tries=3, wait=5, label=""):
    for a in range(tries):
        try:
            return fn()
        except Exception as e:
            if a == tries - 1:
                print(f"  ! {label} gave up after {tries}: {e}", file=sys.stderr)
                return None
            time.sleep(wait * (a + 1))
