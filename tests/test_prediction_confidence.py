"""v_city_prediction_confidence must read one side of the ticket, not both.

THE BUG THIS EXISTS TO PREVENT, because I shipped it once already.

v_prediction_ladder carries one row per SIDE, so every band appears twice -
2,244 rows over 1,122 real bands on the live board. calibrated_prob is the
same on both rows (it is the chance the band hits, not the chance a side
pays), but market_price is not: the YES rows average 0.09 and the NO rows
0.83, being the two halves of one book.

Selecting `distinct on (city_key, for_date) ... order by calibrated_prob
desc` therefore picks an arbitrary side, and puts a NO price beside a YES
probability. Austin read "most likely 89F or below at 21%, market 99.9%",
which looks like the desk violently disagreeing with the market. The true
YES price is 1.0%. The disagreement is real and in the opposite direction,
and the number on screen was neither.
"""
import pathlib
import re

import pytest

SRC = (pathlib.Path(__file__).parent.parent / "sql"
       / "ad4_58_city_prediction_confidence.sql").read_text()

def _code_only(sql: str) -> str:
    """Strip -- comments.

    Without this the guard below passes on a broken file, because the comment
    EXPLAINING the side filter contains the same text as the filter. A test
    that a prose paragraph can satisfy is not a test.
    """
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


# Every CTE that reads the ladder. The last one closes with ")" before the
# final SELECT rather than "),", so the lookahead has to accept both.
LADDER_CTES = [_code_only(body) for _name, body
               in re.findall(r"(\w+) as \((.*?)\n\)(?=,|\s*select)", SRC, re.S)
               if "v_prediction_ladder" in body]


def test_the_view_reads_the_ladder():
    assert LADDER_CTES, "no CTE reads v_prediction_ladder - did the view change shape?"


@pytest.mark.parametrize("n", range(2))
def test_every_ladder_read_picks_one_side(n):
    """Both the top-band pick and the ladder-wide counts must filter a side."""
    assert len(LADDER_CTES) > n, f"expected at least {n + 1} ladder CTEs"
    assert re.search(r"side\s*=\s*'YES'", LADDER_CTES[n]), (
        "this CTE reads both sides of every band - market_price and any "
        "count of bands will be wrong")


def test_the_reason_is_recorded_where_the_next_person_will_look():
    assert "one row per" in SRC and "side" in SRC
    assert "market_price" in SRC


def test_stated_and_measured_uncertainty_are_both_published():
    """The gap between them is the whole point; neither may be dropped."""
    for col in ("stated_sigma_c", "measured_mae_c", "measured_p90_err_c", "skill_n_days"):
        assert col in SRC, f"{col} missing - the view stops being able to say if an edge is real"


def test_a_lead_with_no_measured_skill_says_so_rather_than_guessing():
    """derived_forecast_skill starts at lead 1, so same-day has no accuracy.

    Borrowing tomorrow's number would be inventing a confidence.
    """
    assert "No measured accuracy" in SRC
    assert "left join skill" in SRC, "an inner join would silently drop unscored leads"


def test_overconfidence_is_named_as_such():
    """A model stating +/-1C that misses by 4C overstates every edge from it."""
    assert "OVERCONFIDENT" in SRC


def test_the_skill_series_is_read_newest_first():
    """derived_forecast_skill is a time series - one row per recompute."""
    skill = re.search(r"skill as \(\s*(.*?)\n\),", SRC, re.S)
    assert skill, "no skill CTE"
    body = skill.group(1)
    assert "distinct on (city_key, lead_days)" in body
    assert "computed_at desc" in body, "without this the view can read a stale measurement"
