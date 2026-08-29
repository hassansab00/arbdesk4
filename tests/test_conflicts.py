from strategies.base import Signal
from strategies.conflicts import resolve_conflicts, city_day_exposure


def make_signal(strategy_id, band_id, side, action="ENTER"):
    return Signal(strategy_id=strategy_id, band_id=band_id, side=side, action=action,
                  reason="test", price_at_fire=0.5, prob_at_fire=0.5, edge_at_fire=0.1,
                  suggested_shares=10, confidence=0.8, regime_label="SHARP", severity="high",
                  dedupe_key=f"{strategy_id}:{band_id}:{side}:{action}:test")


def test_same_band_same_side_allowed():
    existing = [{"band_id": "b1", "side": "YES", "strategy_id": "s3_concentration"}]
    new = [make_signal("s1_buy_low_sell_signal", "b1", "YES")]
    allowed, blocked, conflicts = resolve_conflicts(new, existing)
    assert allowed == new
    assert blocked == []
    assert conflicts == []


def test_same_band_opposite_sides_blocked_and_logged():
    existing = [{"band_id": "b1", "side": "YES", "strategy_id": "s3_concentration"}]
    new = [make_signal("s4_tail_fade", "b1", "NO")]
    allowed, blocked, conflicts = resolve_conflicts(new, existing)
    assert allowed == []
    assert blocked == new
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "opposite_side_same_band"
    assert conflicts[0]["strategy_a"] == "s4_tail_fade"
    assert conflicts[0]["strategy_b"] == "s3_concentration"


def test_different_bands_same_city_both_allowed():
    existing = [{"band_id": "b1", "side": "YES", "strategy_id": "s3_concentration",
                 "city_key": "paris", "resolution_date": "2026-08-30", "shares": 100, "avg_fill_price": 0.4}]
    new = [make_signal("s4_tail_fade", "b2", "NO")]
    allowed, blocked, conflicts = resolve_conflicts(new, existing)
    assert allowed == new
    assert blocked == []


def test_two_new_signals_same_band_opposite_sides_second_one_blocked():
    new = [make_signal("s1_buy_low_sell_signal", "b1", "YES"),
           make_signal("s4_tail_fade", "b1", "NO")]
    allowed, blocked, conflicts = resolve_conflicts(new, [])
    assert len(allowed) == 1 and allowed[0].side == "YES"
    assert len(blocked) == 1 and blocked[0].side == "NO"


def test_exit_signals_never_blocked():
    existing = [{"band_id": "b1", "side": "YES", "strategy_id": "s3_concentration"}]
    new = [make_signal("s4_tail_fade", "b1", "NO", action="EXIT")]
    allowed, blocked, conflicts = resolve_conflicts(new, existing)
    assert allowed == new
    assert blocked == []


def test_city_day_exposure_aggregates_across_bands():
    positions = [
        {"city_key": "paris", "resolution_date": "2026-08-30", "band_id": "b1", "shares": 100, "avg_fill_price": 0.3},
        {"city_key": "paris", "resolution_date": "2026-08-30", "band_id": "b2", "shares": 50, "avg_fill_price": 0.5},
        {"city_key": "tokyo", "resolution_date": "2026-08-30", "band_id": "b3", "shares": 10, "avg_fill_price": 0.2},
    ]
    exposure = city_day_exposure(positions)
    assert exposure[("paris", "2026-08-30")] == 100 * 0.3 + 50 * 0.5
    assert exposure[("tokyo", "2026-08-30")] == 10 * 0.2
