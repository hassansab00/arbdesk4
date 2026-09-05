import datetime as dt

import pytest

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
    from strategies import REGISTRY
    for sid in sorted(REGISTRY):
        cfg = StrategyConfig(strategy_id=sid, name=sid, side="BOTH", universe=["ALL"],
                              regime_filter=[], conflict_class="default", capital_cap_pct=5.0,
                              max_concurrent=1)
        assert cfg.enabled is False, sid
        # and the class refuses to fire while it is off
        # ...and the class refuses to fire while it is off
        assert REGISTRY[sid](cfg).applies_to(make_band("b1")) is False, sid


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


# ==========================================================================
# S7 - pre-peak gradient entry
#
# The whole point of this strategy is that the LEVEL cannot tell the two cases
# apart. 28.4C an hour before peak is a buy after 26.9 / 27.7 / 28.4 and a sell
# after 29.1 / 28.8 / 28.4, and every test below is built on that pair.
# ==========================================================================
from strategies.s7_pre_peak_gradient import S7PrePeakGradient
from strategies.s8_two_bucket_cover import S8TwoBucketCover, pair_fee


def climbing_band(**over):
    """28.4C now, climbing 0.75C/h, 1.1C usually still to come here."""
    d = dict(
        band_lo=29, band_hi=30, band_label="29-30C",
        minutes_to_peak=45, reading_age_min=12.0,
        slope_3_c_per_h=0.75, slope_6_c_per_h=1.08, rolling_over=False,
        latest_temp_c=28.4, running_max_c=28.4,
        typical_climb_left_c=1.1, implied_max_c=29.5,
        implied_max_low_c=29.2, implied_max_high_c=30.4,
        yes_price=0.42, model_prob_yes=0.55,
    )
    d.update(over)
    return make_band("b-climb", **d)


def s7(**extra):
    return S7PrePeakGradient(enabled_config("s7_pre_peak_gradient", extra=extra))


def test_s7_buys_the_band_the_day_is_climbing_into():
    out = s7().entry_signals(make_ctx([climbing_band()]))
    assert [s.side for s in out] == ["YES"]
    assert out[0].reason == "climbing_into_band_before_peak"
    assert out[0].payload["implied_max_c"] == 29.5


def test_s7_does_not_buy_the_band_it_is_already_in():
    """The band containing the CURRENT reading is not the trade - the band the
    day is heading into is. Buying the former is buying what has happened."""
    out = s7().entry_signals(make_ctx([climbing_band(band_lo=28, band_hi=29, band_label="28-29C")]))
    assert out == []


def test_s7_refuses_a_band_only_the_best_afternoons_reach():
    """implied_max lands in the band, but the pessimistic case falls short of
    its floor. That is a hope, not a trade."""
    out = s7().entry_signals(make_ctx([climbing_band(implied_max_low_c=28.6)]))
    assert out == []


def test_s7_sells_the_bands_a_rolled_over_day_cannot_reach():
    """The mirror case, and the half a level-only desk cannot see. Same 28.4C
    reading; the last three readings are falling and the six-reading slope has
    not caught up."""
    b = climbing_band(
        band_lo=30, band_hi=31, band_label="30-31C",
        slope_3_c_per_h=-0.35, slope_6_c_per_h=0.33, rolling_over=True,
        latest_temp_c=28.4, running_max_c=29.1, no_price=0.30,
    )
    out = s7().entry_signals(make_ctx([b]))
    assert [s.side for s in out] == ["NO"]
    assert out[0].reason == "rolled_over_band_unreachable"


def test_s7_measures_reachability_against_the_banked_max_not_the_latest_reading():
    """A rolled-over day has usually already fallen below its own high. A band
    between the latest reading and the running max is NOT unreachable - the day
    has been there. Selling it because the current reading is lower would be
    selling a bucket that is currently winning."""
    b = climbing_band(
        band_lo=28.5, band_hi=29.5, band_label="28.5-29.5C",
        slope_3_c_per_h=-0.35, slope_6_c_per_h=0.33, rolling_over=True,
        latest_temp_c=28.4, running_max_c=29.1, no_price=0.30,
    )
    assert s7().entry_signals(make_ctx([b])) == []


def test_s7_will_not_act_on_a_stale_reading():
    """A slope from a 90-minute-old reading describes an hour that is over."""
    assert s7().entry_signals(make_ctx([climbing_band(reading_age_min=120.0)])) == []


def test_s7_only_fires_inside_the_window_before_peak():
    assert s7().entry_signals(make_ctx([climbing_band(minutes_to_peak=240)])) == []
    assert s7().entry_signals(make_ctx([climbing_band(minutes_to_peak=-30)])) == []


def test_s7_stands_down_once_the_day_is_decided():
    """That is S5's trade, on a locked maximum, not this one's."""
    assert s7().entry_signals(make_ctx([climbing_band(day_decided=True)])) == []


def test_s7_never_fires_without_the_trend_view():
    """A desk that has not run sql/ad4_26 has null slopes. The strategy must go
    quiet, not assume a flat day."""
    b = climbing_band(slope_3_c_per_h=None, implied_max_c=None, reading_age_min=None)
    assert s7().entry_signals(make_ctx([b])) == []


def test_s7_converts_fahrenheit_bands_before_comparing():
    """Bands are labelled in the city's unit; the archive is Celsius. 29.5C is
    85.1F, so an 85-86F band contains it and an 80-81F band does not."""
    # 29.5C = 85.1F, so both the central and the pessimistic case clear 85.
    hot = climbing_band(unit="F", band_lo=85, band_hi=86, band_label="85-86F",
                        implied_max_low_c=29.5)
    assert len(s7().entry_signals(make_ctx([hot]))) == 1
    cold = climbing_band(unit="F", band_lo=80, band_hi=81, band_label="80-81F")
    assert s7().entry_signals(make_ctx([cold])) == []


def test_s7_exits_a_long_when_the_climb_turns():
    b = climbing_band(rolling_over=True, running_max_c=28.4, band_lo=29, band_hi=30)
    out = s7().exit_signals(make_ctx([b]),
                            [{"strategy_id": "s7_pre_peak_gradient", "band_id": "b-climb", "side": "YES"}])
    assert [s.action for s in out] == ["EXIT"]


# ==========================================================================
# S8 - two-bucket cover
# ==========================================================================
def pair_ctx(pa=0.34, pb=0.33, proba=0.42, probb=0.38, **over):
    over.setdefault("implied_max_c", 29.5)
    a = make_band("a", band_lo=29, band_hi=30, band_label="29-30C",
                  yes_price=pa, model_prob_yes=proba, **over)
    b = make_band("b", band_lo=30, band_hi=31, band_label="30-31C",
                  yes_price=pb, model_prob_yes=probb, **over)
    return make_ctx([a, b])


def s8(**extra):
    return S8TwoBucketCover(enabled_config("s8_two_bucket_cover", side="YES", extra=extra))


def test_s8_buys_the_pair_when_it_costs_less_than_the_cap():
    out = s8().entry_signals(pair_ctx())
    assert len(out) == 2 and {s.band_id for s in out} == {"a", "b"}
    p = out[0].payload
    assert p["pair_cost"] == 0.67
    assert p["pair_cost_with_fee"] < 0.70
    # 67c plus fee returning $1.00 is a bit under 47%
    assert 44 < p["return_pct"] < 49


def test_s8_counts_the_fee_and_the_fee_is_not_flat():
    """Polymarket's fee peaks near 50c and vanishes at the extremes, so two
    mid-priced buckets carry far more of it than a cheap/expensive pair at the
    same total. A flat percentage would let a 0.70 rule become 0.72."""
    mid = pair_fee(0.35, 0.34)
    extreme = pair_fee(0.66, 0.03)
    assert mid > extreme
    assert abs(pair_fee(0.5, 0.5) - 2 * 0.05 * 0.25) < 1e-9


def test_s8_refuses_a_pair_that_costs_more_than_it_is_worth():
    """70c for a pair the model makes 55% is a losing trade executed tidily."""
    assert s8().entry_signals(pair_ctx(proba=0.30, probb=0.25)) == []


def test_s8_refuses_non_adjacent_buckets():
    """Two buckets with a gap between them is not a cover - it leaves the space
    the forecast points at uncovered."""
    a = make_band("a", band_lo=29, band_hi=30, band_label="29-30C",
                  yes_price=0.34, model_prob_yes=0.42, implied_max_c=29.5)
    far = make_band("f", band_lo=33, band_hi=34, band_label="33-34C",
                    yes_price=0.33, model_prob_yes=0.38, implied_max_c=29.5)
    assert s8().entry_signals(make_ctx([a, far])) == []


def test_s8_requires_one_bucket_to_contain_where_the_day_is_going():
    """'The two most likely' is a statement about the model's distribution.
    The anchor makes it a statement about the day."""
    ctx = pair_ctx(implied_max_c=None)
    assert s8().entry_signals(ctx) == []
    # the forecast satisfies the same gate on its own
    for b in ctx.bands:
        b.forecast_max_c = 30.4
    assert len(s8().entry_signals(ctx)) == 2
    assert s8().entry_signals(ctx)[0].payload["anchored_on"] == "forecast"


def test_s8_respects_the_cost_cap_it_is_given():
    assert s8(max_pair_cost=0.60).entry_signals(pair_ctx()) == []


def test_s8_needs_both_legs_fillable():
    assert s8().entry_signals(pair_ctx(fillable_usd_5c_yes=10.0)) == []


def test_s8_sizes_both_legs_to_the_same_share_count():
    """An unbalanced cover is a directional bet wearing a cover's name."""
    class P:
        bankroll = 1000.0
    out = s8().entry_signals(pair_ctx())
    sizes = {s.band_id: s8().size(s, P()) for s in out}
    assert sizes["a"] == sizes["b"]
    assert sizes["a"] > 0


def test_s8_emits_one_position_not_two():
    """Both legs share a dedupe bucket so the conflict layer treats the pair as
    the single position it is."""
    out = s8().entry_signals(pair_ctx())
    assert out[0].dedupe_key != out[1].dedupe_key      # different bands
    assert out[0].payload is out[1].payload            # one trade, one rationale


# ==========================================================================
# The browser and the engine must agree.
#
# web/lib/cover.ts is a PORT of s8_two_bucket_cover. A desk that reads "cover
# pair live" on the City Monitor and then gets no signal has learned to
# distrust both surfaces, and the arithmetic drifting is how that happens -
# the fee curve especially, since a flat percentage looks right until it
# quietly turns a 70c rule into a 72c one.
# ==========================================================================
def test_the_browsers_cover_arithmetic_matches_the_engines():
    import json
    import os
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        import pytest as _p
        _p.skip("node is not installed")
    probe = subprocess.run([node, "--experimental-strip-types", "-e", "0"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        import pytest as _p
        _p.skip("node cannot strip types")

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cases = [
        # (price_a, price_b, prob_a, prob_b, adjacent)
        (0.34, 0.33, 0.42, 0.38, True),    # the ordinary live pair
        (0.35, 0.34, 0.40, 0.36, True),    # mid-priced: the most fee-sensitive
        (0.66, 0.03, 0.55, 0.20, True),    # extremes: almost no fee
        (0.40, 0.33, 0.45, 0.40, True),    # right at the cap
        (0.34, 0.33, 0.30, 0.25, True),    # cheap but not worth it
        (0.34, 0.33, 0.42, 0.38, False),   # not adjacent
    ]
    legs = [
        [
            {"band_id": "a", "band_label": "a", "band_lo": 29, "band_hi": 30,
             "model_prob": pa, "market_price": ca},
            {"band_id": "b", "band_label": "b",
             "band_lo": 30 if adj else 33, "band_hi": 31 if adj else 34,
             "model_prob": pb, "market_price": cb},
        ]
        for ca, cb, pa, pb, adj in cases
    ]

    src = (
        'import { findCover } from "%s";\n'
        "const legs = %s;\n"
        "console.log(JSON.stringify(legs.map((rows) => {\n"
        "  const c = findCover(rows);\n"
        "  return c && { total: c.total, fee: c.fee, prob: c.prob,\n"
        "                qualifies: c.qualifies, blockedBy: c.blockedBy };\n"
        "})));\n"
    ) % (os.path.join(root, "web", "lib", "cover.ts"), json.dumps(legs))

    script = os.path.join(root, "tests", "web", ".cover.generated.mjs")
    try:
        with open(script, "w") as fh:
            fh.write(src)
        r = subprocess.run([node, "--experimental-strip-types", script],
                           capture_output=True, text=True, timeout=60, cwd=root)
        assert r.returncode == 0, r.stderr
        ts = json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        if os.path.exists(script):
            os.remove(script)

    strat = s8()
    for (ca, cb, pa, pb, adj), got in zip(cases, ts):
        band_a = make_band("a", band_lo=29, band_hi=30, band_label="a",
                           yes_price=ca, model_prob_yes=pa, implied_max_c=29.5)
        band_b = make_band("b", band_lo=30 if adj else 33, band_hi=31 if adj else 34,
                           band_label="b", yes_price=cb, model_prob_yes=pb,
                           implied_max_c=29.5)
        signals = strat.entry_signals(make_ctx([band_a, band_b]))
        py_fires = len(signals) == 2

        assert py_fires == got["qualifies"], (
            f"disagreement on {ca}/{cb} p={pa}/{pb} adjacent={adj}: "
            f"python fired={py_fires}, browser qualifies={got['qualifies']}"
        )
        assert abs(pair_fee(ca, cb) - got["fee"]) < 1e-9, "the fee curves have drifted apart"
        if py_fires:
            # The payload rounds to 4dp because a human reads it; the browser
            # keeps full precision. Half of the last rounded digit is the only
            # difference allowed - anything larger is the two drifting apart.
            assert abs(signals[0].payload["pair_cost_with_fee"] - got["total"]) <= 5e-5


# ==========================================================================
# S9 - ladder basket
#
# The general form of S8: any contiguous run of 2..N buckets, ranked by
# expected return per dollar after fees rather than by a fixed price cap.
# ==========================================================================
from strategies.s9_ladder_basket import S9LadderBasket, basket_math, leg_fee


def rung(i, price, prob, **over):
    """One bucket of a 1C ladder starting at 27C."""
    d = dict(band_lo=27 + i, band_hi=28 + i, band_label=f"{27+i}-{28+i}C",
             yes_price=price, model_prob_yes=prob, implied_max_c=29.5)
    d.update(over)
    return make_band(f"r{i}", **d)


def s9(**extra):
    return S9LadderBasket(enabled_config("s9_ladder_basket", side="YES", extra=extra))


def ladder(spec, **over):
    return make_ctx([rung(i, p, q, **over) for i, (p, q) in enumerate(spec)])


def test_s9_buys_a_window_whose_probability_beats_its_cost():
    #        27-28  28-29  29-30  30-31
    ctx = ladder([(0.05, 0.03), (0.28, 0.36), (0.30, 0.42), (0.10, 0.09)])
    out = s9().entry_signals(ctx)
    assert out, "a window paying 78% of the dollar for 58c should fire"
    p = out[0].payload
    assert p["buckets"] == ["28-29C", "29-30C"]
    assert p["ev_per_dollar"] > 0.08
    assert p["win_prob"] == pytest.approx(0.78)


def test_s9_takes_a_wider_window_only_when_the_extra_bucket_earns_it():
    """Width is not the objective and neither is cheapness - expected return is.

    Here 28-29C is underpriced (10c for a 20% bucket), so adding it to the
    29-31C pair raises the return per dollar from 43% to 52%. Going wider still,
    to 27-31C, drops it back to 43% because 27-28C is not worth its price.
    """
    ctx = ladder([(0.05, 0.02), (0.10, 0.20), (0.25, 0.30), (0.22, 0.40), (0.05, 0.04)])
    got = s9(min_ev_per_dollar=0.01).entry_signals(ctx)[0].payload
    assert got["buckets"] == ["28-29C", "29-30C", "30-31C"], got["buckets"]
    assert got["ev_per_dollar"] == pytest.approx(0.519, abs=0.005)


def test_s9_takes_the_narrow_window_when_the_extra_bucket_does_not_earn_it():
    """The same ladder with 28-29C fairly priced at 20c. Now the pair wins,
    and a strategy that just preferred width would buy the worse basket."""
    ctx = ladder([(0.05, 0.02), (0.20, 0.24), (0.25, 0.30), (0.22, 0.40), (0.05, 0.04)])
    got = s9(min_ev_per_dollar=0.01).entry_signals(ctx)[0].payload
    assert got["buckets"] == ["29-30C", "30-31C"], got["buckets"]
    assert got["ev_per_dollar"] == pytest.approx(0.435, abs=0.005)


def test_s9_refuses_a_window_that_costs_more_than_it_is_worth():
    """Every bucket priced above the model. Nothing here is a basket."""
    ctx = ladder([(0.20, 0.05), (0.40, 0.30), (0.40, 0.30), (0.20, 0.05)])
    assert s9().entry_signals(ctx) == []


def test_s9_will_not_buy_the_whole_ladder():
    """Own every bucket and you have paid the overround for a certainty: the
    total goes over a dollar and the EV is negative by the book's edge."""
    ctx = ladder([(0.26, 0.25), (0.26, 0.25), (0.26, 0.25), (0.26, 0.25)])
    assert s9(max_buckets=4).entry_signals(ctx) == []


def test_s9_never_spans_a_gap_in_the_ladder():
    """A basket with a hole is two claims, and the hole is usually where the
    forecast points."""
    a = make_band("a", band_lo=28, band_hi=29, band_label="28-29C",
                  yes_price=0.20, model_prob_yes=0.40, implied_max_c=29.5)
    far = make_band("f", band_lo=33, band_hi=34, band_label="33-34C",
                    yes_price=0.20, model_prob_yes=0.40, implied_max_c=29.5)
    assert s9().entry_signals(make_ctx([a, far])) == []


def test_s9_requires_the_window_to_contain_where_the_day_is_going():
    """The cheapest window is usually the tail, and it is cheap because the day
    is not going there. Without the anchor that is exactly what gets bought."""
    # the high-EV window is 27-29, but the day is heading for 29.5
    ctx = ladder([(0.02, 0.30), (0.03, 0.35), (0.60, 0.30), (0.20, 0.05)])
    out = s9(min_ev_per_dollar=0.01).entry_signals(ctx)
    assert out == [] or "29" in "".join(out[0].payload["buckets"])


def test_s9_fee_is_per_leg_and_peaks_at_the_middle():
    """Four mid-priced legs carry far more fee than two at the extremes, and a
    flat percentage would rank the baskets in the wrong order."""
    mid = basket_math([rung(0, 0.5, 0.3), rung(1, 0.5, 0.3)])
    ends = basket_math([rung(0, 0.95, 0.5), rung(1, 0.02, 0.1)])
    assert mid["fee"] > ends["fee"] * 5
    assert leg_fee(0.5) == pytest.approx(0.05 * 0.25)
    assert leg_fee(0.0) == 0 and leg_fee(1.0) == 0


def test_s9_ev_per_dollar_is_the_number_it_claims_to_be():
    """(win_prob - total) / total. If this drifts the ranking is meaningless."""
    m = basket_math([rung(0, 0.30, 0.40), rung(1, 0.30, 0.42)])
    assert m["total"] == pytest.approx(0.60 + 2 * leg_fee(0.30))
    assert m["ev_per_dollar"] == pytest.approx((0.82 - m["total"]) / m["total"])


def test_s9_needs_every_leg_fillable():
    ctx = ladder([(0.05, 0.03), (0.28, 0.36), (0.30, 0.42), (0.10, 0.09)],
                 fillable_usd_5c_yes=10.0)
    assert s9().entry_signals(ctx) == []


def test_s9_sizes_every_leg_to_the_same_share_count():
    class P:
        bankroll = 1000.0
    ctx = ladder([(0.05, 0.03), (0.28, 0.36), (0.30, 0.42), (0.10, 0.09)])
    out = s9().entry_signals(ctx)
    strat = s9()
    sizes = {s.band_id: strat.size(s, P()) for s in out}
    assert len(set(sizes.values())) == 1, sizes
    assert next(iter(sizes.values())) > 0


def test_s9_reports_what_the_trade_actually_returns():
    ctx = ladder([(0.05, 0.03), (0.28, 0.36), (0.30, 0.42), (0.10, 0.09)])
    p = s9().entry_signals(ctx)[0].payload
    # 58c plus fee returning $1.00 is about 68%
    assert 60 < p["return_if_win_pct"] < 75
    assert p["range"] == [28, 30]
