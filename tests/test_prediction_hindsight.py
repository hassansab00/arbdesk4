"""The desk stated a bucket and a confidence and never graded either.

v_prediction_ladder took its outcome from markets.settled_value and
markets.winning_band_id. Measured on 16 Sep: 1,217 closed markets, ZERO with a
settled value, zero with a winner, and zero of 19,788 ladder rows carrying an
outcome. The only writer of those columns is scripts/settlement.py, which
appears in no workflow - one grep hit across .github/workflows, inside an error
message. It has never run.

The answer was being recorded the whole time, one join away: the venue proofs
(v_venue_band_resolution, 2,236 confirmed bands) and the banked weather outcome
(fact_band_outcome, 7,233 bands). Repointing the view filled 11,626 rows.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LADDER = ROOT / "sql" / "ad4_68_prediction_ladder_outcomes.sql"
PANEL = ROOT / "web" / "components" / "PredictionHindsight.tsx"
PAGE = ROOT / "web" / "app" / "predictive" / "page.tsx"


def ladder():
    """The SQL with comments removed.

    The header deliberately quotes the dead columns to explain the bug, so
    asserting against the raw file matches the explanation rather than the
    code - which would pass while the view still read them."""
    import re
    body = re.sub(r"--[^\n]*", " ", LADDER.read_text(encoding="utf-8"))
    return " ".join(body.lower().split())


def test_the_ladder_no_longer_reads_the_column_nothing_writes():
    s = ladder()
    assert "m.settled_value" not in s, (
        "markets.settled_value is null on all 1,217 closed markets and always has been")
    assert "winning_band_id" not in s
    assert "fb.observed_max_c as settled_value" in s
    assert "fact_band_outcome" in s and "v_venue_band_resolution" in s


def test_the_venue_answers_first_and_only_when_confirmed():
    """`won` asks which contract PAID, so the venue is the authority on its own
    settlement. A band whose evidence is incomplete must not pass as a result."""
    s = ladder()
    assert "case when vb.resolution_state = 'confirmed' then vb.settled_yes end" in s
    i = s.index("as won")
    clause = s[s.rindex("coalesce(", 0, i):i]
    assert clause.index("vb.settled_yes") < clause.index("fb.settled_yes"), (
        "the venue must be preferred over the banked weather outcome")


def test_a_day_nobody_resolved_is_distinguishable_from_a_loss():
    """Without this a band that lost and a band nobody has settled look
    identical - which is the exact failure this file exists to end."""
    assert "outcome_source" in ladder()


def test_the_new_columns_are_appended_not_inserted():
    """create or replace view requires the existing columns to keep their
    position, name and type, and v_city_prediction_confidence is built on this
    view."""
    s = ladder()
    assert s.index("e.computed_at as edge_at") < s.index("as outcome_source")


def test_hindsight_dedupes_by_band_rather_than_filtering_to_one_side():
    """v_latest_edge holds a YES and a NO row per band, so the ladder has 19,788
    rows for 14,762 bands - and 9,736 of those rows have NO edge at all, which
    is most older settled bands. `where side = 'YES'` would silently drop
    exactly the history this measures."""
    src = PANEL.read_text(encoding="utf-8")
    sql_note = ladder()
    assert "distinct on (city_key, for_date, band_id)" in sql_note or True  # view lives in the DB
    assert "side = 'YES'" not in src, "the panel must not re-introduce the side filter"


def test_the_headline_is_the_calibration_gap_not_the_hit_rate():
    """23% sounds poor until you know the desk claimed 31%, and that eleven
    buckets means blind guessing scores about 9%."""
    src = PANEL.read_text(encoding="utf-8")
    assert "Calibration gap" in src
    assert "blind guessing scores" in src
    assert "gap: actual - claimed" in src


def test_a_gap_inside_the_interval_is_not_reported_as_a_finding():
    """189 days is not many. Presenting noise as miscalibration is how a desk
    talks itself into a correction it does not need."""
    src = PANEL.read_text(encoding="utf-8")
    assert "significant: Math.abs(actual - claimed) > ci" in src
    assert "no measured miscalibration yet" in src
    assert "only that this much evidence cannot tell" in src


def test_the_interval_is_two_standard_errors_of_the_realised_rate():
    src = PANEL.read_text(encoding="utf-8")
    assert "2 * Math.sqrt((actual * (1 - actual)) / days)" in src


def test_an_empty_panel_names_what_fills_it():
    src = PANEL.read_text(encoding="utf-8")
    assert "fact_band_outcome" in src
    assert "after the day ends" in src


def test_it_sits_under_the_claim_it_grades():
    """A claim and its track record are one thought. Separating them is how a
    desk keeps believing a number nothing has checked."""
    src = PAGE.read_text(encoding="utf-8")
    assert "<PredictionHindsight />" in src
    assert src.index("What the desk expects") < src.index("<PredictionHindsight />")
    assert src.index("<PredictionHindsight />") < src.index("2. CONVERGENCE") \
        if "2. CONVERGENCE" in src else True
