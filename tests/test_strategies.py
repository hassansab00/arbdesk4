import datetime as dt

from strategies.base import BandView, Context, StrategyConfig, Signal
from strategies.s1_buy_low_sell_signal import S1BuyLowSellSignal
from strategies.s2_combination_arb import S2CombinationArb
from strategies.s3_concentration import S3Concentration
from strategies.s4_tail_fade import S4TailFade
from strategies.s5_running_max_lock import S5RunningMaxLock
from strategies.s6_anchor_insurance import S6AnchorInsurance


def make_band(band_id, city_key="paris", resolution_date="2026-08-30", **overrides):
    defaults = dict(
        band_id=band_id, city_key=city_key, resolution_date=resolution_date,
        band_lo=24, band_hi=25, open_low=False, open_high=False, band_label="24-25C",
        unit="C", model_prob_yes=0.5, yes_price=0.40, no_price=0.60,
        yes_edge_net_pp=0.05, no_edge_net_pp=0.05, yes_tradeable=True, yes_block_reason=None,
        no_tradeable=True, no_block_reason=None, confidence=0.8, regime_label="SHARP",
        market_state="LIVE", fillable_usd_5c_yes=1000.0, fillable_usd_5c_no=1000.0,
    )
    defaults.update(overrides)
    return BandView(**defaults)


def make_ctx(bands, settings=None):
    return Context(bands=bands, settings=settings or {}, now=dt.datetime(2026, 8, 30, tzinfo=dt.timezone.utc))


def enabled_config(strategy_id, side="BOTH", extra=None):
    return StrategyConfig(strategy_id=strategy_id, name=strategy_id, side=side, universe=["ALL"],
                           regime_filter=["SHARP", "NORMAL"], conflict_class="default",
                           capital_cap_pct=5.0, max_concurrent=10, enabled=True, extra=extra or {})


# --------------------------------------------------------------------------
# All six ship disabled by default
# --------------------------------------------------------------------------

def test_all_strategies_default_disabled():
    for cls, sid in [(S1BuyLowSellSignal, "s1"), (S2CombinationArb, "s2"), (S3Concentration, "s3"),
                      (S4TailFade, "s4"), (S5RunningMaxLock, "s5"), (S6AnchorInsurance, "s6")]:
        cfg = StrategyConfig(strategy_id=sid, name=sid, side="BOTH", universe=["ALL"],
                              regime_filter=[], conflict_class="default", capital_cap_pct=5.0,
                              max_concurrent=1)
        assert cfg.enabled is False
        strat = cls(cfg)
        band = make_band("b1")
        assert strat.applies_to(band) is False  # disabled strategies never apply


# --------------------------------------------------------------------------
# S1
# --------------------------------------------------------------------------

def test_s1_fires_when_edge_beats_round_trip_cost():
    cfg = enabled_config("s1_buy_low_sell_signal", extra={"entry_mode": "price", "price_threshold": 0.5})
    strat = S1BuyLowSellSignal(cfg)
    band = make_band("b1", yes_price=0.20, yes_edge_net_pp=0.30, spread=0.02)
    signals = strat.entry_signals(make_ctx([band]))
    assert len(signals) == 1
    assert signals[0].side == "YES" and signals[0].action == "ENTER"


def test_s1_blocked_when_edge_does_not_beat_round_trip_cost():
    # wide spread makes the round-trip bar big; edge is small
    cfg = enabled_config("s1_buy_low_sell_signal", extra={"entry_mode": "price", "price_threshold": 0.5})
    strat = S1BuyLowSellSignal(cfg)
    band = make_band("b1", yes_price=0.20, yes_edge_net_pp=0.02, spread=0.18)
    signals = strat.entry_signals(make_ctx([band]))
    assert signals == []


def test_s1_time_mode_uses_lead_days():
    cfg = enabled_config("s1_buy_low_sell_signal", extra={"entry_mode": "time", "lead_days_trigger": 2})
    strat = S1BuyLowSellSignal(cfg)
    far = make_band("b1", yes_price=0.10, yes_edge_net_pp=0.30, lead_days=5, spread=0.01)
    near = make_band("b2", yes_price=0.10, yes_edge_net_pp=0.30, lead_days=1, spread=0.01)
    signals = strat.entry_signals(make_ctx([far, near]))
    assert {s.band_id for s in signals} == {"b2"}


def test_s1_exit_on_target_convergence():
    cfg = enabled_config("s1_buy_low_sell_signal")
    strat = S1BuyLowSellSignal(cfg)
    band = make_band("b1", yes_price=0.48, model_prob_yes=0.50)
    pos = {"strategy_id": "s1_buy_low_sell_signal", "band_id": "b1", "side": "YES", "shares": 100,
           "prob_at_entry": 0.50}
    signals = strat.exit_signals(make_ctx([band]), [pos])
    assert len(signals) == 1 and signals[0].reason == "target_converged"


# --------------------------------------------------------------------------
# S2
# --------------------------------------------------------------------------

def test_s2_fires_on_cheap_all_yes_basket():
    cfg = enabled_config("s2_combination_arb")
    strat = S2CombinationArb(cfg)
    # 3-band toy market (not the real 11) summing well under $1
    bands = [make_band(f"b{i}", yes_price=0.20, yes_tradeable=True) for i in range(3)]
    signals = strat.entry_signals(make_ctx(bands))
    yes_signals = [s for s in signals if s.side == "YES"]
    assert len(yes_signals) == 1
    assert yes_signals[0].payload["fee_inclusive_cost"] < 1.0


def test_s2_does_not_fire_when_expensive():
    cfg = enabled_config("s2_combination_arb")
    strat = S2CombinationArb(cfg)
    # YES basket: 3 x 0.40 = 1.20 > 1 (no arb). NO basket: payout is
    # len(bands)-1 = 2, so it only takes sum(no_price) >= ~2 to kill it.
    bands = [make_band(f"b{i}", yes_price=0.40, no_price=0.70) for i in range(3)]
    signals = strat.entry_signals(make_ctx(bands))
    assert signals == []


def test_s2_incomplete_basket_is_not_an_arb():
    cfg = enabled_config("s2_combination_arb")
    strat = S2CombinationArb(cfg)
    bands = [make_band("b0", yes_price=0.10), make_band("b1", yes_price=None, yes_tradeable=False)]
    signals = strat.entry_signals(make_ctx(bands))
    assert [s for s in signals if s.side == "YES"] == []


# --------------------------------------------------------------------------
# S3
# --------------------------------------------------------------------------

def test_s3_gated_by_mae_bands():
    cfg = enabled_config("s3_concentration")
    strat = S3Concentration(cfg)
    good = make_band("b1", mae_bands=0.5, model_prob_yes=0.6, yes_edge_net_pp=0.10, band_lo=24, band_hi=25)
    bad_city_bands = [make_band("b2", city_key="tokyo", mae_bands=2.0, model_prob_yes=0.6,
                                 yes_edge_net_pp=0.10, band_lo=24, band_hi=25)]
    signals = strat.entry_signals(make_ctx([good] + bad_city_bands))
    assert any(s.band_id == "b1" for s in signals)
    assert all(s.band_id != "b2" for s in signals)


# --------------------------------------------------------------------------
# S4
# --------------------------------------------------------------------------

def test_s4_fires_on_open_tail():
    cfg = enabled_config("s4_tail_fade")
    strat = S4TailFade(cfg)
    tail = make_band("tail", open_low=True, band_lo=None, band_hi=20, no_price=0.05, no_edge_net_pp=0.02)
    mid = make_band("mid", band_lo=24, band_hi=25, no_price=0.50, no_edge_net_pp=0.10)
    signals = strat.entry_signals(make_ctx([tail, mid]))
    assert any(s.band_id == "tail" for s in signals)


def test_s4_never_fires_uncertain_or_blocked():
    cfg = enabled_config("s4_tail_fade")
    strat = S4TailFade(cfg)
    tail = make_band("tail", open_low=True, band_lo=None, band_hi=20, no_price=0.05,
                      no_edge_net_pp=0.02, regime_label="UNCERTAIN")
    signals = strat.entry_signals(make_ctx([tail]))
    assert signals == []


# --------------------------------------------------------------------------
# S5
# --------------------------------------------------------------------------

def test_s5_seasonal_gate_blocks_when_not_allowed():
    cfg = enabled_config("s5_running_max_lock", side="YES")
    strat = S5RunningMaxLock(cfg)
    band = make_band("b1", running_max_c=24.3, day_decided=True, s5_allowed=False, yes_price=0.5)
    assert strat.entry_signals(make_ctx([band])) == []


def test_s5_fires_when_day_decided_and_still_cheap():
    cfg = enabled_config("s5_running_max_lock", side="YES")
    strat = S5RunningMaxLock(cfg)
    band = make_band("b1", band_lo=24, band_hi=25, running_max_c=24.3, day_decided=True,
                      s5_allowed=True, yes_price=0.60)
    signals = strat.entry_signals(make_ctx([band]))
    assert len(signals) == 1 and signals[0].severity == "critical"


def test_s5_does_not_fire_before_day_decided():
    cfg = enabled_config("s5_running_max_lock", side="YES")
    strat = S5RunningMaxLock(cfg)
    band = make_band("b1", band_lo=24, band_hi=25, running_max_c=24.3, day_decided=False,
                      s5_allowed=True, yes_price=0.60)
    assert strat.entry_signals(make_ctx([band])) == []


# --------------------------------------------------------------------------
# S6
# --------------------------------------------------------------------------

def test_s6_rejects_when_covered_basket_exceeds_one_dollar():
    cfg = enabled_config("s6_anchor_insurance", side="YES")
    strat = S6AnchorInsurance(cfg)
    bands = [make_band(f"b{i}", band_lo=24 + i, band_hi=25 + i, yes_price=0.45, yes_edge_net_pp=0.05)
             for i in range(3)]
    assert strat.entry_signals(make_ctx(bands)) == []


def test_s6_fires_and_computes_n_for_target_profit():
    cfg = enabled_config("s6_anchor_insurance", side="YES", extra={"target_profit_usd": 40.0, "width_bands": 1})
    strat = S6AnchorInsurance(cfg)
    bands = [make_band(f"b{i}", band_lo=24 + i, band_hi=25 + i, yes_price=0.20, yes_edge_net_pp=0.05)
             for i in range(3)]
    signals = strat.entry_signals(make_ctx(bands))
    assert len(signals) == 1
    sig = signals[0]
    assert sig.payload["fee_inclusive_sum_ask"] < 1.0
    assert sig.suggested_shares > 0
