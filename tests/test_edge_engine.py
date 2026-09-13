import math
import datetime as dt

import edge_engine as ee
import market_state as ms


def test_market_state_price_checked_before_sides():
    # ask <= 0.02 with a bid present must still be DEAD_LOSER, not ONE_SIDED
    # or LIVE - this is the exact bug the spec calls out (190/231 rows).
    assert ms.classify(best_bid=0.5, best_ask=0.01) == "DEAD_LOSER"
    assert ms.classify(best_bid=0.97, best_ask=None) == "DEAD_WINNER"


def test_market_state_no_book():
    assert ms.classify(None, None) == "NO_BOOK"


def test_market_state_one_sided_and_wide_and_live():
    assert ms.classify(0.40, None) == "ONE_SIDED"
    assert ms.classify(0.30, 0.45) == "WIDE"
    assert ms.classify(0.40, 0.45) == "LIVE"


def test_levels_for_no_side_is_complement_of_yes_bids():
    snapshot = {"bid_levels": [{"price": 0.60, "size": 100}, {"price": 0.55, "size": 50}]}
    levels = ee.levels_for_side(snapshot, "NO")
    # best (cheapest) NO ask corresponds to the highest YES bid
    assert math.isclose(levels[0]["price"], 0.40) and levels[0]["size"] == 100
    assert math.isclose(levels[1]["price"], 0.45) and levels[1]["size"] == 50


def test_compute_edge_positive_case():
    levels = [{"price": 0.30, "size": 1000}]
    edge = ee.compute_edge(model_prob=0.45, levels=levels, quoted_price=0.30,
                            max_slippage=0.05, reference_usd=100.0)
    assert edge["executable_price"] == 0.30
    assert math.isclose(edge["edge_pp"], 0.15)
    assert edge["edge_net_pp"] < edge["edge_pp"]  # fee reduces it
    assert edge["edge_net_pp"] > 0.10  # fee on a thin/cheap side shouldn't eat the whole edge


def test_compute_edge_no_book_falls_back_to_quoted():
    edge = ee.compute_edge(model_prob=0.5, levels=[], quoted_price=0.5, max_slippage=0.05)
    assert edge["executable_price"] == 0.5
    assert edge["shares"] == 0.0


def test_tradeability_below_7c_blocks_yes_only():
    tradeable, reason = ee.classify_tradeability("YES", 0.05, distance_bands=0, mstate="LIVE")
    assert tradeable is False and reason == "below_7c_yes"
    tradeable, reason = ee.classify_tradeability("NO", 0.05, distance_bands=0, mstate="LIVE")
    assert tradeable is True


def test_tradeability_far_from_forecast_blocks_yes():
    tradeable, reason = ee.classify_tradeability("YES", 0.50, distance_bands=10, mstate="LIVE",
                                                  max_bands_from_centre=4)
    assert tradeable is False and reason == "far_from_forecast"


def test_tradeability_dead_band_blocks_both_sides():
    for side in ("YES", "NO"):
        tradeable, reason = ee.classify_tradeability(side, 0.50, distance_bands=0, mstate="DEAD_LOSER")
        assert tradeable is False and reason == "dead_band"


def test_tradeability_no_book_blocks():
    tradeable, reason = ee.classify_tradeability("NO", 0.50, distance_bands=0, mstate="NO_BOOK")
    assert tradeable is False and reason == "no_book"


def test_band_distance_zero_for_open_tails():
    assert ee.band_distance_in_bands(100, None, 20, open_low=True, open_high=False) == 0.0
    assert ee.band_distance_in_bands(100, 29, None, open_low=False, open_high=True) == 0.0


def test_band_distance_scales_by_band_width():
    # centre right at the band edge -> half a band-width away
    d = ee.band_distance_in_bands(24, 24, 25, open_low=False, open_high=False)
    assert math.isclose(d, 0.5)


def test_opportunity_score_penalizes_unfillable_book():
    big_edge_thin_book = ee.opportunity_score(edge_net_pp=0.30, confidence=0.9, fillable_usd_5c=1.0)
    modest_edge_deep_book = ee.opportunity_score(edge_net_pp=0.05, confidence=0.9, fillable_usd_5c=50000.0)
    assert modest_edge_deep_book > big_edge_thin_book


def test_stale_or_future_evidence_is_rejected():
    now = dt.datetime(2026, 9, 13, 8, tzinfo=dt.timezone.utc)
    assert ee._age_exceeds("2026-09-13T05:59:59+00:00", dt.timedelta(hours=2), now)
    assert not ee._age_exceeds("2026-09-13T06:00:01+00:00", dt.timedelta(hours=2), now)
    assert ee._age_exceeds("2026-09-13T08:06:00+00:00", dt.timedelta(hours=2), now)
    assert ee._age_exceeds(None, dt.timedelta(hours=2), now)


def test_edge_reads_complete_band_scope_and_latest_probability_view(monkeypatch):
    calls = []

    def fake_rest_all(path, params, **kwargs):
        calls.append((path, dict(params), kwargs))
        return []

    monkeypatch.setattr(ee, "rest_all", fake_rest_all)
    ee._bands_for_markets([str(i) for i in range(101)])
    ee._latest_by_band("v_latest_prob", "*", [str(i) for i in range(101)], "computed_at")
    assert sum(1 for call in calls if call[0] == "v_canonical_bands") == 2
    assert sum(1 for call in calls if call[0] == "v_latest_prob") == 2
    assert all(call[2].get("page_size") == 500 for call in calls)
