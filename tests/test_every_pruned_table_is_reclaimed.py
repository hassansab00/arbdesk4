"""A table the archive prunes but nothing reclaims will breach the tier.

WHY THIS FILE EXISTS. sql/ad4_66_reclaim_archived_tables.sql was written when
the archive ran weekly over three tables, and it scheduled exactly those three.
Then research_captures joined the archive on 17 Sep, paper_resolution_evidence
on 19 Sep, and the archive itself went daily. Neither new table got a reclaim
job and the cadence stayed weekly.

A prune marks rows dead; only VACUUM FULL hands the pages back to the
operating system, and pg_database_size - the number the 500 MB free tier is
measured against - moves for the second, not the first. So the two
fastest-shedding tables on the desk shed about 40 MB a day into space nothing
ever returned. Measured 20 Sep: 591 MB against a 500 MB limit whose
enforcement is read-only mode. Reclaiming the set by hand gave back 76 MB the
same minute.

Nothing was broken. Two halves of one cycle were edited independently, and
nobody owned the join. This test is that owner: add a dataset to the archive
without a reclaim job and it fails here, naming the table.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

RECLAIM_SQL = ROOT / "sql" / "ad4_66_reclaim_archived_tables.sql"


def _scheduled_jobs():
    """{table: cron schedule} for every cron.schedule in the reclaim file."""
    text = RECLAIM_SQL.read_text(encoding="utf-8")
    jobs = {}
    for name, schedule, command in re.findall(
        r"cron\.schedule\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", text
    ):
        table = command.rsplit(".", 1)[-1].strip()
        jobs[table] = schedule
        assert name == f"ad4_reclaim_{table}", (
            f"job {name!r} vacuums {table} - the name and the table must agree, "
            "because cron.schedule replaces BY NAME and a mismatch silently "
            "leaves two jobs where one was meant"
        )
    return jobs


def _archived_tables():
    import archive_observations as ao

    return {name: spec["table"] for name, spec in ao.TABLES.items()}


def test_every_table_the_archive_prunes_has_a_reclaim_job():
    """The exact gap: two tables joined the archive and nothing reclaimed them."""
    jobs = _scheduled_jobs()
    missing = {
        name: table for name, table in _archived_tables().items() if table not in jobs
    }
    assert not missing, (
        f"these datasets are pruned by scripts/archive_observations.py and never "
        f"reclaimed: {missing}. A prune alone does not shrink the database - add a "
        f"cron.schedule to {RECLAIM_SQL.name} or the free tier fills up again."
    )


@pytest.mark.parametrize("dataset,table", sorted(_archived_tables().items()))
def test_a_daily_prune_is_reclaimed_daily(dataset, table):
    """Cadence has to follow the prune that feeds it.

    research and resolution are pruned EVERY day - about 7,400 and 4,000 rows,
    the latter at ~7 KB each. Reclaiming those weekly banks a week of dead
    pages before returning any, which is 280 MB on a 500 MB tier.
    """
    import archive_observations as ao

    # The same default the script applies, so a spec that stops declaring one
    # is read here exactly as the archive reads it.
    keep_days = ao.TABLES[dataset].get("keep_days", 90)
    jobs = _scheduled_jobs()
    if table not in jobs:
        # test_every_table_the_archive_prunes_has_a_reclaim_job names this
        # properly; failing twice on the same cause just buries its message.
        pytest.skip(f"{table} has no reclaim job at all")
    schedule = jobs[table]
    day_of_week = schedule.split()[4]
    if keep_days <= 3:
        assert day_of_week == "*", (
            f"{table} keeps only {keep_days} days, so the archive sheds rows from "
            f"it every night, but its reclaim runs {schedule!r}. A week of daily "
            "pruning banked behind a weekly reclaim is what breached the tier."
        )


def test_the_reclaim_does_not_collide_with_the_daily_pipeline():
    """The weekly jobs began at 04:00 Monday, which is when pipeline_daily
    fires - and that pipeline runs ingest_forecasts.py, writing the very first
    table in the sequence. VACUUM FULL takes an ACCESS EXCLUSIVE lock."""
    pipeline = (ROOT / ".github" / "workflows" / "pipeline_daily.yml").read_text(encoding="utf-8")
    hour = int(re.search(r"cron:\s*'(\d+)\s+(\d+)", pipeline).group(2))
    for table, schedule in _scheduled_jobs().items():
        minute, job_hour = schedule.split()[0], schedule.split()[1]
        assert int(job_hour) != hour, (
            f"{table} reclaims at {schedule!r}, the same hour pipeline_daily runs "
            f"({hour:02d}:00 UTC). An exclusive lock against a live writer stalls it."
        )


def test_the_daily_reclaims_run_after_the_archive_that_feeds_them():
    """Reclaiming before the prune rewrites the same rows and returns nothing."""
    archive = (ROOT / ".github" / "workflows" / "archive_observations.yml").read_text(encoding="utf-8")
    m = re.search(r"cron:\s*'(\d+)\s+(\d+)\s+\*\s+\*\s+\*'", archive)
    assert m, "archive_observations.yml no longer has a daily cron"
    archive_minutes = int(m.group(2)) * 60 + int(m.group(1))
    for table, schedule in _scheduled_jobs().items():
        minute, hour, _, _, dow = schedule.split()
        if dow != "*":
            continue
        assert int(hour) * 60 + int(minute) > archive_minutes, (
            f"{table} reclaims at {schedule!r}, at or before the {m.group(2)}:{m.group(1)} "
            "archive that creates the dead rows it is meant to return"
        )


# --------------------------------------------------------------------------
# THE CADENCE, not just the existence of a job.
#
# Measured 2026-09-22 with the last weekly run six days old: 22,737 dead rows
# on book_snapshots and 15,234 on edges, and reclaiming just those two took
# the database from 529.4 MB to 499.4 MB - thirty megabytes of a five hundred
# megabyte plan sitting in files nothing could read. The jobs existed; their
# cadence was the defect.
# --------------------------------------------------------------------------

import pathlib as _pathlib
import re as _re

_RECLAIM = (_pathlib.Path(__file__).resolve().parents[1]
            / "sql/ad4_66_reclaim_archived_tables.sql")


def _schedule_of(jobname):
    sql = _RECLAIM.read_text()
    m = _re.search(rf"'{_re.escape(jobname)}',\s*\n\s*'([^']+)'", sql)
    return m.group(1) if m else None


@pytest.mark.parametrize("jobname", [
    "ad4_reclaim_book_snapshots",
    "ad4_reclaim_edges",
])
def test_the_tables_that_shed_most_are_reclaimed_daily(jobname):
    sched = _schedule_of(jobname)
    assert sched, f"{jobname} is not scheduled at all"
    minute, hour, dom, month, dow = sched.split()
    assert (dom, dow) == ("*", "*"), (
        f"{jobname} runs on '{sched}' - pinning day-of-month or day-of-week "
        "makes it weekly, and a week of these two is thirty megabytes of a "
        "five hundred megabyte tier"
    )


def test_the_two_daily_rewrites_do_not_overlap():
    # VACUUM FULL holds the old file and the new one at once. Two large
    # rewrites at the same minute need both peaks at the same time.
    a = _schedule_of("ad4_reclaim_book_snapshots").split()
    b = _schedule_of("ad4_reclaim_edges").split()
    assert (a[1], a[0]) != (b[1], b[0]), "both large rewrites start at the same minute"


def test_the_reclaims_finish_before_the_daily_pipeline():
    # pipeline_daily's cron is 0 4 * * *. A rewrite still holding an ACCESS
    # EXCLUSIVE lock when it starts stalls the whole run.
    for jobname in ("ad4_reclaim_book_snapshots", "ad4_reclaim_edges"):
        minute, hour = _schedule_of(jobname).split()[:2]
        assert int(hour) < 4 or (int(hour) == 3), (
            f"{jobname} at {hour}:{minute} does not clear pipeline_daily's 04:00"
        )
