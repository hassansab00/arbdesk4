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

    # Every SQL file, not a hardcoded two: a workflow registered in a new file
    # is registered. Naming the files here made adding one fail a test that was
    # actually satisfied.
    seeded = "".join(open(f).read()
                     for f in sorted(glob.glob(os.path.join(ROOT, "sql", "*.sql"))))
    for path in sorted(glob.glob(os.path.join(ROOT, "n8n", "*.json"))):
        f = os.path.basename(path)
        if f.endswith(".snippet.json"):
            continue
        job = f.split(".template.")[0].split(".scaffold.")[0]
        assert job in seeded, f"{job} has no row in settings.workflow_schedules"


# ------------------------------------------------- failure diagnosis -------
# Both weather workflows failed on a real desk with "37 failed" and nothing
# else. A 403 on the User-Agent, a malformed query and a request that never
# arrived all produced that identical line, and each needs a different fix.
# These hold the workflows to naming which one it was.
import copy
import tempfile


def run_with(workflow, plan_name, mutate):
    plan = json.load(open(os.path.join(ROOT, "tests", "n8n", plan_name)))
    mutate(plan)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(plan, fh)
        path = fh.name
    try:
        out = subprocess.run([NODE, HARNESS, os.path.join(ROOT, "n8n", workflow), path],
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(path)


def all_obs(resp):
    def m(plan):
        plan["seed"]["Fetch observation"] = [resp] * len(plan["seed"]["Fetch observation"])
    return m


def test_a_403_says_it_is_the_user_agent():
    """weather.gov refuses a missing or generic User-Agent with 403. That
    response carries a `status` field, so it passes the missing-response test
    AND is not a 404 - it lands in a third branch that the first version of
    this diagnostic did not instrument, and reported 'No detail captured'."""
    r = run_with("P1.2_nws_monitor.template.json", "plan_P1.2_nws_monitor.json",
                 all_obs({"status": 403, "title": "Forbidden", "detail": "blocked"}))
    assert r["ok"] is False and r["node"] == "Guard: did anything parse?"
    assert "403" in r["error"] and "User-Agent" in r["error"]
    assert "user_agent" in r["error"], "it must name the Config field to fix"


def test_a_400_says_the_query_was_malformed_not_the_station():
    r = run_with("P1.2_nws_monitor.template.json", "plan_P1.2_nws_monitor.json",
                 all_obs({"status": 400, "title": "Bad Request", "detail": 'Parameter "start" is invalid'}))
    assert "400" in r["error"] and "malformed" in r["error"]
    assert "start" in r["error"], "the API's own detail must survive"


def test_a_request_that_never_arrived_is_not_blamed_on_the_data():
    r = run_with("P1.2_nws_monitor.template.json", "plan_P1.2_nws_monitor.json",
                 all_obs({"error": "ETIMEDOUT connect to api.weather.gov:443"}))
    assert "never completed" in r["error"] and "ETIMEDOUT" in r["error"]
    assert "network" in r["error"] or "timeout" in r["error"]


def test_an_unrecognised_shape_shows_the_body():
    """When nothing matches, print what actually came back rather than guess."""
    r = run_with("P1.2_nws_monitor.template.json", "plan_P1.2_nws_monitor.json",
                 all_obs({"foo": "bar"}))
    assert '{"foo":"bar"}' in r["error"]


def test_the_failure_names_the_city_and_the_url():
    """Without these the operator cannot reproduce it in a browser."""
    r = run_with("P1.2_nws_monitor.template.json", "plan_P1.2_nws_monitor.json",
                 all_obs({"status": 403, "title": "Forbidden"}))
    assert "nyc" in r["error"]
    assert "api.weather.gov/stations/KNYC/observations" in r["error"]


def test_the_start_parameter_has_no_fractional_seconds():
    """toISOString() appends milliseconds. api.weather.gov documents ISO8601
    and accepts a plain Z timestamp; the milliseconds are one more thing that
    can be rejected for no benefit."""
    r = run_with("P1.2_nws_monitor.template.json", "plan_P1.2_nws_monitor.json",
                 all_obs({"status": 403}))
    import re
    assert re.search(r"start=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}Z", r["error"]), r["error"]


def test_p13_points_failure_names_the_cause_and_blames_p12_first():
    """P1.3 falls back to the grid ids P1.2 caches. With no cached grid - which
    is what a desk looks like when P1.2 has never succeeded - it must say so,
    because fixing P1.3 alone would achieve nothing."""
    def m(plan):
        for c in plan["seed"]["Load cities"]:
            c["nws_grid_wfo"] = c["nws_grid_x"] = c["nws_grid_y"] = None
        plan["seed"]["Fetch point"] = [{"status": 403, "title": "Forbidden"}] * 4
        plan["seed"]["Fetch hourly forecast"] = []
    r = run_with("P1.3_nws_forecast.template.json", "plan_P1.3_nws_forecast.json", m)
    assert r["ok"] is False and r["node"] == "Build forecast urls"
    assert "403" in r["error"] and "User-Agent" in r["error"]
    assert "P1.2" in r["error"], "it must point at the workflow that is actually broken"


# ------------------------------------------- the geo+json decode bug -------
# THE ACTUAL CAUSE of "37 failed" in both P1.2 and P1.3, found only after the
# diagnostic above was added and still could not be read.
#
# n8n's HTTP node autodetects the response format from Content-Type, testing
# `contentType.includes('application/json')`. api.weather.gov answers
# `application/geo+json`, which does not contain that substring, so every
# response was decoded as TEXT and reached the Code nodes as
# `{ data: "{\"properties\":..." }` - no properties, no status, no title.
# Not a 404, not an error, nothing to report. Every city, every run, forever.

WEATHER_GOV_FETCHES = {
    "P1.2_nws_monitor.template.json": ["Fetch observation", "Fetch alerts", "Fetch point"],
    "P1.3_nws_forecast.template.json": ["Fetch point", "Fetch hourly forecast"],
    "P1.4_nws_gridpoint.template.json": ["Fetch gridpoint"],
}


@pytest.mark.parametrize("workflow,nodes", sorted(WEATHER_GOV_FETCHES.items()))
def test_weather_gov_fetches_ask_for_json_explicitly(workflow, nodes):
    """Autodetect is wrong for every api.weather.gov endpoint. Say json."""
    d = json.load(open(os.path.join(ROOT, "n8n", workflow)))
    by_name = {n["name"]: n for n in d["nodes"]}
    for name in nodes:
        n = by_name[name]
        assert n["type"] == "n8n-nodes-base.httpRequest", name
        resp = n["parameters"].get("options", {}).get("response", {}).get("response", {})
        assert resp.get("responseFormat") == "json", (
            f"{workflow} / {name} leaves the response format to autodetect, which "
            f"decodes application/geo+json as text")


def test_a_text_decoded_body_is_parsed_anyway():
    """Belt and braces: the option above is the fix, but an n8n build that
    ignores it must not put the desk back where it was."""
    def m(plan):
        plan["seed"]["Fetch point"] = [
            {"data": json.dumps(p)} for p in plan["seed"]["Fetch point"]]
    r = run_with("P1.3_nws_forecast.template.json", "plan_P1.3_nws_forecast.json", m)
    assert r["ok"], r
    urls = [i["hourly_url"] for i in r["outputs"]["Build forecast urls"]]
    assert urls and all("api.weather.gov" in u for u in urls), urls


def test_a_text_decoded_observation_series_is_parsed_anyway():
    def m(plan):
        for key in ("Fetch observation", "Fetch alerts", "Fetch point"):
            plan["seed"][key] = [{"data": json.dumps(p)} for p in plan["seed"][key]]
    r = run_with("P1.2_nws_monitor.template.json", "plan_P1.2_nws_monitor.json", m)
    assert r["ok"], r
    assert r["outputs"]["Build rows"][0]["n_obs"] > 0


def test_a_text_body_that_is_not_json_names_the_response_format_setting():
    """The one shape asJson() cannot rescue - a body that is not JSON at all -
    and the message must say which node to change, not print the count again.

    The cached grid is cleared too: with it, P1.3 assembles the URL itself and
    never notices, which is the whole point of that fallback existing."""
    def m(plan):
        for c in plan["seed"]["Load cities"]:
            c["nws_grid_wfo"] = c["nws_grid_x"] = c["nws_grid_y"] = None
        n = len(plan["seed"]["Fetch point"])
        plan["seed"]["Fetch point"] = [{"data": "<html>503 Service Unavailable</html>"}] * n
        plan["seed"]["Fetch hourly forecast"] = []
    r = run_with("P1.3_nws_forecast.template.json", "plan_P1.3_nws_forecast.json", m)
    assert r["ok"] is False and r["node"] == "Build forecast urls", r
    first = r["error"].splitlines()[0]
    assert "TEXT" in first and "Response Format" in first, first


@pytest.mark.parametrize("workflow,plan,seed,key", [
    ("P1.2_nws_monitor.template.json", "plan_P1.2_nws_monitor.json",
     "Fetch observation", "Fetch observation"),
    ("P1.3_nws_forecast.template.json", "plan_P1.3_nws_forecast.json",
     "Fetch point", "Fetch point"),
])
def test_the_first_line_carries_the_cause_not_the_count(workflow, plan, seed, key):
    """n8n's error banner shows ONE line, and one line is what gets pasted into
    a bug report. Three times the reason sat below a blank line, unread, while
    "37 failed" was reported as if it were the diagnosis."""
    def m(p):
        p["seed"][seed] = [{"status": 403, "title": "Forbidden"}] * len(p["seed"][seed])
        if workflow.startswith("P1.3"):
            for c in p["seed"]["Load cities"]:
                c["nws_grid_wfo"] = c["nws_grid_x"] = c["nws_grid_y"] = None
            p["seed"]["Fetch hourly forecast"] = []
    r = run_with(workflow, plan, m)
    assert r["ok"] is False
    first = r["error"].splitlines()[0]
    assert "403" in first and "User-Agent" in first, first
    assert not first.startswith("Parsed 0"), first
    assert not first.startswith("No forecast URLs"), first


# ------------------------------------------- a refused write is not a run ---
# The workflows ran, reported healthy, and wrote nothing. Every write node
# carries onError=continueRegularOutput, so a 401/42501 was walked straight
# past and only the LAST node in the chain showed an error. Reads worked
# because anon is granted SELECT; every write was refused because it is
# granted nothing else.

WRITE_NODES = {
    "P1.1_live_weather_alerts.template.json": ["Mark Notified"],
    "P1.2_nws_monitor.template.json": ["Write observations", "Write live_weather",
                                       "Save NWS ids", "Raise alerts"],
    "P1.3_nws_forecast.template.json": ["Write forecasts", "Save NWS ids"],
    "P1.4_nws_gridpoint.template.json": ["Write forecast features"],
}


@pytest.mark.parametrize("workflow,nodes", sorted(WRITE_NODES.items()))
def test_write_nodes_return_the_body_instead_of_throwing(workflow, nodes):
    """Without neverError the refusal is an exception that onError swallows,
    leaving nothing to read and nothing to report."""
    d = json.load(open(os.path.join(ROOT, "n8n", workflow)))
    by = {n["name"]: n for n in d["nodes"]}
    for name in nodes:
        resp = by[name]["parameters"].get("options", {}).get("response", {}).get("response", {})
        assert resp.get("neverError") is True, f"{workflow} / {name}"


@pytest.mark.parametrize("workflow,nodes", sorted(WRITE_NODES.items()))
def test_the_summary_refuses_to_report_success_on_a_refused_write(workflow, nodes):
    code = json.load(open(os.path.join(ROOT, "n8n", workflow)))["nodes"]
    code = [n for n in code if n["name"] == "Summary"][0]["parameters"]["jsCode"]
    assert "DID THE DATABASE ACTUALLY TAKE IT?" in code, workflow
    for name in nodes:
        assert json.dumps(name) in code, f"{workflow} does not check {name}"


def test_a_refused_write_names_the_anon_key():
    def m(plan):
        plan["seed"]["Write observations"] = [{
            "code": "42501", "details": None, "hint": None,
            "message": "permission denied for table weather_observations"}]
    r = run_with("P1.2_nws_monitor.template.json", "plan_P1.2_nws_monitor.json", m)
    assert r["ok"] is False and r["node"] == "Summary", r
    first = r["error"].splitlines()[0]
    assert "ANON key" in first and "service_role" in first, first
    assert "Nothing was written" in first, first


def test_a_clean_run_is_not_flagged():
    r = run("P1.2_nws_monitor.template.json", "plan_P1.2_nws_monitor.json")
    assert r["ok"], r
    assert r["outputs"]["Summary"][0]["rows"] > 0


def test_the_gate_stops_on_a_permission_error_rather_than_failing_open():
    """The gate fails OPEN on an unreachable RPC, which is right. 42501 is not
    unreachable - it is proof the key cannot write, on the first node that can
    show it, two nodes before any data is touched. Failing open on THAT answer
    is what let a whole run look healthy while writing nothing."""
    r = run("P1.2_nws_monitor.template.json", "plan_gate_denied.json")
    assert r["ok"] is False and r["node"] == "Run now?", r
    assert "ANON key" in r["error"] and "service_role" in r["error"]


@pytest.mark.parametrize("workflow", sorted(
    [f for f in os.listdir(os.path.join(ROOT, "n8n")) if f.endswith(".json")]))
def test_every_gate_carries_the_permission_check(workflow):
    d = json.load(open(os.path.join(ROOT, "n8n", workflow)))
    gate = [n for n in d["nodes"] if n["name"] == "Run now?"]
    if not gate:
        pytest.skip("no schedule gate in this file")
    assert "42501" in gate[0]["parameters"]["jsCode"], workflow


# ------------------------------------------------------------- P1.5 --------
# api.weather.gov covers the United States and its territories. Warsaw,
# Ankara, Moscow and Jinan get a 404 from it, so P1.2-P1.4 skip them - and
# nothing else wrote a live reading or a forward forecast for them at all.
# ingest_forecasts.py looks like it does and does not: its window is
# today-10 -> today, so it backfills what WAS forecast, never what will
# happen. Open-Meteo is global and answers plain application/json.

@pytest.fixture(scope="module")
def p15():
    r = run("P1.5_open_meteo.template.json", "plan_P1.5_open_meteo.json")
    assert r["ok"], r
    return r["outputs"]


def test_all_cities_go_out_in_one_request(p15):
    """Comma-separated coordinate lists: 37 cities cost one HTTP call."""
    url = p15["Build requests"][0]["url"]
    assert url.startswith("https://api.open-meteo.com/v1/forecast?")
    assert "latitude=52.1657%2C41.7868" in url, url
    assert "longitude=20.9671%2C-87.7522" in url, url
    assert p15["Build requests"][0]["n"] == 2


def test_the_request_asks_for_the_units_the_columns_already_use(p15):
    """weather_observations came from IEM METAR, so wind is KNOTS and precip
    is INCHES. Writing km/h into a knots column is wrong and not
    wrong-looking, which is the kind nobody finds."""
    url = p15["Build requests"][0]["url"]
    assert "wind_speed_unit=kn" in url
    assert "precipitation_unit=inch" in url
    assert "temperature_unit=celsius" in url
    assert "timezone=auto" in url, "a daily max only means anything in local time"


def test_cloud_percent_becomes_oktas(p15):
    """The one unit Open-Meteo cannot serve in the stored form: it answers
    percent, the column is eighths."""
    feats = {f["city_key"]: f for f in p15["Build rows"][0]["features"]}
    assert feats["warsaw"]["cloud_max"] == 4, "50% is 4 oktas"
    assert feats["chicago"]["cloud_max"] == 8, "100% is 8 oktas, not 100"


def test_a_live_reading_is_marked_as_a_model_not_a_station(p15):
    """Open-Meteo's current block is model output interpolated to a
    coordinate, not an instrument reading at the ICAO the market settles on.
    A page that cannot tell them apart is making a claim it cannot support."""
    live = p15["Build rows"][0]["live"]
    assert live and all(r["source_kind"] == "model" for r in live)
    assert all(r["source"] == "open-meteo" for r in live)


def test_it_never_writes_observations():
    """weather_observations is the settlement evidence and what the model is
    FITTED on. Model output in it would train the model on its own guess."""
    d = json.load(open(os.path.join(ROOT, "n8n", "P1.5_open_meteo.template.json")))
    for n in d["nodes"]:
        p = n["parameters"]
        for field in ("url", "jsonBody", "jsCode"):
            assert "weather_observations" not in str(p.get(field, "")), \
                f"{n['name']}.{field}"


def test_lead_days_count_from_the_citys_own_first_local_day(p15):
    fc = [f for f in p15["Build rows"][0]["forecasts"] if f["city_key"] == "chicago"]
    assert sorted(f["lead_days"] for f in fc) == [0, 1, 2]


def test_the_daily_max_is_the_peak_of_the_hourly_curve(p15):
    fc = {(f["city_key"], f["lead_days"]): f for f in p15["Build rows"][0]["forecasts"]}
    w = fc[("warsaw", 0)]
    assert w["forecast_max_c"] == 22, w
    assert w["variables"]["max_at_local"].endswith("T15:00"), "the seeded curve peaks at 15:00"
    assert w["variables"]["min_c"] == 14


def test_features_carry_the_same_column_names_as_the_observed_ones(p15):
    """So a model fitted on days that happened reads a forecast day with no
    translation - the thing that makes Model Forecast possible at all."""
    f = p15["Build rows"][0]["features"][0]
    for col in ("morning_temp_c", "morning_dewpoint_c", "dewpoint_depression_c",
                "morning_humidity", "cloud_mean", "cloud_max", "wind_mean",
                "wind_max", "precip_total", "forecast_max_c", "forecast_min_c"):
        assert col in f, col


def test_a_single_city_answer_is_not_an_array(p15):
    """Open-Meteo returns a bare object for one location and an array for
    many. n8n splits an array into items; the parser must take both."""
    code = [n for n in json.load(open(os.path.join(
        ROOT, "n8n", "P1.5_open_meteo.template.json")))["nodes"]
        if n["name"] == "Build rows"][0]["parameters"]["jsCode"]
    assert "if (res.length === 1 && Array.isArray(res[0])) res = res[0];" in code


def test_the_code_node_sandbox_has_no_URLSearchParams():
    """It does not, and a workflow that throws ReferenceError on its first
    node is worse than a longer join."""
    for n in json.load(open(os.path.join(
            ROOT, "n8n", "P1.5_open_meteo.template.json")))["nodes"]:
        if n["type"] == "n8n-nodes-base.code":
            assert "URLSearchParams" not in n["parameters"]["jsCode"], n["name"]
