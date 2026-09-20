"""A health panel that names a job nobody writes reports a false outage.

"wy am i seein tis wen everytin was workin - 4 of 7 feeds are not running."

Because four of the seven names in PipelineStatus.tsx were never written by
anything. Measured on the live database 2026-09-20, while the panel said
"never run" for all four:

    observations    no such job, ever          scripts write ingest_observations
    forecasts       no such job, ever          scripts write ingest_forecasts
    probabilities   no such job, ever          scripts write probability_engine
    live_weather    real, last run 5 Sep       the live path is n8n's P1.2

ingest_observations had written at 11:13, P1.2_nws_monitor at 15:00,
ingest_forecasts at 09:01 and probability_engine at 13:05 - every one of them
that same day. The panel then pointed at the healthiest link in the chain and
told him to start there.

The three names that were RIGHT are the three n8n ones, whose job strings are
their template filenames. The four that were wrong are the Python side, where
the name was typed from memory and nothing ever compared it to what the
scripts log. That is this file's job.

IT ALSO COVERS THE SHAPE, because a feed can have more than one writer. Live
weather and forecasts each have an Actions workflow AND an n8n flow, so the
NEWEST of them is the truth; probabilities and edges are both required, so the
OLDEST is, and a half-running feed must not read as healthy.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PANEL = (ROOT / "web" / "components" / "PipelineStatus.tsx").read_text(encoding="utf-8")


def _names_the_repo_writes():
    """Every job string something in this repository actually logs."""
    written = set()

    # Python: log_run("name", ...) and log_run('name', ...), including the
    # multi-line calls that put the name on its own line.
    for path in (ROOT / "scripts").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        written.update(re.findall(r"log_run\(\s*[\"']([\w.]+)[\"']", src))
        written.update(re.findall(r"log_run\(\s*\n\s*[\"']([\w.]+)[\"']", src))

    # n8n: a flow's job string is its template filename.
    for path in (ROOT / "n8n").glob("*.template.json"):
        written.add(path.name.removesuffix(".template.json"))

    return written


def _chain():
    """The CHAIN entries as (jobs, mode, label)."""
    block = PANEL[PANEL.index("const CHAIN"): PANEL.index("export default")]
    entries = []
    for m in re.finditer(r"\{\s*jobs:\s*\[([^\]]*)\]\s*,\s*mode:\s*\"(either|both)\"\s*,\s*label:\s*\"([^\"]+)\"", block):
        jobs = re.findall(r"\"([\w.]+)\"", m.group(1))
        entries.append((jobs, m.group(2), m.group(3)))
    return entries


def test_the_chain_parses_at_all():
    """If this breaks, every assertion below silently passes on nothing."""
    chain = _chain()
    assert len(chain) >= 7, f"only parsed {len(chain)} chain entries from the panel"


@pytest.mark.parametrize("jobs,mode,label", _chain())
def test_every_job_the_panel_watches_is_one_something_writes(jobs, mode, label):
    """The exact bug: a name nobody logs can only ever read 'never run'."""
    written = _names_the_repo_writes()
    unknown = [j for j in jobs if j not in written]
    assert not unknown, (
        f"'{label}' watches {unknown}, which nothing in scripts/ or n8n/ ever writes to "
        f"ingest_log. It can only ever report 'never run', however healthy the feed is."
    )


def test_the_names_that_were_wrong_are_not_back():
    """Named individually, because these four are what the panel reported as
    dead on a desk where all four had written within the hour."""
    written = _names_the_repo_writes()
    for guessed, real in (
        ("observations", "ingest_observations"),
        ("forecasts", "ingest_forecasts"),
        ("probabilities", "probability_engine"),
    ):
        assert guessed not in written, (
            f"something now writes {guessed!r} - if that is deliberate, this test is the "
            "thing to update, but check it is not a second name for the same feed"
        )
        assert real in written, f"{real} is no longer written by anything"
    watched = {j for jobs, _, _ in _chain() for j in jobs}
    assert "observations" not in watched and "forecasts" not in watched
    assert "probabilities" not in watched


def test_live_weather_does_not_rely_on_the_manual_fallback_alone():
    """live_weather IS a real job - it is the Actions workflow, kept as the
    fallback for when n8n is down, and it last ran 5 Sep. Watching only that
    name reports the live desk as two weeks stale."""
    entry = next(e for e in _chain() if e[2] == "Live weather")
    assert "P1.2_nws_monitor" in entry[0], (
        "the live weather feed is served by n8n P1.2; the Actions job is a manual fallback"
    )


def test_a_feed_needing_two_writers_is_judged_on_the_older_one():
    """Probabilities without edges leaves Opportunities empty, so the feed is
    only as fresh as its stalest half."""
    entry = next(e for e in _chain() if e[2] == "Probability + edge")
    jobs, mode, _ = entry
    assert set(jobs) == {"probability_engine", "edge_engine"}
    assert mode == "both", (
        "edges and probabilities are both required - taking the newest would let a dead "
        "edge engine hide behind a live probability engine"
    )


def test_alternative_writers_are_judged_on_the_newer_one():
    """Actions OR n8n: either one running means the feed is running."""
    for label in ("Live weather", "Forecasts"):
        jobs, mode, _ = next(e for e in _chain() if e[2] == label)
        assert len(jobs) > 1 and mode == "either", (
            f"{label} has an Actions path and an n8n path; whichever ran most recently is "
            "the truth about the feed"
        )


# --- and the label has to name something that actually runs ----------------
#
# The names above were only half of it. The panel also told Hassan WHERE to go
# and fix each feed, and two of those were dead ends:
#
#   "Actions · Live Weather"   live_weather.yml has no schedule at all. 47
#                              runs in its life, every one workflow_dispatch,
#                              last on 5 Sep. It is the manual fallback for
#                              when n8n is down, and pointing at it as the
#                              feed's runner sends you to a button.
#   "Actions · Probabilities"  no such workflow has ever existed. Probabilities
#                              and edges run inside pipeline_intraday.
#
# So a label may name an Actions workflow only if that workflow is on a cron.
# A manual one has to say so.

import yaml


def _scheduled_workflow_titles():
    """The `name:` of every .github workflow that actually runs on a schedule."""
    titles = {}
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        # PyYAML reads a bare `on:` key as the boolean True.
        triggers = doc.get("on", doc.get(True)) or {}
        titles[doc.get("name", path.stem)] = bool(
            isinstance(triggers, dict) and triggers.get("schedule")
        )
    return titles


def _labels():
    block = PANEL[PANEL.index("const CHAIN"): PANEL.index("export default")]
    return re.findall(r'label:\s*"([^"]+)",\s*where:\s*"([^"]+)"', block)


def test_a_label_naming_an_actions_workflow_means_one_that_is_scheduled():
    scheduled = _scheduled_workflow_titles()
    for label, where in _labels():
        for named in re.findall(r"Actions · ([A-Za-z0-9 ()/]+?)(?: is manual only|\s*/|$)", where):
            named = named.strip()
            # Prefix, so "Actions · Observations" may stand for the workflow
            # actually called "Observations (IEM METAR)" - short enough for the
            # row, and still the name you will find in the Actions list.
            matches = [w for w in scheduled if w.startswith(named)]
            assert matches, (
                f"'{label}' sends you to Actions · {named}, which is not the start of any "
                f"workflow name in .github/workflows. Known: {sorted(scheduled)}"
            )
            assert any(scheduled[w] for w in matches) or "manual" in where, (
                f"'{label}' points at Actions · {named} as its runner, but that workflow "
                "has no schedule - it only runs when someone presses the button. Say so "
                "in the label or point at whatever actually runs."
            )


def test_the_two_dead_ends_are_not_back():
    wheres = " | ".join(w for _, w in _labels())
    assert "Actions · Probabilities" not in wheres, (
        "no such workflow has ever existed; probabilities run inside pipeline_intraday"
    )
    assert not re.search(r"Actions · Live Weather(?! is manual only)", wheres), (
        "live_weather.yml has no cron - naming it as the feed's runner sends you to a "
        "button that has not been pressed since 5 Sep"
    )
