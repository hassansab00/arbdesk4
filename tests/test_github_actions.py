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


# A private repository meters Actions minutes: 2,000 a month on the free
# plan. GitHub bills each RUN rounded up to the whole minute, so the count of
# runs - not their length - is what the schedules control, and it is the only
# figure these files actually determine.
#
# Measured durations on this repo: observations ~2 min, everything else under
# one. Budget at 2 billed minutes a run, and 700 scheduled runs is ~1,400
# minutes, leaving ~600 for CI. That is the whole allowance, which is why
# adding a schedule is a decision and not a detail.
SCHEDULED_RUN_BUDGET = 700


def test_the_scheduled_workflows_fit_in_the_minute_allowance():
    total = 0
    breakdown = []
    for name, doc in workflows():
        sched = triggers(doc).get("schedule") or []
        n = sum(runs_per_30_days(entry["cron"]) for entry in sched)
        if n:
            breakdown.append((n, name))
            total += n
    breakdown.sort(reverse=True)
    detail = "\n".join(f"    {n:>4} runs/mo  {name}" for n, name in breakdown)
    assert total <= SCHEDULED_RUN_BUDGET, (
        f"scheduled runs are {total}/month, over the {SCHEDULED_RUN_BUDGET} budget.\n"
        f"At ~2 billed minutes each that is ~{total * 2} of the 2,000 free minutes,\n"
        f"before a single CI run. Cut a cadence or raise the budget deliberately.\n{detail}")


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
