"""The IEM feed runs in n8n now, and its JavaScript is tested like code.

WHY IT MOVED. `.github/workflows/observations.yml` asked for `7 */6 * * *`.
What GitHub delivered, over the six scheduled runs before the move:

    11:11 -> 15:57   4h46m
    15:57 -> 20:43   4h46m
    20:43 -> 04:44   8h01m
    04:44 -> 12:39   7h55m
    12:39 -> (none)  8h24m and counting

Not one of them failed. GitHub documents scheduled workflows as best-effort and
drops them under load, so a six-hour cron delivered between 4h45m and 8h24m -
while P1.2, P1.3, P1.4 and P1.5, every other weather feed, were already in n8n
and fresh to the hour.

Porting the parser to JavaScript re-opens two bugs that were expensive to find
the first time, so both are pinned here:

  * `day2` ON THE ASOS SERVICE IS EXCLUSIVE. Asking for [start, today) returns
    every day but the one being traded, and it self-heals overnight, which is
    why it survived three weeks of checks.
  * skyc1 IS A CODE AND cloud_cover IS NUMERIC. Writing "FEW" through is what
    made every run die with `invalid input syntax for type numeric: "FEW"`.

The workflow JSON is the artefact that actually runs, so these tests execute
the jsCode OUT OF IT rather than a copy - a test against a copy would have
passed while the deployed node was wrong.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "n8n/P1.6_iem_observations.template.json"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _js(node_name):
    doc = json.loads(TEMPLATE.read_text())
    for n in doc["nodes"]:
        if n["name"] == node_name:
            return n["parameters"]["jsCode"]
    raise AssertionError(f"{node_name} is not in {TEMPLATE.name}")


def _run(node_name, context, now_iso=None):
    """Execute a workflow Code node under a minimal n8n shim."""
    harness = """
// N8N'S CODE NODE IS NOT A PLAIN NODE PROCESS, and the difference shipped a
// broken workflow: `new URLSearchParams(...)` ran green here and died on the
// first real execution with `URLSearchParams is not defined [line 34]`. A
// harness that offers more than the runtime does is a harness that certifies
// code the runtime will reject, so these are withheld here too.
for (const missing of ['URLSearchParams', 'URL', 'fetch', 'require', 'process']) {
  try { delete globalThis[missing]; } catch (e) { /* non-configurable, leave it */ }
}
const CTX = %s;
const NOW = %s;
if (NOW) {
  const Fixed = new Date(NOW).getTime();
  const Real = Date;
  globalThis.Date = class extends Real {
    constructor(...a) { return a.length ? new Real(...a) : new Real(Fixed); }
    static now() { return Fixed; }
  };
}
function $(name) {
  const items = (CTX[name] || []).map(j => ({ json: j }));
  return { all: () => items, first: () => items[0], isExecuted: true };
}
const $input = { all: () => (CTX.__input || []).map(j => ({ json: j })),
                 first: () => ({ json: (CTX.__input || [])[0] }) };
const $execution = { mode: 'test', id: '1' };
const out = (function () { %s })();
console.log(JSON.stringify(out.map(i => i.json)));
""" % (json.dumps(context), json.dumps(now_iso), _js(node_name))
    p = subprocess.run([NODE, "-e", harness], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip())
    return json.loads(p.stdout)


CSV = (ROOT / "tests/fixtures/iem_asos_sample.csv").read_text


def _fetch(csv_text):
    return [{"csv": csv_text}]


def _req(city="austin", icao="KAUS"):
    return [{"city_key": city, "icao": icao, "url": "https://example.invalid/x"}]


# ---------------------------------------------------------------- the window

def _requests(days_back="1", now="2026-09-21T13:00:00Z"):
    return _run("Build requests", {
        "Config": [{"supabase_url": "", "days_back": days_back, "max_cities_per_run": "0"}],
        "Load cities": [{"city_key": "austin", "icao": "KAUS"}],
        "Check schedule": [{"run": True}],
    }, now_iso=now)


def test_the_window_includes_today_because_day2_is_exclusive():
    url = _requests()[0]["url"]
    # today is the 21st, so the exclusive end must be the 22nd - not the 21st,
    # which is what returned every day but the one being traded.
    assert "year2=2026" in url and "month2=9" in url and "day2=22" in url, url


def test_the_window_starts_days_back_from_today():
    url = _requests(days_back="2")[0]["url"]
    assert "day1=19" in url and "month1=9" in url, url


def test_the_window_crosses_a_month_boundary():
    url = _requests(days_back="1", now="2026-10-01T02:00:00Z")[0]["url"]
    assert "month1=9" in url and "day1=30" in url, url
    assert "month2=10" in url and "day2=2" in url, url


def test_it_asks_iem_for_pressure():
    # weather_observations.pressure_hpa went unwritten for 142,529 rows because
    # the request never named a pressure field. mslp, not alti: sea-level is the
    # only one comparable from Singapore at 5 m to Mexico City at 2,240 m.
    url = _requests()[0]["url"]
    assert "mslp" in url and "alti" not in url, url


def test_a_city_without_a_station_is_not_requested():
    out = _run("Build requests", {
        "Config": [{"supabase_url": "", "days_back": "1", "max_cities_per_run": "0"}],
        "Load cities": [{"city_key": "austin", "icao": "KAUS"},
                        {"city_key": "nowhere", "icao": None}],
        "Check schedule": [{"run": True}],
    }, now_iso="2026-09-21T13:00:00Z")
    assert [o["city_key"] for o in out] == ["austin"]


# ----------------------------------------------------------------- the parse

def _rows(csv_text=None, reqs=None, fetched=None):
    out = _run("Build rows", {
        "Build requests": reqs or _req(),
        "Fetch observations": fetched or _fetch(csv_text if csv_text is not None else CSV()),
    })
    return [r for chunk in out for r in chunk["observations"]], out[0]


def test_it_parses_a_real_shaped_response():
    rows, totals = _rows()
    assert totals["requested"] == 1 and totals["failed"] == 0
    assert len(rows) == 4, [r["valid_at"] for r in rows]
    assert all(r["source"] == "IEM" for r in rows)
    assert all(r["city_key"] == "austin" for r in rows)


def test_fahrenheit_becomes_celsius():
    rows, _ = _rows()
    first = rows[0]
    assert first["temp_f"] == 78.8
    assert first["temp_c"] == 26.0, first["temp_c"]


def test_the_sky_code_becomes_oktas_and_never_reaches_a_numeric_column():
    # `invalid input syntax for type numeric: "FEW"` killed every run once.
    got = [r["cloud_cover"] for r in _rows()[0]]
    assert got == [2, 6, 8, None], got


def test_pressure_is_carried_through():
    assert [r["pressure_hpa"] for r in _rows()[0]] == [1013.5, 1012.8, None, 1011.2]


def test_a_reading_with_no_temperature_is_not_a_reading():
    # The fixture's fifth line has an empty tmpf. It must not become a row with
    # a null temperature, which would drag a daily maximum down.
    assert all(r["temp_f"] is not None for r in _rows()[0])


def test_the_timestamp_becomes_an_iso_instant_in_utc():
    assert _rows()[0][0]["valid_at"] == "2026-09-20T00:53:00Z"


def test_empty_fields_become_null_not_zero():
    # missing=empty and trace=empty mean a blank is "not measured". Zero is a
    # measurement, and a zero dewpoint or wind would be read as one.
    third = _rows()[0][2]
    assert third["dewpoint_c"] is None and third["precip"] is None


def test_a_station_that_returned_nothing_is_counted_as_failed():
    rows, totals = _rows(csv_text="station,valid,tmpf\n")
    assert rows == [] and totals["failed"] == 1
    assert totals["failed_stations"] == ["KAUS"]


def test_responses_are_never_paired_with_the_wrong_city():
    # n8n preserves item order, but a silent misalignment would file one city's
    # readings under another's name - worse than failing.
    with pytest.raises(RuntimeError, match="refusing to pair them by index"):
        _rows(reqs=_req() + _req("dallas", "KDAL"), fetched=_fetch(CSV()))


def test_the_write_is_chunked():
    # One POST of every station's two days is a body Supabase refuses outright,
    # and a refused write walks past a continue-on-error node looking healthy.
    head, body = CSV().split("\n", 1)
    big = head + "\n" + (body.strip().split("\n")[0] + "\n") * 1200
    out = _run("Build rows", {"Build requests": _req(), "Fetch observations": _fetch(big)})
    assert len(out) == 3, [len(c["observations"]) for c in out]
    assert [len(c["observations"]) for c in out] == [500, 500, 200]
    # Every chunk carries the run's totals, so Summary reading first() is right.
    assert {c["n_obs"] for c in out} == {1200}


# --------------------------------------------------------------- the fetch

def _fetch_node():
    doc = json.loads(TEMPLATE.read_text())
    for n in doc["nodes"]:
        if n["name"] == "Fetch observations":
            return n
    raise AssertionError("Fetch observations is gone")


def test_stations_are_fetched_one_at_a_time():
    """IEM RATE-LIMITS BY IP, and answers a burst in plain words:

        Too many requests from your IP address, slow down.

    Measured against the live service while porting this:

        12 concurrent   44 of 48 stations returned nothing
         4 concurrent   42 of 48 returned nothing
         1 at a time    the whole board

    Both bursts "succeeded" - they wrote the handful that got through and
    logged partial - so the damage is a board that silently loses most of its
    cities, which is exactly the failure this move was meant to end. The
    Python job is sequential for the same reason and covers 48 stations in
    about 115 seconds, inside n8n's 300 second execution cap, so there is
    nothing to buy by widening it.
    """
    batch = _fetch_node()["parameters"]["options"]["batching"]["batch"]
    assert batch["batchSize"] == 1, (
        f"stations are fetched {batch['batchSize']} at a time; IEM answers that with "
        "'Too many requests from your IP address, slow down.' and the board loses cities"
    )
    assert batch["batchInterval"] >= 200, (
        "no gap between requests is the same burst with extra steps"
    )


def test_the_fetch_carries_no_supabase_credential():
    # It goes to IEM. A Supabase credential on it would send the service key
    # to a third party; scripts/validate_n8n_workflows.py checks the same
    # thing across every file, and this states it for the node that is most
    # obviously at risk of acquiring one by copy-paste.
    assert "credentials" not in _fetch_node()
