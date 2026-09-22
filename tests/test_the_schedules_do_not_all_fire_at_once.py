"""Ten workflows, one free-tier Postgres, and every one of them fired at :00.

THE FAILURE. `AD4 P0.3 - Book + Volume Snapshot` failed almost every day with

    Supabase refused the write at "Insert snapshots":
    canceling statement due to statement timeout (57014)

and the workflow's own Summary node had already guessed right: "That is the
DATABASE being too busy to reply, not a bad row... Check whether several
workflows are running at once."

It was not a slow insert. EXPLAIN ANALYZE of an 1,100-row insert - exactly
P0.3's size, with its foreign-key and archive triggers - measured 476 ms
against a statement_timeout of two minutes:

    insert 254 ms   FK trigger 143 ms   archive triggers 74 ms

What was true instead is visible in the execution log for 19 Sep. Three
different workflows started within nine seconds of each other and all three
died at the same second:

    SA3nGidtSpXOrz9g  P0.4  started 15:00:44  stopped 15:01:13
    e0HiTavIzilGmBPo  P0.3  started 15:00:51  stopped 15:01:13
    speyDmtDN01cEI5N  P0.2  started 15:00:53  stopped 15:01:13

Eight AD4 workflows began within 53 seconds of the top of the hour, because a
Schedule Trigger with `hoursInterval` and no `triggerAtMinute` fires at minute
zero - and eight of them had been written that way, independently, each one
correct on its own.

`settings.workflow_schedules` in the database is NOT the cron. It is a
minimum-gap gate the workflows consult after they start, so it can stop a run
from doing work but cannot stop it from opening a connection. The start times
live only in the Schedule Trigger nodes, which is why they are what this file
reads.

WHY MINUTES AND NOT HOURS. The n8n instance evaluates schedules in its own
timezone, which is currently UTC+3 - P0.5's daily trigger at hour 0 fires at
21:00 UTC, and P2.2's `*/6` cron fires at 21/03/09/15 UTC. That offset is a
whole number of hours and it changes with DST, so the HOUR a job runs in UTC is
not stable but the MINUTE PAST THE HOUR is. Everything below is therefore
reasoned in minutes past the hour, which is the part that does not move.

AND NOT AGAINST GITHUB ACTIONS. Actions crons are nominal: observations.yml is
scheduled `7 */6 * * *` and its last four scheduled runs started at 15:57,
11:11, 04:44 and 20:31. A private repository's scheduled runs are queued, not
timed, so there is no minute to negotiate with. The allocation below still
leaves the nominal Actions minutes (0, 7, 10, 15, 30) clear, because it costs
nothing, but this file does not assert it - a test may only hold what is
actually under our control, and the collision that killed those three runs was
n8n against n8n.
"""

import json
import re
from pathlib import Path

import pytest

N8N = Path(__file__).resolve().parents[1] / "n8n"

# ---------------------------------------------------------------------------
# HOW LONG EACH ONE ACTUALLY HOLDS THE DATABASE.
#
# Worst of the last runs of each workflow, read from the n8n execution log on
# 2026-09-20. These are not estimates and the gaps below are sized from them.
#
# P0.3 is the whole reason this table exists. Everything else is under twenty
# seconds; P0.3 is a hundred and forty, because it walks the CLOB order book
# for every live band. The first draft of this stagger put P2.2 at :26 - two
# minutes after P0.3 - and that is inside P0.3's window. Guessing durations is
# how the fix recreates the bug.
# ---------------------------------------------------------------------------
RUNTIME_SECONDS = {
    "AD4 P0.2 - Market Discovery":       5,    # 2.9-4.1 s
    "AD4 P0.3 - Book + Volume Snapshot": 140,  # 105-140 s
    "AD4 P0.4 - Trade History":          13,   # 7.9-12.6 s
    "AD4 P0.5 - Refresh Rules Text":     6,    # 4.6-5.9 s
    "AD4 P1.2 - NWS Monitor":            18,   # 5.7-17.3 s
    "AD4 P1.3 - NWS Forecast":           7,    # 2.2-6.5 s
    "AD4 P1.4 - NWS Gridpoint":          4,    # 2.7-3.7 s
    "AD4 P1.5 - Open-Meteo Global":      13,   # 2.8-12.8 s
    # One batched IEM request for the whole board, not 48. Measured on the
    # live service from this instance: 48 of 48 stations in ~4 s over a
    # two-day window, and 10 KB over a four-hour one. Held at 20 s to cover
    # the Supabase read and write either side of it.
    "AD4 P1.6 - Station Observations (IEM METAR)": 20,
    "AD4 P2.2 - Paper Maintenance":      4,    # 2.6-3.8 s
    "AD4 - P4.1 Health Watchdog":        13,   # 3.5-12.8 s
    # Inactive (no SMTP credential), so it has no scheduled runs to measure.
    # Held at P0.3's figure so that enabling it cannot quietly overlap anything.
    "AD4 - P3.1 Email Digests":          140,
}

# A minute of head-room on top of the measured worst case. P0.3's runs vary by
# 35 s across the sample, so a minute covers a bad one, and it is far smaller
# than the tightest gap the allocation actually uses (three minutes).
MARGIN_SECONDS = 60


# ---------------------------------------------------------------------------
# Resolving a Schedule Trigger to the minutes of the day it fires.
# ---------------------------------------------------------------------------
def _cron_field(spec: str, lo: int, hi: int) -> list[int]:
    """The subset of [lo, hi] a cron field selects.

    Deliberately narrow: `*`, `*/n`, `a`, and comma lists of those. Anything
    else raises rather than being quietly treated as "fires never", which would
    make a schedule this file cannot read look like a schedule that never
    collides.
    """
    out: set[int] = set()
    for part in spec.split(","):
        if part == "*":
            out |= set(range(lo, hi + 1))
        elif m := re.fullmatch(r"\*/(\d+)", part):
            out |= set(range(lo, hi + 1, int(m.group(1))))
        elif re.fullmatch(r"\d+", part):
            out.add(int(part))
        else:
            raise ValueError(f"cron field {spec!r} uses syntax this test cannot read")
    return sorted(out)


def fire_minutes(interval: dict) -> list[int]:
    """Minutes-since-midnight at which one interval entry fires."""
    field = interval["field"]
    if field == "cronExpression":
        parts = interval["expression"].split()
        if len(parts) == 6:          # n8n allows a leading seconds field
            parts = parts[1:]
        if len(parts) != 5:
            raise ValueError(f"unreadable cron {interval['expression']!r}")
        minute, hour, dom, _month, dow = parts
        # Nothing here restricts by date, and a cron that did would need this
        # file to reason about which days two jobs share. Refusing is the only
        # answer that cannot quietly widen a schedule it did not understand.
        if (dom, dow) != ("*", "*"):
            raise ValueError(
                f"cron {interval['expression']!r} restricts by day; this file compares "
                "minutes past the hour and cannot tell which days two jobs share"
            )
        return sorted(h * 60 + m
                      for h in _cron_field(hour, 0, 23)
                      for m in _cron_field(minute, 0, 59))
    at_minute = int(interval.get("triggerAtMinute", 0))
    if field == "hours":
        # n8n compiles hoursInterval n to cron `*/n` on the hour field.
        step = int(interval.get("hoursInterval", 1))
        return [h * 60 + at_minute for h in range(0, 24, step)]
    if field == "days":
        if int(interval.get("daysInterval", 1)) != 1:
            raise ValueError("a multi-day interval needs its own handling here")
        return [int(interval.get("triggerAtHour", 0)) * 60 + at_minute]
    raise ValueError(
        f"schedule field {field!r} is not handled - add it rather than letting a "
        "schedule this file cannot read pass by default"
    )


def _schedules():
    """(workflow name, node name, minutes-of-day) for every scheduled trigger."""
    found = []
    for path in sorted(N8N.glob("*.template.json")):
        wf = json.loads(path.read_text(encoding="utf-8"))
        for node in wf["nodes"]:
            if node["type"] != "n8n-nodes-base.scheduleTrigger":
                continue
            for interval in node["parameters"]["rule"]["interval"]:
                found.append((wf["name"], node["name"], fire_minutes(interval)))
    return found


SCHEDULES = _schedules()


def test_there_are_schedules_to_check():
    """A glob that silently matches nothing would make every test below pass."""
    assert len(SCHEDULES) >= 11, f"only {len(SCHEDULES)} schedule triggers found in {N8N}"


def test_every_scheduled_workflow_has_a_measured_runtime():
    """The enumeration. A workflow added to the schedule without anyone timing
    it cannot be placed safely, and the first draft of this stagger proves the
    point: P2.2 was put two minutes behind a workflow that runs for two and a
    third."""
    missing = sorted({name for name, _, _ in SCHEDULES} - set(RUNTIME_SECONDS))
    assert not missing, (
        f"{missing} is scheduled and has no measured runtime in RUNTIME_SECONDS. "
        "Read its last few executions in n8n and record the worst one."
    )


def test_no_two_workflows_start_in_the_same_minute():
    """The regression itself, stated plainly: eight of these fired at :00."""
    by_minute: dict[int, list[str]] = {}
    for name, node, minutes in SCHEDULES:
        for m in minutes:
            by_minute.setdefault(m % 60, []).append(f"{name} / {node}")
    clashes = {m: sorted(set(v)) for m, v in by_minute.items() if len(set(v)) > 1}
    assert not clashes, (
        "these share a minute past the hour and so can start in the same second: "
        + "; ".join(f":{m:02d} -> {v}" for m, v in sorted(clashes.items()))
    )


def test_each_workflow_finishes_before_the_next_one_starts():
    """The property that actually matters. Distinct minutes are not enough when
    one workflow runs for 140 seconds."""
    events = []   # (minute of day, workflow)
    for name, _, minutes in SCHEDULES:
        events += [(m, name) for m in minutes]
    events.sort()

    failures = []
    for i, (start, name) in enumerate(events):
        nxt_start, nxt_name = events[(i + 1) % len(events)]
        if nxt_name == name:
            continue
        gap = (nxt_start - start) % 1440 * 60
        need = RUNTIME_SECONDS[name] + MARGIN_SECONDS
        if gap < need:
            failures.append(
                f"{name} starts at {start // 60:02d}:{start % 60:02d} and runs "
                f"{RUNTIME_SECONDS[name]}s, but {nxt_name} starts {gap}s later "
                f"(needs {need}s)"
            )
    assert not failures, "\n".join(failures)


def test_nothing_has_gone_back_to_firing_on_the_hour():
    """`hoursInterval` with no `triggerAtMinute` defaults to minute 0, so this
    regression reappears by DELETING a line rather than by writing one - which
    is why it is worth naming on its own."""
    on_the_hour = sorted({f"{name} / {node}" for name, node, minutes in SCHEDULES
                          if any(m % 60 == 0 for m in minutes)})
    assert not on_the_hour, (
        f"{on_the_hour} fire at minute 0 again. A Schedule Trigger needs an explicit "
        "triggerAtMinute (or a cron with one) or it joins the pile at the top of the hour."
    )


@pytest.mark.parametrize("expression,expected", [
    ("31 */6 * * *", [31, 391, 751, 1111]),
    ("58 */6 * * *", [58, 418, 778, 1138]),
    ("0 4 * * *", [240]),
])
def test_the_cron_reader_agrees_with_cron(expression, expected):
    """This file's conclusions are only as good as its parser."""
    assert fire_minutes({"field": "cronExpression", "expression": expression}) == expected


def test_the_interval_reader_matches_what_n8n_compiles():
    """n8n turns hoursInterval n into `*/n` on the hour field, so every-6-hours
    means 0/6/12/18 and not "six hours after activation"."""
    got = fire_minutes({"field": "hours", "hoursInterval": 6, "triggerAtMinute": 20})
    assert got == [20, 380, 740, 1100]
    assert fire_minutes({"field": "days", "daysInterval": 1,
                         "triggerAtHour": 1, "triggerAtMinute": 38}) == [98]


def test_an_unreadable_schedule_fails_rather_than_passing():
    """A schedule shape this file cannot resolve must stop the suite, not be
    treated as "fires never" and so collide with nothing."""
    with pytest.raises(ValueError):
        fire_minutes({"field": "weeks", "weeksInterval": 1})
    with pytest.raises(ValueError):
        fire_minutes({"field": "cronExpression", "expression": "0 0 * * MON-FRI"})


# ---------------------------------------------------------------------------
# AND THE TWO PLACES THAT DESCRIBE THE SAME SCHEDULE IN WORDS.
#
# The stagger was applied to twelve Schedule Triggers, and two other files
# already claimed to say when these workflows run. Both were wrong before this
# change and neither had anything holding it:
#
#   n8n/README.md            listed P0.3 at "15 min" and P0.2 and P0.4 at
#                            "1 h" - the cadences before 15 Sep - and did not
#                            mention P2.2 at all
#   sql/ad4_20_schedules.sql seeds the gate at every_minutes EQUAL to the
#                            trigger cadence (360 against a 6 h trigger), which
#                            skips every other run, and omitted P1.4, P1.5 and
#                            P2.1 entirely
#
# The live settings row had been corrected by hand; the file that installs it
# had not, so only a fresh install would have got the broken version - which is
# exactly when nobody is watching for it.
# ---------------------------------------------------------------------------
README = (N8N / "README.md").read_text(encoding="utf-8")
SCHEDULES_SQL = (Path(__file__).resolve().parents[1]
                 / "sql" / "ad4_20_schedules.sql").read_text(encoding="utf-8")


def _by_template():
    """template filename -> every minute-of-day it fires, across all triggers."""
    out: dict[str, list[int]] = {}
    for path in sorted(N8N.glob("*.template.json")):
        wf = json.loads(path.read_text(encoding="utf-8"))
        minutes: list[int] = []
        for node in wf["nodes"]:
            if node["type"] == "n8n-nodes-base.scheduleTrigger":
                for interval in node["parameters"]["rule"]["interval"]:
                    minutes += fire_minutes(interval)
        if minutes:
            out[path.name] = sorted(minutes)
    return out


def cadence_label(minutes: list[int]) -> str:
    """How often, in the words the README uses."""
    if len(minutes) == 1:
        return "1 day"
    gaps = {(minutes[(i + 1) % len(minutes)] - m) % 1440 for i, m in enumerate(minutes)}
    if len(gaps) == 1:
        gap = gaps.pop()
        return "1 h" if gap == 60 else f"{gap // 60} h" if gap % 60 == 0 else f"{gap} min"
    return f"{len(minutes)}×/day"


def starts_label(minutes: list[int]) -> str:
    """When, in the words the README uses: a bare minute when it is the same
    every time, and a clock time when it is not."""
    if len(minutes) > 1 and len({m % 60 for m in minutes}) == 1:
        return f":{minutes[0] % 60:02d}"
    return ", ".join(f"{m // 60:02d}:{m % 60:02d}" for m in minutes)


def _readme_rows():
    rows = {}
    for line in README.splitlines():
        m = re.match(r"\|\s*\d+\s*\|\s*`([\w.]+\.template\.json)`\s*\|(.*)\|\s*$", line)
        if m:
            cells = [c.strip() for c in m.group(2).split("|")]
            rows[m.group(1)] = cells
    return rows


def test_the_readme_lists_every_template():
    listed, on_disk = set(_readme_rows()), {p.name for p in N8N.glob("*.template.json")}
    assert listed == on_disk, (
        f"only in the README: {sorted(listed - on_disk)}; "
        f"only on disk: {sorted(on_disk - listed)}"
    )


@pytest.mark.parametrize("template", sorted(_by_template()))
def test_the_readme_cadence_and_start_match_the_template(template):
    minutes = _by_template()[template]
    cells = _readme_rows()[template]
    cadence, starts = cells[-3], cells[-2]
    assert cadence == cadence_label(minutes), (
        f"{template}: README says it runs every {cadence!r}, the Schedule Trigger says "
        f"{cadence_label(minutes)!r}"
    )
    assert starts == starts_label(minutes), (
        f"{template}: README says it starts at {starts!r}, the Schedule Trigger says "
        f"{starts_label(minutes)!r}"
    )


def _seeded_gate() -> dict:
    body = SCHEDULES_SQL[SCHEDULES_SQL.index("values ('workflow_schedules', '{"):]
    body = body[body.index("{"):body.index("}'::jsonb)") + 1]
    return json.loads(body)


# Minutes the gate must stay below the trigger cadence by. It absorbs the run
# itself - ingest_log is stamped at the end, so consecutive stamps sit a cadence
# apart plus however long the run took - and P0.3, the longest, takes 140 s.
GATE_HEADROOM_MINUTES = 5


@pytest.mark.parametrize("template", sorted(_by_template()))
def test_every_scheduled_workflow_has_a_gate_setting(template):
    job = template.replace(".template.json", "")
    assert job in _seeded_gate(), (
        f"{job} has a Schedule Trigger and no entry in sql/ad4_20_schedules.sql, so a "
        "fresh install gates it on nothing"
    )


@pytest.mark.parametrize("template", sorted(_by_template()))
def test_the_gate_sits_below_the_cadence_it_gates(template):
    """A gate set to the cadence skips every other run, because the run's own
    duration pushes the next trigger inside the minimum gap."""
    minutes = _by_template()[template]
    shortest_gap = min((minutes[(i + 1) % len(minutes)] - m) % 1440
                       for i, m in enumerate(minutes)) if len(minutes) > 1 else 1440
    every = _seeded_gate()[template.replace(".template.json", "")]["every_minutes"]
    assert every <= shortest_gap - GATE_HEADROOM_MINUTES, (
        f"{template}: the gate enforces a {every} min minimum gap but the trigger fires "
        f"every {shortest_gap} min, so runs arriving on time are skipped"
    )
