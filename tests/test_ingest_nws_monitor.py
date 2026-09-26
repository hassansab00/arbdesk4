"""NWS station readings, alerts and solar transit in the tick (replaces n8n
P1.2, plan v2 P6.2).

HELD TO THE ORIGINAL BY RUNNING THE ORIGINAL. The P1.2 template's own Code
nodes ('Build requests', 'Build rows', 'Guard', 'Stamp live source') run under
Node (tests/n8n/harness.mjs) and ingest_nws_monitor runs on the same NWS
responses, saved 26 Sep: four US stations in four time zones and London's real
answer (an empty FeatureCollection). The rows must match as JSON text.

No heat alert was active on 26 Sep (the live alerts were a Coastal Flood
Warning, a Wind Advisory and a Beach Hazards Statement, which the filter
drops), so a SYNTHETIC variant adds heat alerts to exercise the event path:
one that changed from live_weather's prior alert (raises an event) and one
that did not (raises none).
"""
import copy
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile

import pytest

import ingest_nws_monitor as m

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "n8n", "harness.mjs")
TEMPLATE = "P1.2_nws_monitor.template.json"
NODE = shutil.which("node")
FX = json.load(open(os.path.join(ROOT, "tests", "fixtures", "nws_monitor_26sep.json")))
NOW = dt.datetime(2026, 9, 26, 16, 36, tzinfo=dt.timezone.utc)
NOW_ISO = "2026-09-26T16:36:00.000Z"
CLOCK_FIELDS = ("updated_at", "nws_checked_at")        # new Date() in n8n


def _config():
    wf = json.load(open(os.path.join(ROOT, "n8n", TEMPLATE)))
    node = next(n for n in wf["nodes"] if n["name"] == "Config")
    return {a["name"]: a["value"] for a in node["parameters"]["assignments"]["assignments"]}


def _strip(rows):
    return [{k: v for k, v in r.items() if k not in CLOCK_FIELDS} for r in rows]


def _same(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _n8n(fx, prior):
    cities = [dict(c, prior_alert=[{"nws_alert": prior[c["city_key"]]}] if c["city_key"] in prior else [])
              for c in fx["cities"]]
    order = [c["city_key"] for c in fx["cities"]]
    plan = {"seed": {"Config": _config(), "Load cities": cities, "Check schedule": {},
                     "Fetch observation": [fx["obs"][k] for k in order],
                     "Fetch alerts": [fx["alerts"][k] for k in order],
                     "Fetch point": [fx["point"][k] for k in order]},
            "run": [{"node": n, "input": None} for n in
                    ("Build requests", "Build rows", "Guard: did anything parse?", "Stamp live source")]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(plan, f)
    try:
        out = subprocess.run([NODE, HARNESS, os.path.join(ROOT, "n8n", TEMPLATE), f.name],
                             capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["ok"], r
    return r["outputs"]


def _port(fx, prior):
    reqs = m.requests_for(fx["cities"], prior, NOW)
    obs, live, events, support, outcomes = [], [], [], [], []
    for q in reqs:
        k = q["city_key"]
        outcome, rows, lv, ev, sup = m.city_rows(q, fx["obs"][k], fx["alerts"][k], fx["point"][k], NOW_ISO)
        outcomes.append(outcome)
        obs += rows
        live += [lv] if lv else []
        events += [ev] if ev else []
        support += [sup] if sup else []
    return reqs, obs, live, events, support, outcomes


def _compare(fx, prior):
    got = _n8n(fx, prior)
    reqs, obs, live, events, support, outcomes = _port(fx, prior)
    # Build requests: identical apart from the ?start= window, which n8n takes
    # from its own clock.
    drop = lambda r: {k: (v.split("?start=")[0] if k == "obs_url" else v) for k, v in r.items()}
    assert [drop(r) for r in got["Build requests"]] == [drop(r) for r in reqs]
    built = got["Build rows"][0]
    stamped = got["Stamp live source"][0]
    assert _same(built["observations"], obs)
    assert _same(_strip(stamped["liveRows"]), _strip(live))
    assert _same(built["events"], events)
    assert _same(_strip(built["support"]), _strip(support))
    assert (built["ok"], built["not_us"], built["failed"]) == (
        outcomes.count("ok"), outcomes.count("not_us"), outcomes.count("failed"))
    return obs, live, events


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_26_sep_responses_give_the_n8n_rows():
    obs, live, events = _compare(FX, {})
    assert len(obs) > 150 and len(live) == 4 and events == []
    assert all(r["source"] == "NWS" and r["source_kind"] == "station" for r in live)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_heat_alert_raises_an_event_only_when_it_changed():
    fx = copy.deepcopy(FX)
    heat = {"event": "Heat Advisory", "severity": "Moderate", "headline": "Heat Advisory (synthetic)",
            "id": "urn:synthetic:1"}
    fx["alerts"]["denver"]["features"].insert(0, {"properties": heat})      # new: raises
    fx["alerts"]["chicago"]["features"].insert(0, {"properties": heat})     # unchanged: does not
    fx["alerts"]["nyc"]["features"].insert(0, {"properties": dict(heat, event="Wind Advisory")})
    obs, live, events = _compare(fx, {"chicago": "Heat Advisory"})
    assert [e["city_key"] for e in events] == ["denver"] and events[0]["severity"] == "medium"
    assert {r["city_key"]: r["nws_alert"] for r in live}["nyc"] is None     # not a heat event


# ---- units the fixtures do not reach ----------------------------------------------

def test_units():
    assert m.deg_c({"value": 212, "unitCode": "wmoUnit:degF"}) == 100
    assert m.deg_c({"value": 300, "unitCode": "wmoUnit:K"}) == 26.85
    assert m.knots({"value": 10, "unitCode": "wmoUnit:m_s-1"}) == 19.438
    assert m.knots({"value": 10, "unitCode": "wmoUnit:furlong"}) is None
    assert m.inches({"value": 2.54, "unitCode": "wmoUnit:cm"}) == 1
    assert m.inches({"value": 1, "unitCode": "wmoUnit:m"}) == 39.3701
    assert m.oktas([{"amount": "FEW"}, {"amount": "BKN"}, {"amount": "XX"}]) == 6
    assert m.jt([]) and m.jt({}) and not m.jt("") and not m.jt(0)


def test_a_404_marks_the_city_not_us():
    q = m.requests_for([FX["cities"][0]], {}, NOW)[0]
    outcome, *_, sup = m.city_rows(q, {"status": 404, "title": "Not Found"}, {}, {}, NOW_ISO)
    assert outcome == "not_us" and sup["nws_supported"] is False


# ---- the run ------------------------------------------------------------------

class _R:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self.body


def _wire(monkeypatch, mode="auto", get=None):
    logged, written, patched = [], {}, []
    monkeypatch.setattr(m, "mode_of", lambda job: mode)
    monkeypatch.setattr(m, "get_cities", lambda **kw: FX["cities"])
    monkeypatch.setattr(m, "load_prior", lambda: {})
    monkeypatch.setattr(m, "upsert_replace", lambda t, rows, key: written.setdefault(t, rows) and len(rows))
    monkeypatch.setattr(m, "insert", lambda t, rows: written.setdefault(t, rows) and len(rows))
    monkeypatch.setattr(m, "save_support", lambda s: patched.extend(s) or [])
    monkeypatch.setattr(m, "log_run", lambda job, status, rows, detail: logged.append((job, status, rows, detail)))

    def fixture_get(url, headers, timeout):
        for c in FX["cities"]:
            k = c["city_key"]
            q = m.requests_for([c], {}, NOW)[0]
            if url.split("?start=")[0] == q["obs_url"].split("?start=")[0]:
                return _R(FX["obs"][k])
            if url == q["alerts_url"]:
                return _R(FX["alerts"][k])
            if url == q["point_url"]:
                return _R(FX["point"][k])
        raise AssertionError(url)
    monkeypatch.setattr(m._fetch_all.__globals__["requests"], "get", get or fixture_get)
    return logged, written, patched


def test_only_the_even_hours_run():
    assert m.main(now=dt.datetime(2026, 9, 26, 15, 36, tzinfo=dt.timezone.utc)) == {}


def test_a_run_writes_every_table_and_logs(monkeypatch):
    logged, written, patched = _wire(monkeypatch)
    d = m.main(now=NOW)
    assert d["status"] == "ok" and d["failed"] == 1                 # London: no readings
    assert set(written) == {"weather_observations", "live_weather"}   # no alert changed
    assert {p["city_key"] for p in patched} == {"nyc", "chicago", "denver", "los_angeles"}
    assert logged[0][0] == "P1.2_nws_monitor" and logged[0][3]["trigger"] == "tick"


def test_the_workflows_page_switch_is_obeyed(monkeypatch):
    logged, written, _ = _wire(monkeypatch, mode="manual")
    assert m.main(now=NOW)["status"] == "skipped" and written == {}


def test_no_readings_at_all_is_an_error(monkeypatch):
    logged, written, _ = _wire(monkeypatch, get=lambda url, headers, timeout: _R({"title": "Bad Gateway", "status": 502}, 502))
    assert m.main(now=NOW)["status"] == "error" and written == {} and logged[0][1] == "error"
