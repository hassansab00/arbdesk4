"""A dispatched run that never logged is a failure (audit repair 2, 29 Sep).

run_health_watchdog() counted only ingest_log rows with status 'error', and a
job that dies before its own log_run writes none. 27-29 Sep: 19 production runs
failed (Actions run list) and every watchdog run said failed_jobs_24h 0.

  * supabase/migrations/20260930001000_a_dispatched_run_that_never_logged.sql:
    clock_expected_jobs (what each dispatched workflow must log, and how soon),
    v_run_arrivals, and the watchdog counting the missing runs.
    tests/database/run-arrivals.cjs holds its behaviour.
  * scripts/common.py _log_crash: an uncaught exception logs 'crash:<script>'.

These tests hold the expectations to the code: a job listed for a workflow is
one a script of that workflow writes, and its window covers the workflow's own
timeout.
"""
import pathlib
import re
import sys

import common

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "supabase" / "migrations" / "20260930001000_a_dispatched_run_that_never_logged.sql").read_text()
AD4_91 = (ROOT / "sql" / "ad4_91_database_jobs.sql").read_text()
WORKFLOWS = ROOT / ".github" / "workflows"

# A job name built at run time, or written by a script the workflow's script
# starts, rather than spelled out in the workflow's own scripts.
INDIRECT = {
    ("archive_observations.yml", "archive_observations"): ("archive_observations.py", 'f"archive_{name}"'),
    ("forecasts.yml", "ingest_forecasts"): ("forecast_backfill_job.py", "scripts/ingest_forecasts.py"),
}


def _expected():
    block = MIGRATION[MIGRATION.index("insert into public.clock_expected_jobs"):]
    block = block[:block.index("on conflict")]
    return re.findall(r"\('([a-z_]+\.yml)',\s*'([A-Za-z0-9_.]+)',\s*(\d+),", block)


def _scripts(workflow_text):
    return sorted(set(re.findall(r"python3? (?:scripts/)?([a-z_/]+\.py)", workflow_text)))


def test_the_seed_is_what_was_measured():
    rows = _expected()
    assert len(rows) == 37
    assert len({(f, j) for f, j, _ in rows}) == 37
    assert {f for f, _, _ in rows} == {
        "tick.yml", "pipeline_intraday.yml", "pipeline_daily.yml", "archive_observations.yml",
        "forecasts.yml", "observations.yml", "paper_trade_log.yml", "weather_model.yml"}
    # the jobs that failed silently 27-29 Sep are all expected now
    for pair in [("pipeline_intraday.yml", "paper_exits"), ("tick.yml", "P0.4_trade_history"),
                 ("tick.yml", "tick"), ("weather_model.yml", "weather_model"),
                 ("archive_observations.yml", "P2.9_honest_record")]:
        assert pair in {(f, j) for f, j, _ in rows}, pair


def test_every_expected_job_is_written_by_a_script_its_workflow_runs():
    for file, job, _ in _expected():
        wf = (WORKFLOWS / file).read_text()
        scripts = _scripts(wf)
        assert scripts, file
        if (file, job) in INDIRECT:
            script, marker = INDIRECT[(file, job)]
            assert script in scripts, (file, script)
            assert marker in (ROOT / "scripts" / script).read_text(), (file, job, marker)
            continue
        sources = "".join((ROOT / "scripts" / s).read_text() for s in scripts)
        assert re.search(rf"""["']{re.escape(job)}["']""", sources), (
            f"{file} is expected to log {job}, and none of its scripts ({scripts}) names it")


def test_every_window_covers_the_workflow_s_own_timeout():
    for file, job, within in _expected():
        wf = (WORKFLOWS / file).read_text()
        job_timeout = int(re.search(r"^    timeout-minutes: (\d+)", wf, re.M).group(1))
        assert int(within) >= job_timeout + 10, (file, job, within, job_timeout)
        assert int(within) <= 180


def _watchdog(text):
    i = text.index("create or replace function public.run_health_watchdog()")
    return text[i:text.index("end $$;", i)]


def test_the_sql_file_and_the_migration_define_the_same_watchdog():
    assert _watchdog(AD4_91) == _watchdog(MIGRATION)
    body = _watchdog(MIGRATION)
    assert "to_regclass('public.v_run_arrivals') is not null" in body, "a fresh install without the view still runs"
    assert "missed_runs_24h" in body and "not_ok_24h" in body
    assert "v_status := case when cardinality(v_failures) > 0 then 'attention' else 'ok' end;" in body


# --- the crash hook -----------------------------------------------------------

def _raise(exc):
    try:
        raise exc
    except BaseException as e:           # noqa: BLE001 - the hook receives any of them
        return type(e), e, e.__traceback__


def test_importing_common_installs_the_crash_hook():
    # by name: other tests reload common, which installs the new copy
    assert (sys.excepthook.__module__, sys.excepthook.__name__) == ("common", "_log_crash")


def test_an_uncaught_exception_logs_a_crash_row_then_the_usual_traceback(monkeypatch):
    logged, previous = [], []
    monkeypatch.setattr(common, "log_run", lambda *a: logged.append(a))
    monkeypatch.setattr(sys, "argv", ["scripts/paper_exits.py"])
    monkeypatch.setenv("GITHUB_RUN_ID", "36627478517")
    exc_type, exc, tb = _raise(TypeError("unsupported operand type(s) for *: 'decimal.Decimal' and 'float'"))
    common._log_crash(exc_type, exc, tb, _previous=lambda *a: previous.append(a))
    assert len(logged) == 1
    job, status, rows, detail = logged[0]
    assert (job, status, rows) == ("crash:paper_exits.py", "error", 0)
    assert detail["error"] == "TypeError: unsupported operand type(s) for *: 'decimal.Decimal' and 'float'"
    assert detail["github_run_id"] == "36627478517"
    assert detail["where"] and all(re.fullmatch(r"[\w.]+:\d+ in \S+", w) for w in detail["where"]), (
        "file, line and function only - ingest_log is readable by anon, source lines stay out")
    assert previous == [(exc_type, exc, tb)], "the traceback is still printed and the exit code is still 1"


def test_a_crash_that_cannot_log_still_prints_its_traceback(monkeypatch):
    previous = []

    def refused(*a):
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(common, "log_run", refused)
    common._log_crash(*_raise(ValueError("x")), _previous=lambda *a: previous.append(a))
    assert len(previous) == 1


def test_an_interrupt_is_not_a_crash(monkeypatch):
    logged, previous = [], []
    monkeypatch.setattr(common, "log_run", lambda *a: logged.append(a))
    common._log_crash(*_raise(KeyboardInterrupt()), _previous=lambda *a: previous.append(a))
    assert logged == [] and len(previous) == 1


def test_every_step_the_paper_switch_skips_is_marked_in_its_expectation():
    """vars.PAPER_TRADES_ENABLED = 'false' skips steps; their rows must then be
    removed, and the note says which rows those are."""
    wf = (WORKFLOWS / "pipeline_intraday.yml").read_text()
    gated = set()
    for step in re.split(r"\n      - name: ", wf)[1:]:
        if "vars.PAPER_TRADES_ENABLED != 'false'" in step:
            gated.update(re.findall(r"python3? (?:scripts/)?([a-z_/]+\.py)", step))
    assert len(gated) == 6, gated
    notes = dict(((f, j), n) for f, j, n in re.findall(
        r"\('(pipeline_intraday\.yml)',\s*'([A-Za-z0-9_.]+)',\s*\d+,\s*('[^']*'|null)\)", MIGRATION))
    marked = {j for (f, j), n in notes.items() if "PAPER_TRADES_ENABLED" in n}
    for script in gated:
        src = (ROOT / "scripts" / script).read_text()
        jobs = [j for (_, j) in notes if re.search(rf"""["']{re.escape(j)}["']""", src)]
        assert jobs, script
        for job in jobs:
            assert job in marked, f"{job} ({script}) is skipped by the paper switch; its note must say so"


def test_the_edge_engine_logs_a_run_with_nothing_to_price(monkeypatch):
    import edge_engine
    logged = []
    monkeypatch.setattr(edge_engine, "get_cities", lambda **k: [])
    monkeypatch.setattr(edge_engine, "_upcoming_markets", lambda: [])
    monkeypatch.setattr(edge_engine, "_bands_for_markets", lambda ids: [])
    monkeypatch.setattr(edge_engine, "log_run", lambda *a: logged.append(a))
    edge_engine.main()
    assert [(j, s, n) for j, s, n, _ in logged] == [("edge_engine", "ok", 0)], (
        "a dispatched intraday run with no edge_engine row reads as missing")
