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


def test_every_ladder_read_picks_one_side():
    """Every CTE touching the ladder must filter a side, however many there are."""
    assert LADDER_CTES, "no CTE reads the ladder"
    for i, body in enumerate(LADDER_CTES):
        assert re.search(r"side\s*=\s*'YES'", body), (
            f"ladder CTE {i} reads both sides of every band - market_price and "
            "any count of bands will be wrong")


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


# --------------------------------------------------------------------------
# the open-ended buckets
#
# The ladder's end buckets run to infinity: "89F or below", "108F or higher".
# An infinitely wide bucket collects more probability mass than a 2F one
# without being more likely. Taking max(calibrated_prob) over all of them
# reported Austin at lead 1 as "most likely 89F or below, 21.3%" while its own
# centre was 95.7F and every real bucket sat near 10%.
# --------------------------------------------------------------------------
def test_the_modal_bucket_ignores_the_open_ended_ones():
    top = next(b for b in LADDER_CTES if "calibrated_prob desc" in b)
    assert "band_lo is not null" in top and "band_hi is not null" in top, (
        "an open-ended bucket can win on width alone and be reported as the mode")


def test_the_open_tails_are_still_published():
    """Excluded from the mode, not from the answer: 21% below the whole board
    is a real statement about the day."""
    assert "tail_low_pct" in SRC and "tail_high_pct" in SRC


def test_the_modal_pick_is_deterministic():
    """Austin's 98-99F and 100-101F were both 15.7%; the winner flipped."""
    top = next(b for b in LADDER_CTES if "calibrated_prob desc" in b)
    assert re.search(r"calibrated_prob desc,\s*band_lo", top), (
        "ties need a stable tie-break or 'most likely' changes between reads")


# --------------------------------------------------------------------------
# units
#
# Twelve cities settle in Fahrenheit and their band labels already are. A US
# row reading "34.4" beside "94-95F" looks like a disagreement and is the same
# temperature - which is also how the first pass wrongly concluded the centre
# fell outside its own modal band on 27 of 55 rows.
# --------------------------------------------------------------------------
def test_the_expected_temperature_is_published_in_the_city_scale():
    assert "expected_max_display" in SRC and "display_unit" in SRC
    assert "expected_max_c" in SRC, "the Celsius value stays, for anything that computes"


def test_the_centre_bucket_is_matched_in_the_band_scale():
    centre = next(b for b in LADDER_CTES if "centre_band" in b)
    assert "9.0/5.0 + 32" in centre.replace(" ", "") or "9.0 / 5.0 + 32" in centre, (
        "band_lo/band_hi are in the city's unit - comparing a Celsius centre "
        "to a Fahrenheit bucket is the bug this replaced")


def test_the_view_can_be_reinstalled_after_a_shape_change():
    """create or replace cannot rename columns; this file has renamed some."""
    assert "drop view if exists v_city_prediction_confidence" in SRC
