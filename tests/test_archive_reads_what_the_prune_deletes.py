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
    "decisions":    "decisions",
    # stamps, never deletes: the snapshots whose ladder the archive lacks (P5.13)
    "ladders":      "v_unarchived_ladders",
    # the whole table older than a day: no reader looks back past 900 s (P1.6)
    "book_evidence": "paper_book_evidence",
    # the table with its three-column key joined into one to page on (P1.6)
    "forecast_features": "v_forecast_features_export",
    # only the signals still carrying decision_inputs; the rows stay (P1.6)
    "signal_inputs": "v_signal_inputs_export",
    # the table with its four-column key joined into one to page on (P1.6 phase 2)
    "forecast_models": "v_forecast_models_export",
    # the prices no reader selects, with their market's date (P1.6 phase 2)
    "probabilities": "v_prunable_band_probabilities",
    # every correlation a newer one of its pair superseded, with the
    # three-column key joined into one to page on (WXPredict build 2.A)
    "correlation":  "v_prunable_city_correlation",
    # a week-old forecast's payload, the rows stay (WXPredict build 2.A)
    "forecast_variables": "v_forecast_variables_export",
    "model_payloads":     "v_model_payloads_export",
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
        "v_unarchived_ladders":           "../supabase/migrations/20260927100000_a_ladder_is_archived_before_it_is_pruned.sql",
        "v_forecast_features_export":     "ad4_93_prune_forecast_features.sql",
        "v_signal_inputs_export":         "ad4_94_prune_signal_inputs.sql",
        "v_forecast_models_export":       "ad4_95_prune_forecast_models.sql",
        "v_prunable_band_probabilities":  "ad4_96_prune_band_probabilities.sql",
        "v_prunable_city_correlation":    "ad4_prune_city_correlation.sql",
        "v_forecast_variables_export":    "ad4_prune_forecast_payloads.sql",
        "v_model_payloads_export":        "ad4_prune_forecast_payloads.sql",
    }
    assert source in defines, f"{source} has no SQL file registered here"
    sql = (Path(ao.__file__).resolve().parents[1]
           / "sql" / defines[source]).read_text(encoding="utf-8")
    start = sql.find(f"create or replace view {source}")
    if start < 0:
        start = sql.index(f"create or replace view public.{source}")
    view = sql[start:sql.index("comment on view", start)]

    # A view may take the columns one at a time or take them all. `select s.*`
    # off the spec's own table carries every column by construction - there is
    # nothing to check and pretending otherwise by scanning for names would
    # just fail on a view that is correct. A view that projects a SUBSET must
    # still name every exported column. The star may follow a column the view
    # builds itself - v_forecast_features_export's paging key (P1.6) - and
    # the key the export pages on must then be named in the view.
    import re
    star = re.search(r"(?:select|,)\s+(\w+)\.\*\s+from\s+(?:public\.)?(\w+)\s+\1\b", view)
    if star:
        assert star.group(2) == spec["table"], (
            f"{source} selects * from {star.group(2)}, not from {spec['table']}, so the "
            "exported columns are not the ones the prune deletes"
        )
        assert spec["pk"] in view, f"{source} does not name {spec['pk']}, the key the export pages on"
        return
    for col in [spec["pk"]] + list(spec["columns"]):
        assert col in view, f"{source} does not select {col}, so the export would 400"


def test_the_book_view_is_executed_as_a_contract_not_grepped_as_a_string():
    """The invariant the whole design rests on, and where it is now proven.

    book_snapshots could not be pruned by age: scripts/backtest/runner.py asks
    for the newest book per band at an arbitrary as_of, and v_backtest_window
    bounds every backtest by min(observed_at) - so an age window silently
    shortens what can be backtested. What is safe to remove is the intra-day
    redundancy, and only because each band-day keeps its CLOSING book.

    THIS TEST USED TO READ THE PREDICATE AS TEXT. It asserted the string
    "rn_in_day" appeared with "> 1" after it, and said so in its own docstring:
    a structural check, because "the view lives in sql/ and the PGlite harness
    applies only supabase/migrations".

    That was true of the harness and is no longer. ad4_79 is applied there now
    and the protection is asserted by running it against real Postgres, on
    constructed data including the tied timestamps this table actually
    contains - every band-day still represented, min(observed_at) unmoved, the
    prune deleting exactly the set the view offered.

    Which is the point: the string check would have passed happily on the
    version of this view that could not run in production at all, and on the
    version whose two window functions disagreed with each other about 307
    tied rows. It tested that the author had typed a word.

    What is left here is the thing this FILE is about - that the dataset reads
    from the relation the prune deletes from - asserted by calling the export
    rather than by reading the spec.
    """
    assert ao.TABLES["books"]["read_from"] == "v_prunable_book_redundancy"

    harness = (Path(__file__).resolve().parents[1]
               / "tests" / "database" / "paper-contracts.cjs").read_text(encoding="utf-8")
    assert "ad4_79_prune_book_redundancy.sql" in harness, (
        "the book prune is no longer applied in the database harness, so nothing "
        "executes it - put the structural check back if this is deliberate"
    )
    assert "prune_book_redundancy(" in harness, (
        "the harness applies ad4_79 but never calls it, which tests that a file parses"
    )


def test_the_book_export_and_the_book_prune_touch_the_same_relation(monkeypatch):
    """Both halves of the count contract, watched rather than declared."""
    seen = _record_reads(monkeypatch)
    ao.export_cold(ao.TABLES["books"], _cutoff())
    assert set(seen) == {"v_prunable_book_redundancy"}, (
        f"the export read {set(seen)}, so the rows uploaded are not the rows "
        "prune_book_redundancy counts - which is how the resolution archive broke"
    )

    sql = (ROOT_SQL / "ad4_79_prune_book_redundancy.sql").read_text(encoding="utf-8")
    body = sql[sql.index("create or replace function public.prune_book_redundancy"):]
    assert "v_prunable_book_redundancy" in body, (
        "the prune no longer reads the view the archive exports"
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
    start = sql.index("create or replace view v_prunable_edge_history")
    view = sql[start:sql.index("comment on view", start)]

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


# --- evidence something points at is never redundancy ----------------------
#
# Three unindexed inbound foreign keys were found on this desk in one day, and
# each one failed its prune the same way - 23503, the whole dataset red:
#
#     edges.book_snapshot_id      -> book_snapshots      (6,075 rows blocked)
#     signals.book_snapshot_id    -> book_snapshots      (0 today, will not stay 0)
#     paper_position_settlements  -> paper_resolution_evidence (46 blocked)
#
# Cascading would "fix" all three by deleting the lineage of a decision. The
# rule instead is that a row something cites is evidence: the book a trade was
# priced from, the proof a settlement paid out on. It is never offered.
#
# These read the views rather than the prune functions, because the view is
# the half BOTH sides share - the archive exports it and the prune deletes
# through it - so a proof excluded here cannot be exported and then orphaned.


def _view_body(path, name):
    sql = (ROOT_SQL / path).read_text(encoding="utf-8")
    start = sql.index(f"create or replace view {name}")
    return sql[start:sql.index("comment on view", start)]


def test_the_book_view_refuses_anything_an_edge_or_signal_cites():
    body = _view_body("ad4_79_prune_book_redundancy.sql", "v_prunable_book_redundancy")
    for child in ("public.edges", "public.signals"):
        assert f"from {child}" in body and "not exists" in body, (
            f"{child} references book_snapshots with ON DELETE NO ACTION; a prune that "
            f"does not exclude its rows raises 23503 and fails the whole dataset"
        )


def test_the_resolution_view_refuses_anything_a_settlement_cites():
    body = _view_body("ad4_74_prune_resolution_evidence.sql",
                      "v_prunable_resolution_evidence")
    assert "paper_position_settlements" in body and "not exists" in body, (
        "a proof a settlement paid out on would be exported and then fail to delete"
    )


def test_every_inbound_key_named_here_has_a_covering_index():
    """The exclusion stops the 23503; the index stops the delete being
    quadratic. Both are needed and they live in different files, so a prune
    that is merely correct can still not finish."""
    migrations = ROOT_SQL.parent / "supabase" / "migrations"
    applied = "\n".join(p.read_text(encoding="utf-8") for p in migrations.glob("*.sql"))
    for column in ("edges (book_snapshot_id)", "signals (book_snapshot_id)",
                   "paper_position_settlements (proof_id)"):
        assert column in applied, (
            f"no index on {column} - each deleted parent row scans that table once, "
            "which is how the book prune failed to finish inside sixty seconds"
        )
