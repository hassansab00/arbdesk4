"""The card shows what was priced (plan v2.3 P4.8).

The city card showed band_probabilities.forecast_max_c - the public forecast
the engine STARTED from - as its "Forecast centre ... after bias correction".
The ladder was integrated on centre_c, after the station correction, the
station-model blend or the trajectory: 28 Sep, wuhan 28.9 C shown, 26.005 C
priced. And the card's "priced" time was the newest edge, not the pricing,
while a same-day pick could already have been passed by the station (68 of 488
same-day checkpoint instants, 24-27 Sep, had the card's pick under 1% in the
fresh ladder the tick computed at that moment).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "supabase" / "migrations" / "20260927170000_the_card_shows_what_was_priced.sql").read_text()
AD4_68 = (ROOT / "sql" / "ad4_68_prediction_ladder_outcomes.sql").read_text()
AD4_58 = (ROOT / "sql" / "ad4_58_city_prediction_confidence.sql").read_text()
CARDS = (ROOT / "web" / "lib" / "cityCards.ts").read_text()
CARD_UI = (ROOT / "web" / "components" / "CityCards.tsx").read_text()

NEW = ("centre_c", "forecast_sigma_c", "observed_floor_c", "prob_at", "priced_from")
OLD_30 = ("city_key, for_date, market_id, band_id, band_index, band_label, band_lo, band_hi, "
          "open_low, open_high, closed, settled_value, won, raw_prob, calibrated_prob, model_prob, "
          "forecast_max_c, sigma_c, confidence, regime_label, side, market_price, edge_pp, "
          "edge_net_pp, depth_5c, tradeable, block_reason, edge_at, outcome_source, settled_at").split(", ")


def _flat(sql):
    return re.sub(r"\s+", " ", sql)


def test_the_wrapper_keeps_its_30_columns_in_order_and_appends_five():
    flat = _flat(MIGRATION)
    for source in ("public.v_prediction_ladder_live $v$", "public.mv_prediction_ladder $v$"):
        end = flat.index(f"from {source}")
        start = flat.rindex("create or replace view public.v_prediction_ladder as select ", 0, end)
        cols = flat[start + len("create or replace view public.v_prediction_ladder as select "):end]
        assert [c.strip() for c in cols.split(",")] == OLD_30 + list(NEW), source


def test_the_new_columns_come_from_the_same_row_as_the_probability():
    for src in (MIGRATION, AD4_68):
        lateral = _flat(src[src.index("from band_probabilities bp"):][:400])
        assert "order by bp.computed_at desc, bp.prob_id desc limit 1" in lateral
        head = _flat(src[src.index("left join lateral ("):src.index("from band_probabilities bp")])
        for col in ("bp.centre_c", "bp.forecast_sigma_c", "bp.observed_floor_c", "bp.computed_at",
                    "bp.forecast_version", "bp.raw_prob"):
            assert col in head, col
        assert "p.computed_at as prob_at" in src
        assert "mv.version_id = p.forecast_version) as priced_from" in src


def test_sql_file_and_migration_expose_the_same_columns():
    for view in ("v_prediction_ladder_bands", "v_prediction_ladder "):
        body = AD4_68[AD4_68.index(f"create or replace view public.{view}"):]
        body = body[:body.index(";")]
        for col in NEW:
            assert col in body, (view, col)


def test_the_migration_is_guarded_and_rerunnable():
    assert "to_regclass('public.v_prediction_ladder_live')" in MIGRATION
    assert "attname = 'priced_from'" in MIGRATION                  # a second run is a no-op
    assert "drop materialized view public.mv_prediction_ladder;" in MIGRATION
    assert "create materialized view public.mv_prediction_ladder as" in MIGRATION   # its own name: refresh_page_cache()
    assert "_next" not in MIGRATION                                   # no transient relation for the generators to list
    assert "create unique index mv_prediction_ladder_key" in MIGRATION  # REFRESH ... CONCURRENTLY needs it
    assert "revoke all on public.mv_prediction_ladder from public, anon, authenticated" in MIGRATION


def test_the_card_centre_is_the_priced_centre_never_the_raw_forecast():
    # the pricing run's centre, or the tick's newer ladder's (plan v2.3 P4.9) - a priced centre either way
    assert "const centre = newest ? newest.centre_c : any?.centre_c ?? null;" in CARDS
    assert "predicted_c" not in CARDS and "predicted_c" not in CARD_UI
    assert "raw_forecast_c: any?.forecast_max_c ?? null" in CARDS
    assert "not recorded for this price" in CARD_UI                # no substitution when it is missing
    # the ladder's own time - the pricing run's, or the tick's newer ladder's (P4.9) - never the edge's
    assert "priced_at: newest ? newest.priced_at : any?.prob_at ?? null" in CARDS


def test_a_passed_pick_is_marked_not_relabelled():
    assert "pickStanding(sameDay.running_max_c" in CARDS
    assert "STANDING_RANK[now] > STANDING_RANK[then]" in CARDS      # only what moved since the price
    assert "Out of date." in CARD_UI
    assert "The next pricing run replaces this pick." in CARD_UI


def test_every_selector_takes_every_bucket_with_one_tie_rule():
    for src in (MIGRATION, AD4_58):
        top = _flat(src[src.index("top_band as ("):src.index("spread as (")])
        assert "band_lo is not null and band_hi is not null" not in top
        assert "order by city_key, for_date, model_prob desc, band_id" in top
    # the tick breaks ties the same way
    tick = (ROOT / "scripts" / "tick.py").read_text()
    assert "sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))" in tick
