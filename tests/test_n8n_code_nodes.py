"""Execute the n8n workflows' Code nodes and assert what they actually produce.

The JSON in n8n/ is not inert configuration: the Code nodes convert units, map
METAR sky cover to oktas, derive daily maxima from hourly series, and decide
whether a weather alert is new. Importing a workflow into n8n proves it loads;
it does not prove any of that is right.

Runs through tests/n8n/harness.mjs (Node, mocked n8n globals). Skipped where
node is not installed, so the Python suite still runs everywhere.
"""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "n8n", "harness.mjs")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run(workflow, plan):
    out = subprocess.run(
        [NODE, HARNESS, os.path.join(ROOT, "n8n", workflow),
         os.path.join(ROOT, "tests", "n8n", plan)],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def p12():
    r = run("P1.2_nws_monitor.template.json", "plan_P1.2_nws_monitor.json")
    assert r["ok"], r
    return r["outputs"]


@pytest.fixture(scope="module")
def p13():
    r = run("P1.3_nws_forecast.template.json", "plan_P1.3_nws_forecast.json")
    assert r["ok"], r
    return r["outputs"]


# ------------------------------------------------------------------ P1.2 ---
def test_unchanged_alert_raises_no_event(p12):
    """Phoenix's Excessive Heat Warning is already in live_weather.

    prior_alert arrives from PostgREST as an EMBEDDED resource - a list, not a
    string. Compared unflattened it never equals the current event, so every
    run would re-raise the same alert and P1.1 would email it again.
    """
    events = p12["Build rows"][0]["events"]
    assert [e["city_key"] for e in events] == ["austin"]


def test_new_alert_is_raised_with_mapped_severity(p12):
    ev = p12["Build rows"][0]["events"][0]
    assert ev["kind"] == "nws_alert"
    assert ev["detail"]["event"] == "Heat Advisory"
    assert ev["severity"] == "medium"        # NWS "Moderate" -> AD4 "medium"


def test_standing_alert_still_reaches_live_weather(p12):
    """Not raising an event must not mean forgetting the alert exists."""
    live = {r["city_key"]: r for r in p12["Build rows"][0]["liveRows"]}
    assert live["phoenix"]["nws_alert"] == "Excessive Heat Warning"


def test_fahrenheit_observation_is_converted(p12):
    """unitCode is authoritative: 113F is stored as 45C, not as 113."""
    obs = {o["city_key"]: o for o in p12["Build rows"][0]["observations"]}
    assert obs["phoenix"]["temp_c"] == pytest.approx(45.0, abs=0.05)
    assert obs["phoenix"]["temp_f"] == pytest.approx(113.0, abs=0.05)


def test_cloud_layers_become_oktas(p12):
    """SCT + BKN -> 6, matching scripts/ingest_observations.sky_oktas."""
    obs = {o["city_key"]: o for o in p12["Build rows"][0]["observations"]}
    assert obs["nyc"]["cloud_cover"] == 6
    assert obs["phoenix"]["cloud_cover"] == 0      # CLR


def test_station_column_holds_the_icao(p12):
    """IEM rows store the ICAO; a different form here splits one station."""
    obs = {o["city_key"]: o for o in p12["Build rows"][0]["observations"]}
    assert obs["nyc"]["station"] == "KNYC"
    assert obs["phoenix"]["station"] == "KPHX"


def test_non_us_city_is_recorded_so_it_is_not_asked_again(p12):
    sup = {s["city_key"]: s for s in p12["Build rows"][0]["support"]}
    assert sup["london"]["nws_supported"] is False
    assert sup["nyc"]["nws_grid_wfo"] == "OKX"


def test_city_already_known_non_us_is_never_requested(p12):
    """paris has nws_supported=false, so it costs no HTTP call at all."""
    assert "paris" not in [r["city_key"] for r in p12["Build requests"]]


def test_station_reported_daily_max_is_kept(p12):
    live = {r["city_key"]: r for r in p12["Build rows"][0]["liveRows"]}
    assert live["nyc"]["max_temp_24h_c"] == pytest.approx(31.1)
    assert live["nyc"]["solar_transit_at"] == "2026-09-03T16:57:00+00:00"


# ------------------------------------------------------------------ P1.3 ---
def rows(p13):
    return p13["Build rows"][0]["forecasts"]


def test_partial_days_are_skipped(p13):
    """A day whose hours miss the 12-18 peak window has an understated max.

    Writing it would not just lose a day - it would invent spread against
    Open-Meteo, and v_forecast_divergence turns spread into a wider sigma.
    """
    by_city = {}
    for r in rows(p13):
        by_city.setdefault(r["city_key"], []).append(r["for_date"])
    # miami's series starts at 20:00 local, so "today" is after the peak
    assert "2026-09-03" not in by_city["miami"]
    assert by_city["miami"] == ["2026-09-04", "2026-09-05"]
    assert p13["Build rows"][0]["partial_days"] == 4


def test_temperature_unit_is_honoured_not_assumed(p13):
    """Denver's mock server ignored units=si and answered in Fahrenheit."""
    den = [r for r in rows(p13) if r["city_key"] == "denver"]
    assert den and all(r["forecast_max_c"] == pytest.approx(36.0, abs=0.1) for r in den)


def test_run_at_is_the_nws_issuance_not_now(p13):
    """So re-running between issuances updates rows instead of piling up."""
    assert {r["run_at"] for r in rows(p13) if r["city_key"] == "nyc"} == {
        "2026-09-03T09:12:00+00:00"
    }


def test_lead_days_counts_from_the_local_day_the_series_starts(p13):
    nyc = sorted((r["for_date"], r["lead_days"]) for r in rows(p13) if r["city_key"] == "nyc")
    assert nyc == [("2026-09-03", 0), ("2026-09-04", 1),
                   ("2026-09-05", 2), ("2026-09-06", 3)]


def test_rows_carry_the_model_label_divergence_looks_for(p13):
    assert {r["model"] for r in rows(p13)} == {"nws"}
    assert {r["source"] for r in rows(p13)} == {"api.weather.gov"}


def test_misaligned_responses_refuse_rather_than_mis_attribute():
    """Rows are paired by position. Fewer responses than requests must stop the
    run: silently shifting them would file one city's forecast under another."""
    r = run("P1.3_nws_forecast.template.json", "plan_P1.3_misaligned.json")
    assert r["ok"] is False
    assert r["node"] == "Build rows"
    assert "out of order" in r["error"] or "paired by position" in r["error"]


# ------------------------------------------------- P1.2 units -------------
# weather_observations was filled by IEM METAR, so wind_speed is KNOTS and
# precip is INCHES. api.weather.gov answers in km/h and mm. An unconverted
# value here is wrong without looking wrong, which is the worst kind.
def test_wind_is_converted_to_knots(p12):
    obs = {o["city_key"]: o for o in p12["Build rows"][0]["observations"]}
    assert obs["nyc"]["wind_speed"] == pytest.approx(9.719, abs=0.01)      # 5 m/s
    assert obs["phoenix"]["wind_speed"] == pytest.approx(5.94, abs=0.01)   # 11 km/h


def test_live_weather_wind_is_the_same_knots(p12):
    live = {r["city_key"]: r for r in p12["Build rows"][0]["liveRows"]}
    assert live["phoenix"]["wind_speed_kt"] == pytest.approx(5.94, abs=0.01)


def test_precipitation_is_converted_to_inches(p12):
    obs = {o["city_key"]: o for o in p12["Build rows"][0]["observations"]}
    assert obs["nyc"]["precip"] == pytest.approx(0.1, abs=0.001)           # 2.54 mm


def test_an_unrecognised_unit_is_null_not_the_raw_number(p12):
    """A value whose unit is unknown is not a measurement. Store nothing."""
    obs = {o["city_key"]: o for o in p12["Build rows"][0]["observations"]}
    assert obs["austin"]["wind_speed"] is None


# --------------------------------------------------------------- schedule --
# n8n bills per execution and a Schedule Trigger cannot be told from outside
# n8n not to fire. The gate is what makes activating these workflows safe: it
# asks the database once and, when the answer is no, returns no items - which
# stops every node below it.
#
# The failure mode that matters is not a missed run. It is a gate that fails
# CLOSED: one missing settings row and every workflow silently stops working,
# looking exactly like a broken pipeline.
def gate(plan):
    r = run("P1.2_nws_monitor.template.json", plan)
    assert r["ok"], r
    return r["outputs"]["Stop if skipped"]


def test_a_run_inside_its_interval_is_skipped():
    assert gate("plan_gate_skip.json") == []


def test_a_run_past_its_interval_proceeds():
    assert len(gate("plan_gate_run.json")) == 2


def test_a_missing_rpc_fails_OPEN():
    """sql/ad4_20 may not have been run. That must mean 'run', never 'stop'."""
    assert len(gate("plan_gate_absent.json")) == 2


@pytest.mark.parametrize("body", [
    {},                                   # empty response
    {"run": None},                        # present but unusable
    {"error": "boom"},                    # an error object
])
def test_any_unusable_answer_fails_open(body, tmp_path):
    import copy
    src = json.load(open(os.path.join(ROOT, "tests", "n8n", "plan_gate_run.json")))
    plan = copy.deepcopy(src)
    plan["seed"]["Check schedule"] = body
    name = "plan_gate_generated.json"
    path = os.path.join(ROOT, "tests", "n8n", name)
    try:
        with open(path, "w") as fh:
            json.dump(plan, fh)
        assert len(gate(name)) == 2, f"{body} must not stop the workflow"
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_every_workflow_is_gated():
    """A workflow without the gate runs at full cost on every schedule fire."""
    import glob
    for path in sorted(glob.glob(os.path.join(ROOT, "n8n", "*.json"))):
        f = os.path.basename(path)
        if f.endswith(".snippet.json"):
            continue                      # a fragment, checked separately below
        wf = json.load(open(path))
        names = {n["name"] for n in wf["nodes"]}
        assert {"Check schedule", "Run now?", "Stop if skipped"} <= names, f"{f} is not gated"
        # and the gate must sit between Config and the work, not beside it
        after_config = [c["node"] for br in wf["connections"]["Config"]["main"] for c in br]
        assert after_config == ["Check schedule"], f"{f}: Config bypasses the gate ({after_config})"


def test_the_gate_snippet_is_the_gate():
    """schedule_gate.snippet.json exists to be pasted into the four P0.x
    workflows that predate should_run(). It is not a workflow - no trigger, no
    Config - but it has to carry the whole gate and be self-contained, because
    it cannot know what the host workflow named its credential node."""
    wf = json.load(open(os.path.join(ROOT, "n8n", "schedule_gate.snippet.json")))
    names = [n["name"] for n in wf["nodes"]]
    assert names == ["Gate config", "Check schedule", "Run now?", "Stop if skipped"]

    blob = json.dumps(wf)
    assert "$('Gate config')" in blob, "the gate must read its own config node"
    assert "$('Config')" not in blob, "it cannot depend on a node the host may not have"

    # chained, so pasting it in gives one path from config to stop
    for a, b in zip(names, names[1:]):
        assert [c["node"] for br in wf["connections"][a]["main"] for c in br] == [b]

    # and it fails OPEN: a missing settings row must not silently disable a job
    decide = next(n for n in wf["nodes"] if n["name"] == "Run now?")
    assert "running anyway" in decide["parameters"]["jsCode"]


# ------------------------------------------------------- P1.4 gridpoint ----
# The raw NWS gridpoint does not speak in the units weather_observations uses,
# and every mismatch here is the dangerous kind: the number stays plausible.
# An overcast day arriving as "100 oktas" or a 25mm forecast stored as 6 inches
# both look like data until something downstream multiplies by them.
@pytest.fixture(scope="module")
def p14():
    r = run("P1.4_nws_gridpoint.template.json", "plan_P1.4_gridpoint.json")
    assert r["ok"], r
    return r["outputs"]["Build rows"][0]["rows"][0]


def test_sky_cover_percent_becomes_oktas(p14):
    """NWS gives 0-100%. weather_observations.cloud_cover is 0-8 oktas, because
    that is what METAR reports. 50% is 4 oktas, not 50."""
    assert p14["cloud_mean"] == pytest.approx(4.0, abs=0.01)


def test_wind_kmh_becomes_knots(p14):
    assert p14["wind_mean"] == pytest.approx(10.0, abs=0.05)


def test_an_accumulating_total_is_not_multiplied_by_its_block_length(p14):
    """The bug this test exists for: 25.4mm forecast over a SIX-HOUR block.

    Expanding the interval to six hourly points and summing gave 6 inches of
    rain where the forecast said one. Temperature over a block means "it is
    22C for each of these hours"; precipitation means "25mm falls across them
    in total", and the two cannot share an expansion rule.
    """
    assert p14["precip_total"] == pytest.approx(1.0, abs=0.001)


def test_instantaneous_series_are_not_divided(p14):
    """The mirror of the above: temperature must NOT be spread across its
    block, or a six-hour 30C forecast would read as 5C."""
    assert p14["forecast_max_c"] == pytest.approx(30.0, abs=0.1)


def test_a_partial_day_is_still_skipped(p14):
    """Same peak-window rule as P1.3: the first local day is missing its
    afternoon, so it is not written - and the run says so, because a day
    silently absent and a day that failed to fetch look identical otherwise."""
    r = run("P1.4_nws_gridpoint.template.json", "plan_P1.4_gridpoint.json")
    out = r["outputs"]["Build rows"][0]
    assert len(out["rows"]) == 1, [x["for_date"] for x in out["rows"]]
    assert out["partial_days"] == 1


def test_columns_match_the_observed_feature_names():
    """The whole design rests on this: a model fitted on observed conditions
    applies to forecast ones only because the names are identical. A rename on
    either side silently decouples them."""
    r = run("P1.4_nws_gridpoint.template.json", "plan_P1.4_gridpoint.json")
    row = r["outputs"]["Build rows"][0]["rows"][0]
    for shared in ("dewpoint_depression_c", "cloud_mean", "wind_mean",
                   "precip_total", "morning_temp_c"):
        assert shared in row, f"{shared} missing — the model could not apply forward"


# --------------------------------------------- P1.2 observation series -----
# P1.2 used to fetch /observations/latest - ONE reading. A daily maximum
# cannot be computed from one reading, which is the number these markets
# settle on, so the NWS feed could never produce the figure it exists for.
def test_the_whole_series_is_archived(p12):
    nyc = [o for o in p12["Build rows"][0]["observations"] if o["city_key"] == "nyc"]
    assert len(nyc) == 4, [o["temp_c"] for o in nyc]
    assert max(o["temp_c"] for o in nyc) == pytest.approx(28.3)


def test_live_weather_takes_the_newest_reading_not_the_first(p12):
    live = {r["city_key"]: r for r in p12["Build rows"][0]["liveRows"]}
    assert live["nyc"]["temp_c"] == pytest.approx(26.9)
    assert live["nyc"]["observed_at"] == "2026-09-04T21:51:00+00:00"


def test_a_single_feature_response_still_works(p12):
    """Not every station answers with a FeatureCollection. The old shape must
    keep working, or switching to the series would break the stations that
    only serve `latest`."""
    phx = [o for o in p12["Build rows"][0]["observations"] if o["city_key"] == "phoenix"]
    assert len(phx) == 1
    assert phx[0]["temp_c"] == pytest.approx(45.0, abs=0.05)


def test_a_series_response_is_not_mistaken_for_a_failed_fetch(p12):
    """The 404 guard predated the series shape: a FeatureCollection has no
    `properties`, so every series response was being counted as a failure and
    dropped. Nothing about that looked wrong from the summary line."""
    assert p12["Build rows"][0]["failed"] == 0
    assert p12["Build rows"][0]["ok"] == 3


def test_every_workflow_file_has_a_row_on_the_workflows_page():
    """A workflow with no row in the UI catalogue has no Run button, no run
    history and no cadence control - it exists only as a file. P1.4 shipped
    that way: registered in settings by sql/ad4_24, invisible in the app.
    """
    import glob
    import re

    page = open(os.path.join(ROOT, "web", "app", "workflows", "page.tsx")).read()
    listed = set(re.findall(r'job: "([^"]+)"', page))

    for path in sorted(glob.glob(os.path.join(ROOT, "n8n", "*.json"))):
        f = os.path.basename(path)
        if f.endswith(".snippet.json"):
            continue
        # P1.2_nws_monitor.template.json -> P1.2_nws_monitor
        job = f.split(".template.")[0].split(".scaffold.")[0]
        assert job in listed, f"{job} has no row on the Workflows page"


def test_every_workflow_file_has_a_cadence_registered():
    """should_run() falls open for an unknown job, so a missing schedule row is
    not fatal - but it does mean the workflow ignores the Workflows page and
    runs on whatever n8n says, which is the thing the gate exists to prevent."""
    import glob

    seeded = (open(os.path.join(ROOT, "sql", "ad4_20_schedules.sql")).read()
              + open(os.path.join(ROOT, "sql", "ad4_24_nws_gridpoint.sql")).read())
    for path in sorted(glob.glob(os.path.join(ROOT, "n8n", "*.json"))):
        f = os.path.basename(path)
        if f.endswith(".snippet.json"):
            continue
        job = f.split(".template.")[0].split(".scaffold.")[0]
        assert job in seeded, f"{job} has no row in settings.workflow_schedules"
