"""NWS station readings, heat alerts and solar transit, in the tick (replaces
n8n P1.2, plan v2 P6.2).

WHY. The last 12 n8n executions a day that the tick can do itself: P1.2 only
reads api.weather.gov and writes this database. Measured 26 Sep, the whole
AD4 n8n load was 61 executions a day against a share of 1,000-1,400 a month.

WHAT IS PORTED, AND HOW IT IS HELD TO THE ORIGINAL. The n8n Code nodes 'Build
requests', 'Build rows' and 'Stamp live source', line for line, with the
JavaScript number rules from ingest_nws (toFixed rounding, -0 as 0).
tests/test_ingest_nws_monitor.py runs the template's own Code nodes under Node
and this module on NWS responses saved 26 Sep and requires identical rows.

Per US city, three requests:
  /stations/{icao}/observations?start=now-30h&limit=60  the reading SERIES
  /alerts/active?point=lat,lon                          heat alerts
  /points/lat,lon                                       solar transit, grid id
and four writes, each exactly as the n8n nodes made them:
  weather_observations  every reading, source 'NWS', merge on
                        (city_key, valid_at, source), in METAR's units
  live_weather          the newest reading, merge on city_key, stamped
                        source 'NWS' / source_kind 'station'; merge updates only
                        the columns sent, so P1.5's other columns stay
  weather_events        kind 'nws_alert', only when the heat alert CHANGED from
                        the one live_weather last held
  cities                grid ids and support flag, one PATCH per city

DIFFERENCES FROM n8n, deliberate:
  * Responses pair with their city by key, not by position.
  * A run with no observation at all logs 'error' with the reason; n8n threw
    before its log node and left no row.
  * cities comes from common.get_cities (the one definition of active) and the
    prior alert from live_weather, where n8n embedded one in the other.
  * Cadence: n8n fired every 2 h at odd UTC hours (:42). This runs in the
    ticks of EVEN hours, so it never shares a tick with the NWS forecast step
    (03/09/15/21) and the job stays inside its one billed minute. The
    Workflows page's P1.2 mode is obeyed; should_run's interval is not.
"""
import argparse
import datetime as dt
import sys
import time
from urllib.parse import quote

from common import get_cities, log_run, rest, upsert_replace, insert
from ingest_nws import (_fetch_all, _num, js_fixed_str, js_round, mode_of,
                        save_support)

JOB = "P1.2_nws_monitor"
HOURS_UTC = tuple(range(0, 24, 2))
OBS_HOURS = 30
OBS_LIMIT = 60
ALERT_EVENTS = ("Excessive Heat Warning", "Heat Advisory", "Extreme Heat Warning",
                "Extreme Heat Watch", "Excessive Heat Watch")
OKTAS = {"SKC": 0, "CLR": 0, "NSC": 0, "NCD": 0, "FEW": 2, "SCT": 4, "BKN": 6, "OVC": 8, "VV": 8}
SEVERITY = {"Extreme": "critical", "Severe": "high", "Moderate": "medium"}


def jt(v):
    """JavaScript truthiness: an empty list or object is TRUE there, false in Python."""
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v == v and v != 0
    if isinstance(v, str):
        return v != ""
    return True


def _enc(s):
    """encodeURIComponent."""
    return quote(str(s), safe="-_.!~*'()")


def requests_for(cities, prior, now):
    """P1.2 'Build requests'. prior: city_key -> the nws_alert live_weather holds."""
    since = (now - dt.timedelta(hours=OBS_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for c in cities:
        if not c or not c.get("city_key"):
            continue
        if c.get("nws_supported") is False:
            continue
        if not c.get("icao") or c.get("latitude") is None or c.get("longitude") is None:
            continue
        lat = js_fixed_str(float(c["latitude"]), 4)
        lon = js_fixed_str(float(c["longitude"]), 4)
        out.append({
            "city_key": c["city_key"], "icao": c["icao"], "unit": c.get("unit") or "C",
            "prior_alert": prior.get(c["city_key"]),
            "obs_url": f"https://api.weather.gov/stations/{_enc(c['icao'])}/observations"
                       f"?start={_enc(since)}&limit={OBS_LIMIT}",
            "alerts_url": f"https://api.weather.gov/alerts/active?point={lat},{lon}",
            "point_url": f"https://api.weather.gov/points/{lat},{lon}",
        })
    return out


# ---- units: each exactly as the n8n node converts them -----------------------

def _qv(qv):
    """The finite value of a QuantitativeValue, or None."""
    if not isinstance(qv, dict) or qv.get("value") is None:
        return None
    v = _num(qv["value"])
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def deg_c(qv):
    v = _qv(qv)
    if v is None:
        return None
    unit = str(qv.get("unitCode") or "")
    if "degF" in unit:
        return js_round((v - 32) * 5 / 9, 4)
    if "K" in unit:
        return js_round(v - 273.15, 4)
    return js_round(v, 4)


def num(qv):
    return _qv(qv)


def knots(qv):
    v = _qv(qv)
    if v is None:
        return None
    u = str(qv.get("unitCode") or "")
    if "km_h" in u:
        return js_round(v / 1.852, 3)
    if "m_s" in u:
        return js_round(v * 1.9438445, 3)
    if "mi_h" in u:
        return js_round(v * 0.8689762, 3)
    if "knot" in u or "kt" in u:
        return js_round(v, 3)
    return None


def inches(qv):
    v = _qv(qv)
    if v is None:
        return None
    u = str(qv.get("unitCode") or "")
    if "mm" in u:
        return js_round(v / 25.4, 4)
    if "cm" in u:
        return js_round(v / 2.54, 4)
    if "m" in u and "mm" not in u and "cm" not in u:
        return js_round(v * 39.3701, 4)
    if "in" in u:
        return js_round(v, 4)
    return None


def oktas(layers):
    if not isinstance(layers, list) or not layers:
        return None
    worst = None
    for layer in layers:
        o = OKTAS.get(str((layer or {}).get("amount") or "").upper())
        if o is not None and (worst is None or o > worst):
            worst = o
    return worst


def _plain(v):
    """A number as JSON.stringify writes it: an integral float is an int."""
    return int(v) if isinstance(v, float) and v.is_integer() else v


def city_rows(req, o, a, p, now_iso):
    """P1.2 'Build rows' + 'Stamp live source' for one city.

    Returns (outcome, observations, live_row, event, support_row); outcome is
    'ok', 'not_us' or 'failed'.
    """
    o = o if isinstance(o, dict) else None
    missing = (not o) or jt(o.get("error")) or (not jt(o.get("properties")) and not jt(o.get("features"))
                                                 and not jt(o.get("status")))
    is_404 = bool(o) and (o.get("status") == 404 or "Not Found" in str(o.get("title") or ""))
    if is_404 or missing:
        if is_404:
            return "not_us", [], None, None, {
                "city_key": req["city_key"], "nws_supported": False, "nws_station_id": None,
                "nws_grid_wfo": None, "nws_grid_x": None, "nws_grid_y": None, "nws_checked_at": now_iso}
        return "failed", [], None, None, None

    feats = o["features"] if isinstance(o.get("features"), list) else (
        [{"properties": o["properties"]}] if jt(o.get("properties")) else [])
    readings = [f["properties"] for f in feats
                if isinstance(f, dict) and isinstance(f.get("properties"), dict)
                and jt(f["properties"].get("timestamp")) and deg_c(f["properties"].get("temperature")) is not None]
    readings.sort(key=lambda r: str(r["timestamp"]))
    if not readings:
        return "failed", [], None, None, None

    props = readings[-1]
    temp_c = deg_c(props.get("temperature"))
    pp = (p or {}).get("properties") or {} if isinstance(p, dict) else {}
    astro = pp.get("astronomicalData") or {}
    support = {"city_key": req["city_key"], "nws_supported": True, "nws_station_id": req["icao"],
               "nws_grid_wfo": pp.get("gridId"), "nws_grid_x": pp.get("gridX"),
               "nws_grid_y": pp.get("gridY"), "nws_checked_at": now_iso}

    observations = []
    for r in readings:
        t = deg_c(r.get("temperature"))
        observations.append({
            "city_key": req["city_key"], "station": req["icao"], "valid_at": r["timestamp"],
            "temp_c": t, "temp_f": None if t is None else js_round(t * 9 / 5 + 32, 2),
            "dewpoint_c": deg_c(r.get("dewpoint")), "humidity": _plain(num(r.get("relativeHumidity"))),
            "wind_speed": knots(r.get("windSpeed")), "wind_dir_deg": _plain(num(r.get("windDirection"))),
            "precip": inches(r.get("precipitationLastHour")), "cloud_cover": oktas(r.get("cloudLayers")),
            "source": "NWS",
        })

    feats_a = a["features"] if isinstance(a, dict) and isinstance(a.get("features"), list) else []
    headline = severity = event = alert_id = None
    for f in feats_a:
        ap = (f or {}).get("properties") or {}
        if ALERT_EVENTS and ap.get("event") not in ALERT_EVENTS:
            continue
        headline = ap.get("headline") if jt(ap.get("headline")) else (ap.get("event") if jt(ap.get("event")) else None)
        severity = ap.get("severity") if jt(ap.get("severity")) else None
        event = ap.get("event") if jt(ap.get("event")) else None
        alert_id = ap.get("id") if jt(ap.get("id")) else None
        break

    ev = None
    if jt(event) and event != req.get("prior_alert"):
        ev = {"city_key": req["city_key"], "kind": "nws_alert",
              "severity": SEVERITY.get(severity) or "low", "temp_c": temp_c,
              "detail": {"source": "api.weather.gov", "event": event, "headline": headline,
                         "nws_severity": severity, "nws_id": alert_id, "station": req["icao"]}}

    live = {
        "city_key": req["city_key"], "updated_at": now_iso, "observed_at": props["timestamp"],
        "temp_c": temp_c, "temp_f": None if temp_c is None else js_round(temp_c * 9 / 5 + 32, 2),
        "humidity": _plain(num(props.get("relativeHumidity"))), "wind_speed_kt": knots(props.get("windSpeed")),
        "wind_dir_deg": _plain(num(props.get("windDirection"))),
        "max_temp_24h_c": deg_c(props.get("maxTemperatureLast24Hours")),
        "solar_transit_at": astro.get("transit"), "sunrise_at": astro.get("sunrise"),
        "sunset_at": astro.get("sunset"), "nws_alert": event, "nws_alert_severity": severity,
        # 'Stamp live source': an instrument reading at the settlement ICAO.
        "obs_source": "NWS", "source": "NWS", "source_kind": "station",
    }
    return "ok", observations, live, ev, support


def load_prior():
    rows = rest("live_weather", [("select", "city_key,nws_alert"), ("limit", "1000")])
    return {r["city_key"]: r.get("nws_alert") for r in rows}


def run(now, now_iso, get=None):
    cities = get_cities(require_coords=False, require_icao=True, extra=("nws_supported",))
    reqs = requests_for(cities, load_prior(), now)
    obs = _fetch_all([q["obs_url"] for q in reqs], get)
    alerts = _fetch_all([q["alerts_url"] for q in reqs], get)
    points = _fetch_all([q["point_url"] for q in reqs], get)
    observations, live, events, support = [], [], [], []
    ok = not_us = failed = 0
    errors = []
    for q, o, a, p in zip(reqs, obs, alerts, points):
        outcome, rows, lv, ev, sup = city_rows(q, o, a, p, now_iso)
        if outcome == "ok":
            ok += 1
        elif outcome == "not_us":
            not_us += 1
        else:
            failed += 1
            errors.append(f"{q['city_key']}: {str((o or {}).get('error') or (o or {}).get('title') or 'no readings')[:120]}")
        observations += rows
        live += [lv] if lv else []
        events += [ev] if ev else []
        support += [sup] if sup else []
    if observations:
        upsert_replace("weather_observations", observations, "city_key,valid_at,source")
        upsert_replace("live_weather", live, "city_key")
        if events:
            insert("weather_events", events)
    errors += save_support(support)
    n = len(observations)
    summary = (f"AD4 P1.2: {n} NWS observations from {len(reqs)} cities"
               + (f", {not_us} not US stations" if not_us else "")
               + (f", {failed} failed" if failed else "")
               + (f". {len(events)} weather alert(s) raised" if events else ". no new alerts") + ".")
    status = "error" if not n else ("attention" if events else "ok")
    return {"summary": summary, "status": status, "rows": n, "requested": len(reqs),
            "not_us": not_us, "failed": failed, "alerts": len(events), "errors": errors[:5],
            "trigger": "tick"}


def main(now=None, force=False):
    now = now or dt.datetime.now(dt.timezone.utc)
    if not force and now.hour not in HOURS_UTC:
        print(f"P1.2 runs in the even-hour ticks; {now.hour:02d}Z is not one")
        return {}
    now_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    started = time.monotonic()
    mode = mode_of(JOB)
    if mode != "auto":
        d = {"summary": f"{JOB} is {mode} on the Workflows page - not run", "status": "skipped", "rows": 0}
    else:
        try:
            d = run(now, now_iso)
        except Exception as e:                        # noqa: BLE001 - logged, never silent
            d = {"summary": f"{JOB} failed: {str(e)[:300]}", "status": "error", "rows": 0}
    d["seconds"] = round(time.monotonic() - started, 1)
    log_run(JOB, d["status"], d.get("rows", 0), {k: v for k, v in d.items() if k not in ("status", "rows")})
    print(d["summary"], f"({d['seconds']} s)")
    return d


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="run whatever the hour")
    res = main(force=ap.parse_args().force)
    sys.exit(1 if res and res["status"] == "error" else 0)
