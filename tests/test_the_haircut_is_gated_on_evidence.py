"""The desk may only haircut a price by as much as the evidence supports.

sql/ad4_75_probability_reliability.sql measures what the model's stated
probability has actually meant, per decile, on settled bands - and decides
whether that measurement is allowed to move anything.

THE THREE THINGS THAT COULD GO WRONG HERE, in order of how badly:

  MEASURING THE WRONG SCALE. fact_band_outcome freezes the probability the desk
  SHOWED, which is the calibrated one, and the map has been swapped and switched
  off repeatedly - measured 2026-09-19, 1,595 of 3,573 settled bands were priced
  under some map and 1,978 raw. Pooling those pools two scales. The view reads
  band_probabilities.raw_prob instead, which is recorded beside calibrated_prob
  on every row, so this is a recorded fact and not a reconstruction.

  OVERFITTING A THIN BUCKET. The 80-90% decile held two bands, one of which won.
  Taken at face value that is a -37.7pp correction on a price.

  APPLYING A GAP THAT IS NOISE. The 30-40% decile shows -6.8pp on 126 bands,
  which is suggestive and is not significant. The rule is that the stated value
  moves only as far as the NEAREST BOUND of the decile's Wilson 95% interval,
  which means it does not move at all while the stated value sits inside one.

Measured on the live database 2026-09-19: every decile's stated mean is inside
its own interval, so the view applies nothing, and says so per row. These tests
check the SQL says that rather than checking the numbers, because the numbers
move every day and the rule must not.
"""

import re

import pytest

SQL = "sql/ad4_75_probability_reliability.sql"


@pytest.fixture(scope="module")
def sql():
    return open(SQL).read()


def test_it_measures_raw_prob_not_the_frozen_calibrated_one(sql):
    assert "p.raw_prob" in sql
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    assert "o.model_prob" not in body, (
        "fact_band_outcome.model_prob is the CALIBRATED probability and the map "
        "has changed under it; bucketing on it pools two different scales")


def test_it_pairs_a_settlement_with_the_row_that_priced_it(sql):
    assert "p.computed_at = o.priced_at" in sql, (
        "joining on band_id alone would pick up later reprices of the same band")


def test_retired_cities_are_excluded(sql):
    assert "status" in sql and "'active'" in sql, (
        "a retired city's settled bands must not shape what the live desk prices")


def test_the_adjustment_stops_at_the_interval_not_at_the_point_estimate(sql):
    """least(hi, greatest(lo, stated)) - the nearest bound, never `realised`."""
    assert "least(b.wilson_hi, greatest(b.wilson_lo, b.mean_stated))" in sql
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    assert re.search(r"as\s+shift", body), "no shift column"
    # The expression that decides how far a price may move, on its own.
    start = body.index("case when b.n >= 30")
    shift = body[start:body.index("else 0 end", start)]
    assert "b.realised" not in shift, (
        "the shift must never be the raw measured gap - that is the overfit "
        "this rule exists to prevent")
    assert "b.wilson_lo" in shift and "b.wilson_hi" in shift


def test_a_thin_bucket_cannot_move_a_price(sql):
    """The floor guards BOTH the shift and the `applies` flag. Guarding only one
    lets a two-band decile report applies=false while still handing out a
    non-zero shift, or the reverse - and the desk reads both."""
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    assert body.count("b.n >= 30") >= 2, (
        "the sample floor must gate the shift AND the applies flag, not one of them")


def test_every_bucket_says_why_it_did_or_did_not_apply(sql):
    """A bucket that applies nothing must say so. 'No adjustment' and 'no data'
    need different actions and looked identical before this."""
    assert "too few to say anything" in sql
    assert "not yet distinguishable from zero" in sql
    assert "as                                                             note" in sql \
        or re.search(r"end\s+as\s+note", sql)


def test_the_wilson_interval_is_the_wilson_interval(sql):
    """z = 1.96: z^2 = 3.8416, z^2/2 = 1.9208, z^2/4 = 0.9604. A normal
    approximation would run below zero on the 3% decile and hand back an
    impossible probability."""
    for constant in ("1.9208", "3.8416", "0.9604", "1.96"):
        assert constant in sql, f"{constant} missing — this is not a Wilson interval"


def test_it_is_a_view_and_never_writes(sql):
    body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--")).lower()
    for forbidden in ("insert into", "update ", "delete from", "create table"):
        assert forbidden not in body, (
            f"{forbidden!r}: this measures pricing, it must never feed it")


def test_it_is_installed():
    order = open("sql/INSTALL_ORDER.txt").read()
    assert "ad4_75_probability_reliability.sql" in order


def test_the_desk_reads_it(sql):
    page = open("web/app/opportunities/page.tsx").read()
    assert "v_probability_reliability" in page
    lib = open("web/lib/calibration.ts").read()
    assert "wilson_lo" in lib and "wilson_hi" in lib


def test_the_list_is_ranked_on_the_same_object_the_card_renders():
    """The bug this replaces: the order came from the server's `score` while
    each card computed its own money, so the top card was the best-LOOKING
    trade rather than the best one."""
    page = open("web/app/opportunities/page.tsx").read()
    assert "byExpectedValue" in page, "the list must be ranked on EV"
    assert "priceRow(" in page
    assert page.count("priceRow(") == 1, (
        "priced once, then sorted and rendered from the same object — a second "
        "call is a second, disagreeing calculation")
    card = page[page.index("function Card") if "function Card" in page else 0:]
    assert "ladderFor(" not in card and "fill(" not in card.replace("fillable", ""), (
        "the card must not recompute the fill it was handed")


def test_the_card_shows_both_outcomes():
    page = open("web/app/opportunities/page.tsx").read()
    assert "If right" in page and "If wrong" in page
    assert "grid-cols-4" in page, "Risk / If right / If wrong / EV"
