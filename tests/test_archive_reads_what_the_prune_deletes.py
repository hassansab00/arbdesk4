"""The export must READ the relation the prune deletes from, not merely name it.

WHY THIS FILE EXISTS. `test_archive_exemption.py` already asserted that the
resolution dataset declares `read_from: v_prunable_resolution_evidence` and
that the prune's DELETE goes through that view. Both assertions passed every
run. Meanwhile `export_cold` called `rest(spec["table"], ...)` and never looked
at `read_from` at all, so the export read `paper_resolution_evidence` whole
while the prune counted only the frozen subset:

    exported 4,540 rows but prune would delete 3,853

The count contract caught it and refused - nothing was uploaded, nothing was
deleted - and Archive Observations then went red every night from 19 Sep while
the fastest-growing table on the desk grew from 75 MB to 86 MB unpruned.

The 687-row difference is old proofs whose band outcome is not yet frozen in
fact_band_outcome. Those are the only copy of their own answer; the view
excludes them on purpose, and exporting them would have been the first half of
deleting them.

A test that reads the spec can only prove the spec. These call the function and
watch which relation the request goes to.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_observations as ao  # noqa: E402

ROOT_SQL = Path(__file__).resolve().parents[1] / "sql"


def _record_reads(monkeypatch, rows_by_call=None):
    """Capture the relation every export page is requested from."""
    seen = []
    pages = list(rows_by_call or [[]])

    def fake_rest(relation, params):
        seen.append(relation)
        return pages.pop(0) if pages else []

    monkeypatch.setattr(ao, "rest", fake_rest)
    return seen


def _cutoff():
    import datetime as dt

    return dt.datetime(2026, 9, 17, 8, 13, 37, tzinfo=dt.timezone.utc)


def test_the_resolution_export_requests_the_view_not_the_table(monkeypatch):
    """The exact bug: a declared `read_from` that nothing read."""
    seen = _record_reads(monkeypatch)
    ao.export_cold(ao.TABLES["resolution"], _cutoff())
    assert seen, "the export made no request at all"
    assert seen[0] == "v_prunable_resolution_evidence", (
        f"the export read {seen[0]!r}. The prune counts and deletes through "
        "v_prunable_resolution_evidence, so reading anything else exports rows "
        "the prune will not remove and the count contract refuses the run."
    )
    assert "paper_resolution_evidence" not in seen


def test_every_page_of_a_view_backed_export_stays_on_the_view(monkeypatch):
    """A first page on the view and later pages on the table would export a
    mixed set - the shape a paging bug takes."""
    page = [
        {"proof_id": f"{i:08d}-0000-0000-0000-000000000000", "captured_at": "2026-09-16T00:00:00+00:00"}
        for i in range(3)
    ]
    seen = _record_reads(monkeypatch, [page, page[:1], []])
    ao.export_cold(ao.TABLES["resolution"], _cutoff())
    assert len(seen) >= 3
    assert set(seen) == {"v_prunable_resolution_evidence"}


# WHAT EACH DATASET MUST READ, all of them, named one by one.
#
# This started as a loop over four hardcoded names, and a mutation proved what
# that costs: DELETING `read_from` from the edges spec left the whole suite
# green, because only `resolution` was ever asserted to read its view and
# `books` and `edges` were in neither list. That is the ORIGINAL BUG's mirror
# image - a declared read_from that nothing read, and now a missing one that
# nothing noticed - so the list is now every dataset, and a new one fails here
# until someone says which relation it reads.
#
# A view is needed exactly when the prune's predicate is one PostgREST cannot
# express: "and its band's outcome is already frozen", "and it is not the
# closing book of its band-day", "and it is not the newest pricing of its band
# and side". The other four prune on a plain timestamp, which a REST filter
# says perfectly well.
EXPORT_SOURCE = {
    "observations": "weather_observations",
    "forecasts":    "weather_forecasts",
    "trades":       "trades_observed",
    "research":     "research_captures",
    "resolution":   "v_prunable_resolution_evidence",
    "books":        "v_prunable_book_redundancy",
    "edges":        "v_prunable_edge_history",
}


def test_every_dataset_is_named_here():
    assert set(EXPORT_SOURCE) == set(ao.TABLES), (
        f"a dataset was added to the archive without saying which relation it exports "
        f"from: {sorted(set(ao.TABLES) ^ set(EXPORT_SOURCE))}"
    )


@pytest.mark.parametrize("name", sorted(EXPORT_SOURCE))
def test_the_export_requests_the_relation_the_prune_deletes_from(monkeypatch, name):
    """Called, not read off the spec. A test that reads `read_from` can only
    prove `read_from` was written down."""
    want = EXPORT_SOURCE[name]
    seen = _record_reads(monkeypatch)
    ao.export_cold(ao.TABLES[name], _cutoff())
    assert seen and seen[0] == want, (
        f"the {name} export read {seen[0] if seen else None!r}, not {want!r}. The prune "
        "counts and deletes through that relation, so reading anything else exports rows "
        "the prune will not remove and the count contract refuses the run."
    )
    if want != ao.TABLES[name]["table"]:
        assert ao.TABLES[name]["table"] not in seen


def test_read_source_is_the_single_place_that_decides():
    """Both the log line and the paging error name what was actually read, so
    a future reader is never told the table when the view was queried."""
    assert ao.read_source(ao.TABLES["resolution"]) == "v_prunable_resolution_evidence"
    assert ao.read_source(ao.TABLES["observations"]) == "weather_observations"
    source = (Path(ao.__file__).read_text(encoding="utf-8"))
    body = source[source.index("def export_cold"):source.index("def count_rows")]
    assert 'rest(source, params)' in body
    assert 'rest(spec["table"]' not in body, (
        "export_cold went back to reading the table directly"
    )


@pytest.mark.parametrize("name", sorted(ao.TABLES))
def test_the_exported_columns_exist_on_whatever_is_read(name):
    """A view that does not carry a declared column makes PostgREST 400 the
    whole export. For the one view-backed dataset, the view's own select list
    is right here in the repository."""
    spec = ao.TABLES[name]
    source = ao.read_source(spec)
    if source == spec["table"]:
        return
    # Each view-backed dataset names the file that defines its view. A new one
    # without an entry fails here rather than silently skipping the check.
    defines = {
        "v_prunable_resolution_evidence": "ad4_74_prune_resolution_evidence.sql",
        "v_prunable_book_redundancy":     "ad4_79_prune_book_redundancy.sql",
        "v_prunable_edge_history":        "ad4_80_prune_edge_history.sql",
    }
    assert source in defines, f"{source} has no SQL file registered here"
    sql = (Path(ao.__file__).resolve().parents[1]
           / "sql" / defines[source]).read_text(encoding="utf-8")
    view = sql[sql.index(f"create or replace view {source}"):sql.index("comment on view")]

    # A view may take the columns one at a time or take them all. `select s.*`
    # off the spec's own table carries every column by construction - there is
    # nothing to check and pretending otherwise by scanning for names would
    # just fail on a view that is correct. A view that projects a SUBSET must
    # still name every exported column.
    import re
    star = re.search(r"select\s+(\w+)\.\*\s+from\s+(?:public\.)?(\w+)\s+\1\b", view)
    if star:
        assert star.group(2) == spec["table"], (
            f"{source} selects * from {star.group(2)}, not from {spec['table']}, so the "
            "exported columns are not the ones the prune deletes"
        )
        return
    for col in [spec["pk"]] + list(spec["columns"]):
        assert col in view, f"{source} does not select {col}, so the export would 400"


def test_the_book_view_protects_both_rows_a_reader_would_ask_for():
    """The invariant the whole design rests on, and the one a mutation walked
    straight through until this existed.

    book_snapshots could not be pruned by age: scripts/backtest/runner.py asks
    for the newest book per band at an arbitrary as_of, and v_backtest_window
    bounds every backtest by min(observed_at) - so an age window silently
    shortens what can be backtested. What is safe to remove is the intra-day
    redundancy, and only because TWO rows are held back:

        rn_in_day  > 1    each band-day keeps its CLOSING book, so every
                          band-day that ever existed still has a row and
                          min(observed_at) cannot move
        rn_in_band > 1    each band keeps its NEWEST book at any age, which
                          is what v_latest_book reads

    Drop either and the view starts offering rows a live reader needs. Verified
    against the live table before anything was removed: 40,986 rows prunable,
    band-days 26,513 before and after, min(observed_at) identical to the
    microsecond.

    This is a STRUCTURAL check - it reads the predicate rather than executing
    it, because the view lives in sql/ and the PGlite harness applies only
    supabase/migrations. It is enough to catch either guard being removed,
    which is the failure that actually happened.
    """
    sql = (ROOT_SQL / "ad4_79_prune_book_redundancy.sql").read_text(encoding="utf-8")
    view = sql[sql.index("create or replace view v_prunable_book_redundancy"):
               sql.index("comment on view")]
    predicate = view[view.rindex("where"):]

    assert "rn_in_day" in predicate and "> 1" in predicate.split("rn_in_day")[1][:12], (
        "the view no longer holds back each band-day's closing book - min(observed_at) "
        "and the backtest window will move the first time this prunes"
    )
    assert "rn_in_band" in predicate and "> 1" in predicate.split("rn_in_band")[1][:12], (
        "the view no longer holds back each band's newest snapshot, which v_latest_book "
        "reads - a band whose collector stopped would lose its last known book"
    )
    assert " and " in predicate, (
        "the two protections must BOTH apply; either one alone leaves a live reader's "
        "row in the prunable set"
    )


# --- and the button that runs it has to offer every dataset ----------------
#
# `--table` on the script derives its choices from TABLES, so it is always
# right. The workflow_dispatch `table:` input is a HAND-WRITTEN COPY of the same
# list, and by 20 Sep it read
#
#     options: ['all', 'observations', 'forecasts', 'trades', 'research']
#
# while TABLES held six datasets. `resolution` joined on 19 Sep and `books` on
# the 20th, and neither could be selected from the Actions page - so the one
# dataset that had been failing for three days was also the one nobody could
# run on its own to test the fix.
#
# This is the same failure as the reclaim jobs and the README cadences: a second
# enumeration of a list, written by hand, with nothing holding it to the first.

WORKFLOW = (Path(ao.__file__).resolve().parents[1]
            / ".github" / "workflows" / "archive_observations.yml").read_text(encoding="utf-8")


def test_the_workflow_offers_every_dataset_the_script_knows():
    import ast
    m = re.search(r"^\s*options:\s*(\[.*\])\s*$", WORKFLOW, re.M)
    assert m, "archive_observations.yml has no table choice list any more"
    offered = set(ast.literal_eval(m.group(1)))
    assert offered == {"all"} | set(ao.TABLES), (
        f"the Actions page offers {sorted(offered)} but the script archives "
        f"{sorted(ao.TABLES)}. A dataset that cannot be selected cannot be run on its own, "
        "which is exactly what you need when one of them is failing."
    )


def test_the_edge_view_protects_the_one_row_every_page_is_built_on():
    """edges is the fourth-largest table and an append-only log: edge_engine
    writes one row per band, per side, every intraday run. 121,644 of its
    136,184 rows have already been superseded.

    Nothing reads the table. Every consumer goes through v_latest_edge, which
    is `distinct on (band_id, side) order by computed_at desc` - exactly one
    row per band and side. So the prunable set is everything that is not that
    row, and the single guard holding it is:

        rn > 1   partitioned by band_id AND SIDE

    THE SIDE IS NOT DECORATION. YES and NO are priced separately and
    v_latest_edge keeps one of each; ranking by band alone would rank the two
    sides against each other and offer up the newest NO row of every band,
    which v_opportunities would then be missing a price for. That is the
    mutation this test exists to catch, and it is invisible in a row count -
    the prunable set barely changes size.

    Structural, like the book guard above: the view lives in sql/ and the
    PGlite harness applies only supabase/migrations.
    """
    sql = (ROOT_SQL / "ad4_80_prune_edge_history.sql").read_text(encoding="utf-8")
    view = sql[sql.index("create or replace view v_prunable_edge_history"):
               sql.index("comment on view")]

    partition = re.search(r"partition by\s+([^\n]+)", view)
    assert partition, "the view no longer ranks the rows at all"
    assert "e.band_id" in partition.group(1) and "e.side" in partition.group(1), (
        f"the ranking partitions by {partition.group(1).strip()!r}. Without the side, the "
        "newest NO pricing of every band becomes prunable while v_latest_edge still "
        "reads it."
    )
    assert re.search(r"order by\s+e\.computed_at\s+desc", view), (
        "the ranking is no longer newest-first, so rn = 1 is not the current pricing"
    )
    predicate = view[view.rindex("where"):]
    assert re.search(r"r\.rn\s*>\s*1", predicate), (
        "the view no longer holds back the newest pricing of each band and side - the "
        "first prune takes every price off the board"
    )
