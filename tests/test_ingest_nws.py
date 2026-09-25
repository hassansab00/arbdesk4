"""NWS forecasts in the tick (replaces n8n P1.3 and P1.4, plan v2 P6.2).

THE PORT IS HELD TO THE ORIGINAL BY RUNNING THE ORIGINAL. The n8n templates'
own Code nodes run under Node (tests/n8n/harness.mjs) on real NWS responses
saved 25 Sep - four US cities in four time zones and a real non-US 404 - and
ingest_nws.py runs on the same responses. The rows must be identical, compared
as JSON text: every value, every rounding, int against float.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile

import pytest

import ingest_nws as nws

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "n8n", "harness.mjs")
NODE = shutil.which("node")
FX = json.load(open(os.path.join(ROOT, "tests", "fixtures", "nws_25sep.json")))
NOW_ISO = "2026-09-25T09:36:00.000Z"


def _config(template):
    wf = json.load(open(os.path.join(ROOT, "n8n", template)))
    node = next(n for n in wf["nodes"] if n["name"] == "Config")
    return {a["name"]: a["value"] for a in node["parameters"]["assignments"]["assignments"]}


def _run_n8n(template, seed, steps):
    plan = {"seed": seed, "run": [{"node": s, "input": None} for s in steps]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(plan, f)
    try:
        out = subprocess.run([NODE, HARNESS, os.path.join(ROOT, "n8n", template), f.name],
                             capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["ok"], r
    return r["outputs"]


def _same(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _py_forecast():
    reqs = nws.point_requests(FX["cities"])
    hreqs, support = [], []
    for q in reqs:
        h, s, _ = nws.forecast_request(q, FX["points"][q["city_key"]], NOW_ISO)
        support += [s] if s else []
        hreqs += [h] if h else []
    rows = []
    for h in hreqs:
        rows += nws.hourly_rows(h, FX["hourly"][h["city_key"]], NOW_ISO)[0]
    return reqs, hreqs, support, rows


# ---- equivalence with the n8n Code nodes ------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_p13_rows_are_the_n8n_rows():
    reqs, hreqs, support, rows = _py_forecast()
    order = [c["city_key"] for c in FX["cities"]]
    got = _run_n8n("P1.3_nws_forecast.template.json", {
        "Config": _config("P1.3_nws_forecast.template.json"),
        "Load cities": FX["cities"], "Check schedule": {},
        "Fetch point": [FX["points"][k] for k in order],
        "Fetch hourly forecast": [FX["hourly"][h["city_key"]] for h in hreqs],
    }, ["Build requests", "Build forecast urls", "Build rows"])
    assert [r["point_url"] for r in got["Build requests"]] == [q["point_url"] for q in reqs]
    assert _same([{k: v for k, v in r.items() if not k.startswith("_")} for r in got["Build forecast urls"]], hreqs)
    built = got["Build rows"][0]
    assert len(rows) > 20 and _same(built["forecasts"], rows)
    strip = lambda s: {k: v for k, v in s.items() if k != "nws_checked_at"}     # the clock time
    assert _same([strip(s) for s in built["support"]], [strip(s) for s in support])
    assert built["not_us"] == 1 and any(s["nws_supported"] is False for s in support)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_p14_rows_are_the_n8n_rows():
    cities = [c for c in FX["cities"] if c["nws_grid_wfo"]]      # P1.4's query: nws_grid_wfo not null
    reqs = nws.grid_requests(cities)
    rows = []
    for q in reqs:
        rows += nws.gridpoint_rows(q, FX["grid"][q["city_key"]], NOW_ISO)[0]
    got = _run_n8n("P1.4_nws_gridpoint.template.json", {
        "Config": _config("P1.4_nws_gridpoint.template.json"),
        "Load cities": cities, "Check schedule": {},
        "Fetch gridpoint": [FX["grid"][q["city_key"]] for q in reqs],
    }, ["Build requests", "Build rows"])
    built = got["Build rows"][0]
    assert len(rows) > 20 and _same(built["rows"], rows)
    assert all(r["wind_u_mean"] is not None for r in rows[:4])      # the resultant is exercised


# ---- the JavaScript number rules the port depends on ------------------------

@pytest.mark.parametrize("x,n,want", [
    (0.125, 2, 0.13),          # an exact binary tie: toFixed rounds up, round() to even
    (-0.125, 2, -0.13),        # half away from zero, on the magnitude
    (2.675, 2, 2.67),          # 2.67499999... in binary: no tie, down
    (22.0, 2, 22),             # integral values are ints, as JSON.stringify writes them
    (-0.001, 2, 0),            # -0 is written 0
    (None, 2, None),
])
def test_js_rounding(x, n, want):
    got = nws.js_round(x, n)
    assert got == want and type(got) is type(want)


def test_an_interval_expands_to_its_hours_and_rain_is_spread_across_them():
    assert len(nws.expand("2026-09-25T06:00:00+00:00/PT6H")) == 6
    assert len(nws.expand("2026-09-25T06:00:00+00:00/P1DT2H")) == 26
    assert len(nws.expand("2026-09-25T06:00:00+00:00/PT1H30M")) == 2
    assert nws.expand("not a time/PT1H") == []
    props = {"quantitativePrecipitation": {"uom": "wmoUnit:mm", "values": [
        {"validTime": "2026-09-25T06:00:00+00:00/PT6H", "value": 12.0}]}}
    pts = nws.series(props, "quantitativePrecipitation")
    assert len(pts) == 6 and sum(v for _, v in pts) == pytest.approx(12.0)


def test_fahrenheit_is_converted_wherever_the_server_says_so():
    props = {"temperature": {"uom": "wmoUnit:degF", "values": [
        {"validTime": "2026-09-25T06:00:00+00:00/PT1H", "value": 212}]}}
    assert nws.series(props, "temperature")[0][1] == pytest.approx(100.0)
    rows, _, _ = nws.hourly_rows({"city_key": "x", "grid_wfo": None}, {"properties": {
        "updateTime": "t", "periods": [{"startTime": f"2026-09-25T{h:02d}:00:00-04:00", "temperature": 50 + h,
                                        "temperatureUnit": "F"} for h in range(24)]}}, NOW_ISO)
    assert rows[0]["forecast_max_c"] == nws.js_round((73 - 32) * 5 / 9, 2)


def test_a_day_missing_its_peak_hours_is_not_written():
    periods = [{"startTime": f"2026-09-25T{h:02d}:00:00-04:00", "temperature": 20, "temperatureUnit": "C"}
               for h in range(19, 24)] + \
              [{"startTime": f"2026-09-26T{h:02d}:00:00-04:00", "temperature": 21, "temperatureUnit": "C"}
               for h in range(24)]
    rows, partial, ok = nws.hourly_rows({"city_key": "x", "grid_wfo": None},
                                        {"properties": {"updateTime": "t", "periods": periods}}, NOW_ISO)
    assert ok and partial == 1 and [r["for_date"] for r in rows] == ["2026-09-26"] and rows[0]["lead_days"] == 1


def test_a_response_for_another_grid_fails_that_city_only():
    with pytest.raises(ValueError):
        nws.hourly_rows({"city_key": "nyc", "grid_wfo": "OKX"},
                        {"properties": {"gridId": "LOT", "periods": [{"startTime": "x"}]}}, NOW_ISO)


# ---- the run -------------------------------------------------------------------

class _R:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self.body


def _wire(monkeypatch, modes=None):
    logged, written, patched = [], {}, []
    monkeypatch.setattr(nws, "mode_of", lambda job: (modes or {}).get(job, "auto"))
    monkeypatch.setattr(nws, "load_cities", lambda: FX["cities"])
    monkeypatch.setattr(nws, "upsert_replace", lambda t, rows, key: written.setdefault(t, rows) and len(rows))
    monkeypatch.setattr(nws, "save_support", lambda s: patched.extend(s) or [])
    monkeypatch.setattr(nws, "log_run", lambda job, status, rows, detail: logged.append((job, status, rows, detail)))

    def get(url, headers, timeout):
        for k, c in ((c["city_key"], c) for c in FX["cities"]):
            if url.startswith("https://api.weather.gov/points/") and url == nws.point_requests([c])[0]["point_url"]:
                p = FX["points"][k]
                return _R(p, p.get("status", 200))
            if c["nws_grid_wfo"] and f"/gridpoints/{c['nws_grid_wfo']}/{c['nws_grid_x']},{c['nws_grid_y']}" in url:
                return _R(FX["hourly"][k] if "/forecast/hourly" in url else FX["grid"][k])
        raise AssertionError(url)
    monkeypatch.setattr(nws.requests, "get", get)
    return logged, written, patched


def test_only_the_nws_hours_run():
    assert nws.main(now=dt.datetime(2026, 9, 25, 10, 36, tzinfo=dt.timezone.utc)) == {}


def test_a_run_writes_both_tables_and_logs_both_jobs(monkeypatch):
    logged, written, patched = _wire(monkeypatch)
    out = nws.main(now=dt.datetime(2026, 9, 25, 9, 36, tzinfo=dt.timezone.utc))
    assert [j for j, *_ in logged] == ["P1.3_nws_forecast", "P1.4_nws_gridpoint"]
    assert out["P1.3_nws_forecast"]["status"] == "ok" and out["P1.4_nws_gridpoint"]["status"] == "ok"
    assert len(written["weather_forecasts"]) == len(_py_forecast()[3])
    assert {p["city_key"] for p in patched} == {c["city_key"] for c in FX["cities"]}
    assert logged[0][3]["not_us"] == 1 and logged[0][3]["trigger"] == "tick"


def test_the_workflows_page_switch_is_obeyed(monkeypatch):
    logged, written, _ = _wire(monkeypatch, {"P1.3_nws_forecast": "off"})
    nws.main(now=dt.datetime(2026, 9, 25, 9, 36, tzinfo=dt.timezone.utc))
    assert logged[0][1] == "skipped" and "weather_forecasts" not in written
    assert logged[1][1] == "ok"


def test_a_run_that_writes_nothing_is_an_error_not_a_silence(monkeypatch):
    logged, _, _ = _wire(monkeypatch)
    monkeypatch.setattr(nws.requests, "get", lambda url, headers, timeout: _R({"title": "Service Unavailable"}, 503))
    out = nws.main(now=dt.datetime(2026, 9, 25, 9, 36, tzinfo=dt.timezone.utc))
    assert out["P1.3_nws_forecast"]["status"] == "error" and out["P1.4_nws_gridpoint"]["status"] == "error"
    assert [s for _, s, *_ in logged] == ["error", "error"]
