"""A workflow can fire on time, report success, and do nothing at all.

Every n8n workflow's first node calls should_run(job, 'schedule'). A refusal
is not a failure - the execution stops at node four and finishes green - and
that is correct behaviour and a perfect hiding place.

WHAT HAPPENED, 2026-09-22. P1.6_iem_observations was moved to an hourly
Schedule Trigger and activated. Its executions read `success` every hour at
:10, two seconds each. settings.workflow_schedules still said
{"mode": "manual"}, so every one of them stopped at the gate. The observation
feed sat 104 minutes stale, 45 of 48 cities last seen four hours earlier, and
0 of 48 were fresh enough for s7_pre_peak_gradient's 90-minute reading gate -
which is a large part of why s7 has never fired a signal in its life.

Nothing in the stack could see it:

  n8n                 green, hourly, on time
  v_workflow_runs     shows the LATEST run per job, and one skipped run is
                      ordinary - so it showed the last REAL run, hours old,
                      with no hint that anything had been refused since
  ingest_log          empty of skips, because the skip path never reaches the
                      "Log run" node at the end of the workflow

THE TWO HALVES HAVE DIFFERENT OWNERS. The cadence lives in n8n (a Schedule
Trigger inside a workflow file); the permission lives in Postgres
(settings.workflow_schedules, editable from the Workflows page). Changing one
is not changing the other and nothing raises when they contradict. So the
contradiction has to become a row somebody can read, and the only moment that
knows about it is the refusal itself.

THE FIX IS IN THE GATE, NOT IN TEN WORKFLOW FILES. should_run now writes an
ingest_log row when it refuses a SCHEDULED trigger on mode - off or manual -
and v_workflow_gate_health reads those rows back. Interval refusals stay
silent: "ran 46 min ago, minimum is 50" is a trigger arriving early, it is
ordinary, and logging it would bury the signal in noise. On a healthy system
this writes nothing at all.

AND IT CANNOT BREAK A WORKFLOW. The insert sits in its own exception block. A
gate that refused to run a job because its bookkeeping failed would turn a
reporting gap into an outage.
"""

import pathlib
import re

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCOPE = ROOT / "sql/ad4_32_run_scope.sql"
WORKFLOWS = ROOT / "sql/ad4_14_workflows.sql"
SCHEDULES = ROOT / "sql/ad4_20_schedules.sql"


def _should_run_body():
    """The LIVE should_run. ad4_20 defines it first and ad4_32 replaces it."""
    s = SCOPE.read_text()
    i = s.index("create or replace function should_run(")
    return s[i:s.index("$ad4$;", i)]


# --------------------------------------------------------------------------
# 1. The refusal leaves a trace.
# --------------------------------------------------------------------------

def test_a_mode_refusal_writes_an_ingest_log_row():
    body = _should_run_body()
    assert "insert into ingest_log" in body, (
        "a gate that refuses a scheduled run and writes nothing is how P1.6 "
        "stayed invisible for hours"
    )
    assert "'skipped'" in body


def test_the_row_records_which_gate_refused_and_why():
    body = _should_run_body()
    for key in ("'trigger'", "'gate'", "'summary'"):
        assert key in body, f"the skipped row must carry {key}"


def test_both_mode_refusals_are_covered_by_one_branch():
    # Two separate `if v_mode = ...` blocks is how one of them gets the
    # logging and the other quietly does not.
    body = _should_run_body()
    assert "v_mode in ('off', 'manual')" in body
    assert "if v_mode = 'off' then" not in body
    assert "if v_mode = 'manual' then" not in body


def test_an_interval_refusal_stays_silent():
    # "ran 46 min ago, minimum is 50" is a trigger arriving early. It is
    # ordinary, it happens on every healthy job, and logging it would bury
    # the signal this whole mechanism exists to surface.
    body = _should_run_body()
    tail = body[body.index("if v_every <= 0 then"):]
    assert "insert into ingest_log" not in tail, (
        "the interval branch must not log - only a mode refusal is a "
        "contradiction between the trigger and the gate"
    )


def test_a_manual_trigger_never_reaches_the_logging_branch():
    body = _should_run_body()
    manual = body.index("p_trigger is distinct from 'schedule'")
    logged = body.index("insert into ingest_log")
    assert manual < logged, (
        "an operator pressing Run must return before the refusal log - "
        "otherwise every Run button click writes a skip it did not cause"
    )


def test_the_logging_cannot_break_the_gate():
    body = _should_run_body()
    ins = body.index("insert into ingest_log")
    window = body[ins:ins + 700]
    assert "exception when others then" in window, (
        "the insert must sit in its own exception block: a gate that refuses "
        "to run a workflow because its bookkeeping failed turns a reporting "
        "gap into an outage"
    )


def test_the_gate_still_ignores_skipped_rows_when_it_measures_the_interval():
    # should_run's own "when did this last run" query reads
    # `status <> 'skipped'`. Now that refusals write skipped rows, a gate that
    # counted them would think a refused job had just run and refuse it again
    # for another interval - a job that never runs again.
    body = _should_run_body()
    assert "where job = p_job and status <> 'skipped'" in body


# --------------------------------------------------------------------------
# 2. The view that reads them back.
# --------------------------------------------------------------------------

def test_the_gate_health_view_exists():
    sql = WORKFLOWS.read_text()
    assert "create or replace view v_workflow_gate_health" in sql


def test_the_view_names_the_contradiction():
    sql = WORKFLOWS.read_text()
    view = sql[sql.index("create or replace view v_workflow_gate_health"):]
    assert "gate_contradicts_trigger" in view, (
        "the flag has to be a column, not a sentence a reader has to parse"
    )
    assert "GATED" in view
    assert "skipped_24h, 0) >= 3" in view, (
        "one skip is ordinary; three refused scheduled attempts in a day is a "
        "Schedule Trigger firing into a closed gate"
    )


def test_the_view_separates_deliberate_from_broken():
    view = WORKFLOWS.read_text()
    view = view[view.index("create or replace view v_workflow_gate_health"):]
    assert "switched off deliberately" in view
    assert "manual-only, and nothing is trying to run it" in view, (
        "a manual job nobody is triggering is fine - only a manual job whose "
        "trigger keeps firing is the defect"
    )


def test_the_view_also_catches_an_auto_job_that_stopped_completing():
    view = WORKFLOWS.read_text()
    view = view[view.index("create or replace view v_workflow_gate_health"):]
    assert "STALLED" in view
    assert "3 * sp.every_minutes" in view


def test_the_view_carries_no_dead_cte():
    view = WORKFLOWS.read_text()
    view = view[view.index("create or replace view v_workflow_gate_health"):]
    assert "where false" not in view, "a CTE that selects nothing is scaffolding, not code"


def test_the_file_holding_the_view_is_installed():
    order = (ROOT / "sql/INSTALL_ORDER.txt").read_text().split()
    assert "ad4_14_workflows.sql" in order or "sql/ad4_14_workflows.sql" in order, order[:5]


# --------------------------------------------------------------------------
# 3. The seed half. The live row is the UI's to change; the seed is ours.
# --------------------------------------------------------------------------

def test_the_observation_feed_is_the_hourly_tick():
    """The station feed moved from n8n's P1.6 into the hourly tick (plan v2
    P6.2: 720 n8n executions a month). So a fresh install collects through
    the tick, and P1.6 seeds OFF - left on, it would collect everything twice
    and spend the executions P6.2 exists to save."""
    import pathlib
    tick_src = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "tick.py").read_text()
    assert "stations = read_stations(now, dry_run)" in tick_src, "the tick no longer reads the stations"
    seed = SCHEDULES.read_text()
    m = re.search(r'"P1\.6_iem_observations":\s*\{\s*"mode":\s*"(\w+)"', seed)
    assert m, "P1.6 is not in the seeded schedule at all"
    assert m.group(1) == "off", "P1.6 would collect the stations a second time beside the tick"


# P1.6 is not here: the tick reads the stations since plan v2 P6.2, and P1.6
# seeds off (test_the_observation_feed_is_the_hourly_tick).
@pytest.mark.parametrize("job", ["P1.2_nws_monitor", "P1.3_nws_forecast", "P1.4_nws_gridpoint",
                                 "P1.5_open_meteo"])
def test_every_weather_feed_seeds_as_auto(job):
    seed = SCHEDULES.read_text()
    m = re.search(rf'"{re.escape(job)}":\s*\{{\s*"mode":\s*"(\w+)"', seed)
    assert m and m.group(1) == "auto", f"{job} does not seed as auto"


def test_v_workflow_runs_is_why_this_was_invisible():
    # Kept as a statement of fact rather than a guard: v_workflow_runs is
    # DISTINCT ON (job) - the latest run only - which is the right shape for
    # "did it work" and the wrong shape for "has it been refused all day".
    sql = WORKFLOWS.read_text()
    runs = sql[sql.index("create or replace view v_workflow_runs"):]
    runs = runs[:runs.index(";")]
    assert "distinct on (l.job)" in runs
