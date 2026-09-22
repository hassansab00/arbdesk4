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


def _req(stations=("KAUS",), by_station=None):
    """The single item Build requests now emits for the WHOLE board."""
    if by_station is None:
        by_station = {}
        for s in stations:
            by_station[s] = {"KAUS": "austin", "KDAL": "dallas"}.get(s, s.lower())
            if len(s) == 4 and s.startswith("K"):
                by_station[s[1:]] = by_station[s]
    return [{"url": "https://example.invalid/x", "stations": list(stations),
             "by_station": by_station, "requested": len(stations),
             "hours_back": 6, "since": "2026-09-20T00:00Z",
             "until": "2026-09-20T06:00Z"}]


# ---------------------------------------------------------------- the window

def _requests(hours_back="6", now="2026-09-21T13:00:00Z", cities=None):
    return _run("Build requests", {
        "Config": [{"supabase_url": "", "hours_back": hours_back,
                    "max_cities_per_run": "0"}],
        "Load cities": cities or [{"city_key": "austin", "icao": "KAUS"}],
        "Check schedule": [{"run": True}],
    }, now_iso=now)


def test_the_window_includes_today_because_day2_is_exclusive():
    url = _requests()[0]["url"]
    # today is the 21st, so the exclusive end must be the 22nd - not the 21st,
    # which is what returned every day but the one being traded.
    assert "year2=2026" in url and "month2=9" in url and "day2=22" in url, url


def test_the_window_starts_hours_back_from_now():
    """Hourly, over hours. A day-granular window means an hourly run refetches
    two whole days to gain 48 readings; sts/ets bound it to an instant, which
    is 10 KB against 138 KB - measured on the live service."""
    url = _requests(hours_back="6", now="2026-09-21T13:00:00Z")[0]["url"]
    assert "sts=2026-09-21T07%3A00Z" in url, url
    assert "ets=2026-09-21T13%3A00Z" in url, url


def test_the_window_crosses_a_month_boundary():
    url = _requests(hours_back="6", now="2026-10-01T02:00:00Z")[0]["url"]
    assert "month1=9" in url and "day1=30" in url, url
    assert "month2=10" in url and "day2=2" in url, url
    assert "sts=2026-09-30T20%3A00Z" in url, url


def test_it_asks_iem_for_pressure():
    # weather_observations.pressure_hpa went unwritten for 142,529 rows because
    # the request never named a pressure field. mslp, not alti: sea-level is the
    # only one comparable from Singapore at 5 m to Mexico City at 2,240 m.
    url = _requests()[0]["url"]
    assert "mslp" in url and "alti" not in url, url


def test_a_city_without_a_station_is_not_requested():
    out = _requests(cities=[{"city_key": "austin", "icao": "KAUS"},
                            {"city_key": "nowhere", "icao": None}])
    assert out[0]["stations"] == ["KAUS"]
    assert "nowhere" not in out[0]["by_station"].values()


def test_the_whole_board_goes_out_in_one_request():
    """THE FIX, STATED AS AN INVARIANT. IEM rate-limits by REQUEST per IP:
    48 requests lost 34 to 44 of 48 stations from n8n's shared egress even at
    one at a time, and one request carrying all 48 lost none."""
    out = _requests(cities=[{"city_key": "austin", "icao": "KAUS"},
                            {"city_key": "london", "icao": "EGLC"},
                            {"city_key": "beijing", "icao": "ZBAA"}])
    assert len(out) == 1, "one item, or the Fetch node makes one request per item again"
    assert out[0]["stations"] == ["KAUS", "EGLC", "ZBAA"]
    url = out[0]["url"]
    assert url.count("station=") == 3, url


def test_a_us_station_is_mapped_under_both_spellings():
    """IEM answers KAUS as AUS. A map keyed on cities.icao alone drops every
    US city - 11 of 48 on this board, New York and Chicago among them - and
    every test passes while it happens."""
    m = _requests(cities=[{"city_key": "austin", "icao": "KAUS"},
                          {"city_key": "london", "icao": "EGLC"}])[0]["by_station"]
    assert m["KAUS"] == "austin" and m["AUS"] == "austin"
    assert m["EGLC"] == "london" and "GLC" not in m


def test_two_cities_on_one_station_id_stop_the_run():
    with pytest.raises(RuntimeError, match="same IEM station id"):
        _requests(cities=[{"city_key": "a", "icao": "KAUS"},
                          {"city_key": "b", "icao": "AUS"}])


# ----------------------------------------------------------------- the parse

def _rows(csv_text=None, reqs=None, fetched=None):
    out = _run("Build rows", {
        "Build requests": reqs or _req(),
        "Fetch observations": fetched or _fetch(csv_text if csv_text is not None else CSV()),
    })
    return [r for chunk in out for r in chunk["observations"]], out[0]


def test_it_parses_a_real_shaped_response():
    rows, totals = _rows()
    assert totals["requested"] == 1 and totals["silent_stations"] == []
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


def test_a_station_that_returned_nothing_is_named():
    rows, totals = _rows(csv_text="station,valid,tmpf\n")
    assert rows == [] and totals["silent_stations"] == ["KAUS"]
    assert totals["n_obs"] == 0


def test_a_quiet_station_in_a_batch_is_named_not_counted_as_the_whole_run():
    """Hourly over a few hours, some stations report and some do not. The
    quiet ones travel in the log; they are not a failed run."""
    rows, totals = _rows(reqs=_req(("KAUS", "KDAL")))
    assert {r["city_key"] for r in rows} == {"austin"}
    assert totals["silent_stations"] == ["KDAL"]
    assert totals["cities_with_rows"] == 1 and totals["requested"] == 2


def test_a_row_is_attributed_by_its_own_station_never_by_position():
    """One response now carries every city. Pairing by index - which is what
    the per-station shape did - would file one city's afternoon under
    another city's market the moment a station went quiet."""
    csv = ("station,valid,tmpf,dwpf,relh,drct,sknt,p01i,skyc1,mslp\n"
           "DAL,2026-09-20 01:53,80.0,60.0,50.0,180,6,0.00,FEW,1010.0\n"
           "AUS,2026-09-20 01:53,91.0,64.0,41.0,140,5,0.00,FEW,1010.9\n")
    rows, _ = _rows(csv_text=csv, reqs=_req(("KAUS", "KDAL")))
    by_city = {r["city_key"]: r for r in rows}
    assert by_city["dallas"]["temp_f"] == 80.0
    assert by_city["austin"]["temp_f"] == 91.0


def test_a_station_nobody_asked_for_is_dropped_and_named():
    csv = ("station,valid,tmpf,dwpf,relh,drct,sknt,p01i,skyc1,mslp\n"
           "ZZZZ,2026-09-20 01:53,80.0,60.0,50.0,180,6,0.00,FEW,1010.0\n"
           "AUS,2026-09-20 01:53,91.0,64.0,41.0,140,5,0.00,FEW,1010.9\n")
    rows, totals = _rows(csv_text=csv)
    assert {r["city_key"] for r in rows} == {"austin"}
    assert totals["unknown_stations"] == ["ZZZZ"]


def test_a_rate_limited_body_is_named_as_such():
    """A rate limit, an outage and a quiet hour were the same number in the
    log. The body is what tells them apart."""
    rows, totals = _rows(csv_text="Too many requests from your IP address, slow down.")
    assert rows == [] and totals["rate_limited"] is True


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


def test_the_board_goes_out_in_one_request_not_forty_eight():
    """IEM RATE-LIMITS BY REQUEST PER IP, and answers a burst in plain words:

        Too many requests from your IP address, slow down.

    Measured against the live service while porting this:

        12 concurrent   44 of 48 stations returned nothing
         4 concurrent   42 of 48
         1 at a time    34 of 48, in 26 s, because most were rejected
                        before they were served

    SERIALISING DID NOT HELP, which is the diagnosis this file previously got
    wrong: it recorded "1 at a time - the whole board", and the workflow's own
    sticky note and ingest_log execution 8284 both say 34 of 48. A concurrency
    cap is cleared by going one at a time; this one was not, so the limiter
    counts requests.

    So the fix is not a longer interval - it is one request. The ASOS service
    accepts repeated station= parameters. Measured from the same egress,
    2026-09-22: 48 of 48 stations, 2,239 rows, 138 KB, ~4 s, no rate limit;
    and over a four-hour window, 168 rows in 10 KB.

    Batching options are therefore absent, and must stay absent: a batchSize
    on a single item does nothing, and reintroducing one item per station is
    what this guards against.
    """
    node = _fetch_node()
    reqs = _requests(cities=[{"city_key": "austin", "icao": "KAUS"},
                             {"city_key": "london", "icao": "EGLC"}])
    assert len(reqs) == 1, (
        f"Build requests emitted {len(reqs)} items; the Fetch node makes one "
        "request per item, and 48 of them is the defect this replaced"
    )
    assert reqs[0]["url"].count("station=") == 2
    assert "batching" not in (node["parameters"].get("options") or {}), (
        "batching is for many items; there is one, and its presence means the "
        "per-station shape has come back"
    )


def test_the_fetch_carries_no_supabase_credential():
    # It goes to IEM. A Supabase credential on it would send the service key
    # to a third party; scripts/validate_n8n_workflows.py checks the same
    # thing across every file, and this states it for the node that is most
    # obviously at risk of acquiring one by copy-paste.
    assert "credentials" not in _fetch_node()
