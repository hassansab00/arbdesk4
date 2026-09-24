"""The GitHub Actions files are the desk's production scheduler, and nothing
tested them.

Every derived number the platform shows - probabilities, edges, signals,
skill, the databank, the calibration feedback - is written by one of these
workflows. If a schedule is wrong, a job has no timeout, or the account runs
out of Actions minutes, the UI does not show an error: it shows yesterday's
numbers, or an empty container, and keeps looking like a working desk.

On 2026-09-07 the account's Actions minutes ran out. Every workflow began
failing in 2-4 seconds with no runner, no steps and no logs - including
scheduled workflows on a commit that had succeeded the evening before. The
ingest stopped, and the platform filled with empty containers that looked
like application bugs. These tests exist so the file that caused it - the
duplicated CI trigger that billed every commit three times - cannot come
back quietly.
"""
import os
import yaml
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF_DIR = os.path.join(ROOT, ".github", "workflows")

# In YAML 1.1 the bare key `on:` parses as the boolean True. Every workflow
# file in the world hits this; safe_load gives you {True: {...}}, not "on".
ON = True


def workflows():
    out = []
    for name in sorted(os.listdir(WF_DIR)):
        if not name.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(WF_DIR, name)) as fh:
            out.append((name, yaml.safe_load(fh)))
    assert out, "no workflow files found"
    return out


def triggers(doc):
    t = doc.get(ON) or doc.get("on") or {}
    return t if isinstance(t, dict) else {k: None for k in (t if isinstance(t, list) else [t])}


# ------------------------------------------------------------------ minutes --

def _cron_matches(expr, minute, hour, dom, month, dow):
    """dow: 0=Sunday, matching cron. Supports *, */n, a-b, lists, and numbers."""
    fields = expr.split()
    assert len(fields) == 5, f"not a 5-field cron: {expr!r}"
    for field, value, lo, hi in zip(fields,
                                    (minute, hour, dom, month, dow),
                                    (0, 0, 1, 1, 0),
                                    (59, 23, 31, 12, 6)):
        if not _field_matches(field, value, lo, hi):
            return False
    return True


def _field_matches(field, value, lo, hi):
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part in ("*", "?"):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
            if step == 1:
                if value == start:
                    return True
                continue
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def runs_per_30_days(expr):
    """Exact count by walking every minute of a 30-day window.

    Approximating this ("*/6 is about four a day") is how a schedule change
    slips past review: the cost of `7 */2 * * *` versus `7 */6 * * *` is 240
    runs a month, and nobody eyeballs that difference correctly."""
    import datetime
    start = datetime.datetime(2026, 1, 1)
    n = 0
    for i in range(30 * 24 * 60):
        t = start + datetime.timedelta(minutes=i)
        # datetime weekday(): Monday=0..Sunday=6. cron: Sunday=0..Saturday=6.
        dow = (t.weekday() + 1) % 7
        if _cron_matches(expr, t.minute, t.hour, t.day, t.month, dow):
            n += 1
    return n


# ============================================================================
# THE BUDGET COUNTS MINUTES NOW, BECAUSE IT WAS MEASURING THE WRONG THING.
#
# A private repository meters Actions MINUTES: 2,000 a month on the free plan.
# The repo is private (plan v2 P0.2, decided 23 Sep), so this is a hard limit,
# and only plan step P6.1 changes the two budgets below.
# This test counted RUNS and asserted "at ~2 billed minutes each". That
# assumption is what made the real cost invisible. Measured from the runs
# themselves on 2026-09-19:
#
#     workflow              runs/mo   min/run   min/mo
#     pipeline_intraday         180      10.2    1,835
#     pipeline_daily             30      29.0      870
#     forecasts                  30      18.5      556
#     observations              120       2.5      305
#     archive + log + model      64       1.5       93
#                                                -----
#                                                3,659
#
# Not ~850. 3,659 against a 2,000-minute allowance - about $13 a month of
# overage at standard Linux rates, which is real but nowhere near the sixty to
# a hundred dollars a month the bill actually shows. The rest is elsewhere:
# Supabase is over its 500 MB tier and Vercel bills separately. Counting runs
# could never have found any of this.
#
# WHAT WAS CUT, AND WHAT IT COST IN QUALITY: nothing.
#
#   -720  the intraday settlement sweep. Measured on a scheduled run: 5,647
#         candidates, 5,322 unreached, 292 proofs captured, ZERO positions
#         settled, 4 minutes 1 second - six times a day. The desk's own
#         holdings are checked in the first seconds; the remaining four
#         minutes walk an outcome archive at about one day of markets per run.
#         The intraday cycle now runs --holdings-only and the daily run keeps
#         the wide 900-second sweep. Positions settle as promptly or sooner,
#         and the archive still fills.
#
# WHAT IS LEFT, MEASURED, FOR THE NEXT DECISION - all three are real work and
# none of them is free:
#
#   ~522  band probabilities, 2.9 min x 180. Recomputing only the bands whose
#         forecast run or book snapshot changed would cut most of it, at the
#         risk of a stale price if the change detection is wrong. It touches
#         the most load-bearing code on the desk.
#   ~556  forecasts.yml, 18.5 min x 30, fetching 54 cities from several models
#         serially.
#   ~450  the daily settlement sweep's own 900 seconds, which is the price of
#         the outcome archive calibration needs.
#
# THE NUMBERS BELOW ARE MEASURED, NOT ASSUMED. A workflow with no measurement
# yet is costed at DEFAULT_MINUTES, which is deliberately generous: an
# unmeasured job should look expensive until somebody measures it.
# ============================================================================
MEASURED_MINUTES = {
    # 6.2 measured 2026-09-19 (mean of six scheduled runs; 10.2 before
    # --holdings-only). Plan v2 P6.1 step 3 (#134, the engine priced on a
    # thread pool): the first run on it, 24 Sep 10:37Z, took 1 min 48 s -
    # "Band probabilities" 34 s for 96 city-days, where 04:43Z took 231 s for
    # 65. Billed per job in whole minutes, so 2.0.
    "pipeline_intraday.yml": 2.0,
    # The hourly checkpoint tick (plan v2 P6.1 / P4.2). Dispatched 24 Sep:
    # 35 s with a cold venv cache, 21 s warm (7 checkpoints, script 11.8 s).
    # Billed at one minute; tick.py stops itself at 45 s.
    "tick.yml": 1.0,
    # 29.0 measured 19 Sep; plan v2.1 P3.8 adds the hit tournament (a synthetic
    # 48-city night: 16.7 s a lane, two lanes). Held at 30.0 until timed.
    "pipeline_daily.yml": 30.0,
    # 18.5 measured 19 Sep. Plan v2.1 P2.8 adds one multi-model request per
    # city and cuts the read timeout from 100 s to 30 s (five timeouts cost
    # about 17 of 22 minutes on 23 Sep); P3.8 adds one current-run request per
    # city (48 x ~2.3 s, the per-city time in the 23 Sep log). None of it is
    # timed yet, so this is held at 24.0 until a run with all three has been.
    "forecasts.yml": 24.0,
    "observations.yml": 2.5,
    # 1.5 measured before the nightly mirror (plan v2.1 P1.7) was added to
    # this job; the mirror's first run copies every table's history. Held at
    # 4.0 until a run with the mirror in it has been timed.
    "archive_observations.yml": 4.0,
    "paper_trade_log.yml": 1.5,
    # 1.5 measured before 2026-09-22; the wind-direction backfill added a
    # step that reads 22k cache rows and scans ~415k archived observations
    # the first time it runs, and writes nothing on every run after that.
    # Held at 4.0 until a run with the step in it has been timed - the
    # budget may round up, it may not guess low.
    "weather_model.yml": 4.0,
    "live_weather.yml": 1.5,
    "verify_resolution_source.yml": 1.5,
    "backtest.yml": 1.5,
}
DEFAULT_MINUTES = 5.0

# 3,000, deliberately, with the reason written here as the repo requires.
#
# The measured total after the cut above is about 2,940 - still 940 over the
# free 2,000, about $7.50 a month. Setting the budget to 2,000 would fail CI
# today and every day until a cadence is cut, which turns a cost signal into a
# broken build; setting it to 3,659 would bank the saving instead of keeping
# it. 3,000 is where the desk actually is, so any INCREASE from here has to be
# argued for - which is what a budget is for.
SCHEDULED_MINUTE_BUDGET = 3000

# Kept so a schedule change that doubles the RUNS is still visible even if the
# per-run time falls. Both ceilings apply.
#
# 1,060, raised from 430 by plan v2 P6.1 (the reason, as the repo requires):
# the hourly checkpoint tick adds 720 runs a month, one billed minute each.
# It is paid for by the engine speed-up it follows - intraday fell from 6.2
# to 2.0 billed minutes a run, 756 minutes a month - so the MINUTE budget
# above is unchanged and still binds (2,956 of 3,000 when this was set).
# Plan v2 targets 800 once intraday, forecasts, observations and paper_fill
# are folded into the tick and daily.yml; lower this then.
SCHEDULED_RUN_BUDGET = 1060


class _StrictLoader(yaml.SafeLoader):
    """A loader that REFUSES a duplicate key instead of quietly picking one."""


def _no_duplicate_keys(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys)


def test_no_workflow_file_declares_the_same_key_twice():
    """THE FAILURE MODE IS SILENT LOCALLY AND TOTAL ON GITHUB.

    A step may carry one `env:` block. Give it two and yaml.safe_load - which
    is what every other test in this file uses, and what a local check uses -
    keeps the LAST and discards the first without a word. GitHub's parser is
    stricter: it rejects the whole file, so the workflow never starts. The run
    that appears has zero jobs, zero seconds, a conclusion of failure, and a
    name that has fallen back to the file path because GitHub could not read
    the `name:` key either.

    Measured: eighteen consecutive Backtest runs failed that way, from the
    commit that added a second `env:` to backtest.yml's last step onward, and
    every local check passed throughout - including the test that asserts the
    budget is declared, because it reads the raw text with a regex and the
    string was there. The string being present and the file being valid are
    different claims, and only one of them was being tested.
    """
    for name, _doc in workflows():
        path = os.path.join(WF_DIR, name)
        try:
            yaml.load(open(path), Loader=_StrictLoader)
        except yaml.YAMLError as e:
            raise AssertionError(
                f"{name} declares the same key twice, so GitHub will refuse "
                f"the whole file and the workflow will never start: {e}"
            ) from None


def n8n_clock():
    """{workflow file: runs per 30 days} dispatched on a clock by n8n (plan v2 P6.1).

    GitHub's cron dropped 6 of the tick's first 7 hourly runs on 24 Sep, so the
    tick and intraday are started by the n8n workflow P6.1_clock instead. Those
    runs bill Actions minutes exactly as scheduled ones do, so they count here:
    the CLOCK table in its "Due this hour" node is one JSON literal, parsed.
    """
    import glob
    import json as _json
    import re as _re

    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "n8n", "*.template.json"))):
        for node in _json.load(open(path)).get("nodes", []):
            code = node.get("parameters", {}).get("jsCode", "")
            m = _re.search(r"const CLOCK = (\[.*?\]);\n", code)
            if not m:
                continue
            for entry in _json.loads(m.group(1)):
                hours = entry["hours_utc"]
                per_day = 24 if hours == "*" else len(set(hours))
                out[entry["file"]] = out.get(entry["file"], 0) + per_day * 30
    return out


def _scheduled():
    """[(runs_per_month, minutes_per_run, name)] for every workflow on a clock:
    GitHub's own cron, or n8n's clock dispatching it."""
    runs = dict(n8n_clock())
    for name, doc in workflows():
        sched = triggers(doc).get("schedule") or []
        n = sum(runs_per_30_days(entry["cron"]) for entry in sched)
        if n:
            runs[name] = runs.get(name, 0) + n
    out = [(n, MEASURED_MINUTES.get(name, DEFAULT_MINUTES), name) for name, n in runs.items() if n]
    return sorted(out, key=lambda r: -r[0] * r[1])


def test_the_n8n_clock_is_the_only_clock_for_what_it_starts():
    """A workflow on the n8n clock that ALSO keeps a GitHub cron runs twice
    whenever GitHub's cron does fire - double the minutes for the same work.
    And the clock can only start a workflow that exists and takes
    workflow_dispatch; GitHub answers anything else with a 404 or a 422."""
    clock = n8n_clock()
    assert clock, "the n8n clock template (n8n/P6.1_clock.template.json) has no CLOCK table"
    docs = dict(workflows())
    for name in clock:
        assert name in docs, f"the n8n clock dispatches {name}, which does not exist"
        t = triggers(docs[name])
        assert "workflow_dispatch" in t, f"{name} is on the n8n clock but cannot be dispatched"
        assert not t.get("schedule"), (
            f"{name} is on the n8n clock AND has a GitHub cron, so it runs twice "
            f"whenever GitHub's cron fires")


def test_the_scheduled_workflows_fit_in_the_minute_allowance():
    """MINUTES, not runs. Counting runs and assuming two billed minutes each
    put the real bill at ~850 when it was 3,659, which is the whole reason the
    cost was invisible."""
    rows = _scheduled()
    total = sum(n * m for n, m, _ in rows)
    detail = "\n".join(f"    {n:>4} runs x {m:>5.1f} min = {n * m:>7.0f} min/mo  {name}"
                        for n, m, name in rows)
    assert total <= SCHEDULED_MINUTE_BUDGET, (
        f"scheduled workflows cost {total:.0f} minutes a month, over the "
        f"{SCHEDULED_MINUTE_BUDGET} budget.\n"
        f"The free allowance is 2,000 and this repo is already past it.\n"
        f"Cut a cadence, make a job faster, or raise the budget WITH THE REASON "
        f"written into the constant.\n{detail}")


def test_the_run_count_is_still_capped_too():
    """A cheap job scheduled every minute is still a problem, and a minute
    budget alone would not see it until the per-run time was measured."""
    rows = _scheduled()
    total = sum(n for n, _, _ in rows)
    assert total <= SCHEDULED_RUN_BUDGET, (
        f"scheduled runs are {total}/month, over the {SCHEDULED_RUN_BUDGET} budget")


def test_every_scheduled_workflow_has_a_measured_cost():
    """An unmeasured job is costed at DEFAULT_MINUTES, which is generous on
    purpose - but a job that stays unmeasured is a hole in the budget."""
    missing = [name for _, _, name in _scheduled() if name not in MEASURED_MINUTES]
    assert not missing, (
        "these scheduled workflows have never had their runtime measured, so the "
        f"budget is guessing at {DEFAULT_MINUTES} minutes each: {missing}")


def test_the_intraday_cycle_does_not_walk_the_whole_outcome_archive():
    """Four minutes a cycle, six cycles a day, settling zero positions. The
    holdings check takes seconds; the archive sweep belongs in the daily run."""
    import re
    src = open(os.path.join(WF_DIR, "pipeline_intraday.yml")).read()
    m = re.search(r"python scripts/paper_settlement\.py[^\n]*", src)
    assert m, "the intraday pipeline no longer runs the settlement sweep at all"
    assert "--holdings-only" in m.group(0), m.group(0)

    daily = open(os.path.join(WF_DIR, "pipeline_daily.yml")).read()
    d = re.search(r"python scripts/paper_settlement\.py[^\n]*", daily)
    assert d and "--holdings-only" not in d.group(0), (
        "the wide sweep has to happen somewhere or the outcome archive stops "
        "filling and calibration starves")


def test_no_workflow_bills_the_same_commit_twice():
    """`on: push` with no branch filter alongside `on: pull_request` runs the
    same sha on the branch, again for the PR, and again when it lands on main.

    Three identical runs for one piece of information. At ~100 commits a month
    that was ~600 of the 2,000 free minutes spent on duplicates."""
    for name, doc in workflows():
        t = triggers(doc)
        if "push" not in t or "pull_request" not in t:
            continue
        push = t["push"] or {}
        assert push.get("branches"), (
            f"{name} runs on every push AND on pull_request, so every commit on a "
            f"branch with a PR open is billed at least twice. Restrict the push "
            f"trigger to the branches that matter (branches: [main]).")


def test_ci_workflows_cancel_a_superseded_run():
    """A second push while the first run is still going makes the first answer
    worthless - it is testing a commit nobody will ship. Paying for it anyway
    is the easiest minutes to give back."""
    for name, doc in workflows():
        t = triggers(doc)
        if not ({"push", "pull_request"} & set(t)):
            continue
        conc = doc.get("concurrency")
        assert conc, f"{name} has no concurrency group - superseded runs bill in full"
        assert conc.get("cancel-in-progress") is True, (
            f"{name} has a concurrency group but does not cancel in progress")


def test_every_job_has_a_timeout():
    """GitHub's default job timeout is SIX HOURS. One hung job - a weather API
    that accepts the connection and never answers - is 360 minutes, 18% of the
    monthly allowance, spent on a job that was never going to finish."""
    for name, doc in workflows():
        for job_name, job in (doc.get("jobs") or {}).items():
            assert job.get("timeout-minutes"), (
                f"{name} / job {job_name} has no timeout-minutes: it can burn "
                f"the default 6 hours of the monthly allowance on one hang")


# A JOB TIMEOUT CEILING, because "has a timeout" turned out to mean nothing.
#
# Measured over this repository's entire run history (694 runs, 2026-08-23 to
# 2026-09-14, 2,445 billed minutes): FIVE runs account for 1,241 of those
# minutes - 51% of everything the desk has ever spent on Actions. All five are
# `forecasts.yml` started by hand, on 26, 27 and 28 August, at 341, 341, 332,
# 121 and 106 minutes each.
#
# Every one of them had a timeout. The timeout was 350 minutes, which is not a
# bound - it is 17% of the monthly allowance handed to ONE run of ONE job, and
# three of them in a row is the account. `test_every_job_has_a_timeout` passed
# throughout.
#
# The cadence was never the cause and cutting it would have saved almost
# nothing; docs/compute_budget.md has the measurement. This is the guard that
# was actually missing.
MAX_JOB_MINUTES = 120


def test_no_single_job_can_burn_a_fifth_of_the_month():
    """120 minutes is the longest anything here legitimately needs - the
    backtest, which replays months of book history and only runs on demand.
    The daily pipeline sits at 90, the intraday at 30, and the backfill that
    caused this is 25 per link with a bounded continuation chain.

    Raising this ceiling is allowed. Raising it by accident is not."""
    for name, doc in workflows():
        for job_name, job in (doc.get("jobs") or {}).items():
            declared = job.get("timeout-minutes")
            if declared is None:
                continue          # test_every_job_has_a_timeout owns that case
            assert int(declared) <= MAX_JOB_MINUTES, (
                f"{name} / job {job_name} may run for {declared} minutes - "
                f"{int(declared) * 100 // 2000}% of the 2,000-minute monthly "
                f"allowance on a single run. The ceiling is "
                f"{MAX_JOB_MINUTES}. Split the work into resumable links, as "
                f"forecasts.yml does, or raise MAX_JOB_MINUTES deliberately "
                f"and say why.")


def test_every_scheduled_workflow_can_be_started_by_hand():
    """When a schedule is missed - the account was out of minutes, GitHub had an
    incident, the runner queue was full - the data does not backfill itself.
    Someone has to start the run, and a workflow with no workflow_dispatch
    cannot be started at all without editing the file."""
    for name, doc in workflows():
        t = triggers(doc)
        if "schedule" not in t:
            continue
        assert "workflow_dispatch" in t, (
            f"{name} is scheduled but has no workflow_dispatch - a missed run "
            f"cannot be started by hand")


def test_no_workflow_exposes_the_service_key_to_the_browser():
    """The service key bypasses every row-level policy. A NEXT_PUBLIC_ name is
    compiled into the JavaScript bundle and served to anyone who opens the
    site, so the two must never meet - not in a workflow env, not in a build
    arg, not anywhere."""
    for name in sorted(os.listdir(WF_DIR)):
        if not name.endswith((".yml", ".yaml")):
            continue
        text = open(os.path.join(WF_DIR, name)).read()
        for line in text.splitlines():
            if "NEXT_PUBLIC" in line and ("SERVICE" in line.upper() or "SECRET" in line.upper()):
                pytest.fail(f"{name}: {line.strip()} - a service key under a "
                            f"NEXT_PUBLIC_ name ships to the browser")


def test_a_workflow_that_retriggers_itself_is_bounded():
    """forecasts.yml re-runs itself while the backfill reports INCOMPLETE.

    Nothing bounded that. A range that can never finish - no data for those
    dates, an API answering with nothing, a bug that always prints INCOMPLETE -
    re-triggers forever at up to 330 billed minutes a link. Six links is the
    entire monthly allowance, and the symptom is not a loud failure: it is
    every OTHER workflow starting to fail for no visible reason, which is
    exactly what happened on 2026-09-07.

    A self-triggering workflow must carry a depth input and refuse to continue
    past a limit."""
    import glob

    for path in sorted(glob.glob(os.path.join(WF_DIR, "*.yml"))):
        text = open(path).read()
        name = os.path.basename(path)
        if "gh workflow run" not in text:
            continue
        doc = yaml.safe_load(text)
        inputs = ((triggers(doc).get("workflow_dispatch") or {}).get("inputs") or {})
        assert "depth" in inputs, (
            f"{name} re-triggers itself but has no depth input - the chain is "
            f"unbounded and one stuck backfill drains the month's minutes")
        jobs = doc.get("jobs") or {}
        retrigger = [j for j in jobs.values() if "gh workflow run" in yaml.dump(j)]
        assert retrigger, name
        for job in retrigger:
            cond = str(job.get("if", ""))
            assert "depth" in cond, (
                f"{name}: the re-trigger job does not check depth, so nothing "
                f"stops the chain")


def test_nothing_polls_on_a_cron():
    """A cron that fires more often than hourly is a poll, and a poll bills a
    full minute each time to discover there is nothing to do.

    backtest.yml used to run every 10 minutes: 4,320 runs a month, ~6,500
    billed minutes against a 2,000-minute allowance, almost all of it spent
    finding an empty queue. Work that has to start promptly belongs on
    repository_dispatch, which costs nothing until something asks."""
    for name, doc in workflows():
        for entry in (triggers(doc).get("schedule") or []):
            expr = entry["cron"]
            minute = expr.split()[0]
            assert minute != "*" and "/" not in minute, (
                f"{name} has cron {expr!r} - that fires within the hour, which "
                f"is a poll. Use repository_dispatch for prompt work.")


def test_the_pipelines_do_not_skip_a_step_after_a_failure():
    """Merging six workflows into one job traded six independent runs for one
    chain, and a chain stops at the first failure by default.

    That would mean a broken settlement run silently costing a day of skill,
    databank, calibration and every derived table - six things going stale
    because one thing broke, which is worse than the bill the merge saved.
    Every step carries `if: !cancelled()`, so a failure is reported and the
    rest still runs."""
    import glob

    checked = 0
    for path in sorted(glob.glob(os.path.join(WF_DIR, "pipeline_*.yml"))):
        doc = yaml.safe_load(open(path))
        name = os.path.basename(path)
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in job["steps"]:
                if "uses" in step or step.get("run", "").startswith("pip install"):
                    continue          # setup: a failure here IS fatal
                cond = str(step.get("if", ""))
                assert "cancelled()" in cond, (
                    f"{name} / {step.get('name', step.get('run'))!r} has no "
                    f"`if: !cancelled()` - one failure upstream silently skips it")
                assert step.get("timeout-minutes"), (
                    f"{name} / {step.get('name')!r} has no step timeout, so one "
                    f"hang burns the whole job's budget")
                checked += 1
    assert checked >= 10, f"expected the pipeline steps to be checked, saw {checked}"


def test_n8n_only_dispatches_workflows_that_exist():
    """P2.1 fires GitHub Actions by FILENAME, and nothing connected the two.

    Merging nine workflows into two pipelines left P2.1 dispatching
    databank.yml, skill.yml and model_forecast.yml - three files that no longer
    exist. GitHub answers a dispatch to a missing workflow with a 404, so the
    relearn chain would have reported failures for stages that were simply
    pointed at the wrong name, days after the rename, with nothing tying the
    cause to the effect.

    A workflow file may be renamed. It may not be renamed without the thing
    that fires it following."""
    import glob
    import json as _json
    import re as _re

    have = {os.path.basename(p) for p in glob.glob(os.path.join(WF_DIR, "*.yml"))}
    n8n_dir = os.path.join(ROOT, "n8n")
    seen = 0
    for path in sorted(glob.glob(os.path.join(n8n_dir, "*.json"))):
        doc = _json.load(open(path))
        for node in doc.get("nodes", []):
            code = node.get("parameters", {}).get("jsCode", "")
            # only the stage table, not the prose around it: a comment may
            # name an old file precisely to explain what it was renamed from
            for line in code.splitlines():
                if line.lstrip().startswith("//"):
                    continue
                for ref in _re.findall(r"'([A-Za-z0-9_]+\.yml)'", line):
                    seen += 1
                    assert ref in have, (
                        f"{os.path.basename(path)} / {node['name']} dispatches "
                        f"{ref}, which is not in .github/workflows/. Every "
                        f"dispatch to it 404s.")
    assert seen >= 3, f"expected to find the dispatch table, saw {seen} references"
