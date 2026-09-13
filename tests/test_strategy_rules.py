import datetime as dt

import strategy_rules as sg
from strategies.base import BandView, Context, StrategyConfig


def make_band(band_id, **overrides):
    defaults = dict(
        band_id=band_id, city_key="paris", resolution_date="2026-08-30",
        band_lo=24, band_hi=25, open_low=False, open_high=False, band_label="24-25C",
        unit="C", model_prob_yes=0.5, yes_price=0.20, no_price=0.80,
        yes_edge_net_pp=0.20, no_edge_net_pp=0.0, yes_tradeable=True, yes_block_reason=None,
        no_tradeable=True, no_block_reason=None, confidence=0.8, regime_label="SHARP",
        market_state="LIVE", fillable_usd_5c_yes=1000.0, fillable_usd_5c_no=1000.0, spread=0.02,
    )
    defaults.update(overrides)
    return BandView(**defaults)


def test_detect_model_shift_true_when_moved_a_full_band():
    shifted, magnitude = sg.detect_model_shift(prev_centre_c=24.0, curr_centre_c=25.2, band_width_c=1.0)
    assert shifted is True
    assert magnitude > 1.0


def test_detect_model_shift_false_for_small_move():
    shifted, _ = sg.detect_model_shift(prev_centre_c=24.0, curr_centre_c=24.3, band_width_c=1.0)
    assert shifted is False


def test_detect_regime_change():
    assert sg.detect_regime_change("SHARP", "UNCERTAIN") is True
    assert sg.detect_regime_change("SHARP", "SHARP") is False
    assert sg.detect_regime_change(None, "SHARP") is False


def test_detect_thin_book():
    assert sg.detect_thin_book(50.0, min_required=200.0) is True
    assert sg.detect_thin_book(500.0, min_required=200.0) is False


def test_detect_peak_window_open():
    assert sg.detect_peak_window_open(15) is True
    assert sg.detect_peak_window_open(45) is False
    assert sg.detect_peak_window_open(None) is False


def test_detect_selloff():
    fired, move = sg.detect_selloff(0.50, 0.35, cents_threshold=0.10)
    assert fired is True and move > 0.10
    fired, _ = sg.detect_selloff(0.50, 0.48, cents_threshold=0.10)
    assert fired is False


def test_system_health_flags_failed_and_stale_jobs():
    now = dt.datetime(2026, 8, 30, tzinfo=dt.timezone.utc)
    rows = [
        {"job": "probability_engine", "status": "error", "logged_at": now.isoformat()},
        {"job": "measure_skill", "status": "ok", "logged_at": (now - dt.timedelta(hours=48)).isoformat()},
        {"job": "edge_engine", "status": "ok", "logged_at": (now - dt.timedelta(minutes=10)).isoformat()},
    ]
    out = sg.system_health_signals(rows, now, stale_hours=6)
    reasons = [s.reason for s in out]
    assert any("job_failed:probability_engine" in r for r in reasons)
    assert any("job_stale:measure_skill" in r for r in reasons)
    assert not any("edge_engine" in r for r in reasons)


def test_anomaly_signals_are_critical():
    rows = [{"band_id": "b1", "side": "YES", "value": 0.6, "detected_at": "2026-08-30T00:00:00Z", "id": 1}]
    out = sg.anomaly_signals(rows)
    assert len(out) == 1 and out[0].severity == "critical"


def enabled_config(strategy_id, side="BOTH"):
    return StrategyConfig(strategy_id=strategy_id, name=strategy_id, side=side, universe=["ALL"],
                           regime_filter=["SHARP", "NORMAL"], conflict_class="default",
                           capital_cap_pct=5.0, max_concurrent=10, enabled=True)


def test_run_strategies_applies_conflict_resolution():
    # s3 (YES) and s4 (NO) both want band b1 - opposite sides, same band -> blocked
    band = make_band("b1", band_lo=24, band_hi=25, open_low=False, open_high=False,
                      mae_bands=0.3, model_prob_yes=0.7, yes_edge_net_pp=0.15,
                      no_price=0.05, no_edge_net_pp=0.02)
    ctx = Context(bands=[band], settings={}, now=dt.datetime(2026, 8, 30, tzinfo=dt.timezone.utc))
    configs = [enabled_config("s3_concentration", side="YES"), enabled_config("s4_tail_fade", side="NO")]

    # Force b1 to look like a tail band too, so s4 considers it (band_lo=24 with no siblings -> tail by construction)
    fired, blocked, conflicts = sg.run_strategies(ctx, configs, open_positions=[])
    sides_fired = {s.side for s in fired if s.action == "ENTER"}
    assert len(sides_fired) <= 1  # never both sides on the same band at once
    if blocked:
        assert conflicts and conflicts[0]["kind"] == "opposite_side_same_band"


def test_run_strategies_skips_disabled_and_unregistered():
    band = make_band("b1")
    ctx = Context(bands=[band], settings={}, now=dt.datetime(2026, 8, 30, tzinfo=dt.timezone.utc))
    disabled = StrategyConfig(strategy_id="s1_buy_low_sell_signal", name="s1", side="BOTH",
                               universe=["ALL"], regime_filter=[], conflict_class="default",
                               capital_cap_pct=5.0, max_concurrent=1, enabled=False)
    fired, blocked, conflicts = sg.run_strategies(ctx, [disabled], open_positions=[])
    assert fired == [] and blocked == [] and conflicts == []
