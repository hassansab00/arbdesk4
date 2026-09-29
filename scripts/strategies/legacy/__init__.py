"""The retired strategies (plan v2 P8.2 step 5).

Hassan, 29 Sep: "Retire all of s1, s3-s9". Their ids are `retired` in
strategy_state (20260929080000_the_old_strategies_retire.sql), so they are
disabled for good and the live signal engine never loads them: they are not
in strategies.REGISTRY. The engine strategies replace them (P8.1: S10 for
S5/S7, S11 for S3/S8/S9 and the S6 idea, S12 for S4, S13 research for S1).

The code is kept, unchanged, for replay comparisons: the backtest can still
run these ids through LEGACY_REGISTRY (backtest/engine.py). The live signal
engine (signal_engine.py, through strategy_rules.run_strategies) never
imports this package.
"""
from strategies.legacy.s1_buy_low_sell_signal import S1BuyLowSellSignal
from strategies.legacy.s3_concentration import S3Concentration
from strategies.legacy.s4_tail_fade import S4TailFade
from strategies.legacy.s5_running_max_lock import S5RunningMaxLock
from strategies.legacy.s6_anchor_insurance import S6AnchorInsurance
from strategies.legacy.s7_pre_peak_gradient import S7PrePeakGradient
from strategies.legacy.s8_two_bucket_cover import S8TwoBucketCover
from strategies.legacy.s9_ladder_basket import S9LadderBasket

LEGACY_REGISTRY = {
    "s1_buy_low_sell_signal": S1BuyLowSellSignal,
    "s3_concentration": S3Concentration,
    "s4_tail_fade": S4TailFade,
    "s5_running_max_lock": S5RunningMaxLock,
    "s6_anchor_insurance": S6AnchorInsurance,
    "s7_pre_peak_gradient": S7PrePeakGradient,
    "s8_two_bucket_cover": S8TwoBucketCover,
    "s9_ladder_basket": S9LadderBasket,
}

__all__ = ["LEGACY_REGISTRY"]
