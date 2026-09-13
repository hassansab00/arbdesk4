"""ad4_50_index_dedupe must never drop a unique index.

This is a source-level guard on a file that cost seven days of data.

ad4_50's rule 3 retires an index whose key columns are a strict prefix of a
longer index, because the longer one answers the same lookups. That is true
of lookups and false of constraints: unique on (a,b) forbids rows that unique
on (a,b,c) permits, and it is the only thing that makes `on conflict (a,b)`
plannable at all.

derived_weather_peak has a primary key on (city_key, month, computed_at) and
ad4_00_preflight builds a unique index on (city_key, month) for
refresh_weather_peak()'s upsert. ad4_50 dropped it as prefix-redundant, and
every subsequent refresh died with

    42P10: there is no unique or exclusion constraint matching the
    ON CONFLICT specification

There is no unit test that can run DDL here, so this asserts the two guard
clauses are present in the file. If someone removes them, this fails with the
reason rather than the table going quietly stale again. The behaviour itself
is verified by sql/ad4_56, which raises if the on-conflict targets are not
backed by a unique index after the install.
"""
import pathlib
import re

import pytest

SRC = (pathlib.Path(__file__).parent.parent / "sql" / "ad4_50_index_dedupe.sql").read_text()

# The file has three `for` loops. Two DROP indexes - identical-key duplicates,
# then prefix-redundant ones - and the third only REINDEXes. Both dropping
# loops must refuse to select a unique index; the reindex loop is harmless.
ALL_LOOPS = re.split(r"\bend loop\s*;", SRC)
LOOPS = [b.split("for r in", 1)[1] for b in ALL_LOOPS
         if "for r in" in b and "drop index" in b]


def test_the_file_still_has_both_drop_loops():
    """If this fails the file was restructured and the guards below moved."""
    assert len(LOOPS) == 2, f"expected 2 index-dropping loops, found {len(LOOPS)}"


def test_the_reindex_loop_is_not_being_mistaken_for_a_drop():
    reindex = [b for b in ALL_LOOPS if "reindex" in b.lower() and "drop index" not in b]
    assert reindex, "the REINDEX loop should still be present and drop nothing"


@pytest.mark.parametrize("n", [0, 1])
def test_neither_drop_loop_can_select_a_unique_index(n):
    body = LOOPS[n]
    assert re.search(r"not\s+\w*\.?indisunique", body), (
        "this loop can select a unique index for dropping - "
        "that removes a constraint and breaks `on conflict`")


def test_the_duplicate_loop_prefers_the_unique_index_as_survivor():
    """Two indexes on IDENTICAL columns, one unique: the unique one stays.

    Rule 2 keeps the physically smaller of two interchangeable indexes. A
    unique and a non-unique index on the same columns are not
    interchangeable, so unique has to outrank size in the ordering.
    """
    body = LOOPS[0]
    order = re.search(r"order by(.*?)\)\s*as rn", body, re.S)
    assert order, "duplicate loop has no survivor ordering"
    clause = order.group(1)
    assert "indisunique desc" in clause.replace("\n", " "), \
        "unique must rank above size when choosing which copy survives"
    assert clause.index("indisunique") < clause.index("bytes"), \
        "size is being preferred over uniqueness"


def test_the_reason_is_written_down_where_the_next_person_will_look():
    """A rule with no stated reason gets re-simplified away."""
    assert "42P10" in SRC
    assert "derived_weather_peak" in SRC


def test_partial_indexes_are_still_left_alone():
    """Unrelated to this bug, and the file's existing promise. Do not lose it."""
    assert SRC.count("indpred is null") >= 2
