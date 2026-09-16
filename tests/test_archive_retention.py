"""The archive has to actually run, and actually reach the big table.

Every fault below was live on 16 Sep with the database at 436 MB of 500, and
none of them produced a failure anybody could see:

  - the scheduled run could never prune, because `--commit` was gated on
    github.event.inputs, which is null on a cron event. It would have fired on
    1 Oct, printed a dry-run summary, deleted nothing and exited green.
  - it had never fired at all: all nine runs in its history were manual.
  - the 180-day window had outrun its own data. 2,522 observation rows and 700
    forecast rows were older than that; at 90 days it is 146,070.
  - it covered 90 MB of 436 and did not know trades_observed existed - 91 MB,
    the largest table, never vacuumed, 90,640 rows older than ninety days.

A green no-op is the worst failure mode a retention job has, so these assert
the mechanism rather than the intention.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "archive_observations.yml"
SCRIPT = ROOT / "scripts" / "archive_observations.py"
PRUNE_TRADES = ROOT / "sql" / "ad4_65_prune_trades.sql"


def _workflow():
    # PyYAML reads a bare `on:` key as the boolean True.
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _run_step():
    job = _workflow()["jobs"]["archive"]
    steps = [s for s in job["steps"] if "archive_observations.py" in str(s.get("run", ""))]
    assert len(steps) == 1, "expected exactly one step that runs the archive"
    return steps[0]["run"]


# --- the fault that made the whole job a no-op -----------------------------

def test_a_scheduled_run_passes_commit():
    """`github.event.inputs.commit == 'true'` is FALSE on a cron event, because
    the inputs object does not exist there. Gating --commit on it alone means
    the scheduled run archives nothing, for ever, silently."""
    run = _run_step()
    assert "--commit" in run
    flag = re.search(r"\$\{\{[^}]*--commit[^}]*\}\}", run)
    assert flag, "--commit must be inside the conditional expression"
    expr = flag.group(0)
    assert "github.event.inputs.commit == 'true'" not in expr or "event_name" in expr, (
        "the commit flag is gated only on a dispatch input, so a scheduled run "
        "can never prune - the whole job becomes a green no-op")
    assert "github.event_name" in expr, (
        "nothing in the flag distinguishes a cron fire from a manual dry run")


def test_a_manual_run_can_still_be_a_dry_run():
    """Inverting the default must not remove the safe way to try a change."""
    inputs = _workflow()[True]["workflow_dispatch"]["inputs"]
    assert inputs["commit"]["type"] == "boolean"
    expr = re.search(r"\$\{\{[^}]*--commit[^}]*\}\}", _run_step()).group(0)
    assert "github.event.inputs.commit == 'true'" in expr, (
        "unticking the box must still produce a dry run")


def test_it_is_scheduled_often_enough_to_matter():
    """Monthly was for a database with room. research_captures alone adds
    ~15 MB a day against 64 MB of headroom."""
    crons = [c["cron"] for c in _workflow()[True]["schedule"]]
    assert crons, "no schedule at all"
    for cron in crons:
        dom = cron.split()[2]
        assert dom == "*", f"'{cron}' fires on one day of the month; that is too slow"


# --- the window has to be shorter than the history ------------------------

def test_the_default_window_is_one_the_data_actually_reaches_past():
    """At 180 days the job archives 3,222 rows out of 436 MB - it runs, it
    succeeds, and the database does not move."""
    run = _run_step()
    default = re.search(r"--keep-days \$\{\{[^}]*\|\|\s*'(\d+)'", run)
    assert default, "no default keep-days in the workflow"
    assert int(default.group(1)) <= 90

    src = SCRIPT.read_text(encoding="utf-8")
    script_default = re.search(r'"--keep-days".*?default=(\d+)', src, re.S)
    assert script_default and int(script_default.group(1)) <= 90
    assert int(script_default.group(1)) == int(default.group(1)), (
        "the workflow and the script disagree about the retention window")


def test_the_thirty_day_floor_survives():
    """The one guard that stops a typo emptying the archive."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "args.keep_days < 30" in src


# --- the biggest table is in the set --------------------------------------

def test_the_archive_covers_the_largest_table():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from archive_observations import TABLES

    assert "trades" in TABLES, (
        "trades_observed is 91 MB of a 436 MB database, larger than either "
        "weather table, and the archive does not know it exists")
    spec = TABLES["trades"]
    assert spec["table"] == "trades_observed"
    assert spec["pk"] == "trade_id", "keyset paging needs the primary key"
    assert spec["cutoff_col"] == "traded_at", (
        "observed_at is NULL on all 156,008 rows; paging or pruning by it "
        "archives nothing and deletes nothing")
    assert spec["prune_rpc"] == "prune_trades"


@pytest.mark.parametrize("name", ["observations", "forecasts", "trades"])
def test_every_table_in_the_set_is_run_by_the_default_invocation(name):
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from archive_observations import TABLES

    run = _run_step()
    default = re.search(r"--table \$\{\{[^}]*\|\|\s*'(\w+)'", run)
    assert default and default.group(1) in ("all", "both")
    assert name in TABLES
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'args.table in ("all", "both")' in src, (
        'the all-tables choice must select every entry in TABLES, and must still '
        'accept the old "both" that saved dispatches send')


def test_every_table_has_its_own_release_tag():
    """One shared tag would have each table overwrite the last one's assets."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from archive_observations import TABLES

    tags = [s["tag"] for s in TABLES.values()]
    assert len(tags) == len(set(tags))


# --- the trades prune keeps the same two-sided contract -------------------

def test_the_trades_prune_is_bound_to_the_verified_count():
    sql = " ".join(PRUNE_TRADES.read_text(encoding="utf-8").lower().split())
    assert "lock table public.trades_observed in share row exclusive mode" in sql
    assert "p_expected_rows is required for a committed prune" in sql
    assert "v_doomed <> p_expected_rows" in sql


def test_the_trades_prune_refuses_without_the_summary_that_survives_it():
    """Deleting raw trades is only safe because archive_daily_city_presence
    already records which city was covered on which day. If a day is not in
    there, nothing summarises it and the delete is permanent loss."""
    sql = " ".join(PRUNE_TRADES.read_text(encoding="utf-8").lower().split())
    assert "archive_daily_city_presence" in sql
    assert "'trades seen'" in sql
    assert "uncovered_city_days" in sql


def test_the_trades_prune_defaults_to_a_dry_run():
    sql = " ".join(PRUNE_TRADES.read_text(encoding="utf-8").lower().split())
    assert "p_dry_run boolean default true" in sql, (
        "a prune that commits unless told otherwise is one typo from empty")


def test_a_row_with_no_trade_time_is_kept_not_orphaned():
    """The export filters `traded_at lt cutoff`, which excludes NULLs. The
    prune has to agree, or a NULL row is deleted without being archived."""
    sql = " ".join(PRUNE_TRADES.read_text(encoding="utf-8").lower().split())
    assert "traded_at >= v_before or traded_at is null" in sql, (
        "the kept-count must include rows with no traded_at, matching the "
        "delete predicate that leaves them alone")
