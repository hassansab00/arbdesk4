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


def test_a_dataset_without_a_view_still_reads_its_own_table(monkeypatch):
    """`read_from` is an exception, not a rename. The three datasets whose
    prune predicate PostgREST *can* express keep reading their tables."""
    for name in ("observations", "forecasts", "trades", "research"):
        spec = ao.TABLES[name]
        assert "read_from" not in spec, (
            f"{name} declares read_from; add it to the view-backed list here"
        )
        seen = _record_reads(monkeypatch)
        ao.export_cold(spec, _cutoff())
        assert seen[0] == spec["table"]


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
