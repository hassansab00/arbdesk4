"""Every row this platform deletes on purpose has to leave its money behind.

A paper desk's books rest on one identity:

    cash = starting_cash - open cost basis + realised P&L + re-basings
           - the cost basis a reset wrote off

Three of those terms are SUMS OVER ROWS. paper_positions.cost_basis is the
open basis; paper_trades.net_pnl is the realised P&L. Delete such a row and
one side of the identity moves and the other does not - not as a bug in the
arithmetic, but as arithmetic doing exactly what it was told about a number
that is no longer there.

TWO PLACES IN THIS REPOSITORY DELETE THOSE ROWS, BOTH DELIBERATELY:

    paper_desk_reset               clears paper_positions, because it is a
                                   running total of records that survive
    prune_exported_paper_trades    deletes closed paper_trades once they are
                                   committed to web/public/paper-trades

The first put a desk permanently out of balance the moment anyone reset one
holding anything. The second was on a timer: it keeps thirty days, so thirty
days after the first trade closed every desk would have gone red and stayed
red - and a detector that is always red is a detector nobody reads.

Both now write the number into paper_activity before deleting, in the same
transaction. paper_activity is append-only - immutable_record() refuses
UPDATE, DELETE and TRUNCATE, and service_role holds only INSERT and SELECT on
it - so the correction cannot be lost the way the rows it accounts for were.

WHAT THIS TEST IS FOR IS THE THIRD ONE.

The contracts in tests/database/paper-contracts.cjs prove the two that exist
work. Nothing there can fail because somebody adds a third delete next month -
another prune, a retention sweep, a cleanup after a migration. This test
enumerates every statement in the repository that deletes from a table the
identity sums over, and fails on one that is not listed here with a stated
reason. Adding a delete means writing down where its money went.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Tables whose rows carry a term of the identity. A delete from one of these
# moves money off the books.
CARRIES_MONEY = ("paper_positions", "paper_trades")

# The deletes that are allowed to exist, and what makes each one safe. The key
# is (file, table); the value is the substring that has to appear in the same
# file and proves the money was recorded.
ACCOUNTED_FOR = {
    ("sql/ad4_59_paper_desks.sql", "paper_positions"):
        ["'basis_written_off', v_basis"],
    ("sql/ad4_67_prune_exported_paper_trades.sql", "paper_trades"): [
        # the money
        "'trades_archived'",
        # ...and the decision behind it. ledger.trade_id references
        # paper_trades with no ON DELETE action, so this delete was refused
        # outright until the link was moved into data. A future edit that
        # drops the detach brings back a prune that cannot delete anything;
        # one that turns it into a cascade destroys the lineage instead.
        "'archived_trade_id', trade_id",
    ],
    # The contract harness deletes inside a transaction it rolls back, to
    # reconstruct a reset recorded before the write-off existed. Nothing it
    # does survives the rollback.
    ("tests/database/paper-contracts.cjs", "paper_positions"):
        ["await db.exec('rollback')"],
}

DELETE = re.compile(
    r"delete\s+from\s+(?:public\.)?(paper_positions|paper_trades)\b", re.I)

SEARCHED = ("sql/*.sql", "supabase/migrations/*.sql", "scripts/*.py",
            "tests/database/*.cjs", "web/app/**/*.ts", "web/app/**/*.tsx",
            "web/lib/*.ts", ".github/workflows/*.yml", "tools/*.py")


def _strip_sql_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"--[^\n]*", " ", src)


def _strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"^\s*//[^\n]*", " ", src, flags=re.M)


def _sites():
    """Every (file, table) in the repository that deletes a money-bearing row."""
    found = {}
    for pattern in SEARCHED:
        for path in sorted(ROOT.glob(pattern)):
            src = path.read_text()
            body = _strip_sql_comments(src) if path.suffix == ".sql" else _strip_js_comments(src)
            for table in set(DELETE.findall(body)):
                found[(path.relative_to(ROOT).as_posix(), table.lower())] = src
    return found


def test_every_delete_of_a_money_bearing_row_is_listed_here():
    """A delete nobody wrote down is a term of the identity going missing."""
    unlisted = sorted(k for k in _sites() if k not in ACCOUNTED_FOR)
    assert not unlisted, (
        "these statements delete rows the desk's books are computed from, and "
        "nothing says where their money went:\n  "
        + "\n  ".join(f"{f} deletes {t}" for f, t in unlisted)
        + "\n\nRecord the total into paper_activity before deleting - see "
          "prune_exported_paper_trades - and add it to ACCOUNTED_FOR with the "
          "line that proves it."
    )


def test_each_listed_delete_still_records_what_it_took():
    """The allow-list is not a licence: the recording has to still be there."""
    sites = _sites()
    for (path, table), proofs in ACCOUNTED_FOR.items():
        assert (path, table) in sites, (
            f"{path} no longer deletes {table}; drop it from ACCOUNTED_FOR "
            "rather than leaving an entry that guards nothing")
        for proof in proofs:
            assert proof in sites[(path, table)], (
                f"{path} deletes {table} and no longer contains {proof!r}, so "
                f"something that left with those rows is not recorded anywhere")


def test_the_identity_terms_the_recordings_feed_are_read_by_the_view():
    """The two recordings only matter if the detector actually reads them."""
    view = (ROOT / "sql" / "ad4_81_paper_desk_integrity.sql").read_text()
    for key in ("'trades_archived'", "'basis_written_off'"):
        assert key in view, (
            f"v_paper_desk_integrity does not read {key}, so the number the "
            "delete recorded is written and never used - the books go out of "
            "balance exactly as if it had never been recorded")
    # And it has to be in the expected cash, not merely selected for display.
    expected = view.split("as expected_cash")[0].rsplit("expected", 1)[-1]
    assert "realized_archived" in expected and "basis_written_off" in expected, (
        "the archived P&L and the written-off basis are reported but not used "
        "in expected_cash, which is the only place they change a verdict")
