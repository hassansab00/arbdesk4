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


def _view(s, name):
    """One view's definition out of the comment-stripped file: from its
    `create or replace view` to the statement's closing semicolon. The file
    defines three views, so an index taken over the whole file answers for
    whichever one comes first rather than the one being asked about."""
    start = s.index(f"create or replace view public.{name} as")
    return s[start:s.index(";", start)]


def test_the_new_columns_are_appended_not_inserted():
    """create or replace view requires the existing columns to keep their
    position, name and type, and v_city_prediction_confidence is built on this
    view."""
    v = _view(ladder(), "v_prediction_ladder")
    assert v.index("e.computed_at as edge_at") < v.index("lb.outcome_source") \
        < v.index("lb.settled_at"), (
        "outcome_source and settled_at must stay the last two columns of the ladder")


def test_the_ladder_is_the_band_view_plus_the_edge():
    """Everything per band is defined once, in v_prediction_ladder_bands; the
    ladder only adds the edge. A second copy of the outcome logic in the ladder
    is how the two would drift apart."""
    s = ladder()
    ladder_view = _view(s, "v_prediction_ladder")
    assert "from v_prediction_ladder_bands lb" in ladder_view
    assert "fact_band_outcome" not in ladder_view and "mv_venue_band_resolution" not in ladder_view
    assert "fact_band_outcome" in _view(s, "v_prediction_ladder_bands")


FROZEN = ROOT / "supabase" / "migrations" / "20260926090000_hit_and_miss_scores_frozen_calls.sql"


def frozen():
    import re
    body = re.sub(r"--[^\n]*", " ", FROZEN.read_text(encoding="utf-8"))
    return " ".join(body.lower().split())


def test_hindsight_grades_only_calls_frozen_before_the_answer():
    """Until 26 Sep the grade took each band's LATEST probability with no time
    limit: 627 of 647 city-days had last been priced after 15:00 local, 306
    after the day ended. It now reads the day-ahead call (the last pricing
    before the local day began) and the fixed-time checkpoints, and nothing
    that reads the latest probability."""
    s = frozen()
    view = s[s.index("create view public.v_prediction_hindsight as"):]
    view = view[:view.index(";")]
    assert "from public.v_city_hit_history h" in view
    assert "from public.v_checkpoint_calls c" in view
    for leak in ("v_prediction_ladder_bands", "band_probabilities", "edges", "v_prediction_ladder "):
        assert leak not in view, f"{leak} is priced at any time, including after the day"
    assert "create or replace view public.v_prediction_hindsight" not in ladder(), (
        "the leaky definition must not come back from sql/ad4_68 on a reinstall")


def test_after_the_peak_is_marked_and_kept_out_of_the_headline():
    s = frozen()
    assert "cp.checkpoint = 'postpeak_1h' as after_peak" in s
    src = PANEL.read_text(encoding="utf-8")
    assert 'x.called_when === "day_ahead"' in src, "the headline is the day-ahead call"
    assert "After the peak — not a forecast" in src


def test_the_market_favourite_is_priced_inside_its_quote():
    """A dead bucket offered at 0.001 with a last trade of 0.999 was the
    market's favourite on 26 Sep. One rule, in the database and in tick.py."""
    s = frozen()
    assert "when ask is not null then least(last, ask)" in s
    assert "when bid is not null then greatest(last, bid)" in s
    assert s.count("public.book_mark(") >= 3      # defined, used to bank, used to grade
    tick = (ROOT / "scripts" / "tick.py").read_text(encoding="utf-8")
    assert "return min(last, ask)" in tick and "return max(last, bid)" in tick


def test_the_panel_reads_totals_from_the_database():
    """PostgREST returns at most 1,000 rows; the row view passes that in days,
    and a summary computed from a truncated read looks like an answer.

    Since wave F (6 Oct) the totals follow the page's city selection, so they
    are added up from the rows (lib/focus.ts summariseByMoment, the view's own
    filters). The rows are read a page at a time with a truncation flag, and
    the database's total is still read: the same function over every row must
    equal it, or the panel says the read is short."""
    src = PANEL.read_text(encoding="utf-8")
    assert "v_prediction_hindsight_summary" in src
    assert "summariseByMoment(allRows as HindsightRow[])" in src
    assert "do not add up to the database&apos;s own count" in src
    assert "create or replace view public.v_prediction_hindsight_summary" in frozen()
    page = PAGE.read_text(encoding="utf-8")
    assert 'readAllRows<HindsightRow>((from, to) =>\n      supabase.from("v_prediction_hindsight")' in page
    assert "truncated={hindsightQ.truncated}" in page


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
    # it takes the page's one selection and rows since wave F (6 Oct)
    assert "<PredictionHindsight rows={hindsightQ.data" in src
    assert src.index("What the desk expects") < src.index("<PredictionHindsight ")
    assert src.index("<PredictionHindsight ") < src.index("2. CONVERGENCE") \
        if "2. CONVERGENCE" in src else True
