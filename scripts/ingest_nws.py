"""NWS forecasts for the US cities, in the hourly tick (replaces n8n P1.3 and
P1.4, plan v2 P6.2).

WHY. Hassan's n8n plan is 2,500 executions a month, shared, and this project
may use 1,000 to 1,400 of them (25 Sep). P1.3 and P1.4 cost 8 a day. They only
ever read api.weather.gov and wrote this database, which the tick can do at
the same hours for no execution at all: on 25 Sep all 33 requests (points,
hourly forecast and gridpoint for 11 cities) took 4.4 s from a cloud
container, on 6 threads.

WHAT IS PORTED, AND HOW IT IS HELD TO THE ORIGINAL. Each function below is
the n8n Code node named in its docstring, line for line: the same days kept
and dropped, the same units, the same rounding (JavaScript's toFixed, which
rounds half away from zero on the exact binary value - Python's round() rounds
half to even, so it is not used), the same order of summation.
tests/test_ingest_nws.py runs the n8n templates' own Code nodes under Node and
this module on the same saved NWS responses and requires identical rows.

  P1.3  weather_forecasts, model 'nws': one daily max per local day from the
        hourly forecast, only for days whose series covers the local peak
        window 12-18; and each city's NWS grid id and support flag on
        `cities` (PATCH, never an insert).
  P1.4  weather_forecast_features: the day's conditions from the raw
        gridpoint series (ISO intervals expanded to hours, accumulating series
        spread across them), in the observation table's units.

Both write merge-duplicates on their keys, as the n8n nodes did; run_at is
NWS's own updateTime, so a run between issuances rewrites the same rows.

DIFFERENCES FROM n8n, deliberate:
  * Responses are paired with their city by key, not by position, so the
    "responses out of order" abort cannot happen; a response that names
    another grid fails that city only.
  * A run that writes nothing logs status 'error' with the reason. n8n threw,
    which stopped the workflow before its log node, so those runs left no
    ingest_log row at all.
  * Cadence: the n8n triggers fired at 03/09/15/21:46 and :50 UTC; this runs
    in the tick of those hours (at :36). Other ticks return at once and log
    nothing. The Workflows page's P1.3/P1.4 mode is obeyed (off or manual
    skips); should_run()'s every_minutes, n8n's cadence, is not used.
"""
import argparse
import datetime as dt
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import requests

from common import _cfg, _headers, get_cities, log_run, rest, upsert_replace

UA = {"User-Agent": "ArbDesk4 (github.com/hassansab00/arbdesk4)", "Accept": "application/geo+json"}
HOURS_UTC = (3, 9, 15, 21)
MODEL = "nws"
HORIZON_DAYS = 7
PEAK_WINDOW = (12, 18)
MORNING_HOUR = 8
TIMEOUT_S = 8
WORKERS = 11                 # one per US city: a stage costs one round trip, not two


# ---- JavaScript's number semantics, where the n8n code relied on them --------

def js_fixed_str(x, n):
    """Number.prototype.toFixed: half away from zero on the exact binary value."""
    d = Decimal(x).quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP)
    return f"{d:.{n}f}"


def js_round(x, n):
    """Number(x.toFixed(n)); None stays None. -0 is 0 and an integral value is
    an int, as JSON.stringify would write them."""
    if x is None:
        return None
    f = float(js_fixed_str(x, n))
    if f == 0:
        return 0
    return int(f) if f.is_integer() else f


def _num(v):
    """Number(v) for the values NWS sends: numbers, numeric strings, else NaN."""
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip()) if v.strip() else 0.0
        except ValueError:
            return float("nan")
    return float("nan")


def _nn(a, b):
    """a ?? b"""
    return a if a is not None else b


def _iso_ms(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


# ---- P1.3 --------------------------------------------------------------------

def point_requests(cities):
    """P1.3 'Build requests'."""
    out = []
    for c in cities:
        if not c or not c.get("city_key"):
            continue
        if c.get("nws_supported") is False:
            continue
        if c.get("latitude") is None or c.get("longitude") is None:
            continue
        lat = js_fixed_str(float(c["latitude"]), 4)
        lon = js_fixed_str(float(c["longitude"]), 4)
        out.append({"city_key": c["city_key"], "icao": c.get("icao"),
                    "grid_wfo": c.get("nws_grid_wfo"), "grid_x": c.get("nws_grid_x"),
                    "grid_y": c.get("nws_grid_y"),
                    "point_url": f"https://api.weather.gov/points/{lat},{lon}"})
    return out


def forecast_request(req, p, now_iso):
    """P1.3 'Build forecast urls', for one city: (hourly request | None, support row | None, outcome)."""
    p = p if isinstance(p, dict) else {}
    pp = p.get("properties") or {}
    if p.get("status") == 404 or "Not Found" in str(p.get("title") or "") or p.get("_http") == 404:
        return None, {"city_key": req["city_key"], "nws_supported": False, "nws_station_id": None,
                      "nws_grid_wfo": None, "nws_grid_x": None, "nws_grid_y": None,
                      "nws_checked_at": now_iso}, "not_us"
    url = pp.get("forecastHourly") or None
    if not url and req.get("grid_wfo") and req.get("grid_x") is not None and req.get("grid_y") is not None:
        url = f"https://api.weather.gov/gridpoints/{req['grid_wfo']}/{req['grid_x']},{req['grid_y']}/forecast/hourly"
    if not url:
        return None, None, "failed"
    sep = "&" if "?" in url else "?"
    hreq = {"city_key": req["city_key"], "grid_wfo": _nn(pp.get("gridId"), req.get("grid_wfo")),
            "grid_x": _nn(pp.get("gridX"), req.get("grid_x")), "grid_y": _nn(pp.get("gridY"), req.get("grid_y")),
            "hourly_url": f"{url}{sep}units=si"}
    support = None
    if pp.get("gridId"):
        support = {"city_key": req["city_key"], "nws_supported": True, "nws_station_id": req.get("icao"),
                   "nws_grid_wfo": pp["gridId"], "nws_grid_x": pp.get("gridX"), "nws_grid_y": pp.get("gridY"),
                   "nws_checked_at": now_iso}
    return hreq, support, "ok"


def _covers_peak(hours):
    return all(h in hours for h in range(PEAK_WINDOW[0], PEAK_WINDOW[1] + 1))


def hourly_rows(req, r, now_iso):
    """P1.3 'Build rows', for one city: (rows, partial days, ok)."""
    props = (r or {}).get("properties") or {}
    periods = props.get("periods") if isinstance(props.get("periods"), list) else []
    if props.get("gridId") and req.get("grid_wfo") and props["gridId"] != req["grid_wfo"]:
        raise ValueError(f"forecast for grid {props['gridId']} came back for {req['grid_wfo']} ({req['city_key']})")
    if not periods:
        return [], 0, False
    run_at = props.get("updateTime") or props.get("generatedAt") or now_iso
    by_day = {}
    for p in periods:
        if not p or not p.get("startTime"):
            continue
        st = str(p["startTime"])
        h = _num(st[11:13])
        day, hour = st[:10], int(h) if math.isfinite(h) else h
        t = _num(p.get("temperature"))
        if not math.isfinite(t):
            continue
        if str(p.get("temperatureUnit") or "").upper() == "F":
            t = (t - 32) * 5 / 9
        d = by_day.setdefault(day, {"hours": set(), "max": -math.inf, "min": math.inf, "at": None, "n": 0})
        d["hours"].add(hour)
        d["n"] += 1
        if t > d["max"]:
            d["max"], d["at"] = t, p["startTime"]
        if t < d["min"]:
            d["min"] = t
    days = sorted(by_day)
    if not days:
        return [], 0, False
    today = dt.date.fromisoformat(days[0])
    rows, partial = [], 0
    for day in days:
        d = by_day[day]
        if not _covers_peak(d["hours"]):
            partial += 1
            continue
        lead = (dt.date.fromisoformat(day) - today).days
        if lead < 0 or lead > HORIZON_DAYS:
            continue
        rows.append({
            "city_key": req["city_key"], "model": MODEL, "run_at": run_at, "for_date": day,
            "lead_days": lead, "forecast_max_c": js_round(d["max"], 2), "source": "api.weather.gov",
            "variables": {
                "min_c": js_round(d["min"], 2), "max_at_local": d["at"], "n_hours": d["n"],
                "peak_window": f"{PEAK_WINDOW[0]}-{PEAK_WINDOW[1]}",
                "grid": f"{req['grid_wfo']}/{req['grid_x']},{req['grid_y']}" if req.get("grid_wfo") else None,
                "issued_at": run_at,
            },
        })
    return rows, partial, bool(rows)


# ---- P1.4 --------------------------------------------------------------------

_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")
ACCUMULATING = {"quantitativePrecipitation", "snowfallAmount", "iceAccumulation"}
SERIES = (("temp", "temperature"), ("dew", "dewpoint"), ("rh", "relativeHumidity"),
          ("sky", "skyCover"), ("wind", "windSpeed"), ("pop", "probabilityOfPrecipitation"),
          ("qpf", "quantitativePrecipitation"), ("app", "apparentTemperature"),
          ("wdir", "windDirection"))            # P1.4's order, which is the summation order


def expand(valid_time):
    """An ISO interval 'start/PnDTnH' -> the epoch ms of each hour it covers."""
    start_s, _, dur = str(valid_time or "").partition("/")
    start = _iso_ms(start_s)
    if start is None:
        return []
    m = _DURATION.match(dur or "PT1H")
    hours = (int(m.group(1) or 0) * 24 + int(m.group(2) or 0) + (1 if int(m.group(3) or 0) >= 30 else 0)) if m else 1
    return [start + h * 3_600_000 for h in range(max(1, hours))]


def series(props, name):
    s = (props or {}).get(name)
    if not s or not isinstance(s.get("values"), list):
        return []
    uom = str(s.get("uom") or "")
    out = []
    for v in s["values"]:
        if v is None or v.get("value") is None:
            continue
        val = _num(v["value"])
        if not math.isfinite(val):
            continue
        if "temperature" in name.lower() or name == "dewpoint":
            if "degF" in uom:
                val = (val - 32) * 5 / 9
        hours = expand(v.get("validTime"))
        if not hours:
            continue
        per = val / len(hours) if name in ACCUMULATING else val
        out.extend((ms, per) for ms in hours)
    return out


def local_parts(ms, tz):
    """(local date, local hour) of an instant in the city's zone; UTC if the zone is unknown."""
    try:
        t = dt.datetime.fromtimestamp(ms / 1000, ZoneInfo(tz))
    except Exception:                                    # noqa: BLE001 - the n8n fallback
        t = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    return t.date().isoformat(), t.hour


def _pct_to_oktas(v):
    return None if v is None else (v / 100) * 8


def _mm_to_inches(v):
    return None if v is None else v / 25.4


def _kmh_to_knots(v):
    return None if v is None else v / 1.852


def _daytime(h):
    return 9 <= h <= 17


def gridpoint_rows(req, r, now_iso):
    """P1.4 'Build rows', for one city: (rows, partial days, ok)."""
    props = (r or {}).get("properties") or {}
    if not props.get("temperature"):
        return [], 0, False
    if props.get("gridId") and not req["grid"].startswith(props["gridId"]):
        raise ValueError(f"gridpoint {props['gridId']} came back for {req['grid']} ({req['city_key']})")
    run_at = props.get("updateTime") or now_iso
    by_day = {}
    for key, name in SERIES:
        for ms, val in series(props, name):
            date, hour = local_parts(ms, req["timezone"])
            d = by_day.setdefault(date, {"hours": set(), "vals": {}})
            d["hours"].add(hour)
            d["vals"].setdefault(key, []).append((hour, val))
    days = sorted(by_day)
    if not days:
        return [], 0, False
    today = dt.date.fromisoformat(days[0])
    rows, partial = [], 0
    for date in days:
        d = by_day[date]
        if not _covers_peak(d["hours"]):
            partial += 1
            continue
        lead = (dt.date.fromisoformat(date) - today).days
        if lead < 0 or lead > HORIZON_DAYS:
            continue

        def at(k, hour):
            lst = d["vals"].get(k) or []
            if not lst:
                return None
            best = lst[0]
            for v in lst:
                if abs(v[0] - hour) < abs(best[0] - hour):
                    best = v
            return best[1]

        def agg(k, fn, keep=None):
            lst = d["vals"].get(k) or []
            if keep:
                lst = [v for v in lst if keep(v[0])]
            if not lst:
                return None
            vals = [v[1] for v in lst]
            if fn == "max":
                return max(vals)
            if fn == "min":
                return min(vals)
            s = 0
            for v in vals:
                s += v
            return s if fn == "sum" else s / len(vals)

        speeds = [v for v in d["vals"].get("wind") or [] if _daytime(v[0])]
        dirs = {h: b for h, b in (d["vals"].get("wdir") or []) if _daytime(h)}
        su = sv = scalar = 0
        n = 0
        for h, spd in speeds:
            bearing = dirs.get(h)
            if bearing is None or spd is None:
                continue
            w = _kmh_to_knots(spd)
            rad = bearing * math.pi / 180
            su += -w * math.sin(rad)
            sv += -w * math.cos(rad)
            scalar += w
            n += 1
        wind_u, wind_v = (None, None) if n < 3 or scalar <= 0 else (js_round(su / scalar, 4), js_round(sv / scalar, 4))

        m_temp, m_dew = at("temp", MORNING_HOUR), at("dew", MORNING_HOUR)
        rows.append({
            "city_key": req["city_key"], "for_date": date, "run_at": run_at,
            "source": "api.weather.gov", "lead_days": lead,
            "forecast_max_c": js_round(agg("temp", "max"), 2),
            "forecast_min_c": js_round(agg("temp", "min"), 2),
            "apparent_max_c": js_round(agg("app", "max"), 2),
            "morning_temp_c": js_round(m_temp, 2),
            "morning_dewpoint_c": js_round(m_dew, 2),
            "dewpoint_depression_c": js_round(m_temp - m_dew, 2) if m_temp is not None and m_dew is not None else None,
            "morning_humidity": js_round(at("rh", MORNING_HOUR), 1),
            "cloud_mean": js_round(_pct_to_oktas(agg("sky", "avg", _daytime)), 2),
            "cloud_max": js_round(_pct_to_oktas(agg("sky", "max", _daytime)), 2),
            "wind_mean": js_round(_kmh_to_knots(agg("wind", "avg", _daytime)), 2),
            "wind_max": js_round(_kmh_to_knots(agg("wind", "max", _daytime)), 2),
            "precip_total": js_round(_mm_to_inches(agg("qpf", "sum")), 4),
            "precip_probability": js_round(agg("pop", "max"), 0),
            "n_hours": len(d["hours"]),
            "wind_u_mean": wind_u,
            "wind_v_mean": wind_v,
        })
    return rows, partial, bool(rows)


# ---- the run -----------------------------------------------------------------

def fetch(url, get=None):
    """The JSON body (an error body included, with its HTTP status as _http), or {'error': ...}."""
    get = get or requests.get          # looked up per call, so a test's patch holds
    try:
        r = get(url, headers=UA, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        return {"error": str(e)[:300]}
    try:
        body = r.json()
    except ValueError:
        return {"error": f"HTTP {r.status_code}, unparseable body: {r.text[:200]}", "_http": r.status_code}
    if isinstance(body, dict):
        body.setdefault("_http", r.status_code)
        return body
    return {"error": f"HTTP {r.status_code}, a {type(body).__name__} body", "_http": r.status_code}


def _fetch_all(urls, get):
    with ThreadPoolExecutor(WORKERS) as ex:
        return list(ex.map(lambda u: fetch(u, get), urls))


def mode_of(job):
    r = rest("settings", [("select", "value"), ("key", "eq.workflow_schedules")])
    return ((r[0].get("value") or {}).get(job) or {}).get("mode", "auto") if r else "auto"


def load_cities():
    """Active cities with their NWS ids. P1.3 skips those without coordinates."""
    return get_cities(require_coords=False,
                      extra=("nws_supported", "nws_grid_wfo", "nws_grid_x", "nws_grid_y"))


def save_support(support):
    """One PATCH per city (P1.3 'Split NWS ids' / 'Save NWS ids'): never an insert."""
    keys = ("nws_supported", "nws_station_id", "nws_grid_wfo", "nws_grid_x", "nws_grid_y", "nws_checked_at")
    failed = []
    for s in support:
        r = requests.patch(f"{_cfg()['url']}/rest/v1/cities", params={"city_key": f"eq.{s['city_key']}"},
                           headers={**_headers(), "Prefer": "return=minimal"},
                           json={k: s.get(k) for k in keys}, timeout=30)
        if r.status_code >= 400:
            failed.append(f"{s['city_key']}: HTTP {r.status_code} {r.text[:120]}")
    return failed


def run_forecast(cities, now_iso, get=None):
    """P1.3 end to end. Returns the log detail."""
    reqs = point_requests(cities)
    points = _fetch_all([q["point_url"] for q in reqs], get)
    hreqs, support, not_us, failed, errors = [], [], 0, 0, []
    for q, p in zip(reqs, points):
        h, s, outcome = forecast_request(q, p, now_iso)
        if s:
            support.append(s)
        if outcome == "not_us":
            not_us += 1
        elif outcome == "failed":
            failed += 1
            errors.append(f"{q['city_key']} points: {str(p.get('error') or p.get('title') or p.get('_http'))[:120]}")
        else:
            hreqs.append(h)
    bodies = _fetch_all([h["hourly_url"] for h in hreqs], get)
    rows, ok, partial = [], 0, 0
    for h, b in zip(hreqs, bodies):
        try:
            got, part, good = hourly_rows(h, b, now_iso)
        except ValueError as e:
            got, part, good = [], 0, False
            errors.append(str(e)[:160])
        rows += got
        partial += part
        if good:
            ok += 1
        else:
            failed += 1
    written = upsert_replace("weather_forecasts", rows, "city_key,model,run_at,for_date") if rows else 0
    errors += save_support(support)
    summary = (f"AD4 P1.3: {len(rows)} NWS forecast day(s) for {ok} cities"
               + (f", {not_us} not US locations" if not_us else "")
               + (f", {failed} failed" if failed else "")
               + (f", {partial} partial day(s) skipped" if partial else "") + ".")
    status = "error" if not rows else ("attention" if failed or errors else "ok")
    return {"summary": summary, "status": status, "rows": written or len(rows), "cities_ok": ok,
            "cities_failed": failed, "partial_days": partial, "not_us": not_us, "requested": len(reqs),
            "errors": errors[:5], "trigger": "tick"}


def grid_requests(cities):
    """P1.4 'Build requests'."""
    out = []
    for c in cities:
        if not c or not c.get("city_key") or c.get("nws_supported") is False:
            continue
        if not c.get("nws_grid_wfo") or c.get("nws_grid_x") is None or c.get("nws_grid_y") is None:
            continue
        grid = f"{c['nws_grid_wfo']}/{c['nws_grid_x']},{c['nws_grid_y']}"
        out.append({"city_key": c["city_key"], "timezone": c.get("timezone") or "UTC", "grid": grid,
                    "url": f"https://api.weather.gov/gridpoints/{grid}"})
    return out


def run_gridpoint(cities, now_iso, get=None):
    """P1.4 end to end. Returns the log detail."""
    reqs = grid_requests(cities)
    bodies = _fetch_all([q["url"] for q in reqs], get)
    rows, ok, failed, partial, errors = [], 0, 0, 0, []
    for q, b in zip(reqs, bodies):
        try:
            got, part, good = gridpoint_rows(q, b, now_iso)
        except ValueError as e:
            got, part, good = [], 0, False
            errors.append(str(e)[:160])
        if not good and b.get("error"):
            errors.append(f"{q['city_key']}: {str(b['error'])[:120]}")
        rows += got
        partial += part
        if good:
            ok += 1
        else:
            failed += 1
    written = upsert_replace("weather_forecast_features", rows, "city_key,for_date,run_at") if rows else 0
    summary = (f"AD4 P1.4: {len(rows)} forecast-condition day(s) for {ok} cities"
               + (f", {failed} failed" if failed else "")
               + (f", {partial} partial day(s) skipped" if partial else "") + ".")
    status = "error" if not rows else ("attention" if failed or errors else "ok")
    return {"summary": summary, "status": status, "rows": written or len(rows), "cities_ok": ok,
            "cities_failed": failed, "partial_days": partial, "requested": len(reqs),
            "errors": errors[:5], "trigger": "tick"}


def main(now=None, force=False):
    now = now or dt.datetime.now(dt.timezone.utc)
    if not force and now.hour not in HOURS_UTC:
        print(f"NWS runs at {HOURS_UTC} UTC; {now.hour:02d}Z is not one of them")
        return {}
    now_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    out = {}
    for job, fn in (("P1.3_nws_forecast", run_forecast), ("P1.4_nws_gridpoint", run_gridpoint)):
        started = time.monotonic()
        mode = mode_of(job)
        if mode != "auto":
            d = {"summary": f"{job} is {mode} on the Workflows page - not run", "status": "skipped", "rows": 0}
        else:
            try:
                # P1.4 reads the grid ids P1.3 has just saved, as it did four
                # minutes after P1.3 in n8n.
                d = fn(load_cities(), now_iso)
            except Exception as e:                   # noqa: BLE001 - logged, never silent
                d = {"summary": f"{job} failed: {str(e)[:300]}", "status": "error", "rows": 0}
        d["seconds"] = round(time.monotonic() - started, 1)
        log_run(job, d["status"], d.get("rows", 0), {k: v for k, v in d.items() if k not in ("status", "rows")})
        print(d["summary"], f"({d['seconds']} s)")
        out[job] = d
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="run whatever the hour")
    res = main(force=ap.parse_args().force)
    # The tick must not turn red for a weather feed: only a run where both
    # jobs wrote nothing fails the step.
    sys.exit(1 if res and all(d["status"] == "error" for d in res.values()) else 0)
