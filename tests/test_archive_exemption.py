"""The archive could never delete anything, and nothing said so out loud.

ad4_69 added prune_research_captures so the archive could move cold captures
to a Release and free the space. It shipped without checking whether anything
already refused the delete. Something did:

    CREATE TRIGGER research_immutable BEFORE DELETE OR UPDATE
      ON public.research_captures FOR EACH ROW
      EXECUTE FUNCTION arbdesk_private.immutable_record()

which raises unconditionally. So every nightly run did the whole job - export
78,291 rows, gzip to 11.6 MB, upload, re-download, verify the count - and then
died on the prune with "Append-only record; write a linked correction
instead". A red workflow every night, and a database that reached 643 MB
against a 500 MB tier while the job that was meant to shrink it reported
failure nobody read.

The guard itself is correct and fifteen tables depend on it. What it lacked
was a way to tell an EDIT from a MOVE: an archive verifies the rows into a
Release before deleting them, and they stay readable there forever.

These pin the four narrowings, because an exemption that widens by accident
is worse than no archive at all.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "sql" / "ad4_70_archive_exemption.sql"


def _code():
    """The file with comment lines stripped.

    Every one of these files explains the bug directly above the fix, so a
    naive substring search matches the prose and passes on code that still has
    the bug. That has happened repeatedly in this repo.
    """
    return "\n".join(l for l in SQL.read_text(encoding="utf-8").splitlines()
                     if not l.strip().startswith("--"))


def test_the_exemption_is_delete_only():
    """An UPDATE is still an edit to evidence. A TRUNCATE still takes rows
    nobody counted. Neither is ever an archive."""
    code = _code()
    assert "tg_op = 'DELETE'" in code, (
        "without this the exemption would cover UPDATE and TRUNCATE too")
    assert "raise exception 'Append-only record" in code, (
        "every path that is not the archive must still raise")


def test_the_exemption_names_one_table_at_a_time():
    """Fifteen tables share this trigger. An exemption obtained for
    research_captures must not unlock fact_band_outcome."""
    code = _code()
    assert "= tg_table_name" in code, (
        "the setting must be compared against the table being written, or one "
        "exemption unlocks every append-only table at once")


def test_the_exemption_cannot_outlive_its_transaction():
    """set_config's third argument is is_local. Without it the setting would
    survive on a pooled connection and silently disarm the guard for whatever
    ran next on that session."""
    code = _code()
    for call in ("set_config('arbdesk.archiving', 'research_captures', true)",
                 "set_config('arbdesk.archiving', '', true)"):
        assert call in code, f"expected transaction-local {call}"
    assert "set_config('arbdesk.archiving', 'research_captures', false)" not in code


def test_the_prune_still_refuses_what_it_refused_before():
    """The exemption is claimed AFTER the guards, immediately around the
    delete, so a failed guard never reaches a transaction that could delete."""
    code = _code()
    assert "p_keep_days < 2" in code
    assert "p_expected_rows is required" in code
    assert "proprietary_data_manifests" in code

    claim = code.index("set_config('arbdesk.archiving', 'research_captures'")
    for guard in ("p_keep_days < 2", "p_expected_rows is required",
                  "proprietary_data_manifests", "archive row count mismatch"):
        assert code.index(guard) < claim, (
            f"{guard!r} must be checked before the exemption is claimed")


def test_only_the_service_role_can_run_the_prune():
    code = _code()
    assert "revoke all on function public.prune_research_captures" in code
    assert "to service_role" in code
    assert "security definer" in code
