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

def test_every_table_carries_a_window_shorter_than_its_own_history():
    """ONE WINDOW CANNOT FIT FOUR TABLES, and that is why it is per table now.

    At 180 days the job archived 3,222 rows out of 436 MB - it ran, it
    succeeded, and the database did not move. Ninety fixed that for the three
    weather and trade tables, which hold months. research_captures holds five
    days, so ninety archives nothing from it at all; two days archives 26,565
    rows of 78,291. A single flag is either right for one group or the other,
    never both.
    """
    from archive_observations import TABLES

    limits = {"observations": 90, "forecasts": 90, "trades": 90, "research": 2}
    for name, spec in TABLES.items():
        window = spec.get("keep_days", 90)
        assert window <= limits[name], (
            f"{name} keeps {window} days; at that window the job succeeds and "
            f"the database does not move")


def test_the_workflow_does_not_override_every_table_with_one_number():
    """The workflow used to pass `--keep-days ... || '90'`, so a scheduled run
    always sent 90 - which would silently reimpose the single window these
    per-table ones exist to replace, and archive nothing from research."""
    run = _run_step()
    assert "|| '90'" not in run, (
        "a hard-coded fallback here overrides every table's own window")
    assert "--keep-days" in run and "github.event.inputs.keep_days" in run, (
        "a manual run must still be able to override the window")


def test_the_floor_is_enforced_by_the_function_that_deletes():
    """The floor moved from the script to each prune RPC, which is the only
    place a typo cannot route around: the script is one caller of several, and
    n8n or a psql session can call the RPC directly."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "args.keep_days < 1" in src, "the script still rejects a nonsense window"

    trades = Path(__file__).resolve().parents[1] / "sql" / "ad4_65_prune_trades.sql"
    research = Path(__file__).resolve().parents[1] / "sql" / "ad4_69_prune_research_captures.sql"
    assert "p_keep_days < 30" in trades.read_text(encoding="utf-8"), (
        "trades needs 30 days - the 24h volume window needs room to be wrong")
    assert "p_keep_days < 2" in research.read_text(encoding="utf-8"), (
        "research needs a floor too, just a shorter one - its whole history is "
        "five days, so a 30-day floor would make the function permanently refuse")


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


# --- the fastest-growing table --------------------------------------------
#
# research_captures reached 92 MB in FIVE DAYS - 78,291 rows, ~29 MB a day on
# the recent trend - while the database went back to 454 MB of a 500 MB tier.
# At that rate it crosses the ceiling in about three days, and a full database
# stops every job at once.
#
# It is not duplication: payload_hash already dedupes and all 78,291 payloads
# are distinct. It is one capture per row of each source relation, six times a
# day, with band_probabilities alone accounting for 30,914.

def test_the_archive_covers_the_fastest_growing_table():
    from archive_observations import TABLES

    assert "research" in TABLES, (
        "research_captures grows ~29 MB a day against 46 MB of headroom; an "
        "archive that does not know it exists is not an archive")
    spec = TABLES["research"]
    assert spec["table"] == "research_captures"
    assert spec["pk"] == "capture_id", (
        "keyset paging needs the PRIMARY KEY; captured_at is not unique and "
        "OFFSET paging over a non-unique order silently skips rows the prune "
        "then deletes anyway")
    assert spec["cutoff_col"] == "captured_at"
    assert spec["cutoff_is_date"] is False


def test_the_capture_payload_is_archived_not_just_its_metadata():
    """An archive of research captures without the payload is a list of
    filenames. payload and payload_hash are the evidence and its checksum."""
    from archive_observations import TABLES

    cols = TABLES["research"]["columns"]
    for needed in ("payload", "payload_hash", "source_relation", "source_key"):
        assert needed in cols, f"{needed} must survive the prune"


def test_research_does_not_block_on_a_cache_that_protects_nothing():
    """The other three archives refuse to run unless refresh_feature_cache
    succeeds, because their derived rows are what survives the prune. A
    research capture has no derived form - the capture IS the artefact, and
    what survives is the Release asset - so requiring the cache there would
    block the archive on a step that protects nothing."""
    from archive_observations import TABLES

    assert TABLES["research"].get("needs_feature_cache") is False
    for name in ("observations", "forecasts", "trades"):
        assert TABLES[name].get("needs_feature_cache", True) is True, (
            f"{name} must still refuse to prune without a covering cache")


def test_the_research_prune_refuses_to_break_a_published_hash():
    """scripts/data_integrity.py fingerprints this table up to a cutoff and
    stores the digest. Deleting a row inside a manifest's range leaves a
    published sha256 nobody can ever reproduce - a broken evidence chain that
    is only discovered by whoever tries to verify it."""
    sql = (Path(__file__).resolve().parents[1]
           / "sql" / "ad4_69_prune_research_captures.sql").read_text(encoding="utf-8")
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    assert "proprietary_data_manifests" in body, (
        "nothing stops this prune deleting rows a manifest has already hashed")
    assert "p_expected_rows" in body, (
        "a committed prune must be gated on the count verified by "
        "re-downloading the uploaded archive")
