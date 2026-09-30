"""The hit record shows the priced centre (audit repair 3, part 2, 29 Sep).

The Predictive page's forward table and hit-and-miss record showed
band_probabilities.forecast_max_c - the public forecast the engine started
from - as "Forecast", and graded its "error", "mean |error|" and "bias" on it,
while the ladder was integrated on centre_c. On the 257 settled day-ahead calls
with a recorded centre (23-28 Sep) the two differ by 1 C or more on 71; Miami
30 Sep showed 24.7 C with 30.26 C priced. v_prediction_hindsight put the raw
input in day-ahead rows and the priced centre in checkpoint rows of one column.

Proved live before the change: the new definition's 30 original columns EXCEPT
ALL the old view's, 0 rows both ways (657 each), and the summary's 22.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "supabase" / "migrations" / "20260930004500_the_hit_record_shows_the_priced_centre.sql").read_text()
AD4_85 = (ROOT / "sql" / "ad4_85_city_hit_history.sql").read_text()
PAGE = (ROOT / "web" / "app" / "predictive" / "page.tsx").read_text()
HINDSIGHT = (ROOT / "web" / "components" / "PredictionHindsight.tsx").read_text()

OLD_30 = ("city_key, display_name, unit, for_date, ladder_bands, model_bands, observed_max_c, forecast_max_c, "
          "error_c, sigma_c, confidence, regime_label, winner, model_call, model_call_prob, model_prob_on_winner, "
          "model_hit, brier_model, brier_uniform, called_at, hours_before_day, head_to_head, bands_scored, "
          "market_call, market_call_price, market_prob_on_winner, market_hit, brier_model_common, brier_market, "
          "brier_uniform_common").split(", ")


def _flat(sql):
    return re.sub(r"\s+", " ", sql)


def _select_list(sql, head, source):
    flat = _flat(sql)
    end = flat.index(f"from {source}")
    start = flat.rindex(head, 0, end)
    return [c.strip() for c in flat[start + len(head):end].split(",")]


def test_the_wrapper_keeps_its_30_columns_in_order_and_appends_two():
    head = "create or replace view public.v_city_hit_history as select "
    assert _select_list(MIGRATION, head, "public.v_city_hit_history_live") == OLD_30, (
        "while the stored copy is rebuilt the page reads the same 30 columns live")
    assert _select_list(MIGRATION, head, "public.mv_city_hit_history") == OLD_30 + ["centre_c", "centre_error_c"]


def test_the_centre_is_the_call_s_own_pricing_run_in_both_definitions():
    for name, src in (("migration", MIGRATION), ("sql/ad4_85", AD4_85)):
        flat = _flat(src)
        assert "bp.forecast_max_c, bp.sigma_c, bp.confidence, bp.regime_label, bp.computed_at, bp.centre_c" in flat, name
        assert "(array_agg(m.centre_c order by m.priced_at desc nulls last, m.band_id))[1] as centre_c" in flat, name
        # appended after the last old column, never in its place
        assert ("round(h.brier_uniform_common::numeric, 4) as brier_uniform_common, "
                "round(m.centre_c::numeric, 2) as centre_c, "
                "round((m.centre_c - m.observed_max_c)::numeric, 2) as centre_error_c from model_picks m") in flat, name
        # the raw input's error is still there, unchanged, under its own name
        assert "round((m.forecast_max_c - m.observed_max_c)::numeric, 2) as error_c" in flat, name


def test_the_summary_appends_the_centre_s_record_after_the_verdict():
    for name, src in (("migration", MIGRATION), ("sql/ad4_85", AD4_85)):
        flat = _flat(src)
        assert ("as verdict, -- the priced centre's own record, on the days it was recorded "
                "count(h.centre_error_c) as centre_days, round(avg(abs(h.centre_error_c)), 3) as centre_mae_c, "
                "round(avg(h.centre_error_c), 3) as centre_bias_c from") in flat, name
        assert "round(avg(abs(h.error_c)), 3) as mae_c" in flat, name


def test_the_hindsight_grades_the_priced_centre_in_both_halves():
    body = _flat(MIGRATION[MIGRATION.index("create or replace view public.v_prediction_hindsight as"):])
    assert "round(h.centre_c, 1) as forecast_max_c" in body
    assert "round(h.observed_max_c - h.centre_c, 1) as forecast_error_c" in body
    assert "h.forecast_max_c" not in body.split(" union all ")[0], "no raw input in the day-ahead half"
    assert "round(c.centre_c, 1), round(o.observed_max_c - c.centre_c, 1)" in body


def test_the_stored_copy_is_rebuilt_once_under_its_own_name():
    assert "attname = 'centre_error_c' and not attisdropped" in MIGRATION        # a second run skips it
    assert "drop materialized view public.mv_city_hit_history;" in MIGRATION
    assert "create materialized view public.mv_city_hit_history as" in MIGRATION  # refresh_page_cache() names it
    assert "_next" not in MIGRATION
    assert "create unique index mv_city_hit_history_key on public.mv_city_hit_history (city_key, for_date)" in MIGRATION
    assert "revoke all on public.mv_city_hit_history from public, anon, authenticated" in MIGRATION


def test_the_page_shows_the_priced_centre_and_names_the_raw_input():
    # the forward table
    assert "forecast_max_c,centre_c,sigma_c" in PAGE
    assert "centre_c: best?.centre_c ?? null" in PAGE
    assert ">Priced centre</th>" in PAGE and ">Raw forecast</th>" in PAGE
    assert 'r.centre_c === null ? <span className="text-muted">not recorded</span>' in PAGE, (
        "a missing centre says so; the raw input never stands in for it")
    # the hit-and-miss record
    assert ">priced centre</th>" in PAGE and ">raw forecast</th>" in PAGE
    assert "r.centre_error_c === null" in PAGE
    assert "hitSum.centre_mae_c" in PAGE and "hitSum.centre_bias_c" in PAGE
    assert 'label="mean |error|"' not in PAGE, "an unnamed error is the raw input's again"
    # the hindsight panel
    assert '"Priced centre", "Error"' in HINDSIGHT and '"Forecast", "Error"' not in HINDSIGHT
