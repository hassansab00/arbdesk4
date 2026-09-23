"""v_strategy_board's verdict must come from the signals, not from desk fills.

WHAT IT SAID BEFORE, for five of nine strategies:

    s3_concentration     1,008 fired   'firing, but nothing has filled yet'
    s6_anchor_insurance  1,361 fired   'firing, but nothing has filled yet'
    s5_running_max_lock     37 fired   'firing, but nothing has filled yet'
    s8 / s9                  4 fired   'firing, but nothing has filled yet'

`filled` is true only where an ACCOUNT took the signal, so that sentence
described a desk's subscription list and read as a fact about the strategy.
The same table already held whether each call was right, scored against
settlement with no desk involved.

The behaviour lives in SQL and the database contracts
(tests/database/paper-contracts.cjs) exercise it against a real Postgres.
What is guarded HERE is the thing a contract cannot see: that the migration
production applies and the sql/ file a fresh install applies say the same
thing. A view defined twice is a view that drifts.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260921210000_a_strategy_is_not_its_desk.sql"
INSTALL = ROOT / "sql/ad4_33_control.sql"


def _statement(text, opening):
    """The statement starting at `opening`, up to its terminating semicolon.

    `--` comments are removed first. A semicolon in a comment is not the end
    of a statement - this used to stop at one, inside the explanation above
    v_signal_mark, and "compared" the two copies' first few comment lines -
    and drift means the SQL differs, not the prose around it."""
    code = re.sub(r"--[^\n]*", "", text)
    start = code.index(opening)
    return code[start:code.index(";", code.index(" from ", start))]


def _latest_migration_defining(opening):
    """The migration production applies LAST for this statement. An applied
    migration is never edited, so a later fix lands in a new one - and that is
    the copy the install file has to match, not the first."""
    hits = [p for p in sorted((ROOT / "supabase/migrations").glob("*.sql"))
            if opening in p.read_text()]
    assert hits, f"no migration defines {opening!r}"
    return hits[-1]


def test_both_copies_of_the_mark_view_are_identical():
    opening = "create or replace view public.v_signal_mark as"
    latest = _latest_migration_defining(opening)
    a = _statement(latest.read_text(), opening)
    b = _statement(INSTALL.read_text(), opening)
    assert a == b, (f"v_signal_mark has drifted between {latest.name} "
                    "and sql/ad4_33_control.sql")


def test_the_mark_reads_the_payload_once_per_use_not_per_reference():
    """signals.payload is TOASTed for most signals, and every reference to it
    re-reads the TOAST pages. It is read in one materialised step; nothing
    after that step may name it again."""
    mark = _statement(INSTALL.read_text(), "create or replace view public.v_signal_mark as")
    raw = mark[mark.index("with raw as materialized ("):mark.index("shaped as (")]
    after = mark[mark.index("shaped as ("):]
    assert raw.count("s.payload") == 3, "the materialised step should read the payload exactly three times"
    assert "payload" not in after, "a step after `raw` reads signals.payload again"


def test_both_copies_of_the_board_are_identical():
    opening = "create or replace view v_strategy_board as"
    latest = _latest_migration_defining(opening)
    a = _statement(latest.read_text(), opening)
    b = _statement(INSTALL.read_text(), opening)
    assert a == b, (f"v_strategy_board has drifted between {latest.name} "
                    "and sql/ad4_33_control.sql")


def test_the_verdict_never_branches_on_a_fill():
    # The specific regression: a verdict arm keyed on n_filled says nothing
    # about the strategy, only about which desk subscribed to it.
    verdict = re.search(r"-- THE VERDICT NOW ASKS THE SIGNALS.*?as verdict",
                        MIGRATION.read_text(), re.S)
    assert verdict, "the verdict arm is gone - this test is no longer guarding anything"
    body = verdict.group(0)
    assert "n_filled" not in body, "the verdict branches on desk fills again"
    assert "n_scored" in body, "the verdict no longer reads the settled-signal count"


def test_the_mark_is_not_frozen_into_the_append_only_record():
    # sql/ad4_70_archive_exemption.sql makes the fact tables append-only. A
    # derived column there could never be corrected or backfilled.
    text = MIGRATION.read_text()
    assert "alter table public.fact_signal_outcome" not in text, (
        "the mark was added as columns on an append-only table")
    assert "create or replace view public.v_signal_mark" in text
