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

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_observations as ao  # noqa: E402


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
    sql = (Path(ao.__file__).resolve().parents[1]
           / "sql" / "ad4_74_prune_resolution_evidence.sql").read_text(encoding="utf-8")
    view = sql[sql.index("create or replace view"):sql.index("comment on view")]
    for col in [spec["pk"]] + list(spec["columns"]):
        assert col in view, f"{source} does not select {col}, so the export would 400"
